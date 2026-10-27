"""角色功能视图测试：导航条按权限矩阵过滤，销售追问清单页（对齐原型矩阵）。"""
from __future__ import annotations

import json

import web_demo


def _make_case(tmp_path, monkeypatch, pend):
    root = tmp_path
    out = root / "output" / "case_x"
    out.mkdir(parents=True)
    (out / "analysis.json").write_text(
        json.dumps({"node2": {"pending_confirmations": pend}}, ensure_ascii=False),
        encoding="utf-8")
    monkeypatch.setattr(web_demo, "PROJECT_ROOT", root)


def test_questions_page_renders_pending_confirmations(tmp_path, monkeypatch):
    _make_case(tmp_path, monkeypatch, [
        {"gap": "预算范围未知", "level": "决定性", "impact": "无法判断定制边界",
         "question": "本期看板的预算范围大概是多少？"}])
    body, code = web_demo.build_questions_page("case_x")
    assert code == 200
    assert "预算范围未知" in body
    assert "本期看板的预算范围大概是多少？" in body
    assert "决定性 1 条" in body
    # 销售视图不应暴露完整报告内容
    assert "priority" not in body.lower() or "pending_confirmations" not in body


def test_questions_page_empty_and_missing_case(tmp_path, monkeypatch):
    _make_case(tmp_path, monkeypatch, [])
    body, code = web_demo.build_questions_page("case_x")
    assert code == 200 and "没有待确认" in body
    body2, code2 = web_demo.build_questions_page("不存在")
    assert code2 == 404 and "还没有生成分析" in body2


def test_role_nav_filters_links_by_matrix():
    """对齐原型权限矩阵：导航条只显示当前角色可用的入口。"""
    admin = web_demo.role_nav({"name": "林然", "role": "admin"})
    assert "成员权限" in admin and "评测看板" in admin
    assert "AI 问答" not in admin                      # 管理员不参与业务问答

    pm = web_demo.role_nav({"name": "林然", "role": "pm"})
    assert all(k in pm for k in ("工作台", "资料库", "AI 问答", "评测看板"))
    assert "成员权限" not in pm

    sales = web_demo.role_nav({"name": "陈屿", "role": "sales"})
    assert "AI 问答" in sales and "评测看板" not in sales and "成员权限" not in sales

    presale = web_demo.role_nav({"name": "周宁", "role": "presale"})
    assert "AI 问答" in presale and "评测看板" not in presale and "成员权限" not in presale

    assert "退出登录" in sales and "陈屿" in sales


def test_legacy_rolenav_links_point_into_spa():
    """旧页面导航条必须指回 SPA hash 路由，避免跳出统一界面。"""
    links = {a: href for a, href, _ in web_demo.NAV_LINKS}
    assert links["chat"] == "/#chat"
    assert links["members"] == "/#members"
    assert links["dashboard"] == "/#eval"
    assert links["home"].startswith("/#")
