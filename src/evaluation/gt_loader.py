"""GT 加载器——Markdown 为唯一真源（07 v1.1 锁定纪律），直接解析 test_data/gt_XX.md。

设计约束：
- 不人工双写 JSON（双写必然漂移）；解析正确性由单测锁定（tests/test_gt_loader.py）
- GT 建在清洗之前、用 P 编号；AI 输出用 D 编号——换算靠 output/<case>/mapping.json
- 关键需求集合（评测前锁定）从 GT 头部标注行解析
"""
import re
from dataclasses import dataclass, field
from pathlib import Path

TEST_DATA = Path(__file__).resolve().parents[2] / "test_data"

CONF_RE = re.compile(r"(高|中|低)\s*$")
TIER_LINE_RE = re.compile(r"(EN\d+)[^|]*?([\d.]+)\s*\**\s*(高|中|低)")


def _rows(section: str) -> list[list[str]]:
    """提取 markdown 表格数据行（跳过表头与分隔行），返回单元格列表。"""
    rows = []
    for line in section.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):  # 分隔行
            continue
        if cells and cells[0] in ("#", "需求", "对象", "缺口"):  # 表头行
            continue
        rows.append(cells)
    return rows


def _clean(cell: str) -> str:
    return cell.replace("**", "").strip()


@dataclass
class GT:
    case_id: str                      # 如 "01"
    key_needs: list = field(default_factory=list)      # 关键需求集合（评测前锁定）
    explicit: list = field(default_factory=list)       # [{id, content, pids}]
    potential: list = field(default_factory=list)      # [{id, content, confidence}]
    pending: list = field(default_factory=list)        # [{gap, level, question}]
    priority_tier: dict = field(default_factory=dict)  # {EN1: 高}
    priority_weighted: dict = field(default_factory=dict)  # {EN1: 4.35}
    capability: dict = field(default_factory=dict)     # {EN1: 匹配}
    no_competitor: bool = False                        # F8 是否声明「无竞品信号」


def load_gt(path: str | Path) -> GT:
    text = Path(path).read_text(encoding="utf-8")
    m = re.search(r"GT-(\d+)", text)
    gt = GT(case_id=m.group(1) if m else Path(path).stem)

    # 关键需求集合（评测基准，锁定行）——截取到加粗结束符，防止把括号备注里的 EN 一并抓入
    km = re.search(r"关键需求集合[^：:]*[：:]\s*([^*]+)", text)
    if km:
        gt.key_needs = re.findall(r"EN\d+", km.group(1))

    # 按 ## 分节
    sections: dict[str, str] = {}
    current = ""
    for line in text.splitlines():
        if line.startswith("## "):
            current = line[3:].strip()
            sections[current] = ""
        elif current:
            sections[current] += line + "\n"

    for title, body in sections.items():
        if title.startswith("F3"):
            for cells in _rows(body):
                if not cells or not cells[0].startswith("EN"):
                    continue
                gt.explicit.append({
                    "id": cells[0], "content": _clean(cells[1]),
                    "pids": re.findall(r"P\d+", " ".join(cells[2:])),
                })
        elif title.startswith("F4"):
            for cells in _rows(body):
                if not cells or not cells[0].startswith("IN"):
                    continue
                cm = CONF_RE.search(cells[-1])
                gt.potential.append({
                    "id": cells[0], "content": _clean(cells[1]),
                    "confidence": cm.group(1) if cm else "未标注",
                })
        elif title.startswith("F5"):
            for cells in _rows(body):
                if len(cells) < 4:
                    continue
                gt.pending.append({
                    "gap": _clean(cells[0]), "level": _clean(cells[1]),
                    "question": _clean(cells[-1]),
                })
        elif title.startswith("F6"):
            for cells in _rows(body):
                if not cells or not cells[0].startswith("EN"):
                    continue
                eid = cells[0].split()[0]  # 首格形如 "EN1 实时看板"，取编号
                wm = re.search(r"([\d.]+)", cells[-2] if len(cells) > 2 else cells[-1])
                tm = re.search(r"(高|中|低)", cells[-1])
                if wm:
                    gt.priority_weighted[eid] = float(wm.group(1))
                if tm:
                    gt.priority_tier[eid] = tm.group(1)
            # 表格外的散行（如 EN4 低优先级备注）
            for line in body.splitlines():
                if line.startswith("|") or line.startswith(">") or not line.strip():
                    continue
                for eid, w, t in TIER_LINE_RE.findall(line):
                    gt.priority_weighted.setdefault(eid, float(w))
                    gt.priority_tier.setdefault(eid, t)
        elif title.startswith("F7"):
            for cells in _rows(body):
                if len(cells) < 3 or not cells[0].startswith("EN"):
                    continue
                gt.capability[cells[0]] = _clean(cells[1])
        elif title.startswith("F8"):
            gt.no_competitor = "无竞品" in body
    return gt


def load_all_gt(test_data_dir: str | Path = TEST_DATA) -> dict[str, GT]:
    gts = {}
    for p in sorted(Path(test_data_dir).glob("gt_*.md")):
        gt = load_gt(p)
        gts[gt.case_id] = gt
    return gts


def dids_to_pids(dids: list, mapping: list) -> set:
    """AI 的 D 编号 → 原始 P 编号（经 output/<case>/mapping.json）。"""
    d2p = {m["did"]: m["pid"] for m in mapping}
    return {d2p[d] for d in dids if d in d2p}
