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


# 停用词（问句功能词，不参与命中——避免「什么/怎么」稀释有效关键词）
STOPWORDS = {"什么", "怎么", "怎样", "哪些", "一下", "这个", "那个", "如何",
             "还是", "以及", "进行", "没有", "是否", "你们", "我们", "他们",
             "对方", "大家", "就是", "是不是", "有没有", "为什么", "多少"}

# 零命中全量回落阈值：段落级材料规模小（≤40 段）时整份给模型自行定位，
# 大材料零命中才真正判证据不足
FULL_FALLBACK_MAX_SEGMENTS = 40


def keyword_search(query: str, index: list, top_k: int = 6) -> list:
    """关键词检索：query 分词（过滤停用词）命中 chunk 计分，归一化后取 top_k。"""
    q = _tokens(query) - STOPWORDS
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


def _load_history(project_id: str, conversation_id: str, max_turns: int = 5) -> list:
    """读取会话历史（最近 max_turns 轮），构造多轮 LLM messages（user/assistant 交替）。"""
    msg_file = storage._resolve_under_base(
        "projects", project_id, "conversations", conversation_id) / "messages.json"
    if not msg_file.exists():
        return []
    msgs = json.loads(msg_file.read_text(encoding="utf-8")).get("messages", [])
    # 取最近 max_turns 轮（一轮 = user + assistant）
    recent = msgs[-max_turns * 2:]
    history = []
    for m in recent:
        if m.get("role") == "user":
            history.append({"role": "user", "content": m["content"]})
        elif m.get("role") == "assistant" and m.get("answer"):
            history.append({"role": "assistant", "content": m["answer"]})
    return history


def _load_history_material(project_id: str, conversation_id: str) -> list:
    """零命中回落（审查约束：总结/追问类问题不依赖当轮检索）——
    从会话历史各轮 assistant 消息的 hits 收集此前用过的段落（clean_text）。
    返回 [{did, text}]，顺序 = 历史出现顺序，去重。"""
    msg_file = storage._resolve_under_base(
        "projects", project_id, "conversations", conversation_id) / "messages.json"
    if not msg_file.exists():
        return []
    msgs = json.loads(msg_file.read_text(encoding="utf-8")).get("messages", [])
    seen, out = set(), []
    for m in msgs:
        if m.get("role") != "assistant":
            continue
        for h in m.get("hits", []):
            if h.get("did") and h["did"] not in seen:
                seen.add(h["did"])
                seg = _segment_text(project_id, h["did"], m)
                if seg:
                    out.append({"did": h["did"], "text": seg})
    return out


def _segment_text(project_id: str, did: str, assistant_msg: dict) -> str | None:
    """按 assistant 消息的 version_refs 定位段落 clean_text（历史轮引用过的段落）。"""
    for ref in assistant_msg.get("version_refs", []) or []:
        try:
            pmap, segments = storage._load_version_data(
                project_id, ref["document_id"], ref["version_id"])
        except ValueError:
            continue
        for s in segments:
            if s["did"] == did:
                return s["clean_text"]
    return None


def answer_question(project_id: str, version_refs: list, question: str,
                    mock: bool = False, conversation_id: str | None = None,
                    eval_trace: bool = False, trace_dir: str | None = None,
                    trace_tag: str | None = None) -> dict:
    """引用式问答主流程：检索 → LLM（或 mock）→ EVD-01 校验 → 证据组装 → 会话落盘。

    version_refs: [{document_id, version_id}, ...]（完整组合定位）。
    conversation_id 传入则多轮对话：历史轮次作为上下文发给模型，新消息追加到同一会话。
    零命中回落：总结/追问类问题检索零命中时，材料块回落到本会话历史轮引用过的段落。
    eval_trace（评测专用，默认 False）：True 时收集评测 trace（LLM messages / 校验前
    raw / 检索候选全文 / 分段耗时 / 回落路径 / usage），写入 trace_dir 独立文件；
    生产会话记录（conversations/）不混入 trace 内容，行为零变化。
    返回 {answer, sufficient, cited_dids, invalid_dids, hits, citations,
    conversation_id, provided_dids}。
    citations 为后端证据版（含 raw excerpt）；前端一律过 citations_for_frontend。
    """
    import time as _time
    t_search0 = _time.time()
    # 1. 加载版本索引
    index, provided_dids = [], set()
    for ref in version_refs:
        pmap, segments = storage._load_version_data(
            project_id, ref["document_id"], ref["version_id"])
        idx = build_index(segments)
        index.extend(idx)
        provided_dids |= {c["did"] for c in idx}

    # 2. 检索；零命中时按序回落（v1.3 修正：小材料永远给全量——历史段落 ⊆ 全量，
    #    互斥回落会导致「历史只命中 1 段时其他历史编号全部被判无效」的假阳性）：
    #    ① 小材料（≤40 段）→ 全量段落给模型自行定位（词汇鸿沟兜底）
    #    ② 大材料 + 多轮会话 → 历史引用过的段落（省 token）
    #    ③ 大材料无会话 → 证据不足
    hits = keyword_search(question, index)
    elapsed_search = _time.time() - t_search0
    fallback_material, fallback_note = [], ""
    if not hits and len(index) <= FULL_FALLBACK_MAX_SEGMENTS:
        fallback_material = [{"did": c["did"], "text": c["text"]} for c in index]
        fallback_note = "（本次检索零命中，已提供全部材料段落，请自行定位与问题相关的内容）"
    elif not hits and conversation_id:
        fallback_material = _load_history_material(project_id, conversation_id)
        fallback_note = "（本次检索零命中，材料段落来自此前对话中引用过的内容）"

    # 3. 生成答案（真实 LLM 或 mock）；多轮时带历史上下文
    material_source = hits if hits else fallback_material
    t_llm0 = _time.time()
    if mock:
        raw = _mock_answer(question, hits)
        usage = None
    else:
        if hits:
            material_block = "\n\n".join(f"{h['did']} {h['text']}" for h in hits)
            material_note = ""
        elif fallback_material:
            material_block = "\n\n".join(f"{m['did']} {m['text']}" for m in fallback_material)
            material_note = fallback_note
        else:
            material_block = "（无命中段落，且无历史引用）"
            material_note = ""
        from pipeline import llm_client, config
        history = _load_history(project_id, conversation_id) if conversation_id else []
        # 历史轮只带问题与答案（不带旧材料块，控 token）；当前轮带检索材料
        messages = [{"role": "system", "content": QA_SYSTEM}] + history + [
            {"role": "user",
             "content": f"【材料段落】\n{material_block}\n{material_note}\n\n【问题】{question}\n\n"
                        "（注：若问题中的代词指代此前轮次的内容，请结合对话历史理解；"
                        "回答须以材料段落或对话历史中已出现的 D 编号为引用依据）"}]
        text = llm_client.chat(messages, json_mode=True, node="QA")
        usage = llm_client.usage_summary()
        raw = json.loads(text)

    # 4. EVD-01 引用真实性校验：可引用范围 = 当轮命中 + 历史回落段落（审查约束）
    provided_hit_dids = {h["did"] for h in hits} | {m["did"] for m in fallback_material}
    cited, invalid = _validate_cited_dids(raw.get("cited_dids", []), provided_hit_dids)
    sufficient = bool(raw.get("sufficient")) and bool(cited) if material_source else False
    if material_source and not cited:
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
              "provided_dids": sorted(provided_hit_dids),   # 实际提供给 LLM 的证据集（评测召回用）
              "citations": citations,
              "mock": mock,
              "prompt_version": "v1.7.1-qa-mock" if mock else "v1.7.1-qa"}

    # 5.5 评测 trace（eval_trace=True 时才收集；独立于生产会话记录，四审 §9.4）
    if eval_trace:
        elapsed_llm = _time.time() - t_llm0
        trace = {"tag": trace_tag, "question": question,
                 "mock": mock, "prompt_version": result["prompt_version"],
                 "hits_full": hits, "fallback_note": fallback_note,
                 "fallback_used": not hits and bool(fallback_material),
                 "elapsed": {"search": round(elapsed_search, 3),
                             "llm": round(elapsed_llm, 3)},
                 "usage": usage,
                 "cited": cited, "invalid": invalid}
        if not mock:
            trace["llm_messages"] = messages     # 含材料块与历史轮（不含凭证）
            trace["raw"] = raw                   # EVD-01 校验前的模型原始输出
        if trace_dir:
            tdir = Path(trace_dir)
            tdir.mkdir(parents=True, exist_ok=True)
            name = f"{trace_tag or 'trace'}_{_time.strftime('%H%M%S')}_{uuid.uuid4().hex[:4]}.json"
            storage._write_json(tdir / name, trace)
        result["trace"] = trace

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
        "hits": result["hits"], "version_refs": version_refs, "ts": storage.now_iso(),
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
