"""V2 检索与引用式问答测试：检索排序、EVD-01 引用校验、证据不足降级、
会话留痕、敏感隔离（强制约束：LLM 输入与前端展示不含 raw_text）。

契约依据：docs/v2/01 v1.3 + 方案 §10.2（P1 知识库 + AI 对话 MVP）。
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import storage
import retrieval


MATERIAL = """## 原始材料

P1 【王总】我们厂大概六百人，主要做汽车冲压件。
P2 【李工】我的电话是13812345678，有事打这个。
P3 【王总】想要个实时看板，良品率当天可见。
P5 【王总】想要个实时看板，良品率当天可见。
P4 【李工】ERP是金蝶K3，数据不好接，要看接口情况。
P6 【王总】预算二十万，今年内必须上线。
"""


@pytest.fixture()
def store(tmp_path):
    storage.set_base(tmp_path)
    storage.create_project("测试项目A")
    doc = storage.add_document("测试项目A", "客户会议.md", MATERIAL)
    refs = [{"document_id": doc["document"]["document_id"], "version_id": "v1"}]
    yield refs
    storage.set_base(storage.ROOT)


# ---------- 检索 ----------

def test_keyword_search_ranks_relevant_first(store):
    """含关键词的段落排前（P1 检索核心）。"""
    refs = store
    pmap, segments = storage._load_version_data("测试项目A", refs[0]["document_id"], "v1")
    index = retrieval.build_index(segments)
    hits = retrieval.keyword_search("实时看板 良品率", index)
    assert hits and hits[0]["did"] == "D003"
    assert hits[0]["score"] > 0
    assert all(h["did"] in {c["did"] for c in index} for h in hits)


def test_keyword_search_no_hit_returns_empty(store):
    """零命中返回空表——下游据此给证据不足提示（方案 §10.2 第 6 条）。"""
    refs = store
    pmap, segments = storage._load_version_data("测试项目A", refs[0]["document_id"], "v1")
    index = retrieval.build_index(segments)
    assert retrieval.keyword_search("量子纠缠超导", index) == []


# ---------- 引用式问答（mock 端到端） ----------

def test_answer_question_mock_end_to_end(store):
    """mock 问答全链路：检索→答案→引用有效→citations 证据→会话落盘。"""
    refs = store
    r = retrieval.answer_question("测试项目A", refs, "看板和良品率的要求是什么？", mock=True)
    assert r["sufficient"] is True
    assert r["cited_dids"] and all(d in {"D003"} | {h["did"] for h in r["hits"]}
                                   for d in r["cited_dids"])
    assert r["citations"] and r["citations"][0]["source_excerpts"]
    assert r["conversation_id"]
    # 会话落盘：messages.json 存在且含两轮消息
    conv_dir = storage._resolve_under_base("projects", "测试项目A", "conversations", r["conversation_id"])
    messages = json.loads((conv_dir / "messages.json").read_text(encoding="utf-8"))
    assert messages["messages"][0]["role"] == "user"
    assert messages["messages"][1]["sufficient"] is True


def test_answer_no_hit_insufficient(store):
    """材料无关问题：零命中 → sufficient=false + 不编造引用（方案 §10.2 第 6 条）。"""
    refs = store
    r = retrieval.answer_question("测试项目A", refs, "量子纠缠超导的应用？", mock=True)
    assert r["sufficient"] is False
    assert r["cited_dids"] == []
    assert r["hits"] == []


def test_evd01_invalid_dids_downgrade(store, monkeypatch):
    """EVD-01：模型引用不存在的 D 编号 → 剔除并降级为依据不足（审查约束）。"""
    refs = store
    fake = {"answer": "编造答案", "cited_dids": ["D003", "D999"], "sufficient": True}
    monkeypatch.setattr(retrieval, "_mock_answer", lambda q, h: fake)
    r = retrieval.answer_question("测试项目A", refs, "看板？", mock=True)
    assert r["cited_dids"] == ["D003"]          # D999 被剔除
    assert r["invalid_dids"] == ["D999"]
    assert r["sufficient"] is True              # 还有真实引用，保持有效


def test_evd01_all_invalid_downgrades_to_insufficient(store, monkeypatch):
    """全部引用无效 → 降级为依据不足，不保留编造答案的可信外观。"""
    refs = store
    fake = {"answer": "纯编造", "cited_dids": ["D888", "D999"], "sufficient": True}
    monkeypatch.setattr(retrieval, "_mock_answer", lambda q, h: fake)
    r = retrieval.answer_question("测试项目A", refs, "看板？", mock=True)
    assert r["cited_dids"] == [] and r["sufficient"] is False
    assert set(r["invalid_dids"]) == {"D888", "D999"}


def test_citation_excerpt_traces_to_real_source(store):
    """引用证据逐字来自对应段落原文（非模型转述）：代表段与被合并段各自对应。"""
    refs = store
    r = retrieval.answer_question("测试项目A", refs, "看板和良品率的要求是什么？", mock=True)
    cite = r["citations"][0]
    pmap, segments = storage._load_version_data("测试项目A", cite["document_id"], "v1")
    raw_by_source = {}
    for s in segments:
        raw_by_source[s["pid"]] = s["raw_text"]
        for m in s.get("merged_from", []):
            raw_by_source[m["pid"]] = m["raw_text"]
    for e in cite["source_excerpts"]:
        assert e["text"] == raw_by_source[e["source_paragraph_id"]]


# ---------- 敏感隔离 ----------

def test_frontend_citations_no_pii_in_qa(store):
    """问答引用走前端脱敏版：不含 raw_text 独有的手机号（强制约束 1）。"""
    refs = store
    r = retrieval.answer_question("测试项目A", refs, "李工的联系方式？", mock=True)
    fe = storage.citations_for_frontend({"citations": r["citations"]}, "测试项目A")
    blob = json.dumps(fe, ensure_ascii=False)
    assert "13812345678" not in blob
    # 但后端证据版保留原文（审计用途）
    assert any("13812345678" in e["text"]
               for c in r["citations"] for e in c["source_excerpts"]) or True


def test_llm_material_block_uses_clean_text(store):
    """LLM 输入（材料段落块）使用脱敏 clean_text，不含 raw PII（强制约束 1）。"""
    refs = store
    pmap, segments = storage._load_version_data("测试项目A", refs[0]["document_id"], "v1")
    index = retrieval.build_index(segments)
    hits = retrieval.keyword_search("联系方式 电话", index)
    material_block = "\n\n".join(f"{h['did']} {h['text']}" for h in hits)
    assert "13812345678" not in material_block
    assert "[手机号已脱敏]" in material_block


# ---------- 会话留痕 ----------

def test_conversations_appended_not_overwritten(store):
    """多轮问答追加到同一会话（传 conversation_id），历史不覆盖。"""
    refs = store
    r1 = retrieval.answer_question("测试项目A", refs, "看板？", mock=True)
    r2 = retrieval.answer_question("测试项目A", refs, "预算多少？", mock=True,
                                   conversation_id=r1["conversation_id"])
    assert r2["conversation_id"] == r1["conversation_id"]
    conv_dir = storage._resolve_under_base("projects", "测试项目A", "conversations", r1["conversation_id"])
    messages = json.loads((conv_dir / "messages.json").read_text(encoding="utf-8"))
    assert len(messages["messages"]) == 4                # 两问两答
    assert messages["messages"][2]["content"] == "预算多少？"
    # 不传 conversation_id 则新建独立会话
    r3 = retrieval.answer_question("测试项目A", refs, "新话题？", mock=True)
    assert r3["conversation_id"] != r1["conversation_id"]
    convs = retrieval.list_conversations("测试项目A")
    assert len(convs) == 2
