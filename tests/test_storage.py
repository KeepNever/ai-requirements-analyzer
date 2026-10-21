"""V2 存储层测试：版本不可覆盖、多对一映射、引用定位、迁移分层幂等、敏感信息隔离。

契约依据：docs/v2/01_Task1_数据现状与引用映射梳理.md v1.3 + 三审实现约束。
覆盖审查 6 问题：失败关闭、多文档定位、同原文新清洗版本、逐段原文、
迁移中断恢复、新旧清洗一致性。
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


def _add(store_fix=True):
    return storage.add_document("测试项目A", "客户会议.md", MATERIAL)


# ---------- 版本管理 ----------

def test_document_id_stable_without_content_hash(store):
    """document_id 不含内容 hash：同文档追加版本保持同一 id（契约 §3.1）。"""
    doc = _add()
    doc_id = doc["document"]["document_id"]
    assert "13812345678" not in doc_id
    v2 = storage.add_document_version("测试项目A", doc_id, MATERIAL_V2)
    assert v2["version"]["version_id"] == "v2"
    assert v2["document"]["current_version_id"] == "v2"
    v1_src = (storage._documents_dir("测试项目A") / doc_id / "versions" / "v1" / "source.md")
    assert "13812345678" in v1_src.read_text(encoding="utf-8")


def test_version_reject_no_change(store):
    """同内容 + 同清洗器版本 → 拒绝（契约 §3.2 双因子）。"""
    doc = _add()
    doc_id = doc["document"]["document_id"]
    with pytest.raises(ValueError, match="清洗版本"):
        storage.add_document_version("测试项目A", doc_id, MATERIAL)


def test_same_source_new_cleaner_version_creates_new_version(store, monkeypatch):
    """审查问题 3：同原文 + 新 CLEANER_VERSION → 允许建新版本（可追溯重新清洗）。"""
    doc = _add()
    doc_id = doc["document"]["document_id"]
    v1_cleaner_version = storage.get_version("测试项目A", doc_id, "v1")["cleaner_version"]
    monkeypatch.setattr(cleaner, "CLEANER_VERSION", v1_cleaner_version + "-next")
    v2 = storage.add_document_version("测试项目A", doc_id, MATERIAL)
    assert v2["version"]["version_id"] == "v2"
    assert v2["version"]["cleaner_version"] == v1_cleaner_version + "-next"
    # 新版本清洗器版本与旧版本并存，旧版未动
    assert storage.get_version("测试项目A", doc_id, "v1")["cleaner_version"] == v1_cleaner_version


def test_duplicate_content_rejected(store):
    """全项目内容查重：一致内容不生成空文档（契约 §3.1）。"""
    _add()
    with pytest.raises(ValueError, match="重复"):
        storage.add_document("测试项目A", "另一份材料.md", MATERIAL)


def test_version_directory_never_overwritten(store):
    """版本目录已存在时拒绝覆盖（契约验收 1）。"""
    doc = _add()
    doc_id = doc["document"]["document_id"]
    vdir = storage._documents_dir("测试项目A") / doc_id / "versions" / "v1"
    with pytest.raises(ValueError, match="拒绝覆盖"):
        storage._write_version(vdir, "v1", doc_id, MATERIAL_V2)


def test_atomic_version_write_no_half_dir(store):
    """审查问题 5：清洗失败时临时目录清理，不留半截版本。"""
    doc = _add()
    doc_id = doc["document"]["document_id"]
    versions_dir = storage._documents_dir("测试项目A") / doc_id / "versions"
    before = set(p.name for p in versions_dir.iterdir())
    with pytest.raises(Exception):
        # 触发清洗失败：传非字符串类型导致 extract_material 抛错
        storage._write_version(versions_dir / "v9", "v9", doc_id, None)
    after = set(p.name for p in versions_dir.iterdir())
    assert before == after and not any(n.startswith(".tmp_") for n in after)


# ---------- 清洗器留痕与多对一映射 ----------

def test_cleaner_merged_from_preserves_sources():
    """刷屏合并不再丢弃：被合并段落进入 merged_from（契约 §3.4）。"""
    cr = cleaner.clean(cleaner.extract_material(MATERIAL))
    assert cr.segments[0].merged_from == []
    seg3 = [s for s in cr.segments if "想要个实时看板" in s.text][0]
    assert seg3.merged_from == [{"pid": "P5", "raw_text": "【王总】想要个实时看板，良品率当天可见。"}]
    assert cr.cleaned_text().count("想要个实时看板") == 1   # LLM 输入形态零变化


def test_paragraph_map_many_to_one(store):
    """paragraph_map 支持一个 D 对应多个 P（契约 §3.4）。"""
    doc = _add()
    pid_, doc_id = "测试项目A", doc["document"]["document_id"]
    pmap = storage._read_json(
        storage._documents_dir(pid_) / doc_id / "versions" / "v1" / "paragraph_map.json")
    pairs = {p["clean_paragraph_id"]: p["source_paragraph_ids"] for p in pmap["pairs"]}
    merged = [p for p in pairs.values() if len(p) > 1]
    assert merged and pairs["D003"] == ["P3", "P5"]


def test_clean_json_single_source_of_text(store):
    """文本唯一数据源：clean.json 有内容，paragraph_map 无文本（契约 §3.3）。"""
    doc = _add()
    d = storage._documents_dir("测试项目A") / doc["document"]["document_id"] / "versions" / "v1"
    clean = json.loads((d / "clean.json").read_text(encoding="utf-8"))
    pmap_text = (d / "paragraph_map.json").read_text(encoding="utf-8")
    assert any("raw_text" in s for s in clean["segments"])
    assert "raw_text" not in pmap_text and "clean_text" not in pmap_text


def test_old_new_cleaning_consistency():
    """审查问题 6：旧路径（load_material+clean）与新存储路径（_clean_source）P/D 映射一致。"""
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as f:
        f.write(MATERIAL)
        path = f.name
    old_cr = cleaner.clean(cleaner.load_material(path))
    new = storage._clean_source(MATERIAL)
    old_pairs = [{"pid": s.pid, "did": s.did} for s in old_cr.segments]
    new_pairs = [{"pid": p["source_paragraph_ids"][0], "did": p["clean_paragraph_id"]}
                 for p in new["paragraph_map"]["pairs"]]
    assert old_pairs == new_pairs
    # LLM 输入形态一致
    assert cleaner.clean(cleaner.extract_material(MATERIAL)).cleaned_text() == old_cr.cleaned_text()


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
    """引用按完整组合定位（审查问题 2）：document_id + version_id + 原文。"""
    doc = _add()
    pid_, doc_id = "测试项目A", doc["document"]["document_id"]
    refs = [{"document_id": doc_id, "version_id": "v1"}]
    c = storage.build_citations(_fake_analysis(), pid_, refs)
    assert c["version_refs"] == refs
    assert len(c["citations"]) == 1
    cite = c["citations"][0]
    assert cite["document_id"] == doc_id and cite["version_id"] == "v1"
    assert cite["clean_paragraph_ids"] == ["D003"]
    assert cite["source_paragraph_ids"] == ["P3", "P5"]   # 多对一：两个物理来源
    for e in cite["source_excerpts"]:
        assert e["text"] == "【王总】想要个实时看板，良品率当天可见。"


def test_merged_excerpt_per_source(store):
    """审查问题 4：合并段的引用逐段取原文——代表段 raw_text、被合并段 merged_from。"""
    doc = _add()
    pid_, doc_id = "测试项目A", doc["document"]["document_id"]
    c = storage.build_citations(_fake_analysis(), pid_,
                                [{"document_id": doc_id, "version_id": "v1"}])
    cite = c["citations"][0]
    excerpts = {e["source_paragraph_id"]: e["text"] for e in cite["source_excerpts"]}
    seg = [s for s in json.loads(
        (storage._documents_dir(pid_) / doc_id / "versions" / "v1" / "clean.json")
        .read_text(encoding="utf-8"))["segments"] if s["did"] == "D003"][0]
    # 代表段与被合并段各自对应自己的原文记录，而非复制合并段长文
    assert excerpts["P3"] == seg["raw_text"]                     # 代表段
    assert excerpts["P5"] == seg["merged_from"][0]["raw_text"]   # 被合并段


def test_multi_document_same_v1_no_crosstalk(store):
    """审查问题 2：两个文档都有 v1，引用按完整组合定位不串档。"""
    doc_a = storage.add_document("测试项目A", "客户A会议.md", MATERIAL)
    other = MATERIAL.replace("良品率", "合格率")
    doc_b = storage.add_document("测试项目A", "客户B会议.md", other)
    pid_ = "测试项目A"
    refs = [{"document_id": doc_b["document"]["document_id"], "version_id": "v1"}]
    c = storage.build_citations(_fake_analysis(), pid_, refs)
    assert c["citations"][0]["document_id"] == doc_b["document"]["document_id"]
    assert c["citations"][0]["document_id"] != doc_a["document"]["document_id"]
    # B 文档的原文用 B 的措辞
    assert all("合格率" in e["text"] for e in c["citations"][0]["source_excerpts"])


def test_citation_unresolved_recorded(store):
    """无结构化引用 / 编号无效 → unresolved 显式记录（契约 §3.5）。"""
    doc = _add()
    pid_, doc_id = "测试项目A", doc["document"]["document_id"]
    bad = _fake_analysis()
    bad["node1"]["explicit_needs"][0]["dids"] = ["D999"]   # 不存在的编号
    c = storage.build_citations(bad, pid_, [{"document_id": doc_id, "version_id": "v1"}])
    unresolved_ids = {(u["conclusion_type"], u["conclusion_id"]) for u in c["unresolved"]}
    assert ("explicit_need", "EN1") in unresolved_ids       # 编号无效
    assert ("explicit_need", "EN2") in unresolved_ids       # dids 为空
    assert ("potential_need", "IN1") in unresolved_ids      # 无结构化字段
    assert ("priority_dimension", "EN1") in unresolved_ids
    assert all("source_excerpts" not in u for u in c["unresolved"])
    assert c["citations"] == []                             # 不生成推测性引用


def test_frontend_citations_fail_closed(store):
    """审查问题 1：失败关闭——缺文档/缺版本/缺映射时不得返回 raw_text。"""
    doc = _add()
    pid_, doc_id = "测试项目A", doc["document"]["document_id"]
    refs = [{"document_id": doc_id, "version_id": "v1"}]
    c = storage.build_citations(_fake_analysis(), pid_, refs)

    # 正常路径：脱敏文本 + displayable
    fe = storage.citations_for_frontend(c, pid_)
    assert fe["citations"][0]["displayable"] is True
    blob = json.dumps(fe, ensure_ascii=False)
    assert "13812345678" not in blob and "quote_model" not in blob

    # 缺版本：version 目录不存在 → 失败关闭
    broken = json.loads(json.dumps(c, ensure_ascii=False))
    broken["citations"][0]["version_id"] = "v999"
    fe2 = storage.citations_for_frontend(broken, pid_)
    assert fe2["citations"][0]["displayable"] is False
    assert all(e["text"] == "" for e in fe2["citations"][0]["source_excerpts"])
    assert "13812345678" not in json.dumps(fe2, ensure_ascii=False)

    # 缺文档：document_id 指向不存在的文档 → 失败关闭
    broken2 = json.loads(json.dumps(c, ensure_ascii=False))
    broken2["citations"][0]["document_id"] = "不存在文档-00000000-aa"
    fe3 = storage.citations_for_frontend(broken2, pid_)
    assert fe3["citations"][0]["displayable"] is False
    assert all(e["text"] == "" for e in fe3["citations"][0]["source_excerpts"])
    assert "13812345678" not in json.dumps(fe3, ensure_ascii=False)


def test_llm_input_never_contains_pii():
    """LLM 请求数据隔离：cleaned_text（进 LLM 的形态）不含原始 PII（强制约束 1）。"""
    cr = cleaner.clean(cleaner.extract_material(MATERIAL))
    assert "13812345678" not in cr.cleaned_text()
    assert "[手机号已脱敏]" in cr.cleaned_text()


# ---------- Run 生命周期 ----------

def test_save_run_outputs_rejects_overwrite_of_done(store):
    """审查问题 5：已完成 Run 拒绝覆盖，历史分析与报告不可重写。"""
    doc = _add()
    refs = [{"document_id": doc["document"]["document_id"], "version_id": "v1"}]
    rdir = storage.create_run("测试项目A", refs, "v1.7.1", "deepseek-chat")
    storage.save_run_outputs(rdir, _fake_analysis(), "# 报告", {}, {"调用次数": 1})
    with pytest.raises(ValueError, match="拒绝覆盖"):
        storage.save_run_outputs(rdir, _fake_analysis(), "# 另一份", {}, None)
    assert (rdir / "report.md").read_text(encoding="utf-8") == "# 报告"
    assert (rdir / "report_version.json").exists()   # ReportVersion 元数据落盘


# ---------- 迁移：分层幂等 + 部分恢复 ----------

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
    rdir = storage._projects_dir() / "case_99_旧客户" / "analysis_runs" / entry["run_id"]
    assert (rdir / "citations.json").exists()
    assert (rdir / "report_version.json").exists()
    meta = json.loads((rdir / "meta.json").read_text(encoding="utf-8"))
    assert meta["prompt_version"] == "unknown"          # 缺失信息不补造
    assert meta["cleaner_version"] == "unknown"
    assert "migration_key" in meta
    assert (rdir / "report.md").read_text(encoding="utf-8") == "# 旧报告"


def test_migration_idempotent_full_rerun(legacy_env):
    """重复迁移：第二次全 reused，不产生重复记录（契约验收 4）。"""
    assert len(storage.migrate_legacy()["migrated"]) == 1
    second = storage.migrate_legacy()
    assert second["migrated"] == [] and len(second["reused"]) == 1
    runs = list((storage._projects_dir() / "case_99_旧客户" / "analysis_runs").iterdir())
    assert len(runs) == 1
    docs = list((storage._projects_dir() / "case_99_旧客户" / "documents").iterdir())
    assert len(docs) == 1 and len(list(docs[0].glob("versions/*"))) == 1


def test_migration_partial_recovery(legacy_env):
    """审查问题 5：Run 不完整（缺 citations + running）时重跑应补齐，版本层复用。"""
    first = storage.migrate_legacy()
    entry = first["migrated"][0]
    rdir = storage._projects_dir() / "case_99_旧客户" / "analysis_runs" / entry["run_id"]
    # 模拟中断：删 citations.json、status 打回 running
    (rdir / "citations.json").unlink()
    meta = json.loads((rdir / "meta.json").read_text(encoding="utf-8"))
    meta["status"] = "running"
    (rdir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")

    second = storage.migrate_legacy()
    assert second["migrated"] and second["migrated"][0]["run_resumed"] is True
    assert second["migrated"][0]["version_reused"] is True
    assert (rdir / "citations.json").exists()           # 缺失文件已补齐
    assert json.loads((rdir / "meta.json").read_text(encoding="utf-8"))["status"] == "done"
    assert len(list((storage._projects_dir() / "case_99_旧客户" / "analysis_runs").iterdir())) == 1


def test_migration_missing_source_no_guess(legacy_env):
    """找不到源文件：迁移失败并报告候选，不猜测（契约 §4）。"""
    (legacy_env / "test_data" / "case_99_旧客户.md").unlink()
    (legacy_env / "output" / "case_98_无源").mkdir()
    (legacy_env / "output" / "case_98_无源" / "analysis.json").write_text("{}", encoding="utf-8")
    report = storage.migrate_legacy()
    missing = {m["case"]: m for m in report["missing_source"]}
    assert "case_98_无源" in missing and missing["case_98_无源"]["candidates"] == []
    assert "case_99_旧客户" not in [f["case"] for f in report["failed"]]


def test_migration_incomplete_output_flagged(legacy_env):
    """不完整输出（无 analysis.json）标记 incomplete，不伪造（强制约束 2）。"""
    (legacy_env / "output" / "case_97_残缺").mkdir()
    (legacy_env / "output" / "case_97_残缺" / "mapping.json").write_text("{}", encoding="utf-8")
    report = storage.migrate_legacy()
    assert any(m["case"] == "case_97_残缺" for m in report["incomplete"])


# ---------- 路径安全 ----------

def test_path_traversal_blocked(store):
    """审查补齐项：ID 含路径穿越时拒绝，解析结果不越界。"""
    with pytest.raises(ValueError, match="非法标识符"):
        storage.get_project("../escape")
    with pytest.raises(ValueError, match="非法标识符"):
        storage._resolve_under_base("projects", "..", "documents")
