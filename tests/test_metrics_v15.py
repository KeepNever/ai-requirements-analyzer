"""⑦ 引用覆盖启发式（metrics v1.5）单测——候选①独立测试，曦拍板 2026-10-19。

规则：一条需求合并多个关键事实点（GT pids > 1）时，GT 引用集全部被 AI 引用覆盖
才算引用完整（完整率二值）；部分覆盖不计支撑。旧连续均值保留作对照，不互相替代。
"""
from evaluation.gt_loader import GT
from evaluation.metrics import compute_case_metrics, match_explicit, METRICS_VERSION


def make_analysis(citations_per_need):
    """构造最小 analysis dict。citations_per_need: [{pids: [P1,...]}]，经 mapping 直映射。"""
    return {
        "node1": {
            "explicit_needs": [
                {"id": f"EN{i+1}", "dids": [f"D{p}" for p in pids]}
                for i, pids in enumerate(citations_per_need)
            ],
            "competitor_signals": [],
        },
        "node2": {"capability_match": [], "pending_confirmations": []},
        "priority": [],
    }


MAPPING = [{"did": f"D{p}", "pid": p} for p in ["P1", "P2", "P3"]]


def make_gt(pids_by_need):
    return GT(
        case_id="T01",
        explicit=[{"id": f"EN{i+1}", "content": f"需求{i+1}", "pids": pids}
                  for i, pids in enumerate(pids_by_need)],
        key_needs=[], priority_tier={}, capability={}, no_competitor=False,
    )


def test_v15_full_coverage_counts_complete():
    """GT 两事实点 P1/P2，AI 引了 P1+P2 → 完整率 1.0。"""
    gt = make_gt([["P1", "P2"]])
    analysis = make_analysis([["P1", "P2"]])
    m = match_explicit(analysis, gt, MAPPING)
    assert "EN1" in m["matched"]
    r = compute_case_metrics(analysis, MAPPING, gt)["⑦引用完整性"]
    assert r["完整率"] == 1.0
    assert r["均值"] == 1.0
    assert r["逐需求完整判定"]["EN1↔EN1"] == {"完整": True, "缺失": []}


def test_v15_partial_coverage_not_complete():
    """GT 两事实点 P1/P2，AI 只引 P1 → 部分覆盖不算引用完整，缺失明细指认 P2。"""
    gt = make_gt([["P1", "P2"]])
    analysis = make_analysis([["P1"]])
    r = compute_case_metrics(analysis, MAPPING, gt)["⑦引用完整性"]
    assert r["完整率"] == 0.0
    assert r["均值"] == 0.5          # 旧连续口径保留作对照
    assert r["逐需求完整判定"]["EN1↔EN1"] == {"完整": False, "缺失": ["P2"]}


def test_v15_mixed_needs_rate():
    """两条已识别需求：一条全覆盖、一条 GT 两事实点只引其一 → 完整率 0.5。"""
    gt = make_gt([["P1", "P2"], ["P2", "P3"]])
    analysis = make_analysis([["P1", "P2"], ["P3"]])
    r = compute_case_metrics(analysis, MAPPING, gt)["⑦引用完整性"]
    assert r["完整率"] == 0.5
    assert r["均值"] == 0.75          # (1.0 + 0.5)/2 旧连续口径对照


def test_v15_group_hit_union_full_coverage():
    """v1.4 拆分感知组命中：GT 三事实点，AI 拆三条各引其一（单条均不足阈值），
    并集全覆盖 → 计完整（启发式作用于组并集，与单条/组两种配对路径一致）。"""
    gt = make_gt([["P1", "P2", "P3"]])
    analysis = make_analysis([["P1"], ["P2"], ["P3"]])
    m = match_explicit(analysis, gt, MAPPING)
    assert len(m["matched"]["EN1"]) == 3   # 组命中
    r = compute_case_metrics(analysis, MAPPING, gt)["⑦引用完整性"]
    assert r["完整率"] == 1.0


def test_v15_version_marker():
    assert "v1.5" in METRICS_VERSION and "引用覆盖启发式" in METRICS_VERSION
