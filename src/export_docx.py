"""Task 6：Word 报告导出（方案 P3 前置，docs/v2/04 纪律）。

纪律约束：
- 绑定明确 run_id：只导出用户指定的历史运行，不自动选最新
- 引用通过已有 Citation 结构生成：run 的 citations.json 若无结构化引用，
  用 storage.build_citations 从 analysis 的结构化 dids 现场生成（同一结构）
- 只写脱敏文本：全部引用经 citations_for_frontend 转换，raw_text 不进 docx
- 无法可靠取得的引用明确标记（unresolved 列表原样呈现），不伪装成有效证据
- 不改 Prompt / 检索 / EVD-01 / 评测器；不影响现有网页功能

章节：元信息 / 背景与目标 / 显性需求 / 优先级评分 / 能力匹配 / 潜在需求 /
     待确认事项 / 关键结论原文引用 / 未解析引用说明。缺数据的章节明确标注。
"""
import json
from pathlib import Path

from docx import Document
from docx.shared import Pt
from docx.oxml.ns import qn

import storage
import retrieval


def _load_run(project_id: str, run_id: str) -> dict:
    rdir = storage._resolve_under_base(
        "projects", project_id, "analysis_runs", run_id)
    if not (rdir / "meta.json").exists():
        raise ValueError(f"运行不存在：{project_id}/{run_id}")
    meta = storage._read_json(rdir / "meta.json")
    ana = (storage._read_json(rdir / "analysis.json")
           if (rdir / "analysis.json").exists() else {})
    citations = (storage._read_json(rdir / "citations.json")
                 if (rdir / "citations.json").exists() else {})
    return {"meta": meta, "analysis": ana, "citations": citations}


def _ensure_citations(project_id: str, run: dict) -> dict:
    """引用生成：run 已有结构化 citations 则用之；否则从 analysis 的 dids
    经 storage.build_citations 现场生成（同一 Citation 结构，不虚构）。"""
    cits = run["citations"]
    if cits.get("citations"):
        return cits
    version_refs = run["meta"].get("version_refs") or []
    if not version_refs:
        return {"project_id": project_id, "citations": [],
                "unresolved": [{"conclusion_type": "export",
                                "conclusion_id": run["meta"].get("run_id"),
                                "reason": "运行缺少版本引用信息，无法生成结构化引用"}]}
    return storage.build_citations(run["analysis"], project_id, version_refs)


def _set_cn_font(doc: Document) -> None:
    """中文不乱码：Normal 样式显式设置中文字体（宋体）与西文（Calibri）。"""
    style = doc.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(11)
    style.element.rPr.rFonts.set(qn("w:eastAsia"), "宋体")


def _add_table(doc: Document, headers: list, rows: list) -> None:
    t = doc.add_table(rows=1, cols=len(headers))
    t.style = "Table Grid"
    for i, h in enumerate(headers):
        cell = t.rows[0].cells[i]
        cell.text = h
        for run in cell.paragraphs[0].runs:
            run.font.bold = True
    for row in rows:
        cells = t.add_row().cells
        for i, v in enumerate(row):
            cells[i].text = str(v)
    doc.add_paragraph()


def export_run_docx(project_id: str, run_id: str, out_path: str | None = None) -> bytes:
    """导出指定历史运行（run_id 绑定）为 Word。返回 docx 二进制；out_path 给定时同时落盘。"""
    run = _load_run(project_id, run_id)
    meta, ana = run["meta"], run["analysis"]
    n1, n2 = ana.get("node1", {}) or {}, ana.get("node2", {}) or {}
    citations = _ensure_citations(project_id, run)
    frontend = storage.citations_for_frontend(citations, project_id)   # 脱敏转换

    doc = Document()
    _set_cn_font(doc)

    # ── 封面元信息 ──
    doc.add_heading(f"客户材料分析报告 · {project_id}", level=0)
    version_refs = meta.get("version_refs") or []
    materials = "；".join(
        f"{r['document_id']}（{r['version_id']}）" for r in version_refs) or "（无版本信息）"
    _add_table(doc, ["项目", "分析运行 ID", "材料与版本", "Prompt 版本", "生成时间"], [[
        project_id, meta.get("run_id", run_id), materials,
        str(meta.get("prompt_version", "unknown")),
        str(meta.get("created_at", "unknown"))]])

    # ── 一、客户背景与业务目标 ──
    doc.add_heading("一、客户背景与业务目标", level=1)
    bg = n1.get("customer_background")
    goals = n1.get("business_goals")
    if bg:
        doc.add_paragraph(str(bg) if not isinstance(bg, list) else "；".join(map(str, bg)))
    if goals:
        doc.add_paragraph("业务目标：" + ("；".join(map(str, goals)) if isinstance(goals, list) else str(goals)))
    if not bg and not goals:
        doc.add_paragraph("（本运行数据中无客户背景与业务目标信息）")

    # ── 二、显性需求清单 ──
    doc.add_heading("二、显性需求清单", level=1)
    ens = n1.get("explicit_needs") or []
    if ens:
        _add_table(doc, ["编号", "需求", "引用段落（D 编号）"],
                   [[e.get("id"), e.get("content"), "、".join(e.get("dids", []))] for e in ens])
    else:
        doc.add_paragraph("（本运行数据中无显性需求数据）")

    # ── 三、需求优先级与评分 ──
    doc.add_heading("三、需求优先级与维度评分", level=1)
    pds = n2.get("priority_dimensions") or []
    if pds:
        _add_table(doc, ["编号", "影响面", "紧迫度", "覆盖度", "信号强度", "评分依据"],
                   [[p.get("id"), p.get("impact"), p.get("urgency"),
                     p.get("coverage"), p.get("signal"), p.get("reason", "")] for p in pds])
    else:
        doc.add_paragraph("（本运行数据中无优先级评分数据）")

    # ── 四、产品能力匹配 ──
    doc.add_heading("四、产品能力匹配情况", level=1)
    cms = n2.get("capability_match") or []
    if cms:
        _add_table(doc, ["需求", "判定", "依据"],
                   [[c.get("target"), c.get("verdict"), c.get("reason", "")] for c in cms])
    else:
        doc.add_paragraph("（本运行数据中无能力匹配数据）")

    # ── 五、潜在需求 ──
    doc.add_heading("五、潜在需求（推断）", level=1)
    pns = n2.get("potential_needs") or []
    if pns:
        _add_table(doc, ["编号", "潜在需求", "推断依据", "置信度"],
                   [[p.get("id"), p.get("content"), p.get("reasoning", ""), p.get("confidence", "")]
                    for p in pns])
    else:
        doc.add_paragraph("（本运行数据中无潜在需求数据）")

    # ── 六、待确认事项与建议追问 ──
    doc.add_heading("六、待确认事项与建议追问", level=1)
    pcs = n2.get("pending_confirmations") or []
    if pcs:
        _add_table(doc, ["缺口", "等级", "影响", "建议追问"],
                   [[p.get("gap"), p.get("level"), p.get("impact", ""), p.get("question", "")]
                    for p in pcs])
    else:
        doc.add_paragraph("（本运行数据中无待确认事项）")

    # ── 七、关键结论的原文引用与来源定位 ──
    doc.add_heading("七、关键结论的原文引用与来源定位", level=1)
    fcites = frontend.get("citations", [])
    if fcites:
        for c in fcites:
            doc.add_heading(f"结论 {c.get('conclusion_id')}（{c.get('document_id')} · {c.get('version_id')}）",
                            level=2)
            for e in c.get("source_excerpts", []):
                doc.add_paragraph(
                    f"【{e.get('source_paragraph_id')}】{e.get('text', '')}")
    else:
        doc.add_paragraph("（本运行无结构化引用——历史迁移运行未生成推测性引用，见第八节说明）")

    # ── 八、未解析引用与数据完整性说明 ──
    doc.add_heading("八、未解析引用与数据完整性说明", level=1)
    unresolved = citations.get("unresolved") or []
    if unresolved:
        doc.add_paragraph("以下结论无法从现有数据可靠定位来源，如实标记、不生成虚假证据：")
        _add_table(doc, ["类型", "结论", "原因"],
                   [[u.get("conclusion_type"), u.get("conclusion_id"), u.get("reason", "")]
                    for u in unresolved])
    else:
        doc.add_paragraph("本运行全部结论均有结构化引用支撑。")

    doc.add_paragraph("— 本报告由 AI 客户材料分析工作台基于指定历史运行导出，"
                      "引用均为脱敏后展示文本 —")

    import io
    buf = io.BytesIO()
    doc.save(buf)
    data = buf.getvalue()
    if out_path:
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_bytes(data)
    return data
