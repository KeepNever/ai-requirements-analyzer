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
    # 阻断 1/4 容错（六审）：不可定位的版本不静默过滤——逐个记录 unresolved，
    # 只对可定位版本生成引用；部分缺失时报告如实呈现「哪些来源无法定位」
    locatable, missing = [], []
    for ref in version_refs:
        try:
            storage._load_version_data(project_id, ref["document_id"], ref["version_id"])
            locatable.append(ref)
        except (ValueError, FileNotFoundError):
            missing.append(ref)
    unresolved = []
    for ref in missing:
        unresolved.append({"conclusion_type": "export",
                           "conclusion_id": f"{ref['document_id']}（{ref['version_id']}）",
                           "reason": "来源版本不可定位（文档或版本缺失），该来源的全部结论无法生成引用"})
    if not locatable:
        unresolved.insert(0, {"conclusion_type": "export",
                              "conclusion_id": run["meta"].get("run_id"),
                              "reason": "全部版本引用不可定位（文档或版本缺失），证据无法展示"})
        return {"project_id": project_id, "citations": [], "unresolved": unresolved}
    result = storage.build_citations(run["analysis"], project_id, locatable)
    result["unresolved"] = unresolved + result.get("unresolved", [])
    return result


def _set_cn_font(doc: Document) -> None:
    """中文不乱码：Normal 样式显式设置中文字体（宋体）与西文（Calibri）。"""
    style = doc.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(11)
    style.element.rPr.rFonts.set(qn("w:eastAsia"), "宋体")


def _add_table(doc: Document, headers: list, rows: list,
               col_widths: list | None = None) -> None:
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
    if col_widths:
        _set_col_widths(t, col_widths)
    _no_split_rows(t)        # 单条记录不跨页断开
    _repeat_header(t)
    doc.add_paragraph()


def _file_name_of(project_id: str, document_id: str) -> str:
    """从 Document 元数据读 file_name（四审改进 1：主展示文件名，ID 作次要）。"""
    doc_file = storage._resolve_under_base("projects", project_id, "documents",
                                           document_id, "doc.json")
    if doc_file.exists():
        return storage._read_json(doc_file).get("file_name", document_id)
    return document_id



def _no_split_rows(table) -> None:
    """表格行禁止跨页断开（视觉验收：避免单条记录被切断成残片）。"""
    from docx.oxml import OxmlElement
    for row in table.rows:
        trPr = row._tr.get_or_add_trPr()
        cs = OxmlElement("w:cantSplit")
        trPr.append(cs)


def _set_col_widths(table, widths_cm: list) -> None:
    """显式列宽（cm 列表），按内容性质分配而不是均分挤爆。"""
    from docx.shared import Cm
    table.autofit = False
    for row in table.rows:
        for i, w in enumerate(widths_cm):
            if i < len(row.cells):
                row.cells[i].width = Cm(w)


def _repeat_header(table) -> None:
    """长表格跨页重复表头（四审内容组织建议）。"""
    from docx.oxml import OxmlElement
    tr = table.rows[0]._tr
    trPr = tr.get_or_add_trPr()
    th = OxmlElement("w:tblHeader")
    th.set(qn("w:val"), "true")
    trPr.append(th)


def _add_footer_page_number(doc: Document) -> None:
    """页脚页码字段（四审内容组织建议：跨页可读性）。"""
    from docx.oxml import OxmlElement
    footer = doc.sections[0].footer
    para = footer.paragraphs[0]
    para.alignment = 1
    run = para.add_run("第 ")
    fld1 = OxmlElement("w:fldChar"); fld1.set(qn("w:fldCharType"), "begin")
    instr = OxmlElement("w:instrText"); instr.text = "PAGE"
    fld2 = OxmlElement("w:fldChar"); fld2.set(qn("w:fldCharType"), "end")
    run._r.append(fld1); run._r.append(instr); run._r.append(fld2)
    para.add_run(" 页")


def export_run_docx(project_id: str, run_id: str, out_path: str | None = None) -> bytes:
    """导出指定历史运行（run_id 绑定）为 Word。返回 docx 二进制；out_path 给定时同时落盘。

    阻止条件（四审改进 4）：运行状态非 done 或缺 analysis.json 时拒绝导出，
    不生成看似完整的空报告。"""
    run = _load_run(project_id, run_id)
    meta, ana = run["meta"], run["analysis"]
    if meta.get("status") != "done":
        raise ValueError(f"运行状态为 {meta.get('status')!r}（非 done），不允许导出正式报告")
    if not ana:
        raise ValueError("运行缺少 analysis.json，不允许导出空报告")
    n1, n2 = ana.get("node1", {}) or {}, ana.get("node2", {}) or {}
    citations = _ensure_citations(project_id, run)
    frontend = storage.citations_for_frontend(citations, project_id)   # 脱敏转换（失败关闭）

    doc = Document()
    _set_cn_font(doc)
    _add_footer_page_number(doc)

    # ── 封面元信息（四审改进 1/4：file_name 主展示；unknown 显示历史缺失）──
    doc.add_heading(f"客户材料分析报告 · {project_id}", level=0)
    version_refs = meta.get("version_refs") or []
    materials = "；".join(
        f"{_file_name_of(project_id, r['document_id'])}（{r['version_id']}）"
        for r in version_refs) or "（无版本信息）"
    pv = str(meta.get("prompt_version", "unknown"))
    pv_display = "未记录（历史数据缺失）" if pv == "unknown" else pv
    _add_table(doc, ["项目", "分析运行 ID", "材料与版本", "Prompt 版本", "生成时间"], [[
        project_id, meta.get("run_id", run_id), materials,
        pv_display, str(meta.get("created_at", "unknown"))]],
        col_widths=[2.8, 4.0, 5.2, 2.2, 2.0])

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
                   [[e.get("id"), e.get("content"), "、".join(e.get("dids", []))] for e in ens],
                   col_widths=[1.4, 10.2, 4.4])
    else:
        doc.add_paragraph("（本运行数据中无显性需求数据）")

    # ── 三、需求优先级与评分 ──
    doc.add_heading("三、需求优先级与维度评分", level=1)
    pri_final = ana.get("priority") or []
    if not isinstance(pri_final, list):
        pri_final = []   # 旧结构（如 {"top": "EN1"}）无逐条加权数据，降级用维度表
    if pri_final:
        # 3.1 评分汇总表：纯数值列，紧凑易横向比较（视觉验收：依据不再塞窄单元格）
        _add_table(doc, ["编号", "影响面", "紧迫度", "覆盖度", "信号强度", "加权总分", "档位"],
                   [[p.get("id"), p.get("impact"), p.get("urgency"), p.get("coverage"),
                     p.get("signal"), p.get("weighted"), p.get("tier")] for p in pri_final],
                   col_widths=[2.0, 2.2, 2.2, 2.2, 2.2, 2.4, 1.6])
        # 3.2 逐需求评分依据：独立小标题 + 正文，长文本正常阅读
        doc.add_heading("评分依据（逐需求）", level=2)
        for p in pri_final:
            doc.add_heading(f"{p.get('id')}　{p.get('content', '')}", level=3)
            doc.add_paragraph(p.get("reason", ""))
    else:
        pds = n2.get("priority_dimensions") or []
        if pds:
            doc.add_heading("评分依据（逐需求）", level=2)
            for p in pds:
                doc.add_heading(f"{p.get('id')}", level=3)
                doc.add_paragraph(p.get("reason", ""))
        else:
            doc.add_paragraph("（本运行数据中无优先级评分数据）")

    # ── 四、产品能力匹配 ──
    doc.add_heading("四、产品能力匹配情况", level=1)
    cms = n2.get("capability_match") or []
    if cms:
        _add_table(doc, ["需求", "判定", "依据"],
                   [[c.get("target"), c.get("verdict"), c.get("reason", "")] for c in cms],
                   col_widths=[5.0, 1.8, 9.2])
    else:
        doc.add_paragraph("（本运行数据中无能力匹配数据）")

    # ── 五、潜在需求 ──
    doc.add_heading("五、潜在需求（推断）", level=1)
    pns = n2.get("potential_needs") or []
    if pns:
        _add_table(doc, ["编号", "潜在需求", "推断依据", "置信度"],
                   [[p.get("id"), p.get("content"), p.get("reasoning", ""), p.get("confidence", "")]
                    for p in pns],
                   col_widths=[1.4, 4.6, 8.4, 1.6])
    else:
        doc.add_paragraph("（本运行数据中无潜在需求数据）")

    # ── 六、待确认事项与建议追问 ──
    doc.add_heading("六、待确认事项与建议追问", level=1)
    pcs = n2.get("pending_confirmations") or []
    if pcs:
        _add_table(doc, ["缺口", "等级", "影响", "建议追问"],
                   [[p.get("gap"), p.get("level"), p.get("impact", ""), p.get("question", "")]
                    for p in pcs],
                   col_widths=[3.6, 1.4, 4.6, 6.4])
    else:
        doc.add_paragraph("（本运行数据中无待确认事项）")

    # ── 七、关键结论的原文引用与来源定位 ──
    doc.add_heading("七、关键结论的原文引用与来源定位", level=1)
    doc.add_paragraph(
        "说明：本节结构化引用仅覆盖能够可靠定位来源的结论；"
        "其余结论（潜在需求、能力匹配、优先级依据、待确认事项等）尚未完成结构化证据关联，"
        "见第八节未解析引用说明，不代表其判断已经过引用核验。")
    fcites = frontend.get("citations", [])
    if fcites:
        for c in fcites:
            doc.add_heading(f"结论 {c.get('conclusion_id')}（{c.get('document_id')} · {c.get('version_id')}）",
                            level=2)
            if not c.get("displayable", True):
                doc.add_paragraph("（该证据定位失败，不可展示——为保护隐私不输出原文）")
                continue
            for e in c.get("source_excerpts", []):
                txt = e.get("text", "")
                if not txt:
                    doc.add_paragraph(f"【{e.get('source_paragraph_id')}】（证据文本不可展示）")
                    continue
                doc.add_paragraph(f"【{e.get('source_paragraph_id')}】{txt}")
    else:
        doc.add_paragraph("（本运行无结构化引用——历史迁移运行未生成推测性引用，见第八节说明）")

    # ── 八、未解析引用与数据完整性说明 ──
    doc.add_heading("八、未解析引用与数据完整性说明", level=1)
    unresolved = citations.get("unresolved") or []
    if unresolved:
        doc.add_paragraph("以下结论无法从现有数据可靠定位来源，如实标记、不生成虚假证据：")
        rows = []
        for i, u in enumerate(unresolved, 1):
            cid = u.get("conclusion_id")
            label = cid if cid not in (None, "") else f"{u.get('conclusion_type')}#{i}"
            rows.append([u.get("conclusion_type"), label, u.get("reason", "")])
        _add_table(doc, ["类型", "结论", "原因"], rows, col_widths=[3.0, 4.4, 8.6])
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
