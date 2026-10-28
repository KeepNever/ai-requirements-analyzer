"""项目级标注存储：需求确认流转、销售客户回复、售前意见（data/notes.json，已 gitignore）。

设计口径：
- 以 `source:name`（case:xxx / project:xxx）为键，与 load_report_payload 的双数据源对齐。
- 只追加历史、不覆盖：需求确认保留 history，回 reply / 售前意见均为 append。
- 不做并发锁：当前单进程标准库 HTTP 服务，读-改-写足够；多进程部署前需换 SQLite。
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

_DATA_LOCK = threading.RLock()   # 可重入：防嵌套调用死锁

DATA_PATH = Path(os.environ.get(
    "NOTES_DATA_PATH",
    Path(__file__).resolve().parents[1] / "data" / "notes.json",
))


def _load() -> dict:
    if not DATA_PATH.exists():
        return {}
    try:
        data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def _save(data: dict) -> None:
    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _DATA_LOCK:
        DATA_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M")


def _key(source: str, name: str) -> str:
    return f"{source}:{name}"


def get(source: str, name: str) -> dict:
    return _load().get(_key(source, name), {})


def need_states(source: str, name: str) -> dict:
    return get(source, name).get("needs", {})


def confirm_need(source: str, name: str, need_id: str, note: str, user: dict) -> dict:
    if not need_id:
        raise ValueError("need_id 为空")
    data = _load()
    item = data.setdefault(_key(source, name), {})
    needs = item.setdefault("needs", {})
    entry = needs.setdefault(need_id, {"status": "confirmed", "history": []})
    entry["status"] = "confirmed"
    entry["history"].append({
        "action": "confirm", "note": (note or "").strip(),
        "by": user.get("email", ""), "by_name": user.get("name", ""), "at": _now(),
    })
    _save(data)
    return entry


def add_reply(source: str, name: str, gap: str, question: str, reply: str, user: dict) -> dict:
    if not (reply or "").strip():
        raise ValueError("回复内容为空")
    data = _load()
    item = data.setdefault(_key(source, name), {})
    rec = {
        "gap": gap or "", "question": question or "", "reply": (reply or "").strip(),
        "by": user.get("email", ""), "by_name": user.get("name", ""), "at": _now(),
    }
    item.setdefault("replies", []).append(rec)
    _save(data)
    return rec


def add_presale_note(source: str, name: str, target: str, verdict: str,
                     note: str, user: dict) -> dict:
    if not (note or "").strip():
        raise ValueError("意见内容为空")
    data = _load()
    item = data.setdefault(_key(source, name), {})
    rec = {
        "target": target or "", "verdict": verdict or "", "note": (note or "").strip(),
        "by": user.get("email", ""), "by_name": user.get("name", ""), "at": _now(),
    }
    item.setdefault("presale_notes", []).append(rec)
    _save(data)
    return rec


def audit_entries(source: str, name: str) -> list:
    """修正留痕：需求确认 + 客户回复 + 售前意见，按时间倒序。"""
    item = get(source, name)
    out = []
    for nid, entry in (item.get("needs") or {}).items():
        for h in entry.get("history", []):
            out.append({"type": "需求确认", "title": f"{nid} 标记为{entry.get('status', 'confirmed')}",
                        "detail": h.get("note", ""), "by": h.get("by_name", ""), "at": h.get("at", "")})
    for r in item.get("replies", []):
        out.append({"type": "客户回复", "title": r.get("question", ""),
                    "detail": r.get("reply", ""), "by": r.get("by_name", ""), "at": r.get("at", "")})
    for n in item.get("presale_notes", []):
        out.append({"type": "售前意见", "title": n.get("target", ""),
                    "detail": n.get("note", ""), "by": n.get("by_name", ""), "at": n.get("at", "")})
    out.sort(key=lambda x: x.get("at", ""), reverse=True)
    return out
