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


def design_custom_scene(requirement: str, material_sample: str, mock: bool = False) -> dict:
    """用户一句话需求 → 输出结构声明（sections 子集，供确认与运行时构造场景）。

    LLM 只负责设计结构；分析用的 system_prompt 由 build_custom_scene 确定性构造，
    引用纪律（dids 真实、quote 原话）内建于模板，不依赖 LLM 自觉。
    """
    requirement = (requirement or "").strip()
    if not requirement:
        raise ValueError("需求描述为空")
    if mock:
        return {
            "scene_name": "自定义提炼",
            "summary_title": "一、提炼摘要",
            "sections": [
                {"key": "key_points", "word_title": "二、关键要点",
                 "citation_type": "key_point", "citation_reason": "支撑要点提炼",
                 "quote_field": "quote", "empty_text": "（本运行无要点数据）",
                 "columns": [{"field": "id", "header": "编号"},
                             {"field": "content", "header": "要点"},
                             {"field": "category", "header": "分类"},
                             {"field": "quote", "header": "原话依据"},
                             {"field": "_dids", "header": "引用段落"}],
                 "col_widths": [1.2, 5.4, 2.4, 6.4, 3.0]},
                {"key": "info_items", "word_title": "三、关键信息条目",
                 "citation_type": "info_item", "citation_reason": "支撑信息提取",
                 "quote_field": "content", "empty_text": "（本运行无信息条目）",
                 "columns": [{"field": "content", "header": "信息内容"},
                             {"field": "note", "header": "说明"},
                             {"field": "_dids", "header": "引用段落"}],
                 "col_widths": [9.4, 5.0, 3.0]}],
        }
    from pipeline import llm_client
    design_sys = ('你是分析场景设计助手。用户会对一份材料提出提炼需求，你需要设计一个 JSON 输出结构。'
                  '只输出 JSON，格式：\n'
                  '{\n'
                  '  "scene_name": "简短场景名",\n'
                  '  "summary_title": "一、xxx摘要",\n'
                  '  "sections": [\n'
                  '    {"key": "英文小写下划线标识", "word_title": "二、中文名（带序号）",\n'
                  '     "citation_type": "英文标识", "citation_reason": "支撑什么结论",\n'
                  '     "quote_field": "承载原话引用的字段名（无则省略）",\n'
                  '     "empty_text": "（本运行无xx数据）",\n'
                  '     "columns": [{"field": "英文字段", "header": "中文列名"}],\n'
                  '     "col_widths": [数字...与columns等长]}\n'
                  '  ]\n'
                  '}\n'
                  '规则：\n'
                  '1. sections 1~3 个，每个 section 的 columns 2~5 列；\n'
                  '2. 需要引用原文的 section：含 "_dids" 特殊列（引用段落）与 quote_field（原话字段）；\n'
                  '3. 第一个 section 通常是分类要点或核心结论（带 id/content/分类）；\n'
                  '4. 按用户需求设计，不要添加用户没要求的内容。')
    messages = [
        {"role": "system", "content": design_sys},
        {"role": "user",
         "content": f"【用户提炼需求】\n{requirement}\n\n【材料样例（前几段）】\n{material_sample}"}]
    text = llm_client.chat(messages, json_mode=True, node="SCENE_DESIGN")
    spec = json.loads(text)
    return _validate_custom_spec(spec)


def _validate_custom_spec(spec: dict) -> dict:
    """校验 LLM 生成的场景声明：结构合法、字段兜底、剔除危险内容。"""
    if not isinstance(spec, dict) or not spec.get("sections"):
        raise ValueError("生成的场景结构无效（sections 为空）")
    spec["scene_name"] = str(spec.get("scene_name", "自定义提炼"))[:30]
    spec["summary_title"] = str(spec.get("summary_title", "一、提炼摘要"))[:40]
    clean_secs = []
    for i, sec in enumerate(spec["sections"][:3]):          # 最多 3 段
        if not isinstance(sec, dict) or not sec.get("columns"):
            continue
        key = "".join(ch for ch in str(sec.get("key", f"sec{i+1}")) if ch.isalnum() or ch == "_")[:30] or f"sec{i+1}"
        cols = []
        for c in (sec.get("columns") or [])[:5]:            # 每段最多 5 列
            if not isinstance(c, dict) or not c.get("field"):
                continue
            if c["field"] in ("system_prompt", "task_instruction"):   # 危险字段剔除
                continue
            cols.append({"field": str(c["field"])[:30], "header": str(c.get("header", c["field"]))[:20]})
        if len(cols) < 1:
            continue
        entry = {"key": key,
                 "word_title": str(sec.get("word_title", f"{i+2}、{key}"))[:40],
                 "citation_type": str(sec.get("citation_type", key))[:30],
                 "citation_reason": str(sec.get("citation_reason", "支撑结论"))[:40],
                 "empty_text": str(sec.get("empty_text", f"（本运行无{key}数据）"))[:30],
                 "columns": cols}
        if sec.get("quote_field"):
            entry["quote_field"] = str(sec["quote_field"])[:30]
        if any(c["field"] == "_dids" for c in cols):
            entry.setdefault("citation_type", key)
        clean_secs.append(entry)
    if not clean_secs:
        raise ValueError("生成的场景结构无效（无合法 section）")
    spec["sections"] = clean_secs
    return spec


def build_custom_scene(spec: dict, requirement: str) -> dict:
    """由结构声明 + 用户需求，确定性构造完整场景 dict（引用纪律内建）。"""
    json_desc_lines = ['{ "summary": "一句话概括核心发现",']
    for i, sec in enumerate(spec["sections"]):
        cols = [c for c in sec["columns"] if c["field"] != "_dids"]
        item_fields = ", ".join(f'"{c["field"]}": "..."' for c in cols)
        need_dids = any(c["field"] == "_dids" for c in sec["columns"])
        item_fields += ', "dids": ["D..."]' if need_dids else ""
        json_desc_lines.append(f'  "{sec["key"]}": [ {{ {item_fields} }} ]'
                               + ("," if i < len(spec["sections"]) - 1 else ""))
    json_desc = "\n".join(json_desc_lines) + " }"
    quote_rule = ""
    quote_secs = [s for s in spec["sections"] if s.get("quote_field")]
    if quote_secs:
        qf = "、".join(f'{s["key"]} 的 {s["quote_field"]}' for s in quote_secs)
        quote_rule = (f"\n- 需要原话引用的字段（{qf}）必须摘录材料中的原话，不得改写或编造；")
    dids_rule = ""
    if any(any(c["field"] == "_dids" for c in s["columns"]) for s in spec["sections"]):
        dids_rule = ("\n- dids 只能引用【材料段落】中真实出现的 D 编号，引用不存在的编号视为无效；")
    system_prompt = (
        f"你是数据分析提炼助手。\n用户需求：{requirement}\n"
        f"依据【材料段落】完成提炼，输出 JSON：\n{json_desc}\n"
        f"规则：\n- 只依据材料内容提炼，材料中没有的信息不要编造；"
        f"{quote_rule}{dids_rule}\n"
        f"- 材料与需求无关时全部输出空列表，summary 说明原因。")
    task = "【任务】请按上述结构对材料做提炼，按 JSON 结构输出。"
    scene = {"id": "custom", "name": spec.get("scene_name", "自定义提炼"),
             "prompt_version": "custom-runtime",
             "report_title": f'{spec.get("scene_name", "自定义提炼")}报告',
             "meta_headers": ["项目", "运行 ID", "材料与版本", "生成时间"],
             "meta_col_widths": [2.6, 3.8, 5.0, 2.8],
             "system_prompt": system_prompt, "task_instruction": task,
             "summary_title": spec.get("summary_title", "一、提炼摘要"),
             "sections": spec["sections"],
             "citation_note": "说明：本节结构化引用仅覆盖能够可靠定位来源的结论。",
             "footer_note": "— 本报告由 AI 客户材料分析工作台基于指定历史运行导出，引用均为脱敏后展示文本 —"}
    if spec.get("actions_section"):
        scene["actions_section"] = spec["actions_section"]
    return scene


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
