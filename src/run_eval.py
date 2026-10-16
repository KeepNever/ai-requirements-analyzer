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
import subprocess
import sys
import traceback
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from pipeline import config, prompts, scoring, workflow  # noqa: E402
from pipeline.llm_client import reset_usage  # noqa: E402
from evaluation import gt_loader, metrics  # noqa: E402
from evaluation.badcase import make_record  # noqa: E402
from evaluation import report as report_mod  # noqa: E402

TEST_DATA = PROJECT_ROOT / "test_data"
VALIDATION_DATA = TEST_DATA / "validation"
OUTPUT = PROJECT_ROOT / "output"
EVAL_DIR = OUTPUT / "evaluation"
EVAL_NAME = "Baseline R1（开发集首轮）"
EVAL_PROGRAM_VERSION = "run_eval v1.3（validation 数据集支持：dev/hold 案例发现+GT 配对+usage 按案例归集）"
GT_VERSION = "07_测试数据集 v1.2（5 案例开发集）"
GT_VERSION_VALIDATION = "validation GT v1.0（6 案例，曦复核锁定 2026-10-16）"


def _git_short() -> str:
    """当前 commit——R1 元数据的核心字段：三周后还能说清「当时跑的是哪版代码」。"""
    try:
        r = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=PROJECT_ROOT,
                           capture_output=True, text=True, timeout=10)
        return r.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def find_cases(selected: list[str] | None = None, dataset: str = "main") -> list[Path]:
    """数据集发现：main=旧开发集 case_XX；dev/hold=validation 目录（GT 与 README 排除）。"""
    if dataset == "main":
        cases = sorted(TEST_DATA.glob("case_*.md"))
        if selected:
            cases = [c for c in cases if any(c.stem.startswith(f"case_{s}") or c.stem == s for s in selected)]
        return cases
    pat = "dev_*.md" if dataset == "dev" else "hold_*.md" if dataset == "hold" else "[dh]*_*.md"
    cases = [c for c in sorted(VALIDATION_DATA.glob(pat))
             if not c.stem.startswith("gt_") and c.stem != "README"]
    if selected:
        cases = [c for c in cases if any(c.stem.split("_")[1] == s or c.stem == s for s in selected)]
    return cases


def gt_path_for(case_path: Path) -> Path:
    """validation 案例 → 配对 GT：dev_06_xxx → gt_dev_06.md（前缀+编号命名约定）。"""
    prefix, num = case_path.stem.split("_")[0], case_path.stem.split("_")[1]
    return VALIDATION_DATA / f"gt_{prefix}_{num}.md"


def main():
    parser = argparse.ArgumentParser(description="AI 客户需求分析助手 · 批量评测")
    parser.add_argument("--from-output", action="store_true", help="复用 output/ 已有 analysis.json，离线算指标")
    parser.add_argument("--cases", nargs="*", help="只评测指定案例编号（如 01 05）")
    parser.add_argument("--dataset", default="main", choices=["main", "dev", "hold"],
                        help="main=旧开发集 case_XX；dev=validation 开发集新增；hold=validation 冻结集（收口专用）")
    parser.add_argument("--eval-name", default=EVAL_NAME,
                        help="评测轮次名（写入报告标题与 run_metadata）")
    parser.add_argument("--report-name", default="baseline_r1_report",
                        help="报告文件名（不含扩展名）；迭代轮次应另起名，防止覆盖基线成绩单")
    args = parser.parse_args()

    cases = find_cases(args.cases, args.dataset)
    if not cases:
        print("[评测] 未找到案例，检查 test_data/"); return
    if args.dataset == "hold":
        print("[评测] ⚠ 冻结验证集：仅限 M3 收口一次性运行，任何 Prompt 迭代前不得预跑（validation README 锁定纪律）")

    EVAL_DIR.mkdir(parents=True, exist_ok=True)
    is_validation = args.dataset != "main"
    main_gts = gt_loader.load_all_gt() if not is_validation else None
    per_case, badcases, errors = {}, {}, []

    for case_path in cases:
        stem = case_path.stem
        if is_validation:
            gt_file = gt_path_for(case_path)
            if not gt_file.exists():
                errors.append(f"{stem}: 无对应 GT（{gt_file.name}）"); continue
            gt = gt_loader.load_gt(gt_file)
            case_id = stem.split("_")[0] + stem.split("_")[1]  # dev_06_xxx → dev06
        else:
            case_id = stem.split("_")[1]
            gt = main_gts.get(case_id)
        if gt is None:
            errors.append(f"{stem}: 无对应 GT"); continue
        print(f"[评测] {stem} ...", flush=True)
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
            print(f"[评测]   ✓ 完成")
        except Exception as e:
            errors.append(f"{stem}: {e}")
            traceback.print_exc()

    if not per_case:
        print("[评测] 无任何案例成功，评测终止"); return

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
        "⑦引用完整性均值": _avg(("⑦引用完整性", "均值")),
    }

    usage = {"调用次数": 0, "输入token": 0, "输出token": 0, "估算成本元": 0}
    for stem in per_case:  # 按成功案例归集（v1.3：兼容 validation 的 dev_*/hold_* 目录名）
        uf = OUTPUT / stem / "usage.json"
        if uf.exists():
            u = json.loads(uf.read_text(encoding="utf-8"))
            for k in usage:
                usage[k] += u.get(k, 0)
    usage["估算成本元"] = round(usage["估算成本元"], 3)

    # Badcase 合并保护（M2 Final 收口新增）：人工复核记录（note 含「人工复核」）不因重算被冲掉，
    # 自动探针记录仅在无同案例同标签的人工记录时追加——人工归因 > 自动探针
    bc_path = EVAL_DIR / "badcases.json"
    human_kept = []
    if bc_path.exists():
        for b in json.loads(bc_path.read_text(encoding="utf-8")):
            if "人工复核" in (b.get("note") or ""):
                human_kept.append(b)
    flat_badcases = list(human_kept)
    for b in (b for blist in badcases.values() for b in blist):
        if not any(h["case_id"] == b["case_id"] and h["type"] == b["type"] for h in flat_badcases):
            flat_badcases.append(b)

    # R1 元数据固化（M2 Final 收口新增）：模型/Prompt/GT/评测程序/代码版本全部入档，
    # R1 由此成为 M3 所有轮次的唯一对照组（外部建议 §7）
    metadata = {
        "评测轮次": args.eval_name,
        "运行模式": "复用已有产物" if args.from_output else "真实调用",
        "时间戳": datetime.now().isoformat(timespec="seconds"),
        "代码版本(commit)": _git_short(),
        "Prompt版本": prompts.PROMPTS_VERSION,
        "模型": config.MODEL_MAIN,
        "权重表": scoring.WEIGHTS_VERSION,
        "测试集版本": GT_VERSION_VALIDATION if is_validation else GT_VERSION,
        "评测程序版本": EVAL_PROGRAM_VERSION,
        "成功案例": sorted(per_case),
        "失败": errors,
        "usage汇总": usage,
    }
    (EVAL_DIR / "run_metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")

    md = report_mod.render_baseline_report(per_case, aggregate, flat_badcases, usage, args.eval_name,
                                           round_note=f"- 运行模式：{'复用已有产物' if args.from_output else '真实调用'}；"
                                                      f"成功 {len(per_case)}/{len(cases)} 案例"
                                                      + (f"；失败：{errors}" if errors else ""))
    report_path = EVAL_DIR / f"{args.report_name}.md"
    report_path.write_text(md, encoding="utf-8")
    (EVAL_DIR / "metrics.json").write_text(
        json.dumps({"aggregate": aggregate, "per_case": per_case}, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8")
    (EVAL_DIR / "badcases.json").write_text(
        json.dumps(flat_badcases, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n[评测] 完成 ✓ 报告：{report_path}")
    print(f"[评测] 指标：{EVAL_DIR / 'metrics.json'}｜Badcase：{bc_path}")
    print(f"[评测] 元数据：{EVAL_DIR / 'run_metadata.json'}（commit {_git_short()}）")
    if errors:
        print(f"[评测] ⚠ 失败案例：{errors}")


if __name__ == "__main__":
    main()
