"""项目级访问控制（data/project_access.json，已 gitignore）。

模型（最小可行，V3 起步版）：
- 默认所有项目全员可见；
- 管理员可将项目标记为「受限」：受限项目仅 admin / pm / 被授权成员可见；
- 授权按 user_id 记录，与角色无关（销售/售前被单独授权即可见）。
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

DATA_PATH = Path(os.environ.get(
    "ACCESS_DATA_PATH",
    Path(__file__).resolve().parents[1] / "data" / "project_access.json",
))
_LOCK = threading.RLock()


def _load() -> dict:
    if not DATA_PATH.exists():
        return {"restricted": [], "grants": {}}
    try:
        data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"restricted": [], "grants": {}}
    data.setdefault("restricted", [])
    data.setdefault("grants", {})
    return data


def _save(data: dict) -> None:
    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        DATA_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def is_restricted(project_id: str) -> bool:
    return project_id in _load()["restricted"]


def visible_to(project_id: str, role: str, user_id: str) -> bool:
    """项目对某用户是否可见：admin/pm 恒可见；受限项目需在授权名单。"""
    if role in ("admin", "pm"):
        return True
    if not is_restricted(project_id):
        return True
    return user_id in _load()["grants"].get(project_id, [])


def set_restricted(project_id: str, restricted: bool, granted_user_ids: list | None = None) -> dict:
    """设置受限状态；restricted=True 时可同时写入授权名单。"""
    with _LOCK:
        data = _load()
        if restricted:
            if project_id not in data["restricted"]:
                data["restricted"].append(project_id)
            if granted_user_ids is not None:
                data["grants"][project_id] = [u for u in granted_user_ids]
        else:
            if project_id in data["restricted"]:
                data["restricted"].remove(project_id)
            data["grants"].pop(project_id, None)
        _save(data)
    return {"project_id": project_id, "restricted": restricted,
            "grants": data["grants"].get(project_id, [])}


def access_overview() -> list:
    """管理端总览：每个受限项目的授权名单。"""
    data = _load()
    return [{"project_id": pid, "grants": data["grants"].get(pid, [])}
            for pid in data["restricted"]]
