"""Workflow 编排层（M1 最小实现）——CASE-01 单案例跑通全链路。

链路：清洗(代码) → 提取(LLM) → 校验闸 → 分析(LLM) → 校验闸 → 评分(代码) → 报告渲染(代码)

兜底设计（05 框架 v1.1 §8）落地：
- 校验失败 → 带错误信息自动重试一次（config.VALIDATE_RETRY）
- 再失败 → 记录降级日志，流程终止并保留已产出中间产物（不静默挂）
"""
import json
import re
import time
from datetime import datetime
from pathlib import Path

from . import cleaner, config, prompts, scoring, validators
from .llm_client import chat

REPORT_NOTE = "> 优先级为 AI 生成的「需求优先级建议」，不等于产品排期决策。"


def _parse_json(text: str) -> dict:
    """解析模型输出：容忍 ```json 围栏。"""
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if m:
        text = m.group(1)
    return json.loads(text)


def _llm_with_gates(name: str, prompt: str, validator, data, log: list) -> dict:
    """调用 LLM → 解析 → 校验；失败带错误反馈重试。返回 (结果, 校验记录)。"""
    messages = [
        {"role": "system", "content": prompts.SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]
    retries_left = config.VALIDATE_RETRY
    for attempt in range(1 + retries_left):
        t0 = time.time()
        raw = chat(messages)
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


# ---------------- Mock 数据（M1 离线自测用，数值与 gt_01 对齐） ----------------

MOCK_NODE1 = {
    "customer_background": [
        "华宇精工：汽车零部件制造商（冲压/焊接件），约 600 人（案例背景）",
        "关键角色：王总（生产副总，主要决策者）、李工（IT 主管，技术把关）",
        "数据散落在金蝶 K3 ERP、MES 与老系统中（D004/D006）",
    ],
    "business_goals": [
        "质量问题当天发现、当天处理，减少批量返工损失（D002）",
        "三季度产线改造完成后新产线即时上线使用（D012）",
    ],
    "explicit_needs": [
        {"id": "EN1", "content": "生产实时看板：良品率、停机时长当日可视，多名车间主任均可查看",
         "dids": ["D003", "D010"], "quote": "车间要有块实时看板"},
        {"id": "EN2", "content": "良品率阈值预警（低于 95%）即时通知，通过企业微信推送",
         "dids": ["D007", "D008"], "quote": "良品率一旦掉到 95 以下必须马上通知我"},
        {"id": "EN3", "content": "集成金蝶 K3 ERP 与设备/MES 数据",
         "dids": ["D004", "D005", "D006"], "quote": "把 ERP 和设备的数据都接进来"},
        {"id": "EN4", "content": "批次质量追溯（产线/班次维度）——客户明确表示可延后",
         "dids": ["D009"], "quote": "查某个问题批次是哪条产线、哪个班次生产的"},
    ],
    "competitor_signals": "无竞品信号（材料未提及任何竞品，不得编造）",
}

MOCK_NODE2 = {
    "potential_needs": [
        {"id": "IN1", "content": "设备老接口的数据接入风险评估与实施保障",
         "basis_dids": ["D013"], "reasoning": "李工主动表达担忧，暗示担心实施失败", "confidence": "高"},
        {"id": "IN2", "content": "以预警替代人工日报，释放文员人力",
         "basis_dids": ["D007"], "reasoning": "现行人工导表方式「人力花了还不及时」", "confidence": "中"},
    ],
    "pending_confirmations": [
        {"gap": "二十万预算是否含实施与首年服务", "level": "决定性",
         "level_reason": "预算口径决定 20 万能否覆盖总成本，若不含实施则本单不成立",
         "impact": "方案配置档位与商务策略", "question": "预算口径是软件年费还是含实施总包？"},
        {"gap": "设备/MES 数据格式与接口现状未知", "level": "关键",
         "level_reason": "影响实施周期与报价，不决定成单",
         "impact": "C5 集成的实施周期与报价", "question": "可否安排现场勘察，确认老设备数据导出方式？"},
    ],
    "capability_match": [
        {"target": "EN1", "verdict": "匹配", "reason": "C2 实时看板，支持大屏/车间投放"},
        {"target": "EN2", "verdict": "匹配", "reason": "C4 阈值预警且明确支持企业微信推送"},
        {"target": "EN3", "verdict": "匹配", "reason": "C5 含主流 ERP 连接器（金蝶 K3 属主流）；设备老接口按 C5 限制需定制，实施前勘察"},
        {"target": "EN4", "verdict": "部分匹配", "reason": "批次维度追溯需定制报表（C1 限制条款）"},
    ],
    "priority_dimensions": [
        {"id": "EN1", "impact": 5, "urgency": 4, "coverage": 3, "signal": 5,
         "reason": "整批返工损失（D002）+ 三季度期限（D012）；信号：D002/D003/D010 反复且王总主动"},
        {"id": "EN3", "impact": 4, "urgency": 3, "coverage": 4, "signal": 4,
         "reason": "一切看板需求的前置（D005）；信号：D004/D005/D006/D013 多处"},
        {"id": "EN2", "impact": 4, "urgency": 4, "coverage": 3, "signal": 3,
         "reason": "「必须马上」（D007）；企微办公（D008）"},
        {"id": "EN4", "impact": 3, "urgency": 1, "coverage": 2, "signal": 1,
         "reason": "客户明确「以后再说」（D009）"},
    ],
}

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

    material_block = data.cleaned_text()

    # 节点2：信息提取（LLM 或 mock）
    if mock:
        node1, log = MOCK_NODE1, log + [{"node": "提取(mock)", "attempt": 1, "耗时秒": 0, "校验错误数": 0, "错误": []}]
        errors = validators.validate_node1(node1, data)
        if errors:
            raise RuntimeError(f"mock 数据未通过校验闸（mock 本身有错）：{errors}")
    else:
        node1 = _llm_with_gates("提取", prompts.LLM_NODE1.format(
            rules=prompts._COMMON_RULES, material=material_block),
            validators.validate_node1, data, log)

    # 节点3~7：需求分析（LLM 或 mock）
    if mock:
        node2, log = MOCK_NODE2, log + [{"node": "分析(mock)", "attempt": 1, "耗时秒": 0, "校验错误数": 0, "错误": []}]
        errors = validators.validate_node2(node2, data)
        if errors:
            raise RuntimeError(f"mock 数据未通过校验闸（mock 本身有错）：{errors}")
    else:
        node2 = _llm_with_gates("分析", prompts.LLM_NODE2.format(
            capability=prompts.CAPABILITY_LIST, rules=prompts._COMMON_RULES,
            material=material_block, node1_json=json.dumps(node1, ensure_ascii=False)),
            validators.validate_node2, data, log)

    # 节点4：优先级评分（代码加权）
    priority = scoring.build_priority_table(node2["priority_dimensions"], node1["explicit_needs"])

    # 节点8：报告渲染 + 产物落盘
    report = _render_report(case_name, data, node1, node2, priority, log)
    (out_dir / "report.md").write_text(report, encoding="utf-8")
    (out_dir / "analysis.json").write_text(json.dumps({
        "node1": node1, "node2": node2, "priority": priority,
        "meta": {"model": config.MODEL_MAIN, "weights": scoring.WEIGHTS_VERSION, "mock": mock},
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_dir
