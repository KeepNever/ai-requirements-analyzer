"""模板场景评测与 GT 机制测试：mock 链路 + 评测逻辑。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from pipeline import scenes as scenes_mod
from evaluation.scene_eval import load_gt, run_checks


def test_new_scenes_registered():
    scenes = scenes_mod.list_template_scenes()
    assert "meeting_notes" in scenes, "会议纪要场景未注册"
    assert "lead_qualification" in scenes, "销售线索预审场景未注册"
    assert scenes["meeting_notes"]["sections"], "会议纪要缺 sections 声明"


def test_meeting_mock_analysis_structure():
    scene = scenes_mod.get_scene("meeting_notes")
    block = Path("test_data/scene_cases/meeting_01_教研例会.md").read_text(encoding="utf-8")
    a = scenes_mod.analyze_scene(scene, block, mock=True)
    for key in ("summary", "attendees", "topics", "todo_items"):
        assert key in a, f"mock 缺字段 {key}"
    assert isinstance(a["attendees"], list) and a["attendees"]


def test_lead_mock_analysis_structure():
    scene = scenes_mod.get_scene("lead_qualification")
    block = Path("test_data/scene_cases/lead_01_恒达精密工单Agent咨询.md").read_text(encoding="utf-8")
    a = scenes_mod.analyze_scene(scene, block, mock=True)
    for key in ("summary", "bant_assessment", "gaps", "next_actions"):
        assert key in a, f"mock 缺字段 {key}"
    dims = [it.get("dimension", "") for it in a["bant_assessment"]]
    for d in ("预算", "决策权", "需求", "时间线"):
        assert any(d in x for x in dims), f"mock BANT 缺维度 {d}"


def test_gt_files_valid():
    for stem in ("meeting_01_教研例会", "lead_01_恒达精密工单Agent咨询"):
        gt = load_gt(stem)
        assert gt.get("checks"), f"{stem} GT 无检查项"


def test_run_checks_budget_status():
    analysis = {"bant_assessment": [
        {"dimension": "预算（Budget）", "status": "部分确认", "evidence": "个人预估"},
        {"dimension": "需求（Needs）", "status": "已确认", "evidence": "明确场景"}]}
    gt = {"checks": [
        {"type": "budget_status", "dimension_keyword": "预算", "expect_status": "部分确认", "scope": "bant_assessment"},
        {"type": "needs_confirmed", "dimension_keyword": "需求", "expect_status": "已确认", "scope": "bant_assessment"}]}
    res = run_checks(analysis, gt)
    assert res["passed"] == 2 and res["total"] == 2


def test_run_checks_catches_wrong_status():
    analysis = {"bant_assessment": [{"dimension": "预算（Budget）", "status": "已确认", "evidence": "材料未提及"}]}
    gt = {"checks": [
        {"type": "budget_status", "dimension_keyword": "预算", "expect_status": "部分确认", "scope": "bant_assessment"},
        {"type": "no_fabricated_status", "expect_no_unsupported_confirmed": True, "scope": "bant_assessment"}]}
    res = run_checks(analysis, gt)
    assert res["passed"] == 0 and res["total"] == 2   # 两项都该被抓出
