"""角色标注功能测试：notes 存储、聚合端点、写权限矩阵。"""
from __future__ import annotations

import json

import auth
import notes
import web_demo


def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(notes, "DATA_PATH", tmp_path / "notes.json")
    return notes


def test_confirm_need_history(tmp_path, monkeypatch):
    _isolated(tmp_path, monkeypatch)
    user = {"email": "pm@demo.cn", "name": "林然"}
    notes.confirm_need("case", "c1", "EN1", "预算已确认", user)
    entry = notes.confirm_need("case", "c1", "EN1", "二次确认", user)
    assert entry["status"] == "confirmed"
    assert len(entry["history"]) == 2
    assert entry["history"][-1]["note"] == "二次确认"
    assert notes.need_states("case", "c1")["EN1"]["status"] == "confirmed"


def test_reply_and_presale_note_append(tmp_path, monkeypatch):
    _isolated(tmp_path, monkeypatch)
    notes.add_reply("project", "p1", "预算缺口", "预算范围？", "本期 50 万", {"name": "陈屿"})
    notes.add_reply("project", "p1", "预算缺口", "预算范围？", "补充：含定制", {"name": "陈屿"})
    notes.add_presale_note("project", "p1", "区域下钻", "部分匹配", "两层下钻需定制",
                           {"name": "周宁"})
    item = notes.get("project", "p1")
    assert len(item["replies"]) == 2 and len(item["presale_notes"]) == 1
    audit = notes.audit_entries("project", "p1")
    types = [a["type"] for a in audit]
    assert types.count("客户回复") == 2 and types.count("售前意见") == 1


def test_empty_reply_rejected(tmp_path, monkeypatch):
    _isolated(tmp_path, monkeypatch)
    try:
        notes.add_reply("case", "c1", "", "q", "  ", {"name": "陈屿"})
        raise AssertionError("空回复应被拒绝")
    except ValueError:
        pass


def test_write_permissions_matrix():
    assert auth.can("pm", "note_confirm") and not auth.can("sales", "note_confirm")
    assert auth.can("sales", "note_reply") and not auth.can("pm", "note_reply")
    assert auth.can("presale", "note_presale") and auth.can("pm", "note_presale")
    assert not auth.can("admin", "note_presale")
    assert auth.can("sales", "my_questions") and auth.can("pm", "my_questions")
    assert auth.can("presale", "cap_review") and not auth.can("sales", "cap_review")


def test_aggregate_questions_marks_answered(tmp_path, monkeypatch):
    _isolated(tmp_path, monkeypatch)
    monkeypatch.setattr(web_demo, "build_overview", lambda: [
        {"id": "c1", "name": "案例一", "source": "case", "questions": 2}])
    monkeypatch.setattr(web_demo, "load_report_payload", lambda x: {
        "source": "case", "name": "c1", "run_id": "c1",
        "analysis": {"node2": {"pending_confirmations": [
            {"gap": "预算", "level": "决定性", "impact": "i1", "question": "预算多少？"},
            {"gap": "时间", "level": "普通", "impact": "i2", "question": "何时上线？"}]}}})
    notes.add_reply("case", "c1", "预算", "预算多少？", "50 万", {"name": "陈屿"})
    out = web_demo.aggregate_questions()
    by_q = {x["question"]: x for x in out}
    assert by_q["预算多少？"]["answered"] is True and by_q["预算多少？"]["reply"] == "50 万"
    assert by_q["何时上线？"]["answered"] is False
    # 决定性 + 未答排最前
    assert out[0]["question"] == "何时上线？"


def test_aggregate_capabilities_attaches_notes(tmp_path, monkeypatch):
    _isolated(tmp_path, monkeypatch)
    monkeypatch.setattr(web_demo, "build_overview", lambda: [
        {"id": "p1", "name": "项目一", "source": "project", "questions": 0}])
    monkeypatch.setattr(web_demo, "load_report_payload", lambda x: {
        "source": "project", "name": "p1", "run_id": "r1",
        "analysis": {"node2": {"capability_match": [
            {"target": "区域下钻", "verdict": "部分匹配", "reason": "层级待验证"}]}}})
    notes.add_presale_note("project", "p1", "区域下钻", "部分匹配", "两层需定制",
                           {"name": "周宁"})
    out = web_demo.aggregate_capabilities()
    assert out[0]["notes"][0]["note"] == "两层需定制"
