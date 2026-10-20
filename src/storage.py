"""V2 存储层：Project / Document / DocumentVersion / AnalysisRun / Citation。

设计依据：docs/v2/01_Task1_数据现状与引用映射梳理.md（v1.3 契约 + 三审实现约束）。
- 纯标准库 JSON 落盘，目录即实体；JSON 原子写入（临时文件 + replace）
- 版本化：原文随版本走，任何写入不覆盖历史；Run 完成后拒绝重写
- 引用：source_excerpts 逐段取自对应来源原文（代表段 raw_text / 被合并段
  merged_from），由系统生成，不信模型转述；前端展示失败关闭（fail-closed），
  任何定位失败都不得返回 raw_text
- 迁移：分层幂等（版本按 source_hash+cleaner_version；Run 按 migration_key），
  不完整 Run 补齐缺失文件而非跳过
- 多文档：AnalysisRun 保存 version_refs = [{document_id, version_id}]，
  引用按完整组合定位，不凭裸 version_id 猜来源

本层不改动 Workflow / Prompt / 评测器的业务行为。
"""
import hashlib
import json
import os
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
UNSAFE_ID_RE = re.compile(r"(\.\.|[/\\]|^[A-Za-z]:)")


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


def _safe_component(component: str) -> str:
    """路径安全校验：id 不得含路径穿越或分隔符（Web 入参防线）。"""
    if not component or UNSAFE_ID_RE.search(component):
        raise ValueError(f"非法标识符：{component!r}")
    return component


def _resolve_under_base(*parts) -> Path:
    """拼接路径并确保结果始终位于存储根内（防穿越）。"""
    safe = [_safe_component(p) for p in parts]
    resolved = (_base().joinpath(*safe)).resolve()
    if not str(resolved).startswith(str(_base().resolve())):
        raise ValueError(f"路径越界：{resolved}")
    return resolved


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _write_json(path: Path, obj) -> None:
    """原子写入：临时文件 + os.replace，进程中断不留半截文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


# ---------- Project ----------

def create_project(name: str) -> dict:
    pid = _safe_component(slug(name))
    pdir = _resolve_under_base("projects", pid)
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
    p = _resolve_under_base("projects", project_id, "doc.json")
    return _read_json(p) if p.exists() else None


def list_projects() -> list:
    idx = _projects_dir() / "index.json"
    return list(_read_json(idx).items()) if idx.exists() else []


# ---------- Document 与版本 ----------

def _documents_dir(project_id: str) -> Path:
    return _resolve_under_base("projects", project_id, "documents")


def _clean_source(source_text: str) -> dict:
    """源文本 → {stats, segments, paragraph_map}。

    审查问题 6 修复：与旧 Workflow 相同的材料提取规则——
    先经 cleaner.extract_material 截取「## 原始材料」之后正文，再清洗，
    保证新旧路径的 P/D 映射一致（对照测试 test_old_new_cleaning_consistency）。
    """
    material = cleaner.extract_material(source_text)
    cr = cleaner.clean(material)
    segments = [{"pid": s.pid, "did": s.did, "raw_text": s.raw_text, "clean_text": s.text,
                 "pii_scrubbed": s.pii_scrubbed, "merged_from": s.merged_from}
                for s in cr.segments]
    pmap = {"version_id": None, "pairs": [
        {"clean_paragraph_id": s.did,
         "source_paragraph_ids": [s.pid] + [m["pid"] for m in s.merged_from]}
        for s in cr.segments]}
    return {"stats": cr.stats, "segments": segments, "paragraph_map": pmap,
            "cleaner_version": cleaner.CLEANER_VERSION}


def _write_version(vdir: Path, version_id: str, document_id: str, source_text: str) -> dict:
    """生成一个版本的三个自包含文件 + version.json。

    审查问题 5 修复：原子化——全部写入临时目录，成功后整体 rename；
    目标目录已存在则拒绝（不可覆盖），失败时清理临时目录，不留半截版本。
    """
    if vdir.exists():
        raise ValueError(f"版本目录已存在，拒绝覆盖：{vdir}")
    tmp_dir = vdir.parent / f".tmp_{version_id}_{uuid.uuid4().hex[:6]}"
    try:
        tmp_dir.mkdir(parents=True)
        (tmp_dir / "source.md").write_text(source_text, encoding="utf-8")
        cleaned = _clean_source(source_text)
        cleaned["paragraph_map"]["version_id"] = version_id
        _write_json(tmp_dir / "clean.json",
                    {"stats": cleaned["stats"], "segments": cleaned["segments"]})
        _write_json(tmp_dir / "paragraph_map.json", cleaned["paragraph_map"])
        meta = {"version_id": version_id, "document_id": document_id,
                "cleaner_version": cleaner.CLEANER_VERSION,
                "source_hash": sha256_text(source_text), "created_at": now_iso()}
        _write_json(tmp_dir / "version.json", meta)
        os.replace(tmp_dir, vdir)
        return meta
    except Exception:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise


def _iter_versions(project_id: str, document_id: str):
    versions_dir = _documents_dir(project_id) / document_id / "versions"
    if not versions_dir.exists():
        return
    for vm_file in sorted(versions_dir.glob("*/version.json")):
        yield _read_json(vm_file)


def _find_reusable_version(project_id: str, source_hash: str, cleaner_version: str):
    """版本复用检查：source_hash + cleaner_version 双因子（契约 §3.2）。"""
    for doc_dir in sorted(_documents_dir(project_id).glob("*/")):
        for vm in _iter_versions(project_id, doc_dir.name):
            if vm["source_hash"] == source_hash and vm["cleaner_version"] == cleaner_version:
                return vm
    return None


def _find_content_duplicate(project_id: str, source_hash: str):
    """内容重复检查：仅按 source_hash，任意清洗版本（契约 §3.1 查重提示）。"""
    for doc_dir in sorted(_documents_dir(project_id).glob("*/")):
        for vm in _iter_versions(project_id, doc_dir.name):
            if vm["source_hash"] == source_hash:
                return vm
    return None


def add_document(project_id: str, file_name: str, source_text: str) -> dict:
    """新建文档并生成 v1。全项目内容查重命中则拒绝（契约：一致内容不生成空版本）。"""
    _documents_dir(project_id).mkdir(parents=True, exist_ok=True)
    source_hash = sha256_text(source_text)
    dup = _find_content_duplicate(project_id, source_hash)
    if dup:
        raise ValueError(f"内容与已有文档重复：{dup['document_id']}/{dup['version_id']}")
    document_id = f"{slug(file_name)}-{time.strftime('%m%d%H%M%S')}-{uuid.uuid4().hex[:2]}"
    vdir = _documents_dir(project_id) / document_id / "versions" / "v1"
    vmeta = _write_version(vdir, "v1", document_id, source_text)
    doc_meta = {"document_id": document_id, "project_id": project_id,
                "file_name": file_name, "current_version_id": "v1",
                "uploaded_at": now_iso()}
    _write_json(_documents_dir(project_id) / document_id / "doc.json", doc_meta)
    return {"document": doc_meta, "version": vmeta}


def add_document_version(project_id: str, document_id: str, source_text: str) -> dict:
    """为已有文档追加新版本。

    审查问题 3 修复：拒绝条件从「hash 相同」改为「hash + 当前 CLEANER_VERSION
    已有版本」——同原文 + 新清洗器版本应允许重新清洗建版。
    """
    doc_file = _documents_dir(project_id) / document_id / "doc.json"
    if not doc_file.exists():
        raise ValueError(f"文档不存在：{document_id}")
    doc_meta = _read_json(doc_file)
    source_hash = sha256_text(source_text)
    reuse = _find_reusable_version(project_id, source_hash, cleaner.CLEANER_VERSION)
    if reuse:
        raise ValueError(f"相同原文已存在相同清洗版本：{reuse['document_id']}/{reuse['version_id']}，不重复生成")
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


# ---------- AnalysisRun ----------

def create_run(project_id: str, version_refs: list, prompt_version: str, model: str) -> Path:
    """version_refs: [{document_id, version_id}, ...] —— 完整组合，杜绝裸 version_id 歧义。"""
    run_id = f"run{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:4]}"
    rdir = _resolve_under_base("projects", project_id, "analysis_runs", run_id)
    if rdir.exists():
        raise ValueError(f"run 目录冲突：{run_id}")
    _write_json(rdir / "meta.json", {
        "run_id": run_id, "project_id": project_id, "version_refs": version_refs,
        "prompt_version": prompt_version, "model": model,
        "status": "running", "created_at": now_iso(), "usage": None,
    })
    return rdir


def save_run_outputs(run_dir: Path, analysis: dict, report_md: str,
                     citations: dict, usage=None) -> None:
    """写入运行产物并置 done。已完成 Run 拒绝覆盖（审查问题 5）；
    ReportVersion 元数据落盘（契约 §3.6）：默认版本 = run_id，人工修正另起 -revN。"""
    meta = _read_json(run_dir / "meta.json")
    if meta.get("status") == "done":
        raise ValueError(f"Run 已完成，拒绝覆盖：{run_dir.name}")
    _write_json(run_dir / "analysis.json", analysis)
    _write_text(run_dir / "report.md", report_md)
    _write_json(run_dir / "citations.json", citations)
    _write_json(run_dir / "report_version.json", {
        "report_version_id": meta["run_id"], "run_id": meta["run_id"],
        "path": "report.md", "revision": 0, "created_at": now_iso()})
    meta["usage"] = usage
    meta["status"] = "done"
    _write_json(run_dir / "meta.json", meta)


def _run_complete(rdir: Path) -> bool:
    """Run 完整性判定：status=done 且三类产物文件齐备。"""
    meta_file = rdir / "meta.json"
    if not meta_file.exists():
        return False
    meta = _read_json(meta_file)
    return (meta.get("status") == "done"
            and (rdir / "analysis.json").exists()
            and (rdir / "citations.json").exists()
            and (rdir / "report.md").exists())


# ---------- Citation 生成 ----------

def _load_version_data(project_id: str, document_id: str, version_id: str):
    """按完整组合加载版本数据（document_id + version_id，无歧义定位）。"""
    vdir = _documents_dir(project_id) / document_id / "versions" / version_id
    if not (vdir / "paragraph_map.json").exists():
        raise ValueError(f"版本不存在：{document_id}/{version_id}")
    return (_read_json(vdir / "paragraph_map.json"),
            _read_json(vdir / "clean.json")["segments"])


def _build_excerpts(pair: dict, seg: dict) -> list:
    """逐段生成证据原文（审查问题 4）：
    代表段取该段 raw_text；被合并段从 merged_from 中按 pid 取各自原文。
    找不到对应原文的段落记 text=None（由调用方降级处理，绝不复制合并段长文）。"""
    excerpts = []
    for p in pair["source_paragraph_ids"]:
        if p == seg["pid"]:
            text = seg["raw_text"]
        else:
            text = next((m["raw_text"] for m in seg.get("merged_from", [])
                         if m["pid"] == p), None)
        excerpts.append({"source_paragraph_id": p, "text": text})
    return excerpts


def build_citations(analysis: dict, project_id: str, version_refs: list) -> dict:
    """从 analysis 的结构化 dids 字段生成规范化引用。

    version_refs: [{document_id, version_id}, ...]（审查问题 2：按完整组合定位）。
    证据字段 source_excerpts 逐段取自 clean.json 原文，不使用模型转述。
    """
    # 本阶段分析绑定单文档版本组合；多文档时由调用方按结论归属扩展
    ref = version_refs[0]
    document_id, version_id = ref["document_id"], ref["version_id"]
    pmap, segments = _load_version_data(project_id, document_id, version_id)
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
        excerpts, broken = [], False
        for d in dids:
            for e in _build_excerpts(did2pair[d], seg_by_did[d]):
                if e["text"] is None:
                    broken = True
                excerpts.append(e)
        if broken:
            unresolved.append({
                "conclusion_type": "explicit_need", "conclusion_id": en.get("id"),
                "reason": "部分来源段落无法定位原文，拒绝生成推测性引用"})
            continue
        citations.append({
            "conclusion_type": "explicit_need", "conclusion_id": en.get("id"),
            "document_id": document_id,
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
    return {"project_id": project_id, "version_refs": version_refs,
            "citations": citations, "unresolved": unresolved}


def citations_for_frontend(citations: dict, project_id: str) -> dict:
    """前端展示版（审查问题 1：失败关闭）。

    任何定位失败（缺文档/缺版本/缺映射/原文缺失）都不得返回 raw_text——
    对应引用标记 displayable=false 并清空文本，宁可少展示不可泄露。
    """
    import copy
    out = copy.deepcopy(citations)
    for c in out.get("citations", []):
        try:
            vid, pid_ = c.get("version_id"), c.get("document_id")
            if not (vid and pid_):
                raise ValueError("引用缺少来源定位")
            vdir = _documents_dir(project_id) / pid_ / "versions" / vid
            if not (vdir / "clean.json").exists():
                raise ValueError("版本数据缺失")
            segs = {s["did"]: s for s in _read_json(vdir / "clean.json")["segments"]}
            pmap = _read_json(vdir / "paragraph_map.json")
            did_by_source = {}
            for p in pmap["pairs"]:
                for sp in p["source_paragraph_ids"]:
                    did_by_source[sp] = p["clean_paragraph_id"]
            for e in c.get("source_excerpts", []):
                did = did_by_source.get(e["source_paragraph_id"])
                if did is None or did not in segs:
                    raise ValueError("原文段落无法映射到脱敏文本")
                e["text"] = segs[did]["clean_text"]   # 脱敏文本（可能含脱敏占位符）
            c["displayable"] = True
            c.pop("quote_model", None)  # 模型转述不出现在前端证据中
        except Exception:
            c["displayable"] = False
            c["unresolved_reason"] = "证据定位失败，引用不可展示"
            for e in c.get("source_excerpts", []):
                e["text"] = ""                        # 失败关闭：清空而非保留原文
            c.pop("quote_model", None)
    return out


# ---------- 旧输出迁移（分层幂等 + 完整性恢复） ----------

def _fingerprint(paths: list) -> str:
    h = hashlib.sha256()
    for p in sorted(paths):
        f = Path(p)
        h.update(f.name.encode("utf-8"))
        if f.exists():
            h.update(f.read_bytes())
    return h.hexdigest()[:12]


def migration_key(legacy_dir: Path) -> str:
    """确定性迁移键：旧输出相对路径 + analysis/mapping/usage 三文件指纹。"""
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


def _find_run_by_migration_key(project_id: str, mkey: str):
    """返回 (rdir, complete)；complete=False 时调用方应补齐缺失文件。"""
    runs = _projects_dir() / project_id / "analysis_runs"
    if not runs.exists():
        return None, False
    for rdir in sorted(runs.iterdir()):
        mf = rdir / "meta.json"
        if mf.exists() and _read_json(mf).get("migration_key") == mkey:
            return rdir, _run_complete(rdir)
    return None, False


def migrate_legacy(output_dir: Path | None = None) -> dict:
    """旧 output/<case>/ → 新结构。

    审查问题 5 修复：分层幂等 + 完整性区分——
    完整 Run 复用跳过；不完整 Run（running/缺文件）补齐缺失部分而非跳过；
    同一材料多次真实分析是合法多条 Run，不因版本存在而跳过 Run 迁移。
    迁移报告五分类：migrated / reused / missing_source / incomplete / failed。
    """
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

            # 层1：Project + Document + Version（hash+cleaner_version 复用）
            project_id = slug(name)
            if not get_project(project_id):
                create_project(name)
            reuse = _find_reusable_version(project_id, source_hash, cleaner.CLEANER_VERSION)
            if reuse:
                document_id, version_id = reuse["document_id"], reuse["version_id"]
                version_reused = True
            else:
                doc = add_document(project_id, f"{name}.md", source_text)
                document_id, version_id = doc["document"]["document_id"], "v1"
                version_reused = False

            # 层2：AnalysisRun（migration_key 复用完整 Run；不完整 Run 补齐）
            mkey = migration_key(case_dir)
            existing_run, complete = _find_run_by_migration_key(project_id, mkey)
            if existing_run and complete:
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

            version_refs = [{"document_id": document_id, "version_id": version_id}]
            if existing_run:                      # 不完整 Run：补齐缺失产物
                rdir = existing_run
                meta = _read_json(rdir / "meta.json")
                meta["status"] = "running"        # 解除 done 保护，允许补写
                _write_json(rdir / "meta.json", meta)
                resumed = True
            else:
                rdir = create_run(project_id, version_refs,
                                  prompt_version="unknown", model="unknown")
                meta = _read_json(rdir / "meta.json")
                meta["migration_key"] = mkey
                meta["legacy_path"] = str(case_dir)
                meta["cleaner_version"] = "unknown"   # 历史清洗无版本记录，不补造
                _write_json(rdir / "meta.json", meta)
                resumed = False
            analysis = _read_json(ana_file)
            report_md = (case_dir / "report.md").read_text(encoding="utf-8") \
                if (case_dir / "report.md").exists() else ""
            usage = _read_json(case_dir / "usage.json") if (case_dir / "usage.json").exists() else None
            save_run_outputs(rdir, analysis, report_md,
                             {"project_id": project_id, "version_refs": version_refs,
                              "citations": [], "unresolved": [
                                  {"conclusion_type": "legacy_analysis",
                                   "conclusion_id": name,
                                   "reason": "历史输出无结构化引用数据，迁移不生成推测性引用"}]},
                             usage)
            entry = {"case": name, "run_id": rdir.name,
                     "document_id": document_id, "version_id": version_id,
                     "version_reused": version_reused, "run_resumed": resumed}
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
