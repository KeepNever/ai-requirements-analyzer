"""校验层单测：引用真实性、枚举、证据降级、覆盖一致性。"""
from pipeline.cleaner import clean
from pipeline.validators import (
    validate_node1, validate_node2, validate_priority_coverage,
)


MATERIAL = """P1 【王总】车间要有块实时看板，良品率当日可见。
P2 【李工】良品率一旦掉到 95 以下必须马上通知我。
P3 【王总】预算二十万。"""


def _data():
    return clean(MATERIAL)


def _good_node1():
    return {"customer_background": ["x"], "business_goals": ["y"], "competitor_signals": "无竞品信号",
            "explicit_needs": [
                {"id": "EN1", "content": "实时看板", "dids": ["D001"], "quote": "车间要有块实时看板"},
                {"id": "EN2", "content": "阈值预警", "dids": ["D002"], "quote": "必须马上通知我"},
            ]}


def test_node1_pass():
    assert validate_node1(_good_node1(), _data()) == []


def test_quote_forgery_caught():
    bad = _good_node1()
    bad["explicit_needs"][0]["quote"] = "这句话材料里根本没有"
    errors = validate_node1(bad, _data())
    assert any("引用造假" in e for e in errors)


def test_missing_reference_rejected():
    bad = _good_node1()
    bad["explicit_needs"][0]["dids"] = []
    errors = validate_node1(bad, _data())
    assert any("无任何引用" in e for e in errors)


def test_nonexistent_did_caught():
    bad = _good_node1()
    bad["explicit_needs"][1]["dids"] = ["D999"]
    errors = validate_node1(bad, _data())
    assert any("D999" in e for e in errors)


def test_enum_violations():
    d = _data()
    node2 = {"potential_needs": [], "pending_confirmations": [
        {"gap": "预算口径", "level": "超级重要", "impact": "x", "question": "q"}],
        "capability_match": [{"target": "EN1", "verdict": "大概是吧", "reason": "r"}],
        "priority_dimensions": [{"id": "EN1", "impact": 5, "urgency": 4, "coverage": 3, "signal": 5}]}
    errors = validate_node2(node2, d)
    assert any("级别非法" in e for e in errors)
    assert any("四档结论非法" in e for e in errors)


def test_high_confidence_without_basis_downgraded():
    d = _data()
    node2 = {"potential_needs": [
        {"id": "IN1", "content": "凭空推断", "basis_dids": [], "reasoning": "直觉", "confidence": "高"}],
        "pending_confirmations": [], "capability_match": [],
        "priority_dimensions": [{"id": "EN1", "impact": 3, "urgency": 3, "coverage": 3, "signal": 3}]}
    errors = validate_node2(node2, d)
    assert any("证据降级" in e for e in errors)


def test_dimension_out_of_range():
    d = _data()
    node2 = {"potential_needs": [], "pending_confirmations": [], "capability_match": [],
             "priority_dimensions": [{"id": "EN1", "impact": 9, "urgency": 3, "coverage": 3, "signal": 3}]}
    errors = validate_node2(node2, d)
    assert any("1~5 整数" in e for e in errors)


def test_coverage_silent_omission_blocked():
    node2 = {"priority_dimensions": [{"id": "EN1", "impact": 5, "urgency": 4, "coverage": 3, "signal": 5}]}
    errors = []
    validate_priority_coverage(_good_node1(), node2, errors)
    assert any("覆盖不全" in e for e in errors)
    errors2 = []
    validate_priority_coverage(_good_node1(), {
        "priority_dimensions": [
            {"id": "EN1", "impact": 5, "urgency": 4, "coverage": 3, "signal": 5},
            {"id": "EN2", "impact": 5, "urgency": 4, "coverage": 3, "signal": 5}]}, errors2)
    assert errors2 == []
