"""优先级评分层（Workflow 节点 4）——AI 只打维度分，加权计算由代码执行（「无黑箱」铁律）。

权重表 W-v0.1（05 框架 v1.1 §6，V1 待校准假设，非行业标准）：
  业务影响 40% / 紧迫性 25% / 用户覆盖 20% / 信号强度 15%
分档阈值：加权 ≥ 3.5 高；≥ 2.5 中；< 2.5 低。

口径（07 v1.2）：输出为「优先级建议」，不是产品排期决策。
"""
WEIGHTS = {"impact": 0.40, "urgency": 0.25, "coverage": 0.20, "signal": 0.15}
WEIGHTS_VERSION = "W-v0.1"
THRESHOLD_HIGH = 3.5
THRESHOLD_MID = 2.5


def weighted_score(dims: dict) -> float:
    return round(sum(dims[k] * w for k, w in WEIGHTS.items()), 2)


def tier(score: float) -> str:
    if score >= THRESHOLD_HIGH:
        return "高"
    if score >= THRESHOLD_MID:
        return "中"
    return "低"


def build_priority_table(dimension_scores: list, explicit_needs: list) -> list:
    """输入 AI 维度分 + 节点1 显性需求，输出 F6 优先级建议表。"""
    content_by_id = {n["id"]: n["content"] for n in explicit_needs}
    table = []
    for item in dimension_scores:
        score = weighted_score(item)
        table.append({
            "id": item["id"],
            "content": content_by_id.get(item["id"], "（警告：维度分引用了未知需求编号）"),
            "impact": item["impact"], "urgency": item["urgency"],
            "coverage": item["coverage"], "signal": item["signal"],
            "reason": item.get("reason", ""),
            "weighted": score,
            "tier": tier(score),
        })
    table.sort(key=lambda r: r["weighted"], reverse=True)
    return table
