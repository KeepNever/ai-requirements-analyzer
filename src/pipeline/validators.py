"""校验层——三道闸中的第一道（输出约束）与第二道（代码级校验）。

第二道闸不需要 LLM，纯代码，最硬（05 框架 v1.1 §8）：
- JSON 结构完整性（schema 必填字段）
- 引用真实性：quote 必须是所引 D 段落的连续子串（字符串匹配，防引用造假）
- 枚举合法性：置信度 / 能力匹配四档 / 待确认三级分级

第三道闸（批评家）为实验性，M3 对照实验才启用，本文件不含。
"""
from .cleaner import CleanResult

CONFIDENCE_LEVELS = {"高", "中", "低"}
MATCH_VERDICTS = {"匹配", "部分匹配", "不支持", "待确认"}
GAP_LEVELS = {"决定性", "关键", "普通"}


def _check_quotes(quote: str, dids: list, data: CleanResult, errors: list, where: str):
    """引用校验：所有 did 必须存在；quote 须在至少一个所引段落中命中（去空白子串匹配）。

    多引用需求的 quote 允许来自其中任意一段（其余为支撑引用），但必须真实存在——防引用造假。
    """
    if not dids:
        return
    hit = False
    for did in dids:
        try:
            seg_text = data.text_of(did).replace(" ", "")
        except KeyError:
            errors.append(f"{where}: 引用了不存在的段落 {did}")
            continue
        if quote.replace(" ", "") in seg_text:
            hit = True
    if not hit:
        errors.append(f"{where}: quote 在所引段落 {'、'.join(dids)} 中均找不到（引用造假嫌疑）→ {quote[:40]}")


def validate_node1(result: dict, data: CleanResult) -> list:
    """节点1（提取）校验，返回错误列表（空 = 通过）。"""
    errors = []
    for key in ("customer_background", "business_goals", "explicit_needs", "competitor_signals"):
        if key not in result:
            errors.append(f"缺少必填字段：{key}")
    if errors:
        return errors

    for i, need in enumerate(result.get("explicit_needs", []), 1):
        where = f"显性需求#{i}"
        for key in ("id", "content", "dids", "quote"):
            if not need.get(key):
                errors.append(f"{where}: 缺少 {key}")
        dids = need.get("dids") or []
        if not dids:
            errors.append(f"{where}: 无任何引用——显性需求必须带 D 编号（缺引用即非法输出）")
        _check_quotes(need.get("quote", ""), dids, data, errors, where)
    return errors


def validate_node2(result: dict, data: CleanResult) -> list:
    """节点2（分析）校验。"""
    errors = []
    for key in ("potential_needs", "pending_confirmations", "capability_match", "priority_dimensions"):
        if key not in result:
            errors.append(f"缺少必填字段：{key}")
    if errors:
        return errors

    for i, item in enumerate(result.get("potential_needs", []), 1):
        where = f"潜在需求#{i}"
        for key in ("id", "content", "basis_dids", "reasoning", "confidence"):
            if not item.get(key):
                errors.append(f"{where}: 缺少 {key}")
        if item.get("confidence") not in CONFIDENCE_LEVELS:
            errors.append(f"{where}: 置信度非法（{item.get('confidence')}），必须为 高/中/低")
        # 证据降级规则：中/高置信度必须有至少一处有效依据引用
        if item.get("confidence") in ("高", "中"):
            for did in item.get("basis_dids") or []:
                if did not in data.did_set():
                    errors.append(f"{where}: 依据引用 {did} 不存在")
            if not (item.get("basis_dids") or []):
                errors.append(f"{where}: 高/中置信度却无依据引用——按证据降级规则应降为待确认")

    for i, item in enumerate(result.get("pending_confirmations", []), 1):
        where = f"待确认#{i}"
        for key in ("gap", "level", "impact", "question"):
            if not item.get(key):
                errors.append(f"{where}: 缺少 {key}")
        if item.get("level") not in GAP_LEVELS:
            errors.append(f"{where}: 级别非法（{item.get('level')}），必须为 决定性/关键/普通")

    for i, item in enumerate(result.get("capability_match", []), 1):
        where = f"能力匹配#{i}"
        for key in ("target", "verdict", "reason"):
            if not item.get(key):
                errors.append(f"{where}: 缺少 {key}")
        if item.get("verdict") not in MATCH_VERDICTS:
            errors.append(f"{where}: 四档结论非法（{item.get('verdict')}）")

    dims = {"impact", "urgency", "coverage", "signal"}
    for i, item in enumerate(result.get("priority_dimensions", []), 1):
        where = f"维度打分#{i}"
        for key in ("id", "impact", "urgency", "coverage", "signal"):
            if key not in item:
                errors.append(f"{where}: 缺少 {key}")
        for d in dims:
            v = item.get(d)
            if not isinstance(v, int) or not 1 <= v <= 5:
                errors.append(f"{where}: 维度 {d} 必须为 1~5 整数（当前 {v}）")
    return errors
