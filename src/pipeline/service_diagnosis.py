"""场景层：学员服务诊断（Task 7，曦拍板方案 A——独立替换分析节点）。

设计原则（「同一底座、可插拔场景层」）：
- 复用底座：清洗器 v3 → 段落供给 → Citation 结构 → 脱敏转换 → Word 导出
- 场景隔离：独立 Prompt（SD_SYSTEM）、独立输出结构、独立报告模板；
  不触碰售前需求分析的 Prompt / 检索 / EVD-01 / 评测器
- 引用纪律与售前一致：dids 只引用真实段落编号，证据逐段取自 clean.json
  脱敏文本；行动清单是服务方建议（非证据陈述），不生成引用，不伪装

输出结构（四段 + 摘要）：
  summary         一句话定性（现状 + 风险等级）
  core_demands    学员核心诉求（带原话锚点）
  emotion_risks   情绪与风险信号
  response_issues 服务方响应问题
  action_items    行动清单（具体可执行，含建议时限）
"""
import json

import storage  # noqa: E402

SD_PROMPT_VERSION = "v1.0-sd"

SD_SYSTEM = """你是学员服务诊断助手。依据【材料段落】中服务方（班主任/老师）与学员的对话，
做一次服务诊断，输出 JSON：
{
  "summary": "一句话定性：学员现状 + 风险等级",
  "core_demands": [
    {"id": "SD1", "content": "诉求描述", "quote": "学员原话", "dids": ["D003"], "priority": "高/中/低"}
  ],
  "emotion_risks": [
    {"type": "情绪或风险描述", "evidence_quote": "学员原话", "dids": ["D030"], "level": "高/中/低"}
  ],
  "response_issues": [
    {"issue": "服务方响应中存在的问题", "dids": ["D010"]}
  ],
  "action_items": [
    {"action": "行动名称", "detail": "具体做法与时限", "priority": "高/中/低"}
  ]
}
规则：
1. dids 只能引用【材料段落】中真实出现的 D 编号；每条诉求必须带学员原话（quote）；
2. 情绪与风险信号要引用触发它的原话；风险等级按流失可能性判断；
3. response_issues 引用服务方的回应原文作为依据；
4. action_items 是给服务方的具体行动建议（给谁做什么、时限多少），必须可执行、不含空话；
   行动是建议而非证据，action_items 不需要 dids；
5. 材料与学员服务无关时全部输出空列表，summary 说明原因。"""


def analyze_service_diagnosis(material_block: str, mock: bool = False) -> dict:
    """服务诊断分析：输入脱敏材料块，输出结构化诊断 dict。"""
    if mock:
        return _mock_diagnosis(material_block)
    from pipeline import llm_client
    messages = [{"role": "system", "content": SD_SYSTEM},
                {"role": "user",
                 "content": f"【材料段落】\n{material_block}\n\n"
                            "【任务】请对这份对话做学员服务诊断，按 JSON 结构输出。"}]
    text = llm_client.chat(messages, json_mode=True, node="SD")
    return json.loads(text)


def _mock_diagnosis(material_block: str) -> dict:
    """离线 mock：简单规则拼接（无 Key 演示与测试用，不保证语义质量）。"""
    lines = [l for l in material_block.splitlines() if l.strip()]
    dids = [l.split(" ")[0] for l in lines if l.strip()][:3]
    samples = "；".join(l[:40] for l in lines[:3])
    return {
        "summary": "（mock）学员在学习过程中遇到执行困难，情绪出现烦躁信号，需要服务方主动跟进。",
        "core_demands": [{"id": "SD1", "content": "（mock）解决材料中反映的核心困难",
                          "quote": samples[:50], "dids": dids[:1], "priority": "高"}],
        "emotion_risks": [{"type": "（mock）情绪烦躁", "evidence_quote": samples[-50:],
                           "dids": dids[-1:], "level": "中"}],
        "response_issues": [{"issue": "（mock）服务方响应及时性有待确认", "dids": dids[:1]}],
        "action_items": [{"action": "（mock）安排跟进", "detail": "48 小时内一对一沟通",
                          "priority": "高"}],
    }


def build_citations_service(sd: dict, project_id: str, version_refs: list) -> dict:
    """服务诊断引用生成：复用 Citation 结构（池逻辑同 storage.build_citations），
    覆盖 core_demands / emotion_risks / response_issues 三类带 dids 的结论；
    action_items 是服务方建议，不生成引用（避免伪装成证据）。"""
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
    spec = [("core_demand", "core_demands", "支撑学员诉求识别"),
            ("emotion_risk", "emotion_risks", "支撑情绪与风险判断"),
            ("response_issue", "response_issues", "支撑服务方响应问题分析")]
    for ctype, key, reason in spec:
        for item in (sd.get(key) or []):
            dids = item.get("dids") or []
            invalid = [d for d in dids if d not in pool]
            if not dids or invalid:
                unresolved.append({
                    "conclusion_type": ctype, "conclusion_id": item.get("id") or item.get("type"),
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
                    "conclusion_type": ctype, "conclusion_id": item.get("id") or item.get("type"),
                    "reason": "部分来源段落无法定位原文，拒绝生成推测性引用"})
                continue
            citations.append({
                "conclusion_type": ctype,
                "conclusion_id": item.get("id") or item.get("type"),
                "document_id": excerpts[0]["document_id"] if excerpts else None,
                "version_id": excerpts[0]["version_id"] if excerpts else None,
                "clean_paragraph_ids": dids,
                "source_paragraph_ids": [e["source_paragraph_id"] for e in excerpts],
                "source_excerpts": excerpts,
                "citation_reason": reason,
                "quote_model": item.get("quote") or item.get("evidence_quote"),
            })
    return {"project_id": project_id, "version_refs": version_refs,
            "citations": citations, "unresolved": unresolved}


def build_service_report(sd: dict, project_id: str, run_id: str) -> str:
    """服务诊断 Markdown 报告。"""
    lines = [f"# 学员服务诊断报告 · {project_id}", "",
             f"- 诊断运行：{run_id}", f"- 摘要：{sd.get('summary', '')}", "",
             "## 一、学员核心诉求", ""]
    for d in (sd.get("core_demands") or []):
        lines.append(f"### {d.get('id')}（{d.get('priority', '')}）")
        lines.append(f"- 诉求：{d.get('content', '')}")
        lines.append(f"- 原话锚点：「{d.get('quote', '')}」（{('、'.join(d.get('dids', []))) or '无引用'}）")
        lines.append("")
    lines += ["## 二、情绪与风险信号", ""]
    for r in (sd.get("emotion_risks") or []):
        lines.append(f"- **{r.get('type', '')}**（等级：{r.get('level', '')}）"
                     f"——「{r.get('evidence_quote', '')}」（{('、'.join(r.get('dids', []))) or '无引用'}）")
    lines += ["", "## 三、服务方响应问题", ""]
    for i in (sd.get("response_issues") or []):
        lines.append(f"- {i.get('issue', '')}（{('、'.join(i.get('dids', []))) or '无引用'}）")
    lines += ["", "## 四、行动清单", ""]
    for a in (sd.get("action_items") or []):
        lines.append(f"- **{a.get('action', '')}**（{a.get('priority', '')}）：{a.get('detail', '')}")
    lines += ["", "## 五、引用与未解析说明", "",
              "本报告引用均为脱敏后展示文本；行动清单为服务方建议，不作为证据引用。",
              "无法可靠定位来源的结论在引用元数据中显式标记，不生成虚假证据。"]
    return "\n".join(lines)
