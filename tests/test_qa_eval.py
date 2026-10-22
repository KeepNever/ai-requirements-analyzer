"""Task 5 问答评测器自身测试：指标计算、快照不覆盖、LOCKED 逐题隔离、
eval_trace 隐私边界（四审 §9.4）。

评测契约依据：docs/v2/04 §9（四审补充契约）。
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import storage
import retrieval
import qa_eval

MATERIAL = """## 原始材料

P1 【王总】我们厂大概六百人，主要做汽车冲压件。
P2 【李工】我的电话是13812345678，有事打这个。
P3 【王总】想要个实时看板，良品率当天可见。
P4 【李工】ERP是金蝶K3，数据不好接。
"""


@pytest.fixture()
def store(tmp_path):
    storage.set_base(tmp_path)
    storage.create_project("评测项目")
    doc = storage.add_document("评测项目", "客户会议.md", MATERIAL)
    refs = [{"document_id": doc["document"]["document_id"], "version_id": "v1"}]
    yield refs


def _fake_llm(answer, cited, sufficient=True):
    def fake_chat(messages, **kw):
        return json.dumps({"answer": answer, "cited_dids": cited, "sufficient": sufficient})
    return fake_chat


# ---------- 指标计算 ----------

def test_recall_and_refuse_accuracy(store, tmp_path, monkeypatch):
    """召回率 = 命中预期证据/全部预期证据；拒答判断按三态口径。"""
    refs = store
    dataset = [{
        "question_id": "DEV-T-001", "category": "single_fact",
        "project_id": "评测项目", "version_refs": refs,
        "question": "看板？",
        "expected_answer_points": ["当天可见"],
        "expected_citations": [{"document_id": refs[0]["document_id"], "version_id": "v1",
                                "source_paragraph_ids": ["P3"], "clean_paragraph_ids": ["D003"]}],
        "evidence_sufficiency": "answerable", "expected_behavior": "answer", "notes": "",
    }, {
        "question_id": "DEV-T-002", "category": "insufficient",
        "project_id": "评测项目", "version_refs": refs,
        "question": "上一期方案？",
        "expected_answer_points": [], "expected_citations": [],
        "evidence_sufficiency": "insufficient", "expected_behavior": "refuse", "notes": "",
    }]
    ds_path = tmp_path / "ds.jsonl"
    ds_path.write_text("\n".join(json.dumps(d, ensure_ascii=False) for d in dataset), encoding="utf-8")

    def fake_chat_by_question(messages, **kw):
        q = messages[-1]["content"]
        if "上一期" in q:
            return json.dumps({"answer": "材料未提上一期方案，无法回答。", "cited_dids": [], "sufficient": False})
        return json.dumps({"answer": "当天可见（D003）。", "cited_dids": ["D003"], "sufficient": True})
    monkeypatch.setattr("pipeline.llm_client.chat", fake_chat_by_question)
    out = qa_eval.run_dataset(str(ds_path), mock=False)
    s = out["summary"]
    assert s["refuse_judgement_accuracy"] == 1.0        # answerable→true, insufficient→false 全对
    assert s["retrieval_recall_mean"] == 1.0            # D003 命中
    assert s["all_evidence_hit_rate"] == 1.0


def test_refuse_mismatch_counts_against(store, tmp_path, monkeypatch):
    """answerable 题模型拒答 → 拒答判断失分（三态口径 §9.3）。"""
    refs = store
    dataset = [{
        "question_id": "DEV-T-003", "category": "single_fact",
        "project_id": "评测项目", "version_refs": refs,
        "question": "看板？",
        "expected_answer_points": ["当天可见"],
        "expected_citations": [{"document_id": refs[0]["document_id"], "version_id": "v1",
                                "source_paragraph_ids": ["P3"], "clean_paragraph_ids": ["D003"]}],
        "evidence_sufficiency": "answerable", "expected_behavior": "answer", "notes": "",
    }]
    ds_path = tmp_path / "ds2.jsonl"
    ds_path.write_text(json.dumps(dataset[0], ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr("pipeline.llm_client.chat",
                        _fake_llm("材料不足以回答。", [], sufficient=False))
    out = qa_eval.run_dataset(str(ds_path), mock=False)
    assert out["summary"]["refuse_judgement_accuracy"] == 0.0


def test_p_to_d_conversion_via_paragraph_map(store, tmp_path, monkeypatch):
    """四审 §9.1：GT 只给 P 编号时经 paragraph_map 换算参与召回（含合并段）。"""
    refs = store
    # P5 不存在于段落集；构造合并场景：在另一文档里 P 与 D 的映射
    dataset = [{
        "question_id": "DEV-T-004", "category": "single_fact",
        "project_id": "评测项目", "version_refs": refs,
        "question": "看板？",
        "expected_answer_points": [],
        "expected_citations": [{"document_id": refs[0]["document_id"], "version_id": "v1",
                                "source_paragraph_ids": ["P3"], "clean_paragraph_ids": []}],
        "evidence_sufficiency": "answerable", "expected_behavior": "answer", "notes": "",
    }]
    ds_path = tmp_path / "ds3.jsonl"
    ds_path.write_text(json.dumps(dataset[0], ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr("pipeline.llm_client.chat", _fake_llm("看板当天可见（D003）。", ["D003"]))
    out = qa_eval.run_dataset(str(ds_path), mock=False)
    assert out["summary"]["retrieval_recall_mean"] == 1.0   # P3 经 map 换算为 D003 → 命中


# ---------- 快照与 LOCKED 隔离 ----------

def _make_dataset_file(tmp_path, split, items):
    p = tmp_path / f"qa_eval_v0.1_{split.lower()}.jsonl"
    p.write_text("\n".join(json.dumps(i, ensure_ascii=False) for i in items), encoding="utf-8")
    return str(p)


def test_locked_summary_only_and_detail_separated(store, tmp_path, monkeypatch):
    """四审 §9.2：LOCKED 只出汇总；逐题明细物理分离；报告不含逐题。"""
    refs = store
    items = [{
        "question_id": "LOCKED-SING-001", "category": "single_fact",
        "project_id": "评测项目", "version_refs": refs,
        "question": "看板？",
        "expected_answer_points": ["当天可见"],
        "expected_citations": [{"document_id": refs[0]["document_id"], "version_id": "v1",
                                "source_paragraph_ids": ["P3"], "clean_paragraph_ids": ["D003"]}],
        "evidence_sufficiency": "answerable", "expected_behavior": "answer", "notes": "",
    }]
    ds = _make_dataset_file(tmp_path, "LOCKED", items)
    monkeypatch.setattr("pipeline.llm_client.chat", _fake_llm("当天可见（D003）。", ["D003"]))
    out = qa_eval.run_dataset(ds, mock=False)
    run_dir = Path(out["run_dir"])
    assert (run_dir / "summary.json").exists()
    assert not (run_dir / "results_dev.jsonl").exists()          # LOCKED 无逐题主文件
    detail_files = list((run_dir / "locked_detail").glob("*.json"))
    assert len(detail_files) == 1                                # 逐题明细独立目录
    report = (run_dir / "report.md").read_text(encoding="utf-8")
    assert "LOCKED-SING-001" not in report                       # 报告不含逐题 ID


def test_rerun_new_snapshot_no_overwrite(store, tmp_path, monkeypatch):
    """重复评测生成全新快照目录（追加不覆盖，契约 §9.4）。"""
    refs = store
    ds = _make_dataset_file(tmp_path, "DEV", [{
        "question_id": "DEV-SING-001", "category": "single_fact",
        "project_id": "评测项目", "version_refs": refs,
        "question": "看板？", "expected_answer_points": [],
        "expected_citations": [], "evidence_sufficiency": "insufficient",
        "expected_behavior": "refuse", "notes": ""}])
    out1 = qa_eval.run_dataset(ds, mock=True)
    out2 = qa_eval.run_dataset(ds, mock=True)
    assert out1["run_dir"] != out2["run_dir"]
    assert Path(out1["run_dir"], "summary.json").exists()        # 第一次快照未被覆盖


# ---------- eval_trace 隐私边界 ----------

def test_eval_trace_off_no_extra_collection(store, monkeypatch):
    """四审 §9.4：eval_trace=False 时会话记录无 trace 字段、无 trace 文件产生。"""
    refs = store
    r = retrieval.answer_question("评测项目", refs, "看板？", mock=True)   # 默认 eval_trace=False
    assert "trace" not in r
    conv_dir = storage._resolve_under_base(
        "projects", "评测项目", "conversations", r["conversation_id"])
    blob = json.dumps(json.loads((conv_dir / "messages.json").read_text(encoding="utf-8")))
    assert "trace" not in blob and "llm_messages" not in blob


def test_eval_trace_on_isolated_from_conversation(store, tmp_path, monkeypatch):
    """eval_trace=True：trace 独立落盘于 trace_dir；会话记录不含 trace 内容（隔离）。"""
    refs = store
    tdir = tmp_path / "qa_traces"
    r = retrieval.answer_question("评测项目", refs, "看板？", mock=True,
                                  eval_trace=True, trace_dir=str(tdir), trace_tag="DEV-T-001")
    assert "trace" in r
    traces = list(tdir.glob("*.json"))
    assert len(traces) == 1
    t = json.loads(traces[0].read_text(encoding="utf-8"))
    assert t["tag"] == "DEV-T-001" and t["mock"] is True
    # 隔离：会话记录（conversations/）不含 trace 内容
    conv_dir = storage._resolve_under_base(
        "projects", "评测项目", "conversations", r["conversation_id"])
    conv_blob = (conv_dir / "messages.json").read_text(encoding="utf-8")
    assert "llm_messages" not in conv_blob and "elapsed" not in conv_blob
    # trace 文件不含 API Key 类凭证
    assert "DEEPSEEK_API_KEY" not in traces[0].read_text(encoding="utf-8")


def test_each_question_independent_conversation(store):
    """每题独立会话：上一轮对话历史不污染下一题（四审 §9.4 会话隔离）。"""
    refs = store
    r1 = retrieval.answer_question("评测项目", refs, "看板？", mock=True)
    r2 = retrieval.answer_question("评测项目", refs, "库存？", mock=True)   # 不传 conversation_id
    assert r1["conversation_id"] != r2["conversation_id"]
