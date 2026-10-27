"""账号与会话底座测试：密码、首次注册、角色边界、签名会话。"""
from __future__ import annotations

import pytest

import auth


@pytest.fixture()
def auth_env(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "USERS_PATH", tmp_path / "users.json")
    monkeypatch.setattr(auth, "SECRET_PATH", tmp_path / "session_secret.key")
    return auth


def test_first_registration_becomes_admin_and_blocks_later_open_registration(auth_env):
    admin = auth.register_first_admin("林然", "PM@Demo.cn", "demo12345")
    assert admin["role"] == "admin"
    assert admin["email"] == "pm@demo.cn"
    assert auth.count_users() == 1
    with pytest.raises(auth.AuthError, match="系统已初始化"):
        auth.register_first_admin("陈屿", "sales@demo.cn", "demo12345")


def test_password_hash_and_authentication(auth_env):
    created = auth.create_user("陈屿", "sales@demo.cn", "demo12345", "sales")
    assert "password_hash" not in created and "salt" not in created
    got = auth.authenticate("Sales@Demo.cn", "demo12345")
    assert got["role"] == "sales"
    with pytest.raises(auth.AuthError, match="邮箱或密码不正确"):
        auth.authenticate("sales@demo.cn", "wrong-password")


def test_duplicate_email_is_rejected(auth_env):
    auth.create_user("陈屿", "sales@demo.cn", "demo12345", "sales")
    with pytest.raises(auth.AuthError, match="已被使用"):
        auth.create_user("陈屿二号", "SALES@demo.cn", "demo12345", "pm")


def test_last_admin_cannot_be_demoted(auth_env):
    auth.register_first_admin("林然", "pm@demo.cn", "demo12345")
    sales = auth.create_user("陈屿", "sales@demo.cn", "demo12345", "sales")
    auth.update_role(sales["email"], "pm")  # 正常调整
    with pytest.raises(auth.AuthError, match="至少需要保留一名管理员"):
        auth.update_role("pm@demo.cn", "sales")


def test_signed_session_roundtrip_and_rejects_tampering(auth_env):
    admin = auth.register_first_admin("林然", "pm@demo.cn", "demo12345")
    token = auth.make_session(admin["user_id"])
    cookie = auth.session_cookie(token)
    assert cookie.startswith("session=") and "HttpOnly" in cookie
    assert auth.user_from_cookie(cookie)["user_id"] == admin["user_id"]
    bad = token[:-1] + ("0" if token[-1] != "0" else "1")
    assert auth.user_from_cookie(f"session={bad}") is None
    assert auth.user_from_cookie(None) is None


def test_fixed_role_permission_matrix(auth_env):
    assert auth.can("sales", "run_analysis")
    assert not auth.can("sales", "report")
    assert auth.can("presale", "report") and auth.can("presale", "ask")
    assert not auth.can("presale", "run_analysis")
    assert auth.can("admin", "members")
    assert not auth.can("admin", "run_analysis")
    assert auth.can("pm", "run_analysis") and auth.can("pm", "report")
