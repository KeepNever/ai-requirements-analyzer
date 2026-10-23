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
    "priority": [{"id": "EN1", "content": "车间实时看板，良品率当天可见", "impact": 5,
                  "urgency": 4, "coverage": 3, "signal": 5, "weighted": 4.5, "tier": "高",
                  "reason": "核心诉求反复强调"}],
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


# ---------- 四审补充：三类异常占位 / 多文档 v1 / 合并段 / 展示改进 ----------

MERGED_MATERIAL = """## 原始材料

P1 【赵总】我们要做会员体系。
P2 【钱经理】会员等级分三档：普通、银卡、金卡。
P6 【钱经理】会员等级分三档：普通、银卡、金卡。
P3 【赵总】预算先按十万走。
"""


def test_export_fail_closed_placeholder_on_missing_doc(store_with_run, tmp_path):
    """四审阻断 1：引用指向不存在文档时，docx 显示占位而非原文。"""
    ana_bad = {"node1": {"explicit_needs": [
        {"id": "ENX", "content": "跨文档需求", "dids": ["D001"], "quote": ""}]},
        "node2": {}}
    bad_refs = [{"document_id": "不存在的文档-00000000-aa", "version_id": "v1"}]
    rdir = storage.create_run("导出测试项目", bad_refs, "v1.7.1", "deepseek-chat")
    storage.save_run_outputs(rdir, ana_bad, "# x", {}, None)
    out = tmp_path / "bad.docx"
    export_docx.export_run_docx("导出测试项目", rdir.name, str(out))
    from docx import Document
    text = _all_text(Document(str(out)))
    assert "不可定位" in text   # 失败关闭：unresolved 显式呈现
    # 需求清单表的 ENX 描述为 analysis 元数据（非引用原文），正常呈现


def test_export_multidoc_both_v1_correct_attribution(store_with_run, tmp_path):
    """四审阻断 2：两份文档都有 v1，导出引用仍指向正确原文。"""
    doc2 = storage.add_document("导出测试项目", "第二家客户.md",
                                "## 原始材料\n\nP1 【刘总】我们要做会员体系，预算十万。\n")
    refs2 = [{"document_id": doc2["document"]["document_id"], "version_id": "v1"},
             store_with_run["refs"][0]]
    ana_md = {"node1": {"explicit_needs": [
        {"id": "EN1", "content": "会员体系", "dids": ["D001"], "quote": ""}],
        "competitor_signals": []}, "node2": {}}
    rdir = storage.create_run("导出测试项目", refs2, "v1.7.1", "deepseek-chat")
    storage.save_run_outputs(rdir, ana_md, "# md",
                             storage.build_citations(ana_md, "导出测试项目", refs2), None)
    out = tmp_path / "md.docx"
    export_docx.export_run_docx("导出测试项目", rdir.name, str(out))
    from docx import Document
    text = _all_text(Document(str(out)))
    assert "我们要做会员体系" in text                       # 正确文档的原文
    assert "主要做汽车冲压件" not in text                   # 另一文档内容不串档


def test_export_merged_paragraph_per_source(store_with_run, tmp_path):
    """四审阻断 3：合并段引用逐段对应原文（P2 与 P6 各自的原文，非复制整段）。"""
    doc3 = storage.add_document("导出测试项目", "会员讨论.md",
                                "## 原始材料\n\n" + MERGED_MATERIAL.split("## 原始材料\n\n")[1])
    refs3 = [{"document_id": doc3["document"]["document_id"], "version_id": "v1"}]
    ana_m = {"node1": {"explicit_needs": [
        {"id": "EN1", "content": "会员体系与分档", "dids": ["D002"], "quote": ""}],
        "competitor_signals": []}, "node2": {}}
    rdir = storage.create_run("导出测试项目", refs3, "v1.7.1", "deepseek-chat")
    storage.save_run_outputs(rdir, ana_m, "# m",
                             storage.build_citations(ana_m, "导出测试项目", refs3), None)
    out = tmp_path / "m.docx"
    export_docx.export_run_docx("导出测试项目", rdir.name, str(out))
    from docx import Document
    text = _all_text(Document(str(out)))
    assert "【P2】" in text and "【P6】" in text            # 两个物理来源各自出现
    assert "会员等级分三档" in text                          # 内容正确（脱敏文本）


def test_export_friendly_file_name_and_priority_tier(store_with_run, tmp_path):
    """四审改进 1/2：file_name 主展示；加权总分与档位展示（不重算）。"""
    out = tmp_path / "e2.docx"
    export_docx.export_run_docx("导出测试项目", store_with_run["run_id"], str(out))
    from docx import Document
    text = _all_text(Document(str(out)))
    assert "客户会议.md（v1）" in text                       # file_name 主展示
    lines = [ln for ln in text.splitlines() if "材料与版本" in ln or "客户会议.md（" in ln]
    assert all("客户会议.md-10" not in ln for ln in lines)   # 元信息表不含长 document_id
    assert "加权总分" in text and "档位" in text             # 优先级表头


def test_export_blocks_not_done_run(store_with_run):
    """四审改进 4：非 done 运行阻止导出。"""
    refs = store_with_run["refs"]
    r2 = storage.create_run("导出测试项目", refs, "v1.7.1", "deepseek-chat")   # status=running
    with pytest.raises(ValueError, match="不允许导出"):
        export_docx.export_run_docx("导出测试项目", r2.name)


def test_export_unknown_prompt_version_displayed(store_with_run, tmp_path):
    """四审改进 4：prompt_version=unknown 显示「未记录（历史数据缺失）」。"""
    refs = store_with_run["refs"]
    rdir = storage.create_run("导出测试项目", refs, "unknown", "deepseek-chat")
    storage.save_run_outputs(rdir, ANALYSIS, "# x",
                             storage.build_citations(ANALYSIS, "导出测试项目", refs), None)
    out = tmp_path / "u.docx"
    export_docx.export_run_docx("导出测试项目", rdir.name, str(out))
    from docx import Document
    text = _all_text(Document(str(out)))
    assert "未记录（历史数据缺失）" in text


# ---------- 四审补充：三类异常占位 / 多文档 v1 / 合并段 / 展示改进 ----------

MERGED_MATERIAL = """## 原始材料

P1 【赵总】我们要做会员体系。
P2 【钱经理】会员等级分三档：普通、银卡、金卡。
P6 【钱经理】会员等级分三档：普通、银卡、金卡。
P3 【赵总】预算先按十万走。
"""


def test_export_fail_closed_placeholder_on_missing_doc(store_with_run, tmp_path):
    """四审阻断 1：引用指向不存在文档时，docx 显示占位而非原文。"""
    ana_bad = {"node1": {"explicit_needs": [
        {"id": "ENX", "content": "跨文档需求", "dids": ["D001"], "quote": ""}]},
        "node2": {}}
    bad_refs = [{"document_id": "不存在的文档-00000000-aa", "version_id": "v1"}]
    rdir = storage.create_run("导出测试项目", bad_refs, "v1.7.1", "deepseek-chat")
    storage.save_run_outputs(rdir, ana_bad, "# x", {}, None)
    out = tmp_path / "bad.docx"
    export_docx.export_run_docx("导出测试项目", rdir.name, str(out))
    from docx import Document
    text = _all_text(Document(str(out)))
    assert "不可定位" in text   # 失败关闭：unresolved 显式呈现
    # 需求清单表的 ENX 描述为 analysis 元数据（非引用原文），正常呈现


def test_export_multidoc_both_v1_correct_attribution(store_with_run, tmp_path):
    """四审阻断 2：两份文档都有 v1，导出引用仍指向正确原文。"""
    doc2 = storage.add_document("导出测试项目", "第二家客户.md",
                                "## 原始材料\n\nP1 【刘总】我们要做会员体系，预算十万。\n")
    refs2 = [{"document_id": doc2["document"]["document_id"], "version_id": "v1"},
             store_with_run["refs"][0]]
    ana_md = {"node1": {"explicit_needs": [
        {"id": "EN1", "content": "会员体系", "dids": ["D001"], "quote": ""}],
        "competitor_signals": []}, "node2": {}}
    rdir = storage.create_run("导出测试项目", refs2, "v1.7.1", "deepseek-chat")
    storage.save_run_outputs(rdir, ana_md, "# md",
                             storage.build_citations(ana_md, "导出测试项目", refs2), None)
    out = tmp_path / "md.docx"
    export_docx.export_run_docx("导出测试项目", rdir.name, str(out))
    from docx import Document
    text = _all_text(Document(str(out)))
    assert "我们要做会员体系" in text                       # 正确文档的原文
    assert "主要做汽车冲压件" not in text                   # 另一文档内容不串档


def test_export_merged_paragraph_per_source(store_with_run, tmp_path):
    """四审阻断 3：合并段引用逐段对应原文（P2 与 P6 各自的原文，非复制整段）。"""
    doc3 = storage.add_document("导出测试项目", "会员讨论.md",
                                "## 原始材料\n\n" + MERGED_MATERIAL.split("## 原始材料\n\n")[1])
    refs3 = [{"document_id": doc3["document"]["document_id"], "version_id": "v1"}]
    ana_m = {"node1": {"explicit_needs": [
        {"id": "EN1", "content": "会员体系与分档", "dids": ["D002"], "quote": ""}],
        "competitor_signals": []}, "node2": {}}
    rdir = storage.create_run("导出测试项目", refs3, "v1.7.1", "deepseek-chat")
    storage.save_run_outputs(rdir, ana_m, "# m",
                             storage.build_citations(ana_m, "导出测试项目", refs3), None)
    out = tmp_path / "m.docx"
    export_docx.export_run_docx("导出测试项目", rdir.name, str(out))
    from docx import Document
    text = _all_text(Document(str(out)))
    assert "【P2】" in text and "【P6】" in text            # 两个物理来源各自出现
    assert "会员等级分三档" in text                          # 内容正确（脱敏文本）


def test_export_friendly_file_name_and_priority_tier(store_with_run, tmp_path):
    """四审改进 1/2：file_name 主展示；加权总分与档位展示（不重算）。"""
    out = tmp_path / "e2.docx"
    export_docx.export_run_docx("导出测试项目", store_with_run["run_id"], str(out))
    from docx import Document
    text = _all_text(Document(str(out)))
    assert "客户会议.md（v1）" in text                       # file_name 主展示
    lines = [ln for ln in text.splitlines() if "材料与版本" in ln or "客户会议.md（" in ln]
    assert all("客户会议.md-10" not in ln for ln in lines)   # 元信息表不含长 document_id
    assert "加权总分" in text and "档位" in text             # 优先级表头


def test_export_blocks_not_done_run(store_with_run):
    """四审改进 4：非 done 运行阻止导出。"""
    refs = store_with_run["refs"]
    r2 = storage.create_run("导出测试项目", refs, "v1.7.1", "deepseek-chat")   # status=running
    with pytest.raises(ValueError, match="不允许导出"):
        export_docx.export_run_docx("导出测试项目", r2.name)


def test_export_unknown_prompt_version_displayed(store_with_run, tmp_path):
    """四审改进 4：prompt_version=unknown 显示「未记录（历史数据缺失）」。"""
    refs = store_with_run["refs"]
    rdir = storage.create_run("导出测试项目", refs, "unknown", "deepseek-chat")
    storage.save_run_outputs(rdir, ANALYSIS, "# x",
                             storage.build_citations(ANALYSIS, "导出测试项目", refs), None)
    out = tmp_path / "u.docx"
    export_docx.export_run_docx("导出测试项目", rdir.name, str(out))
    from docx import Document
    text = _all_text(Document(str(out)))
    assert "未记录（历史数据缺失）" in text


def test_export_partially_missing_version_recorded_not_silent(store_with_run, tmp_path):
    """六审阻断 4：部分版本不可定位时显式记录 unresolved，不静默过滤成看似完整。"""
    ana = {"node1": {"explicit_needs": [
        {"id": "EN1", "content": "会员体系", "dids": ["D001"], "quote": ""}],
        "competitor_signals": []}, "node2": {}}
    refs_mixed = [{"document_id": "幽灵文档-00000000-aa", "version_id": "v1"},
                  store_with_run["refs"][0]]
    rdir = storage.create_run("导出测试项目", refs_mixed, "v1.7.1", "deepseek-chat")
    storage.save_run_outputs(rdir, ana, "# x", {}, None)
    out = tmp_path / "mixed.docx"
    export_docx.export_run_docx("导出测试项目", rdir.name, str(out))
    from docx import Document
    text = _all_text(Document(str(out)))
    assert "不可定位" in text                      # 缺失来源显式呈现
    assert "幽灵文档-00000000-aa" in text          # 指明是哪个来源缺失


def test_export_multidoc_same_pd_codes_no_crosstalk(store_with_run, tmp_path):
    """六审阻断 4：两文档相同 P/D 编号（都从 D001/P1 开始），引用不串档。"""
    doc2 = storage.add_document("导出测试项目", "第二家客户.md",
                                "## 原始材料\n\nP1 【刘总】我们要做会员积分体系。\n")
    refs2 = [{"document_id": doc2["document"]["document_id"], "version_id": "v1"},
             store_with_run["refs"][0]]
    ana_md = {"node1": {"explicit_needs": [
        {"id": "EN1", "content": "会员积分体系", "dids": ["D001"], "quote": ""},
        {"id": "EN2", "content": "看板当天可见", "dids": ["D003"], "quote": ""}],
        "competitor_signals": []}, "node2": {}}
    rdir = storage.create_run("导出测试项目", refs2, "v1.7.1", "deepseek-chat")
    storage.save_run_outputs(rdir, ana_md, "# md",
                             storage.build_citations(ana_md, "导出测试项目", refs2), None)
    out = tmp_path / "md2.docx"
    export_docx.export_run_docx("导出测试项目", rdir.name, str(out))
    from docx import Document
    d = Document(str(out))
    text = _all_text(d)
    assert "会员积分体系" in text and "主要做汽车冲压件" not in text
    # 引用逐段对应：EN1（D001）取第二家客户的原文，EN2（D003）取第一家客户的原文
    assert "我们要做会员积分体系" in text
    assert "【P3】" in text and "想要个实时看板" in text   # 第一家客户 D003 的原文（P3）


def test_export_partially_missing_version_recorded_not_silent(store_with_run, tmp_path):
    """六审阻断 4：部分版本不可定位时显式记录 unresolved，不静默过滤成看似完整。"""
    ana = {"node1": {"explicit_needs": [
        {"id": "EN1", "content": "会员体系", "dids": ["D001"], "quote": ""}],
        "competitor_signals": []}, "node2": {}}
    refs_mixed = [{"document_id": "幽灵文档-00000000-aa", "version_id": "v1"},
                  store_with_run["refs"][0]]
    rdir = storage.create_run("导出测试项目", refs_mixed, "v1.7.1", "deepseek-chat")
    storage.save_run_outputs(rdir, ana, "# x", {}, None)
    out = tmp_path / "mixed.docx"
    export_docx.export_run_docx("导出测试项目", rdir.name, str(out))
    from docx import Document
    text = _all_text(Document(str(out)))
    assert "不可定位" in text                      # 缺失来源显式呈现
    assert "幽灵文档-00000000-aa" in text          # 指明是哪个来源缺失


def test_export_multidoc_same_pd_codes_no_crosstalk(store_with_run, tmp_path):
    """六审阻断 4：两文档相同 P/D 编号（都从 D001/P1 开始），引用不串档。"""
    doc2 = storage.add_document("导出测试项目", "第二家客户.md",
                                "## 原始材料\n\nP1 【刘总】我们要做会员积分体系。\n")
    refs2 = [{"document_id": doc2["document"]["document_id"], "version_id": "v1"},
             store_with_run["refs"][0]]
    ana_md = {"node1": {"explicit_needs": [
        {"id": "EN1", "content": "会员积分体系", "dids": ["D001"], "quote": ""},
        {"id": "EN2", "content": "看板当天可见", "dids": ["D003"], "quote": ""}],
        "competitor_signals": []}, "node2": {}}
    rdir = storage.create_run("导出测试项目", refs2, "v1.7.1", "deepseek-chat")
    storage.save_run_outputs(rdir, ana_md, "# md",
                             storage.build_citations(ana_md, "导出测试项目", refs2), None)
    out = tmp_path / "md2.docx"
    export_docx.export_run_docx("导出测试项目", rdir.name, str(out))
    from docx import Document
    d = Document(str(out))
    text = _all_text(d)
    assert "会员积分体系" in text and "主要做汽车冲压件" not in text
    # 引用逐段对应：EN1（D001）取第二家客户的原文，EN2（D003）取第一家客户的原文
    assert "我们要做会员积分体系" in text
    assert "【P3】" in text and "想要个实时看板" in text   # 第一家客户 D003 的原文（P3）
