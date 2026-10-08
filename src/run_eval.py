#!/usr/bin/env python3
"""M2 批量评测入口：5 案例 Real Baseline。

用法（项目根目录）：
  python src/run_eval.py                    # 真实调用跑 5 案例 → 计算六指标 → 出 Baseline 报告
  python src/run_eval.py --from-output      # 跳过运行，直接用 output/ 已有产物算指标（离线复核）
  python src/run_eval.py --cases 01 05      # 只跑指定案例

产出：output/evaluation/baseline_r1_report.md + metrics.json + badcases.json
"""
import argparse
import json
import sys
import traceback
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from pipeline import workflow  # noqa: E402
from pipeline.llm_client import reset_usage  # noqa: E402
from evaluation import gt_loader, metrics  # noqa: E402
from evaluation.badcase import make_record  # noqa: E402
from evaluation import report as report_mod  # noqa: E402

TEST_DATA = PROJECT_ROOT / "test_data"
OUTPUT = PROJECT_ROOT / "output"
EVAL_DIR = OUTPUT / "evaluation"
EVAL_NAME = "Baseline R1（开发集首轮）"


def find_cases(selected: list[str] | None = None) -> list[Path]:
    cases = sorted(TEST_DATA.glob("case_*.md"))
    if selected:
        cases = [c for c in cases if any(c.stem.startswith(f"case_{s}") or c.stem == s for s in selected)]
    return cases


def main():
    parser = argparse.ArgumentParser(description="AI 客户需求分析助手 · 批量评测")
    parser.add_argument("--from-output", action="store_true", help="复用 output/ 已有 analysis.json，离线算指标")
    parser.add_argument("--cases", nargs="*", help="只评测指定案例编号（如 01 05）")
    args = parser.parse_args()

    cases = find_cases(args.cases)
    if not cases:
        print("[M2] 未找到案例，检查 test_data/"); return

    EVAL_DIR.mkdir(parents=True, exist_ok=True)
    gts = gt_loader.load_all_gt()
    per_case, badcases, errors = {}, {}, []

    for case_path in cases:
        stem = case_path.stem
        case_id = stem.split("_")[1]
        gt = gts.get(case_id)
        if gt is None:
            errors.append(f"{stem}: 无对应 GT"); continue
        print(f"[M2] {stem} ...", flush=True)
        try:
            if not args.from_output:
                reset_usage()
                workflow.run_case(str(case_path), mock=False)
            analysis, mapping = metrics.load_case_artifacts(OUTPUT, stem)
            per_case[stem] = metrics.compute_case_metrics(analysis, mapping, gt)
            for probe in per_case[stem]["BC自动探针"]:
                badcases[stem] = badcases.get(stem, []) + [
                    make_record(case_id, probe["type"], "model", probe["evidence"],
                                "自动探针捕获，人工复核归因")]
            print(f"[M2]   ✓ 完成")
        except Exception as e:
            errors.append(f"{stem}: {e}")
            traceback.print_exc()

    if not per_case:
        print("[M2] 无任何案例成功，评测终止"); return

    # 开发集聚合（均值；小样本不显著，报告内已声明）
    def _avg(path, key=None):
        vals = []
        for r in per_case.values():
            v = r
            for p in path:
                v = v[p]
            if isinstance(v, (int, float)):
                vals.append(v)
        return round(sum(vals) / len(vals), 3) if vals else None

    aggregate = {
        "①提取F1均值": _avg(("①提取F1", "f1")),
        "②关键遗漏率均值": _avg(("②关键遗漏率", "rate")),
        "③引用错位嫌疑均值": _avg(("③事实推断区分", "引用错位嫌疑比例")),
        "⑤优先级一致率均值": _avg(("⑤优先级一致性", "率")),
        "⑥能力匹配一致率均值": _avg(("⑥能力匹配准确率", "率")),
    }

    usage_files = sorted(OUTPUT.glob("case_*/usage.json"))
    usage = {"调用次数": 0, "输入token": 0, "输出token": 0, "估算成本元": 0}
    for uf in usage_files:
        u = json.loads(uf.read_text(encoding="utf-8"))
        for k in usage:
            usage[k] += u.get(k, 0) if k != "估算成本元" else u.get(k, 0)
    usage["估算成本元"] = round(usage["估算成本元"], 3)

    flat_badcases = [b for blist in badcases.values() for b in blist]
    md = report_mod.render_baseline_report(per_case, aggregate, flat_badcases, usage, EVAL_NAME,
                                           round_note=f"- 运行模式：{'复用已有产物' if args.from_output else '真实调用'}；"
                                                      f"成功 {len(per_case)}/{len(cases)} 案例"
                                                      + (f"；失败：{errors}" if errors else ""))
    (EVAL_DIR / "baseline_r1_report.md").write_text(md, encoding="utf-8")
    (EVAL_DIR / "metrics.json").write_text(
        json.dumps({"aggregate": aggregate, "per_case": per_case}, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8")
    (EVAL_DIR / "badcases.json").write_text(
        json.dumps(flat_badcases, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n[M2] 评测完成 ✓ 报告：{EVAL_DIR / 'baseline_r1_report.md'}")
    print(f"[M2] 指标：{EVAL_DIR / 'metrics.json'}｜Badcase：{EVAL_DIR / 'badcases.json'}")
    if errors:
        print(f"[M2] ⚠ 失败案例：{errors}")


if __name__ == "__main__":
    main()
