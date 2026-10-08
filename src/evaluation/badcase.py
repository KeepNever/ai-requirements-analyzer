"""Badcase 记录——症状标签（BC-01~10，07 v1.2 锁定）与归因维度（M2 采纳）双轨。

一条 Badcase 记录同时打两个标签：
- type（症状）：错在哪个环节的**表现**——直接对应 07 v1.2 的十类错误模式
- cause（归因）：**为什么会错**——prompt / model / validator / data / gt / system

丢弃任何一边都会丢信息：只记症状不知道改哪，只记归因没法跨轮对比错误分布。
"""
from dataclasses import asdict, dataclass, field

BC_LABELS = {
    "BC-01": "推断当事实（潜在需求混入显性）",
    "BC-02": "引用造假（quote 与所引段落不符）",
    "BC-03": "编造内容（材料未提及的竞品/预算等）",
    "BC-04": "机械频率计分（信号强度误判，如大屏陷阱）",
    "BC-05": "忽略一票否决前提（部署限制/成单前提）",
    "BC-06": "强行归类（证据不足仍输出匹配/高置信）",
    "BC-07": "漏识别低频关键需求",
    "BC-08": "显性需求优先级误判（显性≠重要的考点失守）",
    "BC-09": "缺口失职（决定性缺口漏报或降级）",
    "BC-10": "建议越权（优先级建议写成排期决策口径）",
}

CAUSES = {"prompt", "model", "validator", "data", "gt", "system"}


@dataclass
class Badcase:
    case_id: str          # 如 "01"
    type: str             # BC-01 ~ BC-10（症状）
    cause: str            # prompt/model/validator/data/gt/system（归因）
    evidence: str         # 具体证据（引用字段/数值差异）
    note: str = ""        # 处置备注（如：进 M3 Prompt 迭代清单）


def make_record(case_id: str, type_: str, cause: str, evidence: str, note: str = "") -> dict:
    """构造一条记录，非法标签直接抛错（防止评测报告里出现手滑标签）。"""
    if type_ not in BC_LABELS:
        raise ValueError(f"未知 Badcase 症状标签：{type_}（合法：{sorted(BC_LABELS)}）")
    if cause not in CAUSES:
        raise ValueError(f"未知归因维度：{cause}（合法：{sorted(CAUSES)}）")
    return asdict(Badcase(case_id=case_id, type=type_, cause=cause, evidence=evidence, note=note))
