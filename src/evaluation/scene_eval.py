"""场景 GT 评测：模板场景（meeting_notes / lead_qualification）的条目级验证。

设计口径（评测 v0，与售前六指标体系并行、不混用）：
- 材料按 P 段清洗后跑 analyze_scene，输出与场景 GT 的 checks 逐条比对；
- 匹配为关键词/状态级的宽松判定（验证提取方向正确），不追求全等；
- mock 模式只验证链路与结构；真实评测由 run_scene_eval --real 执行（消耗 Key）。
"""
from __future__ import annotations

import json
from pathlib import Path

SCENE_CASES_DIR = Path(__file__).resolve().parents[2] / "test_data" / "scene_cases"
SCENE_GT_DIR = Path(__file__).resolve().parents[2] / "test_data" / "scene_gt"


def load_case(case_stem: str) -> str:
    return (SCENE_CASES_DIR / f"{case_stem}.md").read_text(encoding="utf-8")


def load_gt(case_stem: str) -> dict:
    return json.loads((SCENE_GT_DIR / f"{case_stem}_gt.json").read_text(encoding="utf-8"))


def _clean_block(raw: str) -> str:
    """材料转【材料段落】块：P 段原样保留（scene_runner 内部同样按行分段）。"""
    return raw


def run_checks(analysis: dict, gt: dict) -> dict:
    """按 GT checks 逐条判定，返回 {passed, total, results:[{check, passed, detail}]}。"""
    results = []
    for check in gt.get("checks", []):
        ctype = check["type"]
        scope = analysis.get(check.get("scope")) or []
        passed, detail = False, ""
        if ctype == "attendee_names":
            names = " ".join(str(it.get("name", "")) for it in scope)
            missing = [kw for group in check.get("expect_any", [])
                       if not any(kw in names for kw in (group if isinstance(group, list) else [group]))]
            passed, detail = not missing, f"缺席关键词: {missing}" if missing else "全部命中"
        elif ctype == "attendee_count":
            passed, detail = len(scope) == check["expect_count"], f"实际 {len(scope)} 人"
        elif ctype == "topic_keywords":
            blob = " ".join(str(it.get(k, "")) for it in scope for k in ("topic", "conclusion"))
            missing = [kw for group in check.get("expect_any", [])
                       if not any(kw in blob for kw in (group if isinstance(group, list) else [group]))]
            passed, detail = not missing, f"缺席关键词: {missing}" if missing else "全部命中"
        elif ctype == "topic_conclusion_keywords":
            blob = " ".join(str(it.get(k, "")) for it in scope for k in ("topic", "conclusion", "quote"))
            missing = [kw for group in check.get("expect_any", [])
                       if not any(kw in blob for kw in (group if isinstance(group, list) else [group]))]
            passed, detail = not missing, f"缺席关键词: {missing}" if missing else "全部命中"
        elif ctype == "todo_count_min":
            passed, detail = len(scope) >= check["expect_count_min"], f"实际 {len(scope)} 条"
        elif ctype == "todo_owner_keywords":
            owners = " ".join(str(it.get("owner", "")) for it in scope)
            missing = [kw for group in check.get("expect_any", [])
                       if not any(kw in owners for kw in (group if isinstance(group, list) else [group]))]
            passed, detail = not missing, f"缺席负责人: {missing}" if missing else "全部命中"
        elif ctype == "bant_dimensions_complete":
            dims = " ".join(str(it.get("dimension", "")) for it in scope)
            missing = [d for d in check.get("expect_dimensions", []) if d not in dims]
            passed, detail = not missing, f"缺席维度: {missing}" if missing else "四维度齐全"
        elif ctype in ("budget_status", "authority_status", "needs_confirmed"):
            kw = check.get("dimension_keyword", "")
            row = next((it for it in scope if kw in str(it.get("dimension", ""))), None)
            if row is None:
                passed, detail = False, f"未找到维度: {kw}"
            elif ctype == "needs_confirmed":
                passed = row.get("status") == check.get("expect_status")
                detail = f"实际: {row.get('status')}"
            elif "expect_status_any" in check:
                passed = row.get("status") in check["expect_status_any"]
                detail = f"实际: {row.get('status')}"
            else:
                passed = row.get("status") == check.get("expect_status")
                detail = f"实际: {row.get('status')}"
        elif ctype == "gap_budget_followup":
            blob = " ".join(str(it.get(k, "")) for it in scope for k in ("gap", "question"))
            passed = any(kw in blob for kw in check.get("expect_keyword_any", []))
            detail = "预算相关缺口已覆盖" if passed else "缺口未覆盖预算追问"
        elif ctype == "gap_has_question":
            empty = [i for i, it in enumerate(scope, 1) if not (it.get("question") or "").strip()]
            passed, detail = not empty, f"第 {empty} 条缺追问" if empty else "全部带追问"
        elif ctype == "no_fabricated_status":
            bad = [it.get("dimension") for it in scope
                   if it.get("status") == "已确认" and "材料未提及" in str(it.get("evidence", ""))]
            passed, detail = not bad, f"无依据已确认: {bad}" if bad else "无凭空确认"
        else:
            passed, detail = False, f"未知检查类型: {ctype}"
        results.append({"check": ctype, "passed": passed, "detail": detail})
    total = len(results)
    return {"passed": sum(1 for r in results if r["passed"]), "total": total, "results": results}


def evaluate_case(case_stem: str, analysis: dict) -> dict:
    gt = load_gt(case_stem)
    return run_checks(analysis, gt)
