"""六指标计算（07 测试数据集 v1.2 §6）——自动项算数，人工项留槽位，不假装全自动。

口径（07 v1.1/v1.2 锁定）：
- 匹配规则：按业务意图匹配；**合理拆分不扣分**（AI 把 GT 一条拆成多条，覆盖即算命中）；
  合并丢关键约束按遗漏处理（自动代理：GT 需求被 AI 单条覆盖且 P 引用交集不足半数 → 未命中）
- 关键需求遗漏率锚定 GT 预标注的「关键需求集合」，不随 AI 输出动态调整
- 事实/推断区分：自动部分为代理指标（引用错位嫌疑），引用支撑性的语义判断留人工复核
- 追问有效性（指标④）：完全依赖人工判断，双人评分槽位

输入均为已落盘产物：output/<case>/analysis.json + mapping.json + test_data/gt_XX.md
"""
import json
import re
from pathlib import Path

from .gt_loader import GT, dids_to_pids

# 匹配阈值：AI 需求的 P 引用与 GT 需求 P 引用交集占 GT 侧比例 ≥ 0.5 视为覆盖该 GT 需求
COVER_RATIO = 0.5

# 配对规则版本（v1.4 拆分感知配对；v1.5 候选①⑦引用覆盖启发式）
METRICS_VERSION = "v1.5（v1.4 拆分感知配对 + ⑦引用覆盖启发式：多事实点需求须 GT 引用集全覆盖才算引用完整——完整率为二值口径，旧连续均值保留作对照）"


def _ai_explicit_pids(analysis: dict, mapping: list) -> list[set]:
    return [dids_to_pids(n.get("dids", []), mapping) for n in analysis["node1"]["explicit_needs"]]


def match_explicit(analysis: dict, gt: GT, mapping: list) -> dict:
    """显性需求匹配：返回 {gt_id: [ai_idx]}, 未命中 GT, 疑似多余 AI（非拆分）。"""
    ai_pids = _ai_explicit_pids(analysis, mapping)
    gt_matched = {}
    consumed_ai = set()
    for g in gt.explicit:
        gp = set(g["pids"])
        # 最优命中（最大重叠），不取首个——Baseline R1 教训：首个命中会把
        # 「自然语言查数」错配到「监管报送」这类引用偶然重叠的需求上
        best_i, best_r = None, 0.0
        for i, ap in enumerate(ai_pids):
            if gp and ap:
                r = len(gp & ap) / len(gp)
                if r >= COVER_RATIO and r > best_r:
                    best_i, best_r = i, r
        if best_i is not None:
            gt_matched[g["id"]] = [best_i]
            consumed_ai.add(best_i)

    # 第二遍（v1.4 拆分感知配对）：GT 引用打包宽 + AI 合理拆条时，单条交集不足阈值会被误判漏识别。
    # 修正：多条未消费 AI 需求的引用并集覆盖 GT 过半、且每条引用完全落在 GT 引用集内（防误吞无关需求）
    # → 共同命中。组内按与 GT 引用重叠度降序，首条为主条目（⑤⑥对照用）。
    for g in gt.explicit:
        if g["id"] in gt_matched:
            continue
        gp = set(g["pids"])
        if not gp:
            continue
        group = [i for i, ap in enumerate(ai_pids)
                 if i not in consumed_ai and ap and ap <= gp]
        if not group:
            continue
        union = set().union(*(ai_pids[i] for i in group))
        if len(union & gp) / len(gp) >= COVER_RATIO:
            group.sort(key=lambda i: len(ai_pids[i] & gp), reverse=True)
            gt_matched[g["id"]] = group
            consumed_ai.update(group)

    # 拆分不扣分：未被 GT 消费的 AI 需求，若其 P 引用完全落在某个已命中 GT 需求的引用集内，
    # 视为合理拆分（不算 FP）；否则视为疑似多余（FP 候选，人工复核）
    gt_pid_union = {g["id"]: set(g["pids"]) for g in gt.explicit}
    extra_ai = []
    for i, ap in enumerate(ai_pids):
        if i in consumed_ai or not ap:
            continue
        split_of = [gid for gid, gp in gt_pid_union.items() if ap and ap <= gp]
        if not split_of:
            extra_ai.append(i)
    missed_gt = [g["id"] for g in gt.explicit if g["id"] not in gt_matched]
    return {"matched": gt_matched, "missed_gt": missed_gt, "extra_ai": extra_ai}


def compute_case_metrics(analysis: dict, mapping: list, gt: GT) -> dict:
    """单案例六指标。返回可直接进报告的结构（含人工槽位）。"""
    m = match_explicit(analysis, gt, mapping)
    n_gt = len(gt.explicit)
    n_ai = len(analysis["node1"]["explicit_needs"])

    # ① 提取准确性 F1（自动代理）
    tp = len(m["matched"])
    fp = len(m["extra_ai"])
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / n_gt if n_gt else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0

    # ② 关键需求遗漏率（锚定 GT 预标注集合）
    key_set = gt.key_needs or [g["id"] for g in gt.explicit]  # GT 未标注时退化为全集并注明
    missed_keys = [k for k in key_set if k in m["missed_gt"]]
    miss_rate = len(missed_keys) / len(key_set) if key_set else 0.0

    # ③ 事实/推断区分（自动代理 + 人工复核槽位）
    # 代理口径：显性需求引用与 GT 显性需求零交集 → 疑似把推断/无关内容提成了显性
    ai_pids = _ai_explicit_pids(analysis, mapping)
    gt_explicit_pids = [set(g["pids"]) for g in gt.explicit]
    suspect = [i for i, ap in enumerate(ai_pids)
               if ap and not any(ap & gp for gp in gt_explicit_pids)]
    contam_ratio = len(suspect) / n_ai if n_ai else 0.0

    # ⑤ 优先级一致性 / ⑥ 能力匹配准确率：**走语义匹配对**，不用裸编号对表——
    # Baseline R1 教训：AI 与 GT 的 EN 编号语义不同（AI 的 EN3 ≠ GT 的 EN3），
    # 直接按编号比较会制造假阳性（BC-08 误报）。配对来自 match_explicit。
    ai_tier = {r["id"]: r["tier"] for r in analysis.get("priority", [])}
    ai_id_by_index = [n["id"] for n in analysis["node1"]["explicit_needs"]]
    pairs = [(g_id, ai_id_by_index[idxs[0]]) for g_id, idxs in m["matched"].items()
             if idxs and ai_id_by_index[idxs[0]] in ai_tier]

    # AI 能力匹配按 target 里的 EN 编号索引（target 可能是 "EN1" / "EN1 / EN2 / EN6（…）"）
    ai_cap_by_need: dict[str, str] = {}
    for c in analysis["node2"]["capability_match"]:
        for eid in re.findall(r"EN\d+", c["target"]):
            ai_cap_by_need.setdefault(eid, c["verdict"])

    tier_common = [(g, a) for g, a in pairs if g in gt.priority_tier]
    tier_agree = sum(1 for g, a in tier_common if ai_tier[a] == gt.priority_tier[g])
    cap_pairs = [(g, a) for g, a in pairs if g in gt.capability and a in ai_cap_by_need]
    cap_agree = sum(1 for g, a in cap_pairs if ai_cap_by_need[a] == gt.capability[g])

    # ⑦ 引用完整性 Citation Coverage（M2 Final 收口新增，2026-10-08，外部建议 §6）
    # 口径：只评价「已识别（语义配对命中）」需求的证据覆盖 = AI 命中需求 P 引用 ∩ GT 该需求 P 引用 / GT P 引用数
    # 与①分离：①管「有没有识别到」，⑦管「识别后证据引得全不全」，③管「有没有把事实推断放对层」
    # ——三者各管一个命题，混算会掩盖 M3 迭代的真实归因（BC-07 改判的直接动因）
    # v1.5（候选①，曦拍板 2026-10-19）：新增「完整率」二值口径——一条需求合并多个关键事实点时，
    # GT 引用集全部被 AI 引用覆盖才算引用完整，部分覆盖不计支撑（双人评分引用错位判例的规则自动化，
    # 如 case_03 EN4 两事实点仅引 P8 漏 P7）。旧连续「均值」保留作对照，两者并列输出不互相替代。
    gt_pids_by_id = {g["id"]: set(g["pids"]) for g in gt.explicit}
    cov_detail = {}
    cov_full_detail = {}
    for g_id, idxs in m["matched"].items():
        gp = gt_pids_by_id[g_id]
        # v1.4：组命中按组内引用并集计覆盖（单条命中并集即其自身，行为不变）
        ap = set().union(*(ai_pids[i] for i in idxs)) if idxs else set()
        label = f"{g_id}↔{ai_id_by_index[idxs[0]] if idxs else '?'}"
        cov_detail[label] = round(len(gp & ap) / len(gp), 3) if gp else None
        # v1.5 完整率判定：全覆盖=完整；缺失事实点写入明细便于定位
        if gp:
            missing = sorted(gp - ap, key=lambda p: int(p[1:]) if p[1:].isdigit() else 0)
            cov_full_detail[label] = {"完整": ap >= gp, "缺失": missing}
    cov_vals = [v for v in cov_detail.values() if v is not None]
    cov_mean = round(sum(cov_vals) / len(cov_vals), 3) if cov_vals else None
    full_vals = [1 if d["完整"] else 0 for d in cov_full_detail.values()]
    cov_full_rate = round(sum(full_vals) / len(full_vals), 3) if full_vals else None

    # BC 自动探针（确定性规则，且只对语义配对生效；其余靠人工）
    probes = []
    for g, a in tier_common:
        if gt.priority_tier[g] == "低" and ai_tier[a] == "高":
            probes.append({"type": "BC-08", "evidence": f"配对 {g}↔{a}: GT={gt.priority_tier[g]} AI={ai_tier[a]}"})
    if gt.no_competitor and "无竞品" not in analysis["node1"]["competitor_signals"]:
        probes.append({"type": "BC-03", "evidence": f"GT 声明无竞品，AI 输出：{analysis['node1']['competitor_signals'][:50]}"})
    for g, a in cap_pairs:
        if gt.capability[g] == "待确认" and ai_cap_by_need[a] != "待确认":
            probes.append({"type": "BC-06", "evidence": f"配对 {g}↔{a}: GT=待确认 AI={ai_cap_by_need[a]}（证据不足仍强行归类）"})

    return {
        "①提取F1": {"precision": round(precision, 3), "recall": round(recall, 3), "f1": round(f1, 3),
                   "matched": m["matched"], "missed_gt": m["missed_gt"],
                   "extra_ai(疑似拆分外需求)": [analysis["node1"]["explicit_needs"][i]["id"] for i in m["extra_ai"]]},
        "②关键遗漏率": {"rate": round(miss_rate, 3), "missed": missed_keys, "基准集合": key_set},
        "③事实推断区分": {"引用错位嫌疑比例": round(contam_ratio, 3),
                       "嫌疑项": [analysis["node1"]["explicit_needs"][i]["id"] for i in suspect],
                       "人工复核": "槽位：引用支撑性语义判断，双人独立"},
        "④追问有效性": {"自动": None, "待人工": "槽位：有效追问数/追问总数，双人评分（07 §6）",
                     "AI追问数": len(analysis["node2"]["pending_confirmations"])},
        "⑤优先级一致性": {"一致数": tier_agree, "可比数": len(tier_common),
                       "率": round(tier_agree / len(tier_common), 3) if tier_common else None,
                       "配对": [f"{g}↔{a}" for g, a in tier_common]},
        "⑥能力匹配准确率": {"一致数": cap_agree, "可比数": len(cap_pairs),
                        "率": round(cap_agree / len(cap_pairs), 3) if cap_pairs else None,
                        "配对": [f"{g}↔{a}" for g, a in cap_pairs]},
        "⑦引用完整性": {"均值": cov_mean, "完整率": cov_full_rate, "逐需求": cov_detail,
                     "逐需求完整判定": cov_full_detail, "已识别需求数": len(cov_detail),
                     "口径": "仅对已识别需求的证据覆盖（BC-COV 的量化依据）；v1.5 完整率=GT 引用集全覆盖才计完整，均值保留作对照"},
        "BC自动探针": probes,
    }


def load_case_artifacts(output_dir: Path, case_stem: str) -> tuple[dict, list]:
    analysis = json.loads((output_dir / case_stem / "analysis.json").read_text(encoding="utf-8"))
    mapping = json.loads((output_dir / case_stem / "mapping.json").read_text(encoding="utf-8"))["mapping"]
    return analysis, mapping
