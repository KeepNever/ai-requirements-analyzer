"""V2 存储层测试：版本不可覆盖、多对一映射、引用定位、迁移分层幂等、敏感信息隔离。

契约依据：docs/v2/01_Task1_数据现状与引用映射梳理.md v1.3。
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import storage
from pipeline import cleaner


MATERIAL = """## 原始材料

P1 【王总】我们厂大概六百人，主要做汽车冲压件。
P2 【李工】我的电话是13812345678，有事打这个。
P3 【王总】想要个实时看板，良品率当天可见。
P5 【王总】想要个实时看板，良品率当天可见。
P4 【李工】ERP是金蝶K3，数据不好接。
"""

MATERIAL_V2 = MATERIAL.replace("当天可见", "当天可见，最好能投大屏")


@pytest.fixture()
def store(tmp_path):
    storage.set_base(tmp_path)
    storage.create_project("测试项目A")
    yield tmp_path
    storage.set_base(storage.ROOT)


# ---------- 版本管理 ----------

def test_document_id_stable_without_content_hash(store):
    """document_id 不含内容 hash：同文档追加版本保持同一 id（契约 §3.1）。"""
    doc = storage.add_document("测试项目A", "客户会议.md", MATERIAL)
    doc_id = doc["document"]["document_id"]
    assert "13812345678" not in doc_id  # hash 不参与身份
    v2 = storage.add_document_version("测试项目A", doc_id, MATERIAL_V2)
    assert v2["version"]["version_id"] == "v2"
    assert v2["document"]["current_version_id"] == "v2"
    # v1 原文完好
    v1_src = (storage._documents_dir("测试项目A") / doc_id / "versions" / "v1" / "source.md")
    assert "13812345678" in v1_src.read_text(encoding="utf-8")


def test_version_reject_no_change(store):
    """内容无变化拒绝生成新版本（契约 §3.1）。"""
    doc = storage.add_document("测试项目A", "客户会议.md", MATERIAL)
    doc_id = doc["document"]["document_id"]
    with pytest.raises(ValueError, match="一致"):
        storage.add_document_version("测试项目A", doc_id, MATERIAL)


def test_duplicate_content_rejected(store):
    """全项目内容查重：一致内容不生成空文档（契约 §3.1）。"""
    storage.add_document("测试项目A", "客户会议.md", MATERIAL)
    with pytest.raises(ValueError, match="重复"):
        storage.add_document("测试项目A", "另一份材料.md", MATERIAL)


def test_version_directory_never_overwritten(store):
    """版本目录已存在时拒绝覆盖（契约验收 1）。"""
    doc = storage.add_document("测试项目A", "客户会议.md", MATERIAL)
    doc_id = doc["document"]["document_id"]
    vdir = storage._documents_dir("测试项目A") / doc_id / "versions" / "v1"
    with pytest.raises(ValueError, match="拒绝覆盖"):
        storage._write_version(vdir, "v1", doc_id, MATERIAL_V2)


# ---------- 清洗器留痕与多对一映射 ----------

def test_cleaner_merged_from_preserves_sources():
    """刷屏合并不再丢弃：被合并段落进入 merged_from（契约 §3.4）。"""
    cr = cleaner.clean(MATERIAL)
    seg1 = cr.segments[0]  # P1 无合并
    assert seg1.merged_from == []
    seg3 = [s for s in cr.segments if "想要个实时看板" in s.text][0]
    assert seg3.merged_from == [{"pid": "P5", "raw_text": "【王总】想要个实时看板，良品率当天可见。"}]
    # LLM 输入形态零变化：cleaned_text 不含重复段
    assert cr.cleaned_text().count("想要个实时看板") == 1


def test_paragraph_map_many_to_one(store):
    """paragraph_map 支持一个 D 对应多个 P（契约 §3.4）。"""
    doc = storage.add_document("测试项目A", "客户会议.md", MATERIAL)
    pid_, doc_id = "测试项目A", doc["document"]["document_id"]
    pmap = storage._read_json(
        storage._documents_dir(pid_) / doc_id / "versions" / "v1" / "paragraph_map.json")
    pairs = {p["clean_paragraph_id"]: p["source_paragraph_ids"] for p in pmap["pairs"]}
    merged = [p for p in pairs.values() if len(p) > 1]
    assert merged, "刷屏合并应产生多对一映射"
    assert all("P" in p[0] for p in merged)


def test_clean_json_single_source_of_text(store):
    """文本唯一数据源：clean.json 有内容，paragraph_map 无文本（契约 §3.3）。"""
    doc = storage.add_document("测试项目A", "客户会议.md", MATERIAL)
    d = storage._documents_dir("测试项目A") / doc["document"]["document_id"] / "versions" / "v1"
    clean = json.loads((d / "clean.json").read_text(encoding="utf-8"))
    pmap_text = (d / "paragraph_map.json").read_text(encoding="utf-8")
    assert any("raw_text" in s for s in clean["segments"])
    assert "raw_text" not in pmap_text and "clean_text" not in pmap_text


# ---------- 引用定位与敏感隔离 ----------

def _fake_analysis():
    return {
        "node1": {"explicit_needs": [
            {"id": "EN1", "content": "实时看板", "dids": ["D003"], "quote": "模型转述"},
            {"id": "EN2", "content": "无引用需求", "dids": [], "quote": ""},
        ]},
        "node2": {"potential_needs": [{"id": "IN1", "content": "推断"}],
                  "pending_confirmations": [], "capability_match": [],
                  "priority_dimensions": [{"id": "EN1", "impact": 5}]},
    }


def test_citation_locates_document_version_and_source(store):
    """引用可定位到文档、版本、原文段落（契约验收 3）。"""
    doc = storage.add_document("测试项目A", "客户会议.md", MATERIAL)
    pid_, doc_id = "测试项目A", doc["document"]["document_id"]
    c = storage.build_citations(_fake_analysis(), pid_, ["v1"])
    assert len(c["citations"]) == 1
    cite = c["citations"][0]
    assert cite["document_id"] == doc_id and cite["version_id"] == "v1"
    assert cite["clean_paragraph_ids"] == ["D003"]
    assert cite["source_paragraph_ids"] == ["P3", "P5"]   # 多对一：两个物理来源
    # 证据原文来自 clean.json 的 raw_text（含 PII 原样），系统生成而非模型
    assert all(e["text"] == "【王总】想要个实时看板，良品率当天可见。" for e in cite["source_excerpts"])


def test_citation_unresolved_recorded(store):
    """无结构化引用 / 无 node2 结构化字段的结论 → unresolved 显式记录（契约 §3.5）。"""
    doc = storage.add_document("测试项目A", "客户会议.md", MATERIAL)
    storage._documents_dir  # noqa
    c = storage.build_citations(_fake_analysis(), "测试项目A", ["v1"])
    unresolved_ids = {(u["conclusion_type"], u["conclusion_id"]) for u in c["unresolved"]}
    assert ("explicit_need", "EN2") in unresolved_ids          # dids 为空
    assert ("potential_need", "IN1") in unresolved_ids          # 无结构化字段
    assert ("priority_dimension", "EN1") in unresolved_ids
    # 伪造检查：unresolved 不携带任何 excerpt
    assert all("source_excerpts" not in u for u in c["unresolved"])


def test_frontend_citations_no_raw_text(store):
    """敏感隔离：前端版引用不含 raw_text / 手机号 / quote_model（强制约束 1）。"""
    doc = storage.add_document("测试项目A", "客户会议.md", MATERIAL)
    pid_, doc_id = "测试项目A", doc["document"]["document_id"]
    c = storage.build_citations(_fake_analysis(), pid_, ["v1"])
    c = storage._fill_document_ids(c, pid_)
    fe = storage.citations_for_frontend(c, pid_)
    blob = json.dumps(fe, ensure_ascii=False)
    assert "13812345678" not in blob          # 手机号（raw_text 独有）
    assert "quote_model" not in blob          # 模型转述不出现在前端证据
    for cite in fe["citations"]:
        for e in cite["source_excerpts"]:
            assert e["text"] == "【王总】想要个实时看板，良品率当天可见。"  # 脱敏后文本 == clean_text


def test_llm_input_never_contains_pii():
    """LLM 请求数据隔离：cleaned_text（进 LLM 的形态）不含原始 PII（强制约束 1）。"""
    cr = cleaner.clean(MATERIAL)
    assert "13812345678" not in cr.cleaned_text()
    assert "[手机号已脱敏]" in cr.cleaned_text()


# ---------- 迁移：分层幂等 ----------

@pytest.fixture()
def legacy_env(tmp_path, monkeypatch):
    """伪造旧 output/ 与 test_data/ 环境。"""
    out = tmp_path / "output" / "case_99_旧客户"
    out.mkdir(parents=True)
    src_dir = tmp_path / "test_data"
    src_dir.mkdir()
    (src_dir / "case_99_旧客户.md").write_text(MATERIAL, encoding="utf-8")
    (out / "mapping.json").write_text(json.dumps(
        {"mapping": [{"pid": "P1", "did": "D001"}, {"pid": "P2", "did": "D002"},
                     {"pid": "P3", "did": "D003"}, {"pid": "P4", "did": "D004"}],
         "stats": {}}, ensure_ascii=False), encoding="utf-8")
    (out / "analysis.json").write_text(json.dumps(_fake_analysis(), ensure_ascii=False), encoding="utf-8")
    (out / "report.md").write_text("# 旧报告", encoding="utf-8")
    (out / "usage.json").write_text(json.dumps({"调用次数": 3}), encoding="utf-8")
    monkeypatch.setattr(storage, "LEGACY_OUTPUT_DIR", tmp_path / "output")
    monkeypatch.setattr(storage, "TEST_DATA_DIR", src_dir)
    monkeypatch.setattr(storage, "LEGACY_SOURCE_DIRS",
                        [src_dir, src_dir / "validation", src_dir / "final_validation"])
    storage.set_base(tmp_path / "projects_base")
    yield tmp_path
    storage.set_base(storage.ROOT)


def test_migration_success_and_report(legacy_env):
    report = storage.migrate_legacy()
    assert len(report["migrated"]) == 1
    entry = report["migrated"][0]
    assert entry["case"] == "case_99_旧客户"
    # 落盘检查
    rdir = storage._projects_dir() / "case_99_旧客户" / "analysis_runs" / entry["run_id"]
    assert (rdir / "citations.json").exists()
    meta = json.loads((rdir / "meta.json").read_text(encoding="utf-8"))
    assert meta["prompt_version"] == "unknown"          # 缺失信息不补造
    assert meta["cleaner_version"] == "unknown"
    assert "migration_key" in meta
    # 旧报告保留
    assert (rdir / "report.md").read_text(encoding="utf-8") == "# 旧报告"


def test_migration_idempotent_full_rerun(legacy_env):
    """重复迁移：第二次全 reused，不产生重复记录（契约验收 4）。"""
    first = storage.migrate_legacy()
    assert len(first["migrated"]) == 1
    second = storage.migrate_legacy()
    assert second["migrated"] == []
    assert len(second["reused"]) == 1
    # 记录数不变
    runs = list((storage._projects_dir() / "case_99_旧客户" / "analysis_runs").iterdir())
    assert len(runs) == 1
    docs = list((storage._projects_dir() / "case_99_旧客户" / "documents").iterdir())
    assert len(docs) == 1
    assert len(list(docs[0].glob("versions/*"))) == 1


def test_migration_partial_recovery(legacy_env):
    """部分失败恢复：删除 Run 目录后重跑，只补 Run 层，版本层复用（契约验收 4）。"""
    first = storage.migrate_legacy()
    entry = first["migrated"][0]
    rdir = storage._projects_dir() / "case_99_旧客户" / "analysis_runs" / entry["run_id"]
    import shutil
    shutil.rmtree(rdir)  # 模拟上次迁移中 Run 写入失败
    second = storage.migrate_legacy()
    assert second["migrated"] and second["migrated"][0]["version_reused"] is True
    runs = list((storage._projects_dir() / "case_99_旧客户" / "analysis_runs").iterdir())
    assert len(runs) == 1                              # 补齐而非重复


def test_migration_missing_source_no_guess(legacy_env):
    """找不到源文件：迁移失败并报告候选，不猜测（契约 §4）。"""
    (legacy_env / "test_data" / "case_99_旧客户.md").unlink()
    (legacy_env / "output" / "case_98_无源").mkdir()
    (legacy_env / "output" / "case_98_无源" / "analysis.json").write_text("{}", encoding="utf-8")
    report = storage.migrate_legacy()
    missing = {m["case"]: m for m in report["missing_source"]}
    assert "case_98_无源" in missing
    assert missing["case_98_无源"]["candidates"] == []  # 无相似候选，明确报空
    assert "case_99_旧客户" not in [f["case"] for f in report["failed"]]


def test_migration_incomplete_output_flagged(legacy_env):
    """不完整输出（无 analysis.json）标记 incomplete，不伪造（契约验收 + 强制约束 2）。"""
    (legacy_env / "output" / "case_97_残缺").mkdir()
    (legacy_env / "output" / "case_97_残缺" / "mapping.json").write_text("{}", encoding="utf-8")
    report = storage.migrate_legacy()
    assert any(m["case"] == "case_97_残缺" for m in report["incomplete"])
