"""Task 7 场景层测试：学员服务诊断（曦拍板方案 A + 隔离验收）。

验收要求：场景切换隔离 Prompt 和输出结构，不影响售前链路；
原有案例回归无退化（全量 91 项既有测试即回归集）。
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import storage
import export_docx
from pipeline import service_diagnosis as sd
from scene_runner import run_scene_case

MATERIAL_PATH = str(Path(__file__).resolve().parents[1] / "test_data"
                    / "case_30_学员学习困境对话.md")


MATERIAL_PRESALE = """## 原始材料

P1 【王总】我们厂大概六百人，主要做汽车冲压件。
P2 【王总】想要个实时看板，良品率当天可见。
"""


def _all_text(doc):
    parts = [p.text for p in doc.paragraphs]
    for t in doc.tables:
        for row in t.rows:
            for c in row.cells:
                parts.append(c.text)
    return "\n".join(parts)


@pytest.fixture()
def presale_run(tmp_path):
    """售前场景对照数据（隔离验收用）。"""
    import storage as _st
    _st.set_base(tmp_path / "presale")
    _st.create_project("导出测试项目")
    doc = _st.add_document("导出测试项目", "客户会议.md", MATERIAL_PRESALE)
    refs = [{"document_id": doc["document"]["document_id"], "version_id": "v1"}]
    rdir = _st.create_run("导出测试项目", refs, "v1.7.1", "deepseek-chat")
    _st.save_run_outputs(rdir, {"node1": {"explicit_needs": [
        {"id": "EN1", "content": "实时看板", "dids": ["D002"], "quote": ""}]},
        "node2": {}, "priority": []}, "# 售前报告", {}, None)
    yield {"run_id": rdir.name}
    _st.set_base(_st.ROOT)


@pytest.fixture()
def service_run(tmp_path):
    storage.set_base(tmp_path)
    rdir = run_scene_case(MATERIAL_PATH, scene="service_diagnosis", mock=True,
                          project_name="服务诊断测试项目")
    yield {"run_id": rdir.name, "pid": "服务诊断测试项目"}
    storage.set_base(storage.ROOT)


# ---------- 全链路 ----------

def test_scene_run_end_to_end(service_run, tmp_path):
    """mock 全链路：清洗 → 入库 → 分析 → 引用 → 报告 → 落盘，meta 带场景标识。"""
    rdir = storage._resolve_under_base(
        "projects", service_run["pid"], "analysis_runs", service_run["run_id"])
    assert (rdir / "analysis.json").exists()
    assert (rdir / "report.md").exists()
    assert (rdir / "citations.json").exists()
    meta = storage._read_json(rdir / "meta.json")
    assert meta["scene"] == "service_diagnosis"
    assert meta["prompt_version"] == sd.SD_PROMPT_VERSION
    ana = storage._read_json(rdir / "analysis.json")
    assert "summary" in ana and "core_demands" in ana
    report = (rdir / "report.md").read_text(encoding="utf-8")
    assert "学员服务诊断报告" in report and "行动清单" in report


def test_service_citations_use_existing_structure(service_run):
    """引用走同一 Citation 结构：excerpt 级来源绑定；action_items 不生成引用。"""
    rdir = storage._resolve_under_base(
        "projects", service_run["pid"], "analysis_runs", service_run["run_id"])
    cits = storage._read_json(rdir / "citations.json")
    for c in cits["citations"]:
        assert c["conclusion_type"] in ("core_demand", "emotion_risk", "response_issue")
        for e in c["source_excerpts"]:
            assert e.get("document_id") and e.get("version_id")   # excerpt 级来源绑定
    types = {c["conclusion_type"] for c in cits["citations"]}
    assert "action_item" not in types                              # 行动清单不伪装成证据


def test_service_word_export(service_run, tmp_path):
    """场景 run 导出服务诊断 Word：标题与章节正确，run_id 绑定。"""
    out = tmp_path / "sd.docx"
    export_docx.export_run_docx(service_run["pid"], service_run["run_id"], str(out))
    from docx import Document
    d = Document(str(out))
    text = _all_text(d)
    assert "学员服务诊断报告" in text
    assert "诊断摘要" in text and "学员核心诉求" in text and "行动清单" in text
    assert service_run["run_id"] in text


# ---------- 场景隔离 ----------

def test_scene_isolation_presale_untouched(presale_run, tmp_path):
    """隔离验收：售前 run 无 scene 字段、导出仍走售前章节（服务分支不误触）。"""
    from docx import Document
    out = tmp_path / "presale.docx"
    export_docx.export_run_docx("导出测试项目", presale_run["run_id"], str(out))
    d = Document(str(out))
    text = _all_text(d)
    assert "客户材料分析报告" in text                # 售前标题
    assert "学员服务诊断报告" not in text            # 服务分支未误触
    meta = storage._read_json(storage._resolve_under_base(
        "projects", "导出测试项目", "analysis_runs",
        presale_run["run_id"], "meta.json"))
    assert "scene" not in meta                      # 售前 meta 无场景字段


def test_unknown_scene_rejected():
    """未知场景明确报错。"""
    with pytest.raises(ValueError, match="未知场景"):
        run_scene_case(MATERIAL_PATH, scene="horoscope", mock=True)


def test_service_prompt_is_independent():
    """隔离验收：SD Prompt 与售前分析 Prompt 完全独立（不同模块、不同内容）。"""
    from pipeline import prompts
    assert sd.SD_PROMPT_VERSION != prompts.PROMPTS_VERSION
    assert "学员服务诊断" in sd.SD_SYSTEM
    # 售前 Prompt 不含服务诊断指令
    presale = prompts.SYSTEM_PROMPT if hasattr(prompts, "SYSTEM_PROMPT") else ""
    if presale:
        assert "学员服务诊断" not in presale


def test_scene_template_engine_loads_json():
    """Task 8 场景 JSON 化：模板从 scenes/*.json 加载，契约字段齐全。"""
    from pipeline import scenes as scene_engine
    sc = scene_engine.get_scene("service_diagnosis")
    assert sc["id"] == "service_diagnosis"
    assert sc["system_prompt"] and sc["task_instruction"]
    keys = {sec["key"] for sec in sc["sections"]}
    assert {"core_demands", "emotion_risks", "response_issues"} <= keys
    with pytest.raises(ValueError, match="未知模板场景"):
        scene_engine.get_scene("不存在")


def test_mock_analysis_follows_schema():
    """mock 分析按 sections schema 生成，bullet 段与表格段都能出结构。"""
    from pipeline import scenes as scene_engine
    sc = scene_engine.get_scene("service_diagnosis")
    out = scene_engine.analyze_scene(sc, "P1 【学生】我觉得跟不上了。", mock=True)
    assert out["summary"]
    assert out["core_demands"] and out["emotion_risks"] and out["response_issues"]
    assert out["action_items"]                              # actions_section 也生成
