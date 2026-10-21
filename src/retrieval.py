"""V2 检索与引用式问答（方案 P1：知识库 + AI 对话 MVP）。

设计约束（00_方案 §10.2 + 三审实现约束）：
- 关键词检索：段落级 chunk，纯标准库 n-gram 命中计分，不取向量
- 引用式回答：LLM 只能引用材料段落中真实存在的 D 编号（EVD-01 校验），
  引用无效的答案降级为「依据不足」，不编造
- 证据不足提示：sufficient=false 时明确说明缺少什么
- 会话留痕：每次问答 append 进 conversations/<conv_id>/messages.json，
  记录问题、答案、引用、检索命中
- 敏感保护：LLM 输入与前端展示一律用脱敏 clean_text，raw_text 仅后端证据
"""
import json
import re
import time
import uuid
from pathlib import Path

from pipeline import cleaner
import storage


# ---------- 索引与检索 ----------

def build_index(segments: list) -> list:
    """clean.json segments → 检索 chunks（段落级，零额外切分）。"""
    return [{"did": s["did"], "pids": [s["pid"]] + [m["pid"] for m in s.get("merged_from", [])],
             "text": s["clean_text"], "raw_text": s["raw_text"]} for s in segments]


def _tokens(text: str) -> set:
    """查询/段落分词：CJK 连续串切 2-gram + 英数词（零依赖）。"""
    tokens = set()
    for word in re.findall(r"[A-Za-z0-9_]+", text):
        tokens.add(word.lower())
    for run in re.findall(r"[\u4e00-\u9fff]+", text):
        if len(run) == 1:
            tokens.add(run)
        else:
            for i in range(len(run) - 1):
                tokens.add(run[i:i + 2])
    return tokens


def keyword_search(query: str, index: list, top_k: int = 6) -> list:
    """关键词检索：query 分词命中 chunk 计分，归一化后取 top_k。零命中返回空表。"""
    q = _tokens(query)
    if not q:
        return []
    hits = []
    for chunk in index:
        c = _tokens(chunk["text"])
        overlap = q & c
        if not overlap:
            continue
        hits.append({"did": chunk["did"], "pids": chunk["pids"], "text": chunk["text"],
                     "score": round(len(overlap) / len(q), 3)})
    hits.sort(key=lambda h: -h["score"])
    return hits[:top_k]


# ---------- 引用式问答 ----------

QA_SYSTEM = """你是客户材料分析助手。只依据【材料段落】回答用户问题，输出 JSON：
{"answer": "答案文本，300 字以内", "cited_dids": ["D001", ...], "sufficient": true 或 false}
规则：
1. cited_dids 只能引用【材料段落】中真实出现的 D 编号，每条结论都要有编号支撑；
2. 材料不足以回答时 sufficient=false，answer 说明已有信息与还缺什么，不编造；
3. 材料与问题完全无关时 sufficient=false 且 cited_dids 为空。"""


def _validate_cited_dids(cited: list, provided_dids: set) -> tuple[list, list]:
    """EVD-01 引用真实性：剔除不存在于检索提供范围的编号。返回 (有效, 无效)。"""
    valid = [d for d in cited if d in provided_dids]
    invalid = [d for d in cited if d not in provided_dids]
    return valid, invalid


def _mock_answer(question: str, hits: list) -> dict:
    """离线问答：基于检索命中的段落拼接引用型答案（测试与无 Key 演示用）。"""
    if not hits:
        return {"answer": "（mock）检索未命中任何材料段落，无法回答。", "cited_dids": [], "sufficient": False}
    dids = [h["did"] for h in hits[:3]]
    summary = "；".join(f"{h['did']}：{h['text'][:50]}" for h in hits[:3])
    return {"answer": f"（mock）依据检索命中的材料段落，与「{question}」相关的信息如下——{summary}。",
            "cited_dids": dids, "sufficient": True}


def answer_question(project_id: str, version_refs: list, question: str,
                    mock: bool = False, conversation_id: str | None = None) -> dict:
    """引用式问答主流程：检索 → LLM（或 mock）→ EVD-01 校验 → 证据组装 → 会话落盘。

    version_refs: [{document_id, version_id}, ...]（完整组合定位）。
    conversation_id 传入则多轮追加到同一会话，缺省新建。
    返回 {answer, sufficient, cited_dids, invalid_dids, hits, citations, conversation_id}。
    citations 为后端证据版（含 raw excerpt）；前端一律过 citations_for_frontend。
    """
    # 1. 加载版本索引
    index, provided_dids = [], set()
    for ref in version_refs:
        pmap, segments = storage._load_version_data(
            project_id, ref["document_id"], ref["version_id"])
        idx = build_index(segments)
        index.extend(idx)
        provided_dids |= {c["did"] for c in idx}

    # 2. 检索
    hits = keyword_search(question, index)
    provided_hit_dids = {h["did"] for h in hits}

    # 3. 生成答案（真实 LLM 或 mock）
    if mock:
        raw = _mock_answer(question, hits)
        usage = None
    else:
        material_block = "\n\n".join(f"{h['did']} {h['text']}" for h in hits) or "（无命中段落）"
        from pipeline import llm_client, config
        messages = [{"role": "system", "content": QA_SYSTEM},
                    {"role": "user", "content": f"【材料段落】\n{material_block}\n\n【问题】{question}"}]
        text = llm_client.chat(messages, json_mode=True, node="QA")
        usage = llm_client.usage_summary()
        raw = json.loads(text)

    # 4. EVD-01 引用真实性校验（审查约束：引用必须真实存在于提供段落）
    cited, invalid = _validate_cited_dids(raw.get("cited_dids", []), provided_hit_dids)
    sufficient = bool(raw.get("sufficient")) and bool(cited) if hits else False
    if hits and not cited:
        sufficient = False   # 声称有依据但引用全无效 → 降级为依据不足

    # 5. 证据组装（逐段原文，复用 storage 的 excerpt 逻辑）
    citations = []
    for ref in version_refs:
        pmap, segments = storage._load_version_data(
            project_id, ref["document_id"], ref["version_id"])
        seg_by_did = {s["did"]: s for s in segments}
        did2pair = {p["clean_paragraph_id"]: p for p in pmap["pairs"]}
        excerpts = []
        for d in cited:
            if d not in did2pair or d not in seg_by_did:
                continue
            excerpts.extend(storage._build_excerpts(did2pair[d], seg_by_did[d]))
        if excerpts:
            citations.append({"document_id": ref["document_id"], "version_id": ref["version_id"],
                              "clean_paragraph_ids": [d for d in cited
                                                      if d in did2pair and d in seg_by_did],
                              "source_excerpts": excerpts,
                              "citation_reason": "支撑问答结论"})
    result = {"project_id": project_id, "question": question,
              "answer": raw.get("answer", ""), "sufficient": sufficient,
              "cited_dids": cited, "invalid_dids": invalid,
              "hits": [{"did": h["did"], "score": h["score"]} for h in hits],
              "citations": citations,
              "mock": mock,
              "prompt_version": "v1.7.1-qa-mock" if mock else "v1.7.1-qa"}

    # 6. 会话落盘（append 不覆盖；传 conversation_id 则多轮追加同一会话）
    conv_id = conversation_id or f"conv{time.strftime('%Y%m%d')}_{uuid.uuid4().hex[:6]}"
    result["conversation_id"] = conv_id
    conv_dir = storage._resolve_under_base("projects", project_id, "conversations", conv_id)
    msg_file = conv_dir / "messages.json"
    messages = json.loads(msg_file.read_text(encoding="utf-8")) if msg_file.exists() else {
        "conversation_id": conv_id, "project_id": project_id,
        "version_refs": version_refs, "messages": []}
    messages["messages"].append({
        "role": "user", "content": question, "ts": storage.now_iso()})
    messages["messages"].append({
        "role": "assistant", "answer": result["answer"], "sufficient": sufficient,
        "cited_dids": cited, "invalid_dids": invalid,
        "hits": result["hits"], "ts": storage.now_iso(),
        "prompt_version": result["prompt_version"]})
    storage._write_json(msg_file, messages)
    if not (conv_dir / "meta.json").exists():
        storage._write_json(conv_dir / "meta.json", {
            "conversation_id": conv_id, "project_id": project_id,
            "version_refs": version_refs, "created_at": storage.now_iso()})
    return result


def list_conversations(project_id: str) -> list:
    conv_root = storage._resolve_under_base("projects", project_id, "conversations")
    out = []
    if conv_root.exists():
        for cdir in sorted(conv_root.iterdir()):
            mf = cdir / "meta.json"
            if mf.exists():
                out.append(storage._read_json(mf))
    return list(reversed(out))
