"""账号与会话底座（标准库实现，零第三方依赖）。

设计口径：
- 单工作空间。第一次注册创建管理员；之后新增成员由管理员在成员页添加或邀请。
- 四类固定角色：admin / pm / sales / presale，不做自定义 RBAC。
- 密码使用 PBKDF2-HMAC-SHA256 加盐哈希，绝不保存明文。
- 会话使用 HMAC 签名 Cookie：`session=<payload>.<sig>`，Cookie 不落盘。
- 权限矩阵是功能边界的唯一依据，路由层统一执行。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
import uuid
from pathlib import Path
from urllib.parse import quote

PROJECT_ROOT = Path(__file__).resolve().parents[1]
# 测试 / 隔离部署可通过环境变量重定向账号数据；默认落在项目 data/ 下（已 gitignore）
DATA_DIR = Path(os.environ.get("AUTH_DATA_DIR", PROJECT_ROOT / "data"))
USERS_PATH = Path(os.environ.get("AUTH_USERS_PATH", DATA_DIR / "users.json"))
SECRET_PATH = Path(os.environ.get("AUTH_SECRET_PATH", DATA_DIR / "session_secret.key"))

ROLES = {"admin": "管理员", "pm": "产品经理", "sales": "销售", "presale": "售前"}
ALL_ROLES = set(ROLES)

# 功能级权限矩阵（role → 允许动作）。路由层只认这里的口径。
PAGE_ACCESS: dict[str, set[str]] = {
    "home": ALL_ROLES,
    "library": ALL_ROLES,
    "chat": {"pm", "sales", "presale"},
    "dashboard": {"admin", "pm"},
    "members": {"admin"},
    # 完整分析报告 / Pipeline 视图：销售暂只开放追问专用页面
    "report": {"pm", "admin", "presale"},
    # 追问清单是完整报告的销售子集视图，四角色都可看
    "questions": ALL_ROLES,
    "export_docx": {"pm", "admin", "presale"},
    "case": ALL_ROLES,
    # 材料清洗与完整分析属于业务提交动作
    "run_analysis": {"pm", "sales"},
    "ask": {"pm", "sales", "presale"},
    "manage_users": {"admin"},
}


class AuthError(ValueError):
    """账号相关业务错误，Web 层转成 4xx。"""


def _norm_email(email: str) -> str:
    email = (email or "").strip().lower()
    if "@" not in email or email.startswith("@") or email.endswith("@"):
        raise AuthError("邮箱格式不正确")
    return email


def _norm_role(role: str) -> str:
    if role not in ROLES:
        raise AuthError("角色不合法")
    return role


def _hash_password(password: str, salt: str) -> str:
    if len(password or "") < 8:
        raise AuthError("密码至少需要 8 个字符")
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt), 120_000
    ).hex()


def load_users() -> dict:
    if not USERS_PATH.exists():
        return {"users": []}
    try:
        data = json.loads(USERS_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:  # pragma: no cover - 文件损坏兜底
        raise AuthError(f"账号文件读取失败：{e}") from e
    if not isinstance(data, dict) or not isinstance(data.get("users"), list):
        raise AuthError("账号文件结构不合法")
    return data


def save_users(data: dict) -> None:
    USERS_PATH.parent.mkdir(parents=True, exist_ok=True)
    USERS_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def to_public(user: dict) -> dict:
    """返回可放进页面 / 会话的用户信息，剔除任何敏感字段。"""
    return {
        "user_id": user["user_id"],
        "name": user.get("name", ""),
        "email": user.get("email", ""),
        "role": user.get("role", ""),
        "role_name": ROLES.get(user.get("role", ""), user.get("role", "")),
        "org": user.get("org", ""),
        "created_at": user.get("created_at", ""),
    }


def count_users() -> int:
    return len(load_users()["users"])


def _find_user(users: list, email: str) -> dict | None:
    return next((u for u in users if u.get("email", "").lower() == email.lower()), None)


def create_user(name: str, email: str, password: str, role: str,
                *, only_first_admin: bool = False) -> dict:
    """创建成员。`only_first_admin=True` 时仅允许系统尚无账号的首次注册。"""
    name = (name or "").strip()
    email = _norm_email(email)
    role = _norm_role(role)
    if not name:
        raise AuthError("姓名不能为空")
    data = load_users()
    users = data["users"]
    if only_first_admin and users:
        raise AuthError("系统已初始化，请使用管理员邀请或登录")
    if _find_user(users, email):
        raise AuthError("该邮箱已被使用")
    salt = secrets.token_hex(16)
    user = {
        "user_id": uuid.uuid4().hex,
        "name": name,
        "email": email,
        "role": role,
        "salt": salt,
        "password_hash": _hash_password(password, salt),
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    users.append(user)
    save_users(data)
    return to_public(user)


def register_first_admin(name: str, email: str, password: str, org: str = "") -> dict:
    user = create_user(name, email, password, "admin", only_first_admin=True)
    org = (org or "").strip()
    if org:
        data = load_users()
        stored = _find_user(data["users"], email)
        if stored:
            stored["org"] = org
            save_users(data)
            return to_public(stored)
    return user


def authenticate(email: str, password: str) -> dict:
    email = _norm_email(email)
    user = _find_user(load_users()["users"], email)
    if not user:
        raise AuthError("邮箱或密码不正确")
    try:
        got = _hash_password(password, user.get("salt", ""))
    except AuthError:
        raise AuthError("邮箱或密码不正确") from None
    if not hmac.compare_digest(got, user.get("password_hash", "")):
        raise AuthError("邮箱或密码不正确")
    return to_public(user)


def update_role(email: str, role: str) -> dict:
    """管理员调整成员角色。系统至少要保留一名管理员。"""
    role = _norm_role(role)
    data = load_users()
    users = data["users"]
    user = _find_user(users, email)
    if not user:
        raise AuthError("成员不存在")
    old = user["role"]
    user["role"] = role
    if old == "admin" and role != "admin" and not any(u["role"] == "admin" for u in users):
        user["role"] = old
        raise AuthError("至少需要保留一名管理员")
    save_users(data)
    return to_public(user)


def list_users_public() -> list[dict]:
    return [to_public(u) for u in load_users()["users"]]


# ── HMAC 签名会话 ───────────────────────────────────────────
_SESSION_TTL = 7 * 24 * 3600


def _secret() -> bytes:
    if not SECRET_PATH.exists():
        SECRET_PATH.parent.mkdir(parents=True, exist_ok=True)
        SECRET_PATH.write_text(secrets.token_hex(32), encoding="utf-8")
    return bytes.fromhex(SECRET_PATH.read_text(encoding="utf-8").strip())


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64d(val: str) -> bytes:
    return base64.urlsafe_b64decode(val + "=" * (-len(val) % 4))


def make_session(user_id: str, ttl: int = _SESSION_TTL) -> str:
    payload = _b64e(json.dumps(
        {"uid": user_id, "exp": int(time.time()) + ttl}, ensure_ascii=False
    ).encode("utf-8"))
    sig = hmac.new(_secret(), payload.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def verify_session(token: str) -> dict | None:
    """校验签名与有效期，通过返回会话载荷，失败返回 None。"""
    try:
        payload, sig = token.split(".", 1)
        expect = hmac.new(_secret(), payload.encode("ascii"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expect):
            return None
        data = json.loads(_b64d(payload))
        if data.get("exp", 0) < time.time():
            return None
        return data
    except (ValueError, json.JSONDecodeError):
        return None


def session_cookie(token: str) -> str:
    return f"session={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age={_SESSION_TTL}"


def clear_session_cookie() -> str:
    return "session=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0"


def user_from_cookie(cookie_header: str | None) -> dict | None:
    """从请求 Cookie 头解析当前用户；无效会话返回 None。"""
    if not cookie_header:
        return None
    token = ""
    for part in cookie_header.split(";"):
        k, _, v = part.strip().partition("=")
        if k == "session":
            token = v
            break
    data = verify_session(token)
    if not data:
        return None
    user = _find_user(load_users()["users"], data.get("uid", ""))
    # uid 直接存 user_id；这里保持按字段匹配，避免 email 大小写歧义
    if not user and data.get("uid"):
        user = next((u for u in load_users()["users"]
                     if u.get("user_id") == data.get("uid")), None)
    return to_public(user) if user else None


def safe_next(target: str | None) -> str:
    """登录后跳转白名单，避免开放跳转。"""
    if not target or not target.startswith("/") or target.startswith("//"):
        return "/"
    return target


def login_redirect_target(user: dict) -> str:
    role = user.get("role")
    if role == "admin":
        return "/members"
    if role == "pm":
        return "/"
    if role == "sales":
        return "/library"
    return "/library"


def invite_link(email: str) -> str:
    """占位邀请链接：后续接邮件或复制到聊天工具。"""
    return f"/invite?email={quote(email)}"


def can(role: str, action: str) -> bool:
    return role in PAGE_ACCESS.get(action, set())
