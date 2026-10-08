"""数据清洗层（Workflow 节点 1：解析 + 格式清洗）——纯代码，确定性 100%。

对应 05 框架 v1.1 §3 两层漏斗的「格式层」：
- 原始材料按 P 编号解析成段落
- 生成 D 编号（清洗后编号），建立 P↔D 对照表（评测时用于换算）
- PII 正则脱敏（不过脱敏不进 LLM）
- 同人连续刷屏合并、空白归一

铁律（05 §3）：标记不删除——被清洗动作处理的段落留痕于 stats，不静默丢弃。
"""
import re
from dataclasses import dataclass, field

# PII 正则（中国场景常见三类）
RE_PHONE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
RE_IDCARD = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")
RE_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

# 原始材料段落格式：P1 【说话人】内容……
RE_SEGMENT = re.compile(r"^P(\d+)\s+(.*)$", re.S)


@dataclass
class Segment:
    pid: str          # 原始编号 P1、P2……
    did: str          # 清洗后编号 D001、D002……
    text: str         # 脱敏后文本（供 LLM 与引用校验）
    raw_text: str     # 脱敏前文本（留痕，不进 LLM）
    pii_scrubbed: bool = False


@dataclass
class CleanResult:
    segments: list = field(default_factory=list)
    stats: dict = field(default_factory=dict)

    @property
    def mapping(self) -> list:
        """P↔D 对照表（评测换算用）。"""
        return [{"pid": s.pid, "did": s.did} for s in self.segments]

    def did_set(self) -> set:
        return {s.did for s in self.segments}

    def text_of(self, did: str) -> str:
        for s in self.segments:
            if s.did == did:
                return s.text
        raise KeyError(f"未知段落编号：{did}")

    def cleaned_text(self) -> str:
        """带 D 编号的清洗后全文（进 LLM 的形态）。"""
        return "\n\n".join(f"{s.did} {s.text}" for s in self.segments)


def _scrub_pii(text: str) -> tuple[str, bool]:
    """PII 脱敏：命中即替换为占位符。返回 (脱敏后文本, 是否命中)。"""
    scrubbed = False
    for pattern, placeholder in (
        (RE_IDCARD, "[身份证已脱敏]"),
        (RE_PHONE, "[手机号已脱敏]"),
        (RE_EMAIL, "[邮箱已脱敏]"),
    ):
        if pattern.search(text):
            text = pattern.sub(placeholder, text)
            scrubbed = True
    return text, scrubbed


def load_material(path) -> str:
    """读取案例文件，截取「## 原始材料」之后的部分（无该标题则用全文）。"""
    raw = open(path, encoding="utf-8").read()
    marker = "## 原始材料"
    if marker in raw:
        raw = raw.split(marker, 1)[1]
    return raw


def clean(raw_material: str) -> CleanResult:
    """主入口：原始材料文本 → CleanResult。"""
    result = CleanResult()
    # 先按空行分块；若某块内含多个 P 行（无空行分隔的紧凑格式），再按行拆开
    blocks = []
    for b in re.split(r"\n\s*\n", raw_material):
        if not b.strip():
            continue
        lines = [l.strip() for l in b.splitlines() if l.strip()]
        if len(lines) > 1 and sum(1 for l in lines if RE_SEGMENT.match(l)) > 1:
            blocks.extend(lines)
        else:
            blocks.append(b.strip())

    seq = 0
    seen_texts = []  # 同内容连续刷屏合并的判定基准
    merged_count = 0
    for block in blocks:
        m = RE_SEGMENT.match(block.replace("\n", " ").strip())
        if not m:
            continue  # 非正文段落（如表头残留）不进流水线，留痕于 stats
        pid, text = f"P{m.group(1)}", m.group(2).strip()
        text = re.sub(r"\s+", " ", text)  # 空白归一

        # 同内容刷屏合并（仅相邻且完全一致才合并——保守规则，防误杀）
        if seen_texts and seen_texts[-1] == text:
            merged_count += 1
            continue

        scrubbed_text, hit = _scrub_pii(text)
        seq += 1
        result.segments.append(Segment(
            pid=pid, did=f"D{seq:03d}", text=scrubbed_text, raw_text=text, pii_scrubbed=hit,
        ))
        seen_texts.append(text)

    scrubbed_total = sum(1 for s in result.segments if s.pii_scrubbed)
    result.stats = {
        "输入块数": len(blocks),
        "解析段落数": len(result.segments),
        "PII脱敏段落数": scrubbed_total,
        "刷屏合并数": merged_count,
    }
    return result
