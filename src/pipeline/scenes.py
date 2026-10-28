"""场景模板引擎（Task 8：场景 JSON 化，docs/v2/07 借鉴 meetily templates 思想）。

双轨场景体系：
- 代码场景：presale（四节点复杂流程，保持 workflow.py 代码路径）
- 模板场景：单 Prompt 多段输出类（如 service_diagnosis），定义在 scenes/*.json，
  新增同类场景 = 新增一个 JSON 文件，零代码改动

JSON 契约（见 scenes/service_diagnosis.json）：
  id / name / prompt_version / report_title / meta_headers / meta_col_widths
  system_prompt / task_instruction / summary_title
  sections[]: key, word_title, citation_type, citation_reason, quote_field,
              empty_text, columns[{field,header}], col_widths, render(bullet),
              bullet_template
  actions_section（可选）: 不生成引用的服务方建议段
  citation_note / footer_note

引用纪律与售前一致：dids 只引用真实段落编号，excerpt 逐段取自 clean.json
脱敏文本；无 dids 的段（行动清单）不生成引用，不伪装成证据。
"""
import json
from pathlib import Path

import storage

SCENES_DIR = Path(__file__).resolve().parents[2] / "scenes"


def list_template_scenes() -> dict:
    """加载全部模板场景：{scene_id: scene_def}。"""
    out = {}
    if SCENES_DIR.exists():
        for f in sorted(SCENES_DIR.glob("*.json")):
            out[f.stem] = json.loads(f.read_text(encoding="utf-8"))
    return out


def get_scene(scene_id: str) -> dict:
    scenes = list_template_scenes()
    if scene_id not in scenes:
        raise ValueError(f"未知模板场景：{scene_id}（可用：{sorted(scenes)}）")
    return scenes[scene_id]


def analyze_scene(scene: dict, material_block: str, mock: bool = False) -> dict:
    """按场景 JSON 的 system_prompt / task_instruction 做分析。"""
    if mock:
        return _mock_by_schema(scene, material_block)
    from pipeline import llm_client
    messages = [{"role": "system", "content": scene["system_prompt"]},
                {"role": "user",
                 "content": f"【材料段落】\n{material_block}\n\n{scene['task_instruction']}"}]
    text = llm_client.chat(messages, json_mode=True, node="SCENE")
    return json.loads(text)


def _mock_by_schema(scene: dict, material_block: str) -> dict:
    """mock 通用化：按 sections 结构生成占位条目（无 Key 演示与测试用）。"""
    lines = [l for l in material_block.splitlines() if l.strip()]
    dids = [l.split(" ")[0] for l in lines if l.strip()][:2]
    sample = lines[0][:40] if lines else ""
    out = {"summary": f"（mock）{scene['name']}：基于材料完成 {scene['name']} 分析。"}
    for sec in scene["sections"]:
        if sec.get("mock_rows"):                      # 场景自带 mock 样例（如 BANT 四维度）
            out.setdefault(sec["key"], []).extend(
                json.loads(json.dumps(r, ensure_ascii=False)) for r in sec["mock_rows"])
            continue
        cols = sec.get("columns") or []
        item = {}
        if cols:                              # 表格段
            first_col = cols[0]["field"]
            item = {c["field"]: "" for c in cols}
            item[first_col] = "M1"
            item["content"] = f"（mock）依据材料完成的{sec['word_title']}"
            if sec.get("quote_field"):
                item[sec["quote_field"]] = sample
            if "level" in item:
                item["level"] = "中"
            if "priority" in item:
                item["priority"] = "高"
            if any(c["field"] == "_dids" for c in cols):
                item["dids"] = dids[:1]
        else:                                 # bullet 段（无表格列）
            item = {"issue": f"（mock）依据材料完成的{sec['word_title']}", "dids": dids[:1]}
        out.setdefault(sec["key"], []).append(item)
    if scene.get("actions_section"):
        out[scene["actions_section"]["key"]] = [
            {"action": f"（mock）跟进事项", "priority": "高",
             "detail": "建议时限（需服务负责人确认）内完成跟进"}]
    return out


def build_citations_scene(sd: dict, scene: dict, project_id: str,
                          version_refs: list) -> dict:
    """通用引用生成：按 sections 的 citation_type 映射，复用 Citation 结构。
    无 dids 的段（如行动清单，未在 sections 声明 citation_type）不生成引用。"""
    pool = {}
    for ref in version_refs:
        pmap, segments = storage._load_version_data(
            project_id, ref["document_id"], ref["version_id"])
        did2pair = {p["clean_paragraph_id"]: p for p in pmap["pairs"]}
        for s in segments:
            if s["did"] not in pool:
                pool[s["did"]] = {"document_id": ref["document_id"],
                                  "version_id": ref["version_id"],
                                  "pair": did2pair[s["did"]], "seg": s}

    citations, unresolved = [], []
    cite_types = {sec["key"]: sec.get("citation_type") for sec in scene["sections"]}
    for sec in scene["sections"]:
        ctype = cite_types.get(sec["key"])
        if not ctype:
            continue                      # 未声明 citation_type 的段不生成引用
        for item in (sd.get(sec["key"]) or []):
            dids = item.get("dids") or []
            cid = item.get("id") or item.get("type") or sec["key"]
            invalid = [d for d in dids if d not in pool]
            if not dids or invalid:
                unresolved.append({
                    "conclusion_type": ctype, "conclusion_id": cid,
                    "reason": ("引用编号不存在于清洗段落集合: " + ",".join(invalid))
                              if invalid else "无结构化引用字段（dids 为空）"})
                continue
            excerpts, broken = [], False
            for d in dids:
                info = pool[d]
                for e in storage._build_excerpts(info["pair"], info["seg"]):
                    if e["text"] is None:
                        broken = True
                        continue
                    excerpts.append({"document_id": info["document_id"],
                                     "version_id": info["version_id"],
                                     **e})
            if broken:
                unresolved.append({
                    "conclusion_type": ctype, "conclusion_id": cid,
                    "reason": "部分来源段落无法定位原文，拒绝生成推测性引用"})
                continue
            citations.append({
                "conclusion_type": ctype, "conclusion_id": cid,
                "document_id": excerpts[0]["document_id"] if excerpts else None,
                "version_id": excerpts[0]["version_id"] if excerpts else None,
                "clean_paragraph_ids": dids,
                "source_paragraph_ids": [e["source_paragraph_id"] for e in excerpts],
                "source_excerpts": excerpts,
                "citation_reason": sec.get("citation_reason", "支撑结论"),
                "quote_model": item.get(sec.get("quote_field", "quote")),
            })
    return {"project_id": project_id, "version_refs": version_refs,
            "citations": citations, "unresolved": unresolved}


def render_report_scene(sd: dict, scene: dict, project_id: str, run_id: str) -> str:
    """按场景 JSON 渲染 Markdown 报告。"""
    lines = [f"# {scene['report_title']} · {project_id}", "",
             f"- 诊断运行：{run_id}", f"- 摘要：{sd.get(scene.get('summary_field', 'summary'), '')}",
             ""]
    num = 0
    for sec in scene["sections"]:
        num += 1
        items = sd.get(sec["key"]) or []
        lines += [f"## {sec['word_title']}", ""]
        if not items:
            lines += [sec.get("empty_text", "（无数据）"), ""]
            continue
        if sec.get("render") == "bullet":
            for it in items:
                tpl = sec.get("bullet_template", "- {issue}")
                dids = "、".join(it.get("dids", [])) or "无引用"
                it2 = {k: v for k, v in it.items() if k != "dids"}   # 防止模板变量与 dids 冲突
                lines.append(tpl.format(**it2, dids=dids))
        else:
            for it in items:
                lines.append(f"### {it.get('id') or it.get('type')}"
                             f"{('（' + it.get('priority', '') + '）') if it.get('priority') else ''}")
                for c in sec["columns"]:
                    if c["field"] == "_dids":
                        continue
                    v = it.get(c["field"], "")
                    if v:
                        lines.append(f"- {c['header']}：{v}")
                dids = "、".join(it.get("dids", [])) or "无引用"
                lines.append(f"- 引用段落：{dids}")
                lines.append("")
        lines.append("")
    if scene.get("actions_section"):
        num += 1
        act = scene["actions_section"]
        items = sd.get(act["key"]) or []
        lines += [f"## {act['word_title']}", ""]
        for a in items:
            row = "：".join(f"{c['header']} {a.get(c['field'], '')}" for c in act["columns"]
                           if c["field"] != "priority" and a.get(c["field"]))
            lines.append(f"- **{a.get('action', '')}**（{a.get('priority', '')}）："
                         f"{a.get('detail', '')}" if False else
                         f"- **{a.get('action', '')}**（{a.get('priority', '')}）：{a.get('detail', '')}")
        lines.append("")
    lines += ["## 引用与未解析说明", "",
              "本报告引用均为脱敏后展示文本；无法可靠定位来源的结论在引用元数据中"
              "显式标记，不生成虚假证据。"]
    return "\n".join(lines)
