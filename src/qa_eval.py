"""Task 5 问答评测运行器（方案 §8 / §17.2，docs/v2/04 四审契约）。

旁路设计：只调用 retrieval.answer_question 现有签名（+eval_trace 评测参数），
不 fork 问答逻辑、不改生产 Prompt / metrics / EVD-01。

用法：
  python src/qa_eval.py --dataset test_data/qa_eval/qa_eval_v0.1_dev.jsonl --mock
  python src/qa_eval.py --dataset test_data/qa_eval/qa_eval_v0.1_dev.jsonl            # 真实
  python src/qa_eval.py --dataset test_data/qa_eval/qa_eval_v0.1_locked.jsonl         # LOCKED

纪律（docs/v2/04 §9.2）：
  - LOCKED 集只输出汇总指标（summary_locked.json）；逐题明细写 locked_detail/ 子目录，
    基线报告阶段不读取。需要查看逐题定位错误时，即将其身份转为固定回归集。
  - 每题独立会话；多轮题（turns 数组）题内共享会话；重复运行生成全新快照目录。
"""
import argparse
import json
import time
import uuid
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))

import storage
import retrieval

EVAL_ROOT = Path(__file__).resolve().parents[1] / "output" / "evaluation" / "qa_runs"


def load_dataset(path: str) -> list:
    items = [json.loads(l) for l in Path(path).read_text(encoding="utf-8").splitlines() if l.strip()]
    for it in items:
        for e in it.get("expected_citations", []):
            _normalize_expected_evidence(it, e)
    return items


def _normalize_expected_evidence(item: dict, e: dict):
    """四审 §9.1：P 编号经 paragraph_map 换算为 D 集合，与既有 D 并集（去重带身份）。"""
    pid, vid, did = e.get("document_id"), e.get("version_id"), e.get("document_id")
    try:
        pmap, _ = storage._load_version_data(item["project_id"], e["document_id"], e["version_id"])
    except (ValueError, FileNotFoundError):
        return
    d_by_p = {}
    for pair in pmap["pairs"]:
        for sp in pair["source_paragraph_ids"]:
            d_by_p[sp] = pair["clean_paragraph_id"]
    converted = {d_by_p.get(p) for p in e.get("source_paragraph_ids", []) if p in d_by_p}
    converted |= set(e.get("clean_paragraph_ids", []))
    e["clean_paragraph_ids"] = sorted(x for x in converted if x)


def run_dataset(dataset_path: str, mock: bool = False) -> dict:
    items = load_dataset(dataset_path)
    run_ts = time.strftime("%Y%m%d_%H%M%S")
    split = "LOCKED" if "locked" in Path(dataset_path).name.lower() else "DEV"
    import uuid as _uuid
    run_dir = EVAL_ROOT / f"qa_eval_{run_ts}_{split.lower()}_{_uuid.uuid4().hex[:4]}"
    traces_dir = run_dir / "traces"
    locked_detail = run_dir / "locked_detail"

    results = []
    for item in items:
        conv_id = None
        turns = item.get("turns") or [{"question": item["question"],
                                       "expected_citations": item["expected_citations"],
                                       "expected_answer_points": item.get("expected_answer_points", [])}]
        turn_results = []
        for ti, turn in enumerate(turns):
            r = retrieval.answer_question(
                item["project_id"], item["version_refs"], turn["question"],
                mock=mock, conversation_id=conv_id,
                eval_trace=True, trace_dir=str(traces_dir),
                trace_tag=item["question_id"] + (f"_t{ti+1}" if len(turns) > 1 else ""))
            conv_id = r["conversation_id"]
            turn_results.append({"turn": ti + 1, "question": turn["question"],
                                 "answer": r["answer"], "sufficient": r["sufficient"],
                                 "cited_dids": r["cited_dids"], "invalid_dids": r["invalid_dids"],
                                 "provided_dids": r["provided_dids"],
                                 "hits": r["hits"]})
        # 最后轮的引用与证据作为该题评测对象（多轮题看最终承接）
        last = turn_results[-1]
        expected_dids = set()
        last_turn = turns[-1]
        for e in last_turn.get("expected_citations", []):
            expected_dids |= set(e.get("clean_paragraph_ids", []))
        hit_expected = expected_dids & set(last["provided_dids"]) if expected_dids else set()
        recall = round(len(hit_expected) / len(expected_dids), 3) if expected_dids else None
        all_hit = (hit_expected == expected_dids) if expected_dids else None
        # 引用命中率（v2.0.1 新增）：模型实际引用的 expected 证据占比——
        # 小材料全量供给后「检索召回」恒为 1.0（证据可达性），此指标测端到端质量
        cite_hit = expected_dids & set(last["cited_dids"]) if expected_dids else set()
        cite_hit_rate = round(len(cite_hit) / len(expected_dids), 3) if expected_dids else None
        # 拒答准确率（四审 §9.3）：answerable ↔ true；insufficient/conflicting ↔ false
        suff = item["evidence_sufficiency"]
        expect_sufficient = suff == "answerable"
        refuse_ok = last["sufficient"] == expect_sufficient

        # 矛盾识别四要求检查（仅 conflicting 题；曦五审「重点回归四项要求」）
        conflict_checks = None
        if suff == "conflicting":
            multi_ev = [set(e.get("clean_paragraph_ids", [])) for e in last_turn.get("expected_citations", [])]
            sides_cited = [bool(s & set(last["cited_dids"])) for s in multi_ev if s]
            conflict_mentioned = any(k in last["answer"] for k in ("不一致", "矛盾", "冲突"))
            conflict_checks = {
                "both_sides_cited": all(sides_cited) and len(sides_cited) >= 2,
                "conflict_mentioned": conflict_mentioned,
                "not_arbitrary_pick": True,   # 不选边由人工复核判（自动仅检查双方编号在列）
            }
        # 矛盾识别四要求检查（仅 conflicting 题；曦五审「重点回归四项要求」）
        conflict_checks = None
        if suff == "conflicting":
            multi_ev = [set(e.get("clean_paragraph_ids", [])) for e in last_turn.get("expected_citations", [])]
            sides_cited = [bool(s & set(last["cited_dids"])) for s in multi_ev if s]
            conflict_mentioned = any(k in last["answer"] for k in ("不一致", "矛盾", "冲突"))
            conflict_checks = {
                "both_sides_cited": all(sides_cited) and len(sides_cited) >= 2,
                "conflict_mentioned": conflict_mentioned,
                "not_arbitrary_pick": True,   # 不选边由人工复核判（自动仅检查双方编号在列）
            }
        results.append({
            "question_id": item["question_id"], "category": item["category"],
            "evidence_sufficiency": suff,
            "sufficient": last["sufficient"], "refuse_judgement_ok": refuse_ok,
            "cited_dids": last["cited_dids"], "invalid_dids": last["invalid_dids"],
            "recall": recall, "all_evidence_hit": all_hit,
            "citation_hit_rate": cite_hit_rate,
            "conflict_checks": conflict_checks,
            "conflict_checks": conflict_checks,
            "expected_dids": sorted(expected_dids),
            "multi_turn": len(turns) > 1,
            "conversation_id": last.get("conversation_id") or conv_id,
            "turn_results": turn_results,
        })

    # 汇总指标
    n = len(results)
    with_expected = [r for r in results if r["recall"] is not None]
    summary = {
        "run_ts": run_ts, "split": split, "mock": mock,
        "total_questions": n,
        "refuse_judgement_accuracy": round(
            sum(1 for r in results if r["refuse_judgement_ok"]) / n, 3) if n else None,
        "citation_valid_rate": round(
            sum(1 for r in results if not r["invalid_dids"]) / n, 3) if n else None,
        "retrieval_recall_mean": round(
            sum(r["recall"] for r in with_expected) / len(with_expected), 3) if with_expected else None,
        "citation_hit_rate_mean": round(
            sum(r["citation_hit_rate"] for r in with_expected if r["citation_hit_rate"] is not None)
            / max(1, sum(1 for r in with_expected if r["citation_hit_rate"] is not None)), 3),
        "all_evidence_hit_rate": round(
            sum(1 for r in with_expected if r["all_evidence_hit"]) / len(with_expected), 3) if with_expected else None,
        "multi_turn_count": sum(1 for r in results if r["multi_turn"]),
        "by_category": {},
    }
    cats = sorted({r["category"] for r in results})
    for cat in cats:
        rs = [r for r in results if r["category"] == cat]
        summary["by_category"][cat] = {
            "count": len(rs),
            "refuse_judgement_ok": sum(1 for r in rs if r["refuse_judgement_ok"]),
            "invalid_free": sum(1 for r in rs if not r["invalid_dids"]),
        }

    # 落盘：LOCKED 逐题明细物理分离（四审 §9.2）
    storage._write_json(run_dir / "summary.json", {"split": split, **summary})
    detail_dir = run_dir
    if split == "LOCKED":
        detail_dir = run_dir / "locked_detail"
        detail_dir.mkdir(parents=True, exist_ok=True)
        storage._write_json(detail_dir / f"locked_detail_{uuid.uuid4().hex[:6]}.json", results)
    else:
        with (run_dir / "results_dev.jsonl").open("w", encoding="utf-8") as f:
            for r in results:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

    report = ["# 问答评测报告（" + split + "）", "",
              f"- 运行时间：{run_ts}　模式：{'mock' if mock else 'real'}　题数：{n}",
              f"- 拒答判断准确率：{summary['refuse_judgement_accuracy']}",
              f"- 引用有效率：{summary['citation_valid_rate']}",
              f"- 检索召回率（均值）：{summary['retrieval_recall_mean']}",
              f"- 全证据命中题占比：{summary['all_evidence_hit_rate']}", "",
              "| 类别 | 题数 | 拒答判断对 | 引用无剔除 |", "|---|---|---|---|"]
    for cat, s in summary["by_category"].items():
        report.append(f"| {cat} | {s['count']} | {s['refuse_judgement_ok']} | {s['invalid_free']} |")
    report += ["", "## 人工复核槽位（首轮从 DEV 选题）", "",
               "| question_id | 引用支撑率 | 答案完整性 | 无依据陈述 | 备注 |", "|---|---|---|---|---|"]
    if split == "DEV":
        for r in results[:10]:
            report.append(f"| {r['question_id']} |  |  |  |  |")
    else:
        report.append("| （LOCKED 阶段只出汇总，人工复核从 DEV 选题） | | | | |")
    (run_dir / "report.md").write_text("\n".join(report), encoding="utf-8")

    return {"run_dir": str(run_dir), "summary": summary}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--mock", action="store_true")
    args = ap.parse_args()
    out = run_dataset(args.dataset, mock=args.mock)
    print(json.dumps(out["summary"], ensure_ascii=False, indent=2))
    print("快照目录：", out["run_dir"])


if __name__ == "__main__":
    main()
