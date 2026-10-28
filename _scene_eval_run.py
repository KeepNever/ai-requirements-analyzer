"""场景真实评测执行：跑 LLM 分析 + GT 校验，输出报告。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from pipeline import scenes as scenes_mod
from pipeline.scenes import analyze_scene
from evaluation.scene_eval import load_case, load_gt, run_checks

CASES = ["meeting_01_教研例会", "lead_01_恒达精密工单Agent咨询"]
ok_all = True
for stem in CASES:
    gt = load_gt(stem)
    scene = scenes_mod.get_scene(gt["scene"])
    block = load_case(stem)
    analysis = analyze_scene(scene, block, mock=False)
    res = run_checks(analysis, gt)
    ok_all = ok_all and res["passed"] == res["total"]
    print(f"\n=== {gt['scene_name']} · {stem} ===")
    print(f"通过 {res['passed']}/{res['total']}")
    for r in res["results"]:
        print(f"  [{'PASS' if r['passed'] else 'FAIL'}] {r['check']}: {r['detail']}")
    out = Path(f"output/scene_eval/{stem}_analysis.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(analysis, ensure_ascii=False, indent=2), encoding="utf-8")
print("\nSCENE EVAL:", "ALL PASSED" if ok_all else "HAS FAILURES")
