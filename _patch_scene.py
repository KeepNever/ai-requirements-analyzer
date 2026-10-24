# -*- coding: utf-8 -*-
"""一次性改造 export_docx.py（服务诊断场景分支）。执行后删除。"""
from pathlib import Path

p = Path("src/export_docx.py")
s = p.read_text(encoding="utf-8")

# import
old_imp = "import storage\nimport retrieval"
assert old_imp in s
if "service_diagnosis as _sd" not in s:
    s = s.replace(old_imp, old_imp + "\nfrom pipeline import service_diagnosis as _sd")

# 场景分支
old_branch = '''    run = _load_run(project_id, run_id)
    meta, ana = run["meta"], run["analysis"]
    if meta.get("status") != "done":'''
new_branch = '''    run = _load_run(project_id, run_id)
    meta, ana = run["meta"], run["analysis"]
    if meta.get("scene") == "service_diagnosis":
        return _export_service_docx(project_id, run, run_id, out_path)
    if meta.get("status") != "done":'''
assert old_branch in s, "分支锚点未找到"
if "meta.get(\"scene\")" not in s:
    s = s.replace(old_branch, new_branch)

# 专用导出函数
old_tail = '''def export_run_docx(project_id: str, run_id: str, out_path: str | None = None) -> bytes:'''
new_tail = '''def _export_service_docx(project_id: str, run: dict, run_id: str,
                         out_path: str | None = None) -> bytes:
    """服务诊断报告导出（场景层：四段结构 + 引用，纪律与售前一致）。"""
    from docx.shared import Cm as _Cm
    meta, ana = run["meta"], run["analysis"]
    citations = _ensure_citations(project_id, run)
    frontend = storage.citations_for_frontend(citations, project_id)

    doc = Document()
    _set_cn_font(doc)
    for sec in doc.sections:
        sec.left_margin = _Cm(2)
        sec.right_margin = _Cm(2)
    _add_footer_page_number(doc)

    doc.add_heading(f"学员服务诊断报告 · {project_id}", level=0)
    version_refs = meta.get("version_refs") or []
    materials = "；".join(
        f"{_file_name_of(project_id, r['document_id'])}（{r['version_id']}）"
        for r in version_refs) or "（无版本信息）"
    pv = str(meta.get("prompt_version", "unknown"))
    _add_table(doc, ["项目", "诊断运行 ID", "材料与版本", "诊断 Prompt 版本", "生成时间"], [[
        project_id, meta.get("run_id", run_id), materials, pv,
        str(meta.get("created_at", "unknown"))]],
        col_widths=[2.6, 3.8, 5.0, 2.8, 2.8])

    doc.add_heading("一、诊断摘要", level=1)
    doc.add_paragraph(ana.get("summary", "（无摘要）"))

    doc.add_heading("二、学员核心诉求（带原话锚点）", level=1)
    cds = ana.get("core_demands") or []
    if cds:
        _add_table(doc, ["编号", "诉求", "优先级", "原话锚点", "引用段落"],
                   [[c.get("id"), c.get("content"), c.get("priority"),
                     c.get("quote", ""), "、".join(c.get("dids", []))] for c in cds],
                   col_widths=[1.2, 5.4, 1.4, 5.6, 3.4])
    else:
        doc.add_paragraph("（本运行无核心需求数据）")

    doc.add_heading("三、情绪与风险信号", level=1)
    ers = ana.get("emotion_risks") or []
    if ers:
        _add_table(doc, ["信号", "等级", "触发原话", "引用段落"],
                   [[e.get("type"), e.get("level"), e.get("evidence_quote", ""),
                     "、".join(e.get("dids", []))] for e in ers],
                   col_widths=[3.4, 1.4, 7.8, 4.4])
    else:
        doc.add_paragraph("（本运行无情绪与风险数据）")

    doc.add_heading("四、服务方响应问题", level=1)
    ris = ana.get("response_issues") or []
    if ris:
        for i in ris:
            doc.add_paragraph(f"- {i.get('issue', '')}"
                              f"（{('、'.join(i.get('dids', []))) or '无引用'}）")
    else:
        doc.add_paragraph("（本运行无响应问题数据）")

    doc.add_heading("五、行动清单", level=1)
    ais = ana.get("action_items") or []
    if ais:
        _add_table(doc, ["行动", "优先级", "具体做法与时限"],
                   [[a.get("action"), a.get("priority"), a.get("detail", "")] for a in ais],
                   col_widths=[3.4, 1.4, 12.2])
    else:
        doc.add_paragraph("（本运行无行动清单）")

    doc.add_heading("六、原文引用与未解析说明", level=1)
    doc.add_paragraph(
        "说明：本节结构化引用仅覆盖能够可靠定位来源的结论；"
        "行动清单为服务方建议，不作为证据引用。")
    fcites = frontend.get("citations", [])
    if fcites:
        for c in fcites:
            doc.add_heading(f"{c.get('conclusion_type')} · {c.get('conclusion_id')}"
                            f"（{c.get('document_id')} · {c.get('version_id')}）", level=2)
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
        doc.add_paragraph("（本运行无结构化引用）")
    unresolved = citations.get("unresolved") or []
    if unresolved:
        rows = []
        for i, u in enumerate(unresolved, 1):
            cid = u.get("conclusion_id")
            label = cid if cid not in (None, "") else f"{u.get('conclusion_type')}#{i}"
            rows.append([u.get("conclusion_type"), label, u.get("reason", "")])
        _add_table(doc, ["类型", "结论", "原因"], rows, col_widths=[3.0, 4.4, 9.6])

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


def export_run_docx(project_id: str, run_id: str, out_path: str | None = None) -> bytes:'''
assert old_tail in s, "export_run_docx 锚点未找到"
if "_export_service_docx" not in s:
    s = s.replace(old_tail, new_tail, 1)
p.write_text(s, encoding="utf-8")
print("export_docx 服务诊断分支完成")
