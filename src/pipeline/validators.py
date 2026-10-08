"""校验层——三道闸中的第一道（输出约束）与第二道（代码级校验）。

第二道闸不需要 LLM，纯代码，最硬（05 框架 v1.1 §8）：
- JSON 结构完整性（schema 必填字段）
- 引用真实性：quote 必须是所引 D 段落的连续子串（字符串匹配，防引用造假）
- 枚举合法性：置信度 / 能力匹配四档 / 待确认三级分级
- 覆盖一致性：维度打分集合必须与显性需求一一对应（防静默遗漏，08 审阅 P1 修复）

M2 治理：按字段族拆分为独立函数（单测可逐个命中），validate_node1/node2 为薄入口。

第三道闸（批评家）为实验性，M3 对照实验才启用，本文件不含。
"""
from .cleaner import CleanResult

CONFIDENCE_LEVELS = {"高", "中", "低"}
MATCH_VERDICTS = {"匹配", "部分匹配", "不支持", "待确认"}
GAP_LEVELS = {"决定性", "关键", "普通"}
DIMENSIONS = ("impact", "urgency", "coverage", "signal")


# ---------------- 通用子校验 ----------------


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


def _require_keys(item: dict, keys: tuple, errors: list, where: str):
    for key in keys:
        if not item.get(key):
            errors.append(f"{where}: 缺少 {key}")


# ---------------- 字段族子校验（独立函数，单测逐个覆盖） ----------------


def validate_explicit_needs(result: dict, data: CleanResult, errors: list):
    """F3 显性需求：引用强制 + 引用真实性。"""
    for i, need in enumerate(result.get("explicit_needs", []), 1):
        where = f"显性需求#{i}"
        _require_keys(need, ("id", "content", "dids", "quote"), errors, where)
        dids = need.get("dids") or []
        if not dids:
            errors.append(f"{where}: 无任何引用——显性需求必须带 D 编号（缺引用即非法输出）")
        _check_quotes(need.get("quote", ""), dids, data, errors, where)


def validate_potential_needs(result: dict, data: CleanResult, errors: list):
    """F4 潜在需求：置信度枚举 + 证据降级规则（高/中置信必须有依据引用）。"""
    for i, item in enumerate(result.get("potential_needs", []), 1):
        where = f"潜在需求#{i}"
        _require_keys(item, ("id", "content", "basis_dids", "reasoning", "confidence"), errors, where)
        if item.get("confidence") not in CONFIDENCE_LEVELS:
            errors.append(f"{where}: 置信度非法（{item.get('confidence')}），必须为 高/中/低")
        if item.get("confidence") in ("高", "中"):
            for did in item.get("basis_dids") or []:
                if did not in data.did_set():
                    errors.append(f"{where}: 依据引用 {did} 不存在")
            if not (item.get("basis_dids") or []):
                errors.append(f"{where}: 高/中置信度却无依据引用——按证据降级规则应降为待确认")


def validate_pending_confirmations(result: dict, errors: list):
    """F5 待确认信息：三级分级枚举。"""
    for i, item in enumerate(result.get("pending_confirmations", []), 1):
        where = f"待确认#{i}"
        _require_keys(item, ("gap", "level", "impact", "question"), errors, where)
        if item.get("level") not in GAP_LEVELS:
            errors.append(f"{where}: 级别非法（{item.get('level')}），必须为 决定性/关键/普通")


def validate_capability_match(result: dict, errors: list):
    """F7 能力匹配：四档枚举。"""
    for i, item in enumerate(result.get("capability_match", []), 1):
        where = f"能力匹配#{i}"
        _require_keys(item, ("target", "verdict", "reason"), errors, where)
        if item.get("verdict") not in MATCH_VERDICTS:
            errors.append(f"{where}: 四档结论非法（{item.get('verdict')}）")


def validate_priority_dimensions(result: dict, errors: list):
    """维度打分：四维 1~5 整数。"""
    for i, item in enumerate(result.get("priority_dimensions", []), 1):
        where = f"维度打分#{i}"
        for key in ("id",) + DIMENSIONS:
            if key not in item:
                errors.append(f"{where}: 缺少 {key}")
        for d in DIMENSIONS:
            v = item.get(d)
            if not isinstance(v, int) or not 1 <= v <= 5:
                errors.append(f"{where}: 维度 {d} 必须为 1~5 整数（当前 {v}）")


def validate_priority_coverage(node1: dict, node2: dict, errors: list):
    """覆盖一致性（08 审阅 P1 修复）：维度打分必须与显性需求一一对应。

    若 AI 漏给某条显性需求打维度分，该需求将不进优先级表——静默遗漏比格式错误更危险。
    """
    scored = {d.get("id") for d in node2.get("priority_dimensions", [])}
    expected = {n["id"] for n in node1.get("explicit_needs", [])}
    if scored != expected:
        errors.append(
            f"维度打分覆盖不全：显性需求 {sorted(expected)} vs 已打分 {sorted(scored)}，必须一一对应（不多不少）"
        )


# ---------------- 节点级入口（薄封装） ----------------


def validate_node1(result: dict, data: CleanResult) -> list:
    """节点1（提取）校验，返回错误列表（空 = 通过）。"""
    errors = []
    for key in ("customer_background", "business_goals", "explicit_needs", "competitor_signals"):
        if key not in result:
            errors.append(f"缺少必填字段：{key}")
    if errors:
        return errors  # 结构缺失，后续字段族校验无意义
    validate_explicit_needs(result, data, errors)
    return errors


def validate_node2(result: dict, data: CleanResult, node1: dict | None = None) -> list:
    """节点2（分析）校验。传入 node1 时额外执行覆盖一致性校验。"""
    errors = []
    for key in ("potential_needs", "pending_confirmations", "capability_match", "priority_dimensions"):
        if key not in result:
            errors.append(f"缺少必填字段：{key}")
    if errors:
        return errors
    validate_potential_needs(result, data, errors)
    validate_pending_confirmations(result, errors)
    validate_capability_match(result, errors)
    validate_priority_dimensions(result, errors)
    if node1 is not None:
        validate_priority_coverage(node1, result, errors)
    return errors
