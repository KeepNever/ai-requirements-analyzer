"""登录限速与安全属性测试：失败锁定、成功清零、SameSite。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

import auth


def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "USERS_PATH", tmp_path / "users.json")
    monkeypatch.setattr(auth, "SECRET_PATH", tmp_path / "session_secret.key")
    monkeypatch.setattr(auth, "_FAIL_LOG", {})
    return auth


def test_lockout_after_max_failures(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    auth.create_user("测试", "t1@x.cn", "password8", "pm")
    monkeypatch.setattr(auth, "LOGIN_MAX_FAILS", 3)
    monkeypatch.setattr(auth, "LOGIN_LOCK_SECONDS", 900.0)
    monkeypatch.setattr(auth, "LOGIN_FAIL_WINDOW", 600.0)
    for _ in range(3):
        with pytest.raises(auth.AuthError):
            auth.authenticate("t1@x.cn", "wrong-password")
    with pytest.raises(auth.AuthError, match="临时锁定"):
        auth.authenticate("t1@x.cn", "password8")   # 密码对也被锁


def test_success_clears_failures(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    auth.create_user("测试", "t2@x.cn", "password8", "pm")
    monkeypatch.setattr(auth, "LOGIN_MAX_FAILS", 3)
    monkeypatch.setattr(auth, "LOGIN_FAIL_WINDOW", 600.0)
    monkeypatch.setattr(auth, "LOGIN_LOCK_SECONDS", 900.0)
    for _ in range(2):
        with pytest.raises(auth.AuthError):
            auth.authenticate("t2@x.cn", "wrong-password")
    assert auth.authenticate("t2@x.cn", "password8")["email"] == "t2@x.cn"
    assert auth.login_locked("login:t2@x.cn") == 0
    for _ in range(2):   # 之前的失败已清零，再失败 2 次不应锁
        with pytest.raises(auth.AuthError, match="邮箱或密码不正确"):
            auth.authenticate("t2@x.cn", "wrong-password")


def test_nonexistent_email_not_recorded(tmp_path, monkeypatch):
    """不存在的邮箱不计失败——防恶意锁号；报错文案不变防枚举。"""
    _isolate(tmp_path, monkeypatch)
    monkeypatch.setattr(auth, "LOGIN_MAX_FAILS", 3)
    for _ in range(10):
        with pytest.raises(auth.AuthError, match="邮箱或密码不正确"):
            auth.authenticate("nobody@x.cn", "whatever")
    assert auth.login_locked("login:nobody@x.cn") == 0


def test_window_expiry(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    monkeypatch.setattr(auth, "LOGIN_MAX_FAILS", 3)
    monkeypatch.setattr(auth, "LOGIN_FAIL_WINDOW", 600.0)
    monkeypatch.setattr(auth, "LOGIN_LOCK_SECONDS", 900.0)
    for _ in range(3):
        with pytest.raises(auth.AuthError):
            auth.authenticate("x@x.cn", "bad")
    # 手动把失败时间挪出窗口 → 解锁
    auth._FAIL_LOG["login:x@x.cn"] = [0.0] * 3
    with pytest.raises(auth.AuthError, match="邮箱或密码不正确"):
        auth.authenticate("x@x.cn", "bad")


def test_session_cookie_strict():
    cookie = auth.session_cookie("tok.sig")
    assert "SameSite=Strict" in cookie and "HttpOnly" in cookie
    assert "SameSite=Strict" in auth.clear_session_cookie()
