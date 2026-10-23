"""Task 6 Word 报告导出测试（docs/v2 Task6 验收标准）。

覆盖：run_id 绑定、章节完整、敏感信息不泄露、无效引用不伪装、
历史运行一致性、原有测试不回归。
"""
import json
import sys
from io import BytesIO
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import storage
import retrieval
import export_docx

MATERIAL = """## 原始材料

P1 【王总】我们厂大概六百人，主要做汽车冲压件。
P2 【李工】我的电话是13812345678，有事打这个。
P3 【王总】想要个实时看板，良品率当天可见。
P4 【李工】ERP是金蝶K3，数据不好接。
"""

ANALYSIS = {
    "node1": {"customer_background": "汽车冲压件制造厂，约六百人。",
              "business_goals": ["提升良品率监控时效"],
              "explicit_needs": [
                  {"id": "EN1", "content": "车间实时看板，良品率当天可见", "dids": ["D003"],
                   "quote": "想要个实时看板"},
                  {"id": "EN2", "content": "联系方式登记与回访机制", "dids": ["D002"],
                   "quote": "我的电话是13812345678"}],
              "competitor_signals": []},
    "node2": {
        "potential_needs": [{"id": "IN1", "content": "移动端查看", "basis_dids": ["D003"],
                             "reasoning": "车间场景推断", "confidence": "中"}],
        "pending_confirmations": [{"gap": "设备接口可行性", "level": "决定性",
                                   "impact": "决定方案可行性", "question": "能否现场勘查？"}],
        "capability_match": [{"target": "EN1 车间实时看板", "verdict": "匹配",
                              "reason": "实时刷新能力覆盖"}],
        "priority_dimensions": [{"id": "EN1", "impact": 5, "urgency": 4, "coverage": 3,
                                 "signal": 5, "reason": "核心诉求反复强调"}],
    },
    "priority": {"top": "EN1"},
    "meta": {},
}


def _all_text(doc):
    """段落 + 全部表格文本（docx 内容校验用）。"""
    parts = [p.text for p in doc.paragraphs]
    for t in doc.tables:
        for row in t.rows:
            for c in row.cells:
                parts.append(c.text)
    return "\n".join(parts)


def _all_text(doc):
    """段落 + 全部表格文本（docx 内容校验用）。"""
    parts = [p.text for p in doc.paragraphs]
    for t in doc.tables:
        for row in t.rows:
            for c in row.cells:
                parts.append(c.text)
    return "\n".join(parts)


@pytest.fixture()
def store_with_run(tmp_path):
    storage.set_base(tmp_path)
    storage.create_project("导出测试项目")
    doc = storage.add_document("导出测试项目", "客户会议.md", MATERIAL)
    refs = [{"document_id": doc["document"]["document_id"], "version_id": "v1"}]
    rdir = storage.create_run("导出测试项目", refs, "v1.7.1", "deepseek-chat")
    cits = storage.build_citations(ANALYSIS, "导出测试项目", refs)
    storage.save_run_outputs(rdir, ANALYSIS, "# 测试报告", cits, {"调用次数": 2})
    yield {"refs": refs, "run_id": rdir.name, "doc_id": refs[0]["document_id"]}
    storage.set_base(storage.ROOT)


# ---------- 核心导出 ----------

def test_export_binds_exact_run(store_with_run, tmp_path):
    """run_id 绑定：导出指定历史运行，元信息与该运行一致。"""
    out = tmp_path / "export.docx"
    export_docx.export_run_docx("导出测试项目", store_with_run["run_id"], str(out))
    assert out.exists() and out.stat().st_size > 1000
    # 内容校验：docx 可重新打开且含运行元信息
    from docx import Document
    d = Document(str(out))
    text = _all_text(d)
    assert store_with_run["run_id"] in text
    assert "导出测试项目" in text


def test_export_sections_complete(store_with_run, tmp_path):
    """七个必需章节齐全；有数据的章节有内容，无数据的明确标注。"""
    out = tmp_path / "e.docx"
    export_docx.export_run_docx("导出测试项目", store_with_run["run_id"], str(out))
    from docx import Document
    d = Document(str(out))
    text = _all_text(d)
    for sec in ["一、客户背景与业务目标", "二、显性需求清单", "三、需求优先级与维度评分",
                "四、产品能力匹配情况", "五、潜在需求", "六、待确认事项与建议追问",
                "七、关键结论的原文引用", "八、未解析引用"]:
        assert sec in text, f"缺章节：{sec}"
    # 有数据的章节内容存在
    assert "汽车冲压件制造厂" in text
    assert "车间实时看板" in text
    assert "设备接口可行性" in text          # 待确认事项
    # 表格数据
    assert len(d.tables) >= 6


def test_export_citations_from_existing_structure(store_with_run, tmp_path):
    """引用通过已有 Citation 结构生成（build_citations → 脱敏 → docx），带段落定位。"""
    out = tmp_path / "e.docx"
    export_docx.export_run_docx("导出测试项目", store_with_run["run_id"], str(out))
    from docx import Document
    d = Document(str(out))
    text = _all_text(d)
    assert "【P3】" in text                  # 原文段落定位
    assert "想要个实时看板" in text          # 脱敏后原文内容
    assert "结论 EN1" in text


# ---------- 敏感信息 ----------

def test_export_no_raw_pii(store_with_run, tmp_path):
    """敏感信息不泄露：raw_text 独有的手机号不出现在 docx（强制约束 4）。"""
    out = tmp_path / "e.docx"
    export_docx.export_run_docx("导出测试项目", store_with_run["run_id"], str(out))
    from docx import Document
    d = Document(str(out))
    all_text = _all_text(d)
    assert "13812345678" not in all_text     # 原文手机号（P2）
    assert "[手机号已脱敏]" in all_text       # 脱敏占位符正常呈现


# ---------- 无效引用不伪装 ----------

def test_unresolved_explicitly_marked(store_with_run, tmp_path):
    """无法可靠取得的引用明确标记（unresolved 原样呈现），不伪装。"""
    refs = store_with_run["refs"]
    # 构造一个含无效引用的分析（dids 指向不存在段落）+ 无结构化 citations 的迁移式 run
    bad_analysis = {"node1": {"explicit_needs": [
        {"id": "EN9", "content": "无效引用需求", "dids": ["D999"], "quote": ""}]},
        "node2": {}}
    rdir = storage.create_run("导出测试项目", refs, "v1.7.1", "deepseek-chat")
    storage.save_run_outputs(rdir, bad_analysis, "# 坏引用报告", {}, None)
    out = tmp_path / "bad.docx"
    export_docx.export_run_docx("导出测试项目", rdir.name, str(out))
    from docx import Document
    d = Document(str(out))
    text = _all_text(d)
    assert "不生成虚假证据" in text or "无法从现有数据可靠定位" in text
    assert "D999" not in text or "引用编号不存在" in text   # D999 不作为有效证据出现


def test_export_nonexistent_run_404(store_with_run):
    """不存在的 run_id 明确报错，不静默导出其他运行。"""
    with pytest.raises(ValueError, match="运行不存在"):
        export_docx.export_run_docx("导出测试项目", "run2099_不存在_0000")


# ---------- 历史运行一致性 ----------

def test_historical_run_export_matches_its_own_data(store_with_run, tmp_path, monkeypatch):
    """两个不同 run 导出各自内容：不被「最新运行」替换（纪律 2）。"""
    refs = store_with_run["refs"]
    run1 = store_with_run["run_id"]
    # 再建一个内容不同的 run
    ana2 = json.loads(json.dumps(ANALYSIS, ensure_ascii=False))
    ana2["node1"]["explicit_needs"][0]["content"] = "第二版需求：移动端看板"
    ana2["node2"]["capability_match"][0]["target"] = "EN1 第二版：移动端看板覆盖"
    ana2["node2"]["priority_dimensions"][0]["id"] = "EN1v2"
    ana2["node2"]["potential_needs"][0]["content"] = "第二版推断：离线缓存"
    ana2["node2"]["capability_match"][0]["target"] = "EN1 第二版：移动端看板覆盖"
    ana2["node2"]["priority_dimensions"][0]["id"] = "EN1v2"
    ana2["node2"]["potential_needs"][0]["content"] = "第二版推断：离线缓存"
    rdir2 = storage.create_run("导出测试项目", refs, "v1.7.1", "deepseek-chat")
    storage.save_run_outputs(rdir2, ana2, "# v2 报告",
                             storage.build_citations(ana2, "导出测试项目", refs), None)
    from docx import Document
    o1 = tmp_path / "r1.docx"
    o2 = tmp_path / "r2.docx"
    export_docx.export_run_docx("导出测试项目", run1, str(o1))
    export_docx.export_run_docx("导出测试项目", rdir2.name, str(o2))
    t1 = _all_text(Document(str(o1)))
    t2 = _all_text(Document(str(o2)))
    assert "车间实时看板" in t1 and "车间实时看板" not in t2
    assert "第二版需求" in t2 and "第二版需求" not in t1
