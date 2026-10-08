"""清洗层单测：P/D 编号、PII 脱敏、刷屏合并、留痕。"""
from pipeline.cleaner import clean


MATERIAL = """P1 【王总】大家好，开始吧。
P2 【李工】我的手机是 13812345678，有事打这个。
P3 【王总】数据都在 ERP 里。
P3 【王总】数据都在 ERP 里。
P4 【张三】好的收到。"""


def test_pid_to_did_mapping():
    r = clean(MATERIAL)
    assert [s.pid for s in r.segments] == ["P1", "P2", "P3", "P4"]
    assert [s.did for s in r.segments] == ["D001", "D002", "D003", "D004"]
    assert r.mapping[0] == {"pid": "P1", "did": "D001"}


def test_pii_scrubbed_and_raw_kept():
    r = clean(MATERIAL)
    seg = r.segments[1]
    assert "13812345678" not in seg.text          # 进 LLM 的文本已脱敏
    assert "[手机号已脱敏]" in seg.text
    assert "13812345678" in seg.raw_text          # 留痕保留原文
    assert seg.pii_scrubbed is True
    assert r.stats["PII脱敏段落数"] == 1


def test_adjacent_duplicate_merged():
    r = clean(MATERIAL)  # P3 连续出现两次，完全一致 → 合并
    assert r.stats["刷屏合并数"] == 1
    assert len(r.segments) == 4


def test_text_of_and_missing_did():
    r = clean(MATERIAL)
    assert "ERP" in r.text_of("D003")
    try:
        r.text_of("D999")
        assert False, "应抛 KeyError"
    except KeyError:
        pass


def test_too_short_material_flags():
    r = clean("P1 【王总】嗯。")
    assert len(r.cleaned_text()) < 30  # 由 workflow 的 MIN_MATERIAL_CHARS 兜底拒收
