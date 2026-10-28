"""项目级访问控制测试：受限模型、可见性判定、授权名单。"""
from __future__ import annotations

import access


def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(access, "DATA_PATH", tmp_path / "project_access.json")
    return access


def test_default_all_visible(tmp_path, monkeypatch):
    _isolated(tmp_path, monkeypatch)
    assert access.visible_to("p1", "sales", "u1") is True
    assert access.is_restricted("p1") is False


def test_restricted_hidden_without_grant(tmp_path, monkeypatch):
    _isolated(tmp_path, monkeypatch)
    access.set_restricted("p1", True, ["u9"])
    assert access.visible_to("p1", "sales", "u1") is False
    assert access.visible_to("p1", "presale", "u1") is False
    assert access.visible_to("p1", "sales", "u9") is True      # 被授权成员可见
    assert access.visible_to("p1", "pm", "any") is True        # 产品经理恒可见
    assert access.visible_to("p1", "admin", "any") is True     # 管理员恒可见


def test_unrestrict_clears_grants(tmp_path, monkeypatch):
    _isolated(tmp_path, monkeypatch)
    access.set_restricted("p1", True, ["u1"])
    access.set_restricted("p1", False)
    assert access.visible_to("p1", "sales", "u9") is True
    assert access.is_restricted("p1") is False
    assert access.access_overview() == []


def test_grant_update_keeps_restricted(tmp_path, monkeypatch):
    _isolated(tmp_path, monkeypatch)
    access.set_restricted("p1", True, ["u1"])
    access.set_restricted("p1", True, ["u1", "u2"])
    assert access.visible_to("p1", "sales", "u2") is True
    assert access.access_overview()[0]["grants"] == ["u1", "u2"]
