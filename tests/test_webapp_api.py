"""SPA 数据装配层测试：统一报告源、销售脱敏、工作台总览。"""
from __future__ import annotations

import json

import web_demo


def _write(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")


def test_load_report_payload_case_branch(tmp_path, monkeypatch):
    monkeypatch.setattr(web_demo, "PROJECT_ROOT", tmp_path)
    _write(tmp_path / "output" / "case_a" / "analysis.json",
           {"node2": {"pending_confirmations": [{"gap": "g", "level": "决定性",
                                                 "impact": "i", "question": "q"}]}})
    (tmp_path / "output" / "case_a" / "report.md").write_text("# 报告", encoding="utf-8")
    payload = web_demo.load_report_payload("case_a")
    assert payload["source"] == "case"
    assert payload["export_url"] is None
    assert payload["analysis"]["node2"]["pending_confirmations"][0]["gap"] == "g"
    assert "# 报告" in payload["report_md"]
    assert web_demo.load_report_payload("不存在") is None


def test_load_report_payload_project_and_runid_lookup(tmp_path, monkeypatch):
    monkeypatch.setattr(web_demo, "PROJECT_ROOT", tmp_path)
    _write(tmp_path / "projects" / "p1" / "analysis_runs" / "runA" / "analysis.json",
           {"node2": {"pending_confirmations": []}})
    payload = web_demo.load_report_payload("p1")
    assert payload["source"] == "project" and payload["run_id"] == "runA"
    assert payload["export_url"] == "/export/docx/p1/runA"
    # 按 run_id 反查（新建分析后场景返回 run_id 的情况）
    by_run = web_demo.load_report_payload("runA")
    assert by_run is not None and by_run["name"] == "p1"


def test_strip_sales_report_only_returns_questions():
    payload = {"source": "case", "name": "c1", "run_id": "c1",
               "analysis": {"node2": {"pending_confirmations": [{"gap": "g"}]},
                            "priority": [{"id": "EN1"}]}}
    slim = web_demo.strip_sales_report(payload)
    assert slim["questions"] == [{"gap": "g"}]
    assert "analysis" not in slim and "report_md" not in slim


def test_build_overview_merges_projects_and_cases(tmp_path, monkeypatch):
    monkeypatch.setattr(web_demo, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(web_demo.storage, "list_projects",
                        lambda: [("p1", {"name": "项目一"})])
    _write(tmp_path / "projects" / "p1" / "analysis_runs" / "run1" / "analysis.json",
           {"node2": {"pending_confirmations": [{"gap": "a"}, {"gap": "b"}]}})
    _write(tmp_path / "output" / "case_b" / "analysis.json",
           {"node2": {"pending_confirmations": [{"gap": "c"}]}})
    (tmp_path / "output" / "case_b" / "report.md").write_text("# r", encoding="utf-8")
    (tmp_path / "output" / "evaluation").mkdir(parents=True)
    items = web_demo.build_overview()
    ids = {x["id"]: x for x in items}
    assert set(ids) == {"p1", "case_b"}                      # evaluation 目录被排除
    assert ids["p1"]["questions"] == 2 and ids["p1"]["source"] == "project"
    assert ids["case_b"]["questions"] == 1 and ids["case_b"]["source"] == "case"
