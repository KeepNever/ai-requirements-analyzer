"""评测报告生成（07 v1.2 报告三要素：开发/验证口径区分、小样本声明、BC 标签）。"""
import json
from datetime import datetime
from pathlib import Path

from .badcase import BC_LABELS


def render_baseline_report(per_case: dict, aggregate: dict, badcases: list,
                           usage: dict, eval_name: str, round_note: str = "") -> str:
    today = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [
        f"# 评测报告：{eval_name}",
        "",
        f"- 生成时间：{today}",
        f"- 评测口径：**开发集**（5 个正常案例，开发期可反复使用）——本报告结果**不得**用于宣称产品普遍有效",
        f"- {round_note}" if round_note else "",
        f"- 验证集：尚未建设（Step 20 扩展 3 复杂 + 3 异常），建设前**任何**验证集口径的结论都不存在",
        "",
        "> **小样本声明**：开发集仅 5 案例，各指标分母为个位数，波动大、噪声高。结果仅用于"
        "①初步验证链路与 ②定位 Prompt 迭代方向，不得宣称普遍有效（07 v1.2 口径）。",
        "",
        "## 一、逐案例结果",
        "",
        "| 案例 | ①F1(P/R/F1) | ②关键遗漏 | ③引用错位嫌疑 | ⑤优先级一致 | ⑥能力匹配一致 | ⑦引用完整性 | BC探针 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for stem, r in per_case.items():
        f1 = r["①提取F1"]
        m2 = r["②关键遗漏率"]
        m3 = r["③事实推断区分"]
        m5 = r["⑤优先级一致性"]
        m6 = r["⑥能力匹配准确率"]
        m7 = r["⑦引用完整性"]
        bc = "、".join(p["type"] for p in r["BC自动探针"]) or "—"
        lines.append(
            f"| {stem} | {f1['precision']}/{f1['recall']}/{f1['f1']} | {m2['rate']}"
            f"（漏 {('、'.join(m2['missed']) or '无')}） | {m3['引用错位嫌疑比例']} | "
            f"{m5['率'] if m5['率'] is not None else '—'}（{m5['一致数']}/{m5['可比数']}） | "
            f"{m6['率'] if m6['率'] is not None else '—'}（{m6['一致数']}/{m6['可比数']}） | "
            f"{m7['均值'] if m7['均值'] is not None else '—'}"
            f"（完整 {m7.get('完整率') if m7.get('完整率') is not None else '—'}） | {bc} |"
        )

    lines += [
        "",
        "## 二、开发集聚合（均值，仅供参考——小样本不显著）",
        "",
        "| 指标 | 值 |",
        "|---|---|",
    ]
    for k, v in aggregate.items():
        lines.append(f"| {k} | {v} |")

    lines += [
        "",
        "## 三、Badcase 记录（症状 BC-01~10 × 归因双轨）",
        "",
        "症状=错在哪个环节的表现；归因=为什么会错（prompt/model/validator/data/gt/system）。",
        "",
        "| 案例 | 症状 | 症状说明 | 归因 | 证据 | 处置 |",
        "|---|---|---|---|---|---|",
    ]
    if badcases:
        for b in badcases:
            lines.append(f"| {b['case_id']} | {b['type']} | {BC_LABELS.get(b['type'], '（新标签，待补描述）')} | "
                         f"{b['cause']} | {b['evidence']} | {b['note'] or '待定'} |")
    else:
        lines.append("| — | 本轮自动探针未捕获确定性 Badcase，人工评分后补充 | — | — | — | — |")

    lines += [
        "",
        "### 待人工评分槽位（M2 诚实边界）",
        "",
        "- 指标④ 追问有效性：逐案例双人独立评分，交叉复核（一致率目标 ≥ 80%），LLM-as-judge 参考分不终审",
        "- 指标③ 引用支撑性语义判断：自动代理只查引用错位，「引用是否真支撑结论」需人工复核",
        "- 疑似拆分外需求（extra_ai）：逐条人工判定是真 FP 还是 07 匹配规则下的合理拆分",
        "",
        "## 四、成本",
        "",
        f"- 调用 {usage['调用次数']} 次｜输入 {usage['输入token']} tok｜输出 {usage['输出token']} tok｜"
        f"估算 **{usage['估算成本元']} 元**（DeepSeek 官方价折算）",
        "",
        "---",
        "",
        f"*报告由 evaluation 流水线自动生成，人工评分槽位补齐后本报告升级为终版。*",
    ]
    return "\n".join(x for x in lines if x is not None)
