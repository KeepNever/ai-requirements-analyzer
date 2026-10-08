"""评分层单测：加权公式、分档阈值（含边界）、排序。"""
from pipeline.scoring import build_priority_table, tier, weighted_score


def test_weighted_known_values():
    # GT-01 EN1：5/4/3/5 → 4.35；EN4：3/1/2/1 → 2.00
    assert weighted_score({"impact": 5, "urgency": 4, "coverage": 3, "signal": 5}) == 4.35
    assert weighted_score({"impact": 3, "urgency": 1, "coverage": 2, "signal": 1}) == 2.00


def test_tier_boundaries():
    assert tier(3.5) == "高"   # 阈值含边界
    assert tier(3.49) == "中"
    assert tier(2.5) == "中"
    assert tier(2.49) == "低"


def test_priority_table_sorted_and_mapped():
    needs = [{"id": "EN1", "content": "看板"}, {"id": "EN2", "content": "预警"}]
    dims = [
        {"id": "EN2", "impact": 3, "urgency": 3, "coverage": 3, "signal": 3},  # 3.00
        {"id": "EN1", "impact": 5, "urgency": 4, "coverage": 3, "signal": 5},  # 4.35
    ]
    table = build_priority_table(dims, needs)
    assert [r["id"] for r in table] == ["EN1", "EN2"]      # 按加权降序
    assert table[0]["content"] == "看板"                    # 需求内容正确映射
    assert table[0]["tier"] == "高" and table[1]["tier"] == "中"
