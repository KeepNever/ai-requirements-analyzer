"""V2 存储层：Project / Document / DocumentVersion / AnalysisRun / Citation。

设计依据：docs/v2/01_Task1_数据现状与引用映射梳理.md（v1.3 契约）。
- 纯标准库 JSON 落盘，目录即实体
- 版本化：原文随版本走，任何写入不覆盖历史
- 引用：source_excerpts 由系统从 clean.json 的 raw_text 切片生成（证据），
  前端展示一律经 citations_for_frontend() 转为脱敏文本
- 迁移：分层幂等（DocumentVersion 按 source_hash+cleaner_version；
  AnalysisRun 按 migration_key），部分失败可恢复补齐

本层不改动 Workflow / Prompt / 评测器的业务行为。
"""
import hashlib
import json
import re
import shutil
import time
import uuid
from pathlib import Path

from pipeline import cleaner

ROOT = Path(__file__).resolve().parents[1]          # 项目根（src/ 的上一级）
_BASE = ROOT                                        # 存储根，测试可切换
LEGACY_OUTPUT_DIR = ROOT / "output"
TEST_DATA_DIR = ROOT / "test_data"
LEGACY_SOURCE_DIRS = [TEST_DATA_DIR, TEST_DATA_DIR / "validation",
                      TEST_DATA_DIR / "final_validation",
                      TEST_DATA_DIR / "web_uploads"]

SLUG_RE = re.compile(r'[\\/:*?"<>|\s]+')
PROTECTED = ("__", "")


def set_base(path):
    """测试专用：切换存储根目录。"""
    global _BASE
    _BASE = Path(path)


def _base() -> Path:
    return Path(_BASE)


def _projects_dir() -> Path:
    return _base() / "projects"


# ---------- 工具 ----------

def slug(name: str, limit: int = 40) -> str:
    out = SLUG_RE.sub("_", str(name)).strip("_")[:limit].strip("_")
    return out or "unnamed"


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


# ---------- Project ----------

def create_project(name: str) -> dict:
    pid = slug(name)
    pdir = _projects_dir() / pid
    if pdir.exists():
        raise ValueError(f"项目已存在：{pid}")
    meta = {"project_id": pid, "name": name, "created_at": now_iso()}
    _write_json(pdir / "doc.json", meta)
    index_path = _projects_dir() / "index.json"
    index = _read_json(index_path) if index_path.exists() else {}
    index[pid] = {"name": name, "created_at": meta["created_at"]}
    _write_json(index_path, index)
    return meta


def get_project(project_id: str):
    p = _projects_dir() / project_id / "doc.json"
    return _read_json(p) if p.exists() else None


def list_projects() -> list:
    idx = _projects_dir() / "index.json"
    return list(_read_json(idx).items()) if idx.exists() else []


# ---------- Document 与版本 ----------

def _documents_dir(project_id: str) -> Path:
    return _projects_dir() / project_id / "documents"


def _clean_source(source_text: str) -> dict:
    """源文本 → {stats, segments, paragraph_map}（clean.json + paragraph_map 的数据源）。"""
    cr = cleaner.clean(source_text)
    segments = [{"did": s.did, "raw_text": s.raw_text, "clean_text": s.text,
                 "pii_scrubbed": s.pii_scrubbed, "merged_from": s.merged_from}
                for s in cr.segments]
    pmap = {"version_id": None, "pairs": [
        {"clean_paragraph_id": s.did,
         "source_paragraph_ids": [s.pid] + [m["pid"] for m in s.merged_from]}
        for s in cr.segments]}
    return {"stats": cr.stats, "segments": segments, "paragraph_map": pmap,
            "cleaner_version": cleaner.CLEANER_VERSION}


def _write_version(vdir: Path, version_id: str, document_id: str, source_text: str) -> dict:
    """生成一个版本的三个自包含文件 + version.json。目录已存在则拒绝（不可覆盖）。"""
    if vdir.exists():
        raise ValueError(f"版本目录已存在，拒绝覆盖：{vdir}")
    vdir.mkdir(parents=True)
    (vdir / "source.md").write_text(source_text, encoding="utf-8")
    cleaned = _clean_source(source_text)
    cleaned["paragraph_map"]["version_id"] = version_id
    _write_json(vdir / "clean.json",
                {"stats": cleaned["stats"], "segments": cleaned["segments"]})
    _write_json(vdir / "paragraph_map.json", cleaned["paragraph_map"])
    meta = {"version_id": version_id, "document_id": document_id,
            "cleaner_version": cleaner.CLEANER_VERSION,
            "source_hash": sha256_text(source_text), "created_at": now_iso()}
    _write_json(vdir / "version.json", meta)
    return meta


def add_document(project_id: str, file_name: str, source_text: str) -> dict:
    """新建文档并生成 v1。全项目内容查重命中则拒绝（契约：一致内容不生成空版本）。"""
    doc_dir = _documents_dir(project_id)
    doc_dir.mkdir(parents=True, exist_ok=True)
    source_hash = sha256_text(source_text)
    dup = _find_hash_in_project(project_id, source_hash)
    if dup:
        raise ValueError(f"内容与已有文档重复：{dup['document_id']}/{dup['version_id']}")
    document_id = f"{slug(file_name)}-{time.strftime('%m%d%H%M%S')}-{uuid.uuid4().hex[:2]}"
    vdir = doc_dir / document_id / "versions" / "v1"
    vmeta = _write_version(vdir, "v1", document_id, source_text)
    doc_meta = {"document_id": document_id, "project_id": project_id,
                "file_name": file_name, "current_version_id": "v1",
                "uploaded_at": now_iso()}
    _write_json(doc_dir / document_id / "doc.json", doc_meta)
    return {"document": doc_meta, "version": vmeta}


def add_document_version(project_id: str, document_id: str, source_text: str) -> dict:
    """为已有文档追加新版本。内容无变化拒绝；重走全项目查重提示（同名异容由调用方选择入口）。"""
    doc_file = _documents_dir(project_id) / document_id / "doc.json"
    if not doc_file.exists():
        raise ValueError(f"文档不存在：{document_id}")
    doc_meta = _read_json(doc_file)
    source_hash = sha256_text(source_text)
    cur = get_version(project_id, document_id, doc_meta["current_version_id"])
    if cur["source_hash"] == source_hash:
        raise ValueError("内容与当前最新版本一致，不生成新版本")
    versions_dir = _documents_dir(project_id) / document_id / "versions"
    seq = len(list(versions_dir.iterdir())) + 1
    version_id = f"v{seq}"
    vmeta = _write_version(versions_dir / version_id, version_id, document_id, source_text)
    doc_meta["current_version_id"] = version_id
    _write_json(doc_file, doc_meta)
    return {"document": doc_meta, "version": vmeta}


def get_version(project_id: str, document_id: str, version_id: str) -> dict:
    return _read_json(_documents_dir(project_id) / document_id
                      / "versions" / version_id / "version.json")


def _find_hash_in_project(project_id: str, source_hash: str):
    for doc_dir in sorted(_documents_dir(project_id).glob("*/")):
        for vmeta_file in sorted(doc_dir.glob("versions/*/version.json")):
            vm = _read_json(vmeta_file)
            if vm["source_hash"] == source_hash:
                return vm
    return None


# ---------- AnalysisRun ----------

def create_run(project_id: str, version_ids: list, prompt_version: str, model: str) -> Path:
    run_id = f"run{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:4]}"
    rdir = _projects_dir() / project_id / "analysis_runs" / run_id
    if rdir.exists():
        raise ValueError(f"run 目录冲突：{run_id}")
    _write_json(rdir / "meta.json", {
        "run_id": run_id, "project_id": project_id, "version_ids": version_ids,
        "prompt_version": prompt_version, "model": model,
        "status": "running", "created_at": now_iso(), "usage": None,
    })
    return rdir


def save_run_outputs(run_dir: Path, analysis: dict, report_md: str,
                     citations: dict, usage=None) -> None:
    _write_json(run_dir / "analysis.json", analysis)
    (run_dir / "report.md").write_text(report_md, encoding="utf-8")
    _write_json(run_dir / "citations.json", citations)
    meta = _read_json(run_dir / "meta.json")
    meta["usage"] = usage
    meta["status"] = "done"
    _write_json(run_dir / "meta.json", meta)


# ---------- Citation 生成 ----------

def _load_version_data(project_id: str, version_id: str, document_id: str | None = None):
    """返回 (paragraph_map, clean_segments)；document_id 缺省时单文档查找。"""
    if document_id:
        vdir = _documents_dir(project_id) / document_id / "versions" / version_id
        return (_read_json(vdir / "paragraph_map.json"),
                _read_json(vdir / "clean.json")["segments"])
    for doc_dir in sorted(_documents_dir(project_id).glob("*/")):
        vdir = doc_dir / "versions" / version_id
        if (vdir / "paragraph_map.json").exists():
            return (_read_json(vdir / "paragraph_map.json"),
                    _read_json(vdir / "clean.json")["segments"])
    raise ValueError(f"版本不存在：{version_id}")


def build_citations(analysis: dict, project_id: str, version_ids: list) -> dict:
    """从 analysis 的结构化 dids 字段生成规范化引用；无法可靠提取的结论记 unresolved。

    证据字段 source_excerpts 由系统从 clean.json 的 raw_text 切片生成，
    不使用模型转述（quote_model 仅展示）。
    """
    version_id = version_ids[0]
    pmap, segments = _load_version_data(project_id, version_id)
    did2pair = {p["clean_paragraph_id"]: p for p in pmap["pairs"]}
    seg_by_did = {s["did"]: s for s in segments}

    citations, unresolved = [], []
    for en in (analysis.get("node1", {}).get("explicit_needs") or []):
        dids = en.get("dids") or []
        invalid = [d for d in dids if d not in did2pair]
        if not dids or invalid:
            unresolved.append({
                "conclusion_type": "explicit_need", "conclusion_id": en.get("id"),
                "reason": ("引用编号不存在于清洗段落集合: " + ",".join(invalid))
                          if invalid else "无结构化引用字段（dids 为空）"})
            continue
        excerpts = []
        for d in dids:
            for p in did2pair[d]["source_paragraph_ids"]:
                excerpts.append({"source_paragraph_id": p,
                                 "text": seg_by_did[d]["raw_text"]})
        citations.append({
            "conclusion_type": "explicit_need", "conclusion_id": en.get("id"),
            "document_id": None,  # 由调用方按 version 归属填充
            "version_id": version_id,
            "clean_paragraph_ids": dids,
            "source_paragraph_ids": [e["source_paragraph_id"] for e in excerpts],
            "source_excerpts": excerpts,
            "citation_reason": "支撑显性需求识别结论",
            "quote_model": en.get("quote"),
        })
    # 其余结论无结构化引用字段 → 全部 unresolved（不猜测、不伪造）
    for kind, key in (("potential_need", "potential_needs"),
                      ("pending_confirmation", "pending_confirmations"),
                      ("capability_match", "capability_match"),
                      ("priority_dimension", "priority_dimensions")):
        for item in (analysis.get("node2", {}).get(key) or []):
            unresolved.append({
                "conclusion_type": kind, "conclusion_id": item.get("id") or item.get("target"),
                "reason": "该结论无结构化引用字段，来源编号仅存在于自由文本，不做猜测性提取"})
    out = {"project_id": project_id, "version_ids": version_ids,
           "citations": citations, "unresolved": unresolved}
    return _fill_document_ids(out, project_id)


def _fill_document_ids(citations: dict, project_id: str) -> dict:
    """按 version 反查 document_id 并填充（单文档期 1:1；多文档期由 run 的版本清单映射）。"""
    for c in citations.get("citations", []):
        vid = c.get("version_id")
        for doc_dir in sorted(_documents_dir(project_id).glob("*/")):
            if (doc_dir / "versions" / vid).exists():
                c["document_id"] = doc_dir.name
                break
    return citations


def citations_for_frontend(citations: dict, project_id: str) -> dict:
    """前端展示版：source_excerpts 的 text 替换为脱敏 clean_text，杜绝 raw_text 泄露。"""
    import copy
    out = copy.deepcopy(citations)
    for c in out.get("citations", []):
        vid, pid_ = c.get("version_id"), c.get("document_id")
        if not (vid and pid_):
            continue
        vdir = _documents_dir(project_id) / pid_ / "versions" / vid
        if not (vdir / "clean.json").exists():
            continue
        segs = {s["did"]: s for s in _read_json(vdir / "clean.json")["segments"]}
        for e in c.get("source_excerpts", []):
            # 通过 paragraph_map 找到 excerpt 对应的 D 编号，再取脱敏文本
            pmap = _read_json(vdir / "paragraph_map.json")
            dids = [p["clean_paragraph_id"] for p in pmap["pairs"]
                    if e["source_paragraph_id"] in p["source_paragraph_ids"]]
            if dids and dids[0] in segs:
                e["text"] = segs[dids[0]]["clean_text"]
            e.pop("raw", None)
        c.pop("quote_model", None)  # 模型转述不出现在前端证据中
    return out


# ---------- 旧输出迁移（分层幂等） ----------

def _fingerprint(paths: list) -> str:
    h = hashlib.sha256()
    for p in sorted(paths):
        f = Path(p)
        h.update(f.name.encode("utf-8"))
        if f.exists():
            h.update(f.read_bytes())
    return h.hexdigest()[:12]


def migration_key(legacy_dir: Path) -> str:
    """确定性迁移键：旧输出路径 + analysis/mapping/usage 三文件指纹。"""
    rel = str(legacy_dir.relative_to(legacy_dir.parents[1]))
    files = [legacy_dir / n for n in ("analysis.json", "mapping.json", "usage.json")]
    return hashlib.sha256(f"{rel}|{_fingerprint(files)}".encode("utf-8")).hexdigest()[:16]


def _locate_source(case_stem: str):
    for d in LEGACY_SOURCE_DIRS:
        cand = d / f"{case_stem}.md"
        if cand.exists():
            return cand
    return None


def _candidate_sources(case_stem: str) -> list:
    key = re.sub(r"^(case|dev|hold|final)_", "", case_stem).split("_")[0]
    cands = []
    for d in LEGACY_SOURCE_DIRS:
        for f in d.glob(f"*{key}*.md"):
            cands.append(str(f))
    return cands


def _run_exists_by_migration_key(project_id: str, mkey: str):
    runs = _projects_dir() / project_id / "analysis_runs"
    if not runs.exists():
        return None
    for rdir in sorted(runs.iterdir()):
        mf = rdir / "meta.json"
        if mf.exists() and _read_json(mf).get("migration_key") == mkey:
            return rdir
    return None


def migrate_legacy(output_dir: Path | None = None) -> dict:
    """旧 output/<case>/ → 新结构。分层幂等：版本按 hash+cleaner_version 复用，
    Run 按 migration_key 复用；部分完成时补齐缺失层。返回分类迁移报告。"""
    out_dir = Path(output_dir) if output_dir else LEGACY_OUTPUT_DIR
    report = {"migrated": [], "reused": [], "missing_source": [],
              "incomplete": [], "failed": []}
    if not out_dir.exists():
        return report
    for case_dir in sorted(out_dir.iterdir()):
        if not case_dir.is_dir() or case_dir.name == "evaluation":
            continue
        name = case_dir.name
        ana_file = case_dir / "analysis.json"
        if not ana_file.exists():
            report["incomplete"].append({"case": name, "reason": "无 analysis.json"})
            continue
        try:
            src = _locate_source(name)
            if src is None:
                report["missing_source"].append({
                    "case": name, "reason": "无法定位源材料文件",
                    "candidates": _candidate_sources(name)})
                continue
            source_text = Path(src).read_text(encoding="utf-8")
            source_hash = sha256_text(source_text)

            # 层1：Project + Document + Version（按 hash+cleaner_version 复用）
            project_id = slug(name)
            if not get_project(project_id):
                create_project(name)
            dup = _find_hash_in_project(project_id, source_hash)
            if dup:
                document_id, version_id = dup["document_id"], dup["version_id"]
                version_reused = True
            else:
                doc = add_document(project_id, f"{name}.md", source_text)
                document_id, version_id = doc["document"]["document_id"], "v1"
                version_reused = False

            # 层2：AnalysisRun（按 migration_key 复用；版本存在不跳过 Run 迁移）
            mkey = migration_key(case_dir)
            existing_run = _run_exists_by_migration_key(project_id, mkey)
            if existing_run:
                report["reused"].append({"case": name, "run_id": existing_run.name})
                continue

            # 旧 mapping → paragraph_map 一致性校验（新清洗 vs 旧映射）
            old_mapping = _read_json(case_dir / "mapping.json").get("mapping", [])
            new_pmap = _read_json(_documents_dir(project_id) / document_id
                                  / "versions" / version_id / "paragraph_map.json")
            old_pairs = {p["did"]: p["pid"] for p in old_mapping}
            new_pairs = {p["clean_paragraph_id"]: p["source_paragraph_ids"][0]
                         for p in new_pmap["pairs"]}
            mapping_consistent = old_pairs == new_pairs

            rdir = create_run(project_id, [version_id],
                              prompt_version="unknown", model="unknown")
            meta = _read_json(rdir / "meta.json")
            meta["migration_key"] = mkey
            meta["legacy_path"] = str(case_dir)
            meta["cleaner_version"] = "unknown"   # 历史清洗无版本记录，不补造
            _write_json(rdir / "meta.json", meta)
            analysis = _read_json(ana_file)
            report_md = (case_dir / "report.md").read_text(encoding="utf-8") \
                if (case_dir / "report.md").exists() else ""
            usage = _read_json(case_dir / "usage.json") if (case_dir / "usage.json").exists() else None
            save_run_outputs(rdir, analysis, report_md,
                             {"project_id": project_id, "version_ids": [version_id],
                              "citations": [], "unresolved": [
                                  {"conclusion_type": "legacy_analysis",
                                   "conclusion_id": name,
                                   "reason": "历史输出无结构化引用数据，迁移不生成推测性引用"}]},
                             usage)
            entry = {"case": name, "run_id": rdir.name,
                     "document_id": document_id, "version_id": version_id,
                     "version_reused": version_reused}
            if not mapping_consistent:
                entry["warning"] = "旧 P-D 映射与当前清洗结果不一致，clean.json 以当前清洗为准"
                report["incomplete"].append(entry)
            else:
                report["migrated"].append(entry)
        except Exception as e:  # noqa: BLE001
            report["failed"].append({"case": name, "error": f"{type(e).__name__}: {e}"})
    return report


def main():
    """CLI：python src/storage.py migrate   （手动触发旧输出迁移）"""
    import sys
    if len(sys.argv) < 2 or sys.argv[1] != "migrate":
        print("用法：python src/storage.py migrate")
        return
    report = migrate_legacy()
    print(json.dumps({k: len(v) for k, v in report.items()}, ensure_ascii=False))
    for kind, items in report.items():
        for item in items:
            print(f"[{kind}] {json.dumps(item, ensure_ascii=False)[:200]}")


if __name__ == "__main__":
    main()
