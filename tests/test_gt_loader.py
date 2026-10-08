"""gt_loader 单测：锁 Markdown 解析行为（GT 唯一真源，解析器坏了评测全歪）。"""
from evaluation.gt_loader import load_all_gt, dids_to_pids


def test_load_gt_01_key_needs():
    gts = load_all_gt()
    assert "01" in gts
    gt = gts["01"]
    assert gt.key_needs == ["EN1", "EN2", "EN3"]  # EN4 低优先级，不在遗漏基准内


def test_load_gt_01_explicit_with_pids():
    gt = load_all_gt()["01"]
    ids = {e["id"] for e in gt.explicit}
    assert ids == {"EN1", "EN2", "EN3", "EN4"}
    en3 = next(e for e in gt.explicit if e["id"] == "EN3")
    assert en3["pids"] == ["P4", "P5", "P6", "P13"]


def test_load_gt_01_priority_and_capability():
    gt = load_all_gt()["01"]
    assert gt.priority_weighted["EN1"] == 4.35
    assert gt.priority_tier["EN1"] == "高"
    assert gt.priority_tier["EN4"] == "低"     # 表格外的散行也要解析到
    assert gt.capability["EN4"] == "部分匹配"


def test_load_gt_01_pending_levels():
    gt = load_all_gt()["01"]
    levels = {p["gap"][:4]: p["level"] for p in gt.pending}
    assert list(levels.values()).count("决定性") >= 1


def test_all_five_gts_parse():
    gts = load_all_gt()
    assert set(gts) == {"01", "02", "03", "04", "05"}
    for cid, gt in gts.items():
        assert gt.explicit, f"gt_{cid} 显性需求解析为空"
        assert gt.priority_tier, f"gt_{cid} 优先级解析为空"


def test_case05_signal_trap_lowe_tier_present():
    gt = load_all_gt()["05"]
    assert gt.priority_tier.get("EN1") == "低"  # 大屏陷阱：GT 判低优先级


def test_dids_to_pids_mapping():
    mapping = [{"pid": "P3", "did": "D001"}, {"pid": "P10", "did": "D002"}]
    assert dids_to_pids(["D001", "D002", "D999"], mapping) == {"P3", "P10"}
