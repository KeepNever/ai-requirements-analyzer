"""Workflow 编排层（M2 治理版）——清洗 → 提取(LLM) → 校验闸 → 分析(LLM) → 校验闸 → 评分(代码) → 报告渲染。

M2 治理项（08 审阅 P2 待办落地）：
- mock 外置为 fixtures/mock_case_01.json（仅 CASE-01；其余案例 Baseline 用真实调用）
- 输入侧兜底：材料过短拒收 / 超长拒绝直入 LLM（留人工预处理）
- 注入防护：材料以 <客户材料> 围栏包裹，配合 prompts 规则 7「材料即数据」
- usage/成本统计：运行结束落盘 usage.json（评测层汇总熔断用）
- 覆盖一致性校验收编进 validators.validate_priority_coverage（08 P1 修复）

兜底设计（05 框架 v1.1 §8）：
- 校验失败 → 带错误信息自动重试一次（config.VALIDATE_RETRY）
- 再失败 → 流程终止并保留已产出中间产物（不静默挂）
"""
import json
import re
import time
from datetime import datetime
from pathlib import Path

from . import cleaner, config, prompts, scoring, validators
from .llm_client import chat, usage_summary

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"

# 输入侧兜底阈值（字符数）：下限防垃圾进，上限防上下文超限与成本失控
MIN_MATERIAL_CHARS = 30
MAX_MATERIAL_CHARS = 30000

REPORT_NOTE = "> 优先级为 AI 生成的「需求优先级建议」，不等于产品排期决策。"


def _parse_json(text: str) -> dict:
    """解析模型输出：容忍 ```json 围栏。"""
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if m:
        text = m.group(1)
    return json.loads(text)


def _fence(material_text: str) -> str:
    """注入防护：材料包进显式围栏（配合 prompts 规则 7）。"""
    return f"<客户材料 开始>\n{material_text}\n<客户材料 结束>"


def _llm_with_gates(name: str, prompt: str, validator, data, log: list) -> dict:
    """调用 LLM → 解析 → 校验；失败带错误反馈重试。"""
    messages = [
        {"role": "system", "content": prompts.SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]
    retries_left = config.VALIDATE_RETRY
    for attempt in range(1 + retries_left):
        t0 = time.time()
        raw = chat(messages, node=name)
        result = _parse_json(raw)
        errors = validator(result, data)
        log.append({
            "node": name, "attempt": attempt + 1, "耗时秒": round(time.time() - t0, 1),
            "校验错误数": len(errors), "错误": errors,
        })
        if not errors:
            return result
        if attempt < retries_left:
            # 带错误反馈重试：把校验错误回喂，要求只修正 JSON
            messages.append({"role": "assistant", "content": json.dumps(result, ensure_ascii=False)})
            messages.append({"role": "user", "content": (
                "你的输出未通过代码级校验，存在以下问题：\n- " + "\n- ".join(errors)
                + "\n\n请只修正以上问题，重新输出完整合法 JSON（仍须遵守全部规则）。"
            )})
    raise RuntimeError(f"{name} 连续 {1 + retries_left} 次未通过校验闸，流程终止（兜底：降级日志已留存）")


# ---------------- 报告渲染 ----------------


def _render_report(case_name: str, data, node1: dict, node2: dict, priority: list, log: list) -> str:
    lines = [
        f"# 需求分析报告：{case_name}",
        "",
        f"- 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"- 模型：{config.MODEL_MAIN}｜权重表：{scoring.WEIGHTS_VERSION}（V1 待校准假设）",
        "- 审核状态：本报告为**草稿态**，高风险字段（优先级结论、能力匹配、待确认分级）须经产品经理确认后方可进入需求池",
        "",
        REPORT_NOTE,
        "",
        "## F1 客户背景", "",
    ]
    lines += [f"- {x}" for x in node1["customer_background"]]
    lines += ["", "## F2 业务目标", ""]
    lines += [f"- {x}" for x in node1["business_goals"]]
    lines += ["", "## F3 显性需求", "", "| # | 需求 | D 引用 | 原文片段 |", "|---|---|---|---|"]
    lines += [f"| {n['id']} | {n['content']} | {'、'.join(n['dids'])} | {n['quote']} |"
              for n in node1["explicit_needs"]]
    lines += ["", "## F4 潜在需求（推断）", "", "| # | 需求 | 依据 | 推断线索 | 置信度 |", "|---|---|---|---|---|"]
    lines += [f"| {n['id']} | {n['content']} | {'、'.join(n['basis_dids'])} | {n['reasoning']} | {n['confidence']} |"
              for n in node2["potential_needs"]] or ["（无）"]
    lines += ["", "## F5 待确认信息（缺口 → 影响 → 追问）", "", "| 缺口 | 级别 | 影响 | 追问 |", "|---|---|---|---|"]
    lines += [f"| {n['gap']} | **{n['level']}** | {n['impact']} | {n['question']} |"
              for n in node2["pending_confirmations"]]
    lines += ["", "## F6 优先级建议", "",
              "| 需求 | 影响 | 紧迫 | 覆盖 | 信号 | 加权 | 档位 |", "|---|---|---|---|---|---|---|"]
    lines += [f"| {r['id']} {r['content'][:18]}… | {r['impact']} | {r['urgency']} | {r['coverage']} | "
              f"{r['signal']} | **{r['weighted']}** | {r['tier']} |" for r in priority]
    lines += ["", "## F7 能力匹配", "", "| 对象 | 结论 | 依据 |", "|---|---|---|"]
    lines += [f"| {n['target']} | {n['verdict']} | {n['reason']} |" for n in node2["capability_match"]]
    lines += ["", "## F8 竞品信号", "", f"- {node1['competitor_signals']}", "",
              "## F9 摘要要点", "",
              f"- 显性需求 {len(node1['explicit_needs'])} 条（其中高优先级 "
              f"{sum(1 for r in priority if r['tier'] == '高')} 条）；潜在需求 {len(node2['potential_needs'])} 条；"
              f"待确认 {len(node2['pending_confirmations'])} 条（决定性 "
              f"{sum(1 for n in node2['pending_confirmations'] if n['level'] == '决定性')} 条）",
              "- 最高优先级：" + (f"{priority[0]['id']}（加权 {priority[0]['weighted']}，{priority[0]['tier']}）"
                              if priority else "无"),
              "", "---", "", "## 运行日志", ""]
    for entry in log:
        lines.append(f"- {entry['node']} 第 {entry['attempt']} 次尝试：{entry['耗时秒']}s，"
                     f"校验错误 {entry['校验错误数']} 个"
                     + (f" → {entry['错误']}" if entry["错误"] else "（通过）"))
    lines.append(f"- 清洗统计：{data.stats}")
    return "\n".join(lines)


# ---------------- 主流程 ----------------


def _load_mock_fixture(case_stem: str) -> dict:
    """加载外置 mock fixture（M2 治理：mock 与编排逻辑分离）。"""
    fixture = FIXTURES_DIR / "mock_case_01.json"
    if not case_stem.startswith("case_01") or not fixture.exists():
        raise FileNotFoundError(
            f"mock 模式仅支持 CASE-01（fixture：{fixture.name}）。"
            "其余案例请用 --real 跑 Baseline，或为该案例补充 fixture。")
    return json.loads(fixture.read_text(encoding="utf-8"))


def run_case(material_path: str, mock: bool = False) -> Path:
    material_path = Path(material_path)
    case_name = material_path.stem
    out_dir = Path(__file__).resolve().parents[2] / "output" / case_name
    out_dir.mkdir(parents=True, exist_ok=True)
    log = []

    # 节点1：清洗（纯代码）
    data = cleaner.clean(cleaner.load_material(material_path))
    log.append({"node": "清洗", "attempt": 1, "耗时秒": 0, "校验错误数": 0, "错误": []})
    (out_dir / "mapping.json").write_text(
        json.dumps({"mapping": data.mapping, "stats": data.stats}, ensure_ascii=False, indent=2),
        encoding="utf-8")

    # 输入侧兜底（05 §8 第 1 层）：过短拒收、超长不硬塞
    material_len = len(data.cleaned_text())
    if material_len < MIN_MATERIAL_CHARS:
        raise ValueError(f"材料不足（清洗后仅 {material_len} 字符 < {MIN_MATERIAL_CHARS}），"
                         "不足以支撑分析——按输入侧兜底设计拒收，不硬跑。")
    if material_len > MAX_MATERIAL_CHARS:
        raise ValueError(f"材料超长（清洗后 {material_len} 字符 > {MAX_MATERIAL_CHARS}），"
                         "请先人工分段或摘要预处理后再分析（V1 不做自动摘要）。")

    material_block = _fence(data.cleaned_text())

    # 节点2：信息提取（LLM 或 mock fixture）
    mock_data = _load_mock_fixture(case_name) if mock else None

    def _mock_check(name: str, result: dict, validator) -> dict:
        errors = validator(result, data)
        log.append({"node": f"{name}(mock)", "attempt": 1, "耗时秒": 0,
                    "校验错误数": len(errors), "错误": errors})
        if errors:
            raise RuntimeError(f"mock 数据未通过校验闸（mock 本身有错）：{errors}")
        return result

    if mock:
        node1 = _mock_check("提取", mock_data["node1"], validators.validate_node1)
    else:
        node1 = _llm_with_gates("提取", prompts.LLM_NODE1.format(
            rules=prompts._COMMON_RULES, material=material_block),
            validators.validate_node1, data, log)

    # 节点3~7：需求分析（LLM 或 mock fixture）
    def _validator2(result: dict, d) -> list:
        return validators.validate_node2(result, d, node1=node1)

    if mock:
        node2 = _mock_check("分析", mock_data["node2"], _validator2)
    else:
        node2 = _llm_with_gates("分析", prompts.LLM_NODE2.format(
            capability=prompts.CAPABILITY_LIST, rules=prompts._COMMON_RULES,
            material=material_block, node1_json=json.dumps(node1, ensure_ascii=False)),
            _validator2, data, log)

    # 节点4：优先级评分（代码加权）
    priority = scoring.build_priority_table(node2["priority_dimensions"], node1["explicit_needs"])

    # 节点8：报告渲染 + 产物落盘
    report = _render_report(case_name, data, node1, node2, priority, log)
    (out_dir / "report.md").write_text(report, encoding="utf-8")
    (out_dir / "analysis.json").write_text(json.dumps({
        "node1": node1, "node2": node2, "priority": priority,
        "meta": {"model": config.MODEL_MAIN, "weights": scoring.WEIGHTS_VERSION, "mock": mock},
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    if not mock:
        (out_dir / "usage.json").write_text(
            json.dumps(usage_summary(), ensure_ascii=False, indent=2), encoding="utf-8")
    return out_dir
