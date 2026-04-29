from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import TELEGRAM_SUBSCRIBERS_PATH, ensure_dirs
from .storage import store


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_subscribers(path: Path = TELEGRAM_SUBSCRIBERS_PATH) -> dict[str, Any]:
    remote = store()
    if path == TELEGRAM_SUBSCRIBERS_PATH and remote.enabled:
        try:
            rows = remote.select("telegram_subscribers", query={"select": "*", "order": "created_at.asc"})
            return {"subscribers": [remote_to_local(row) for row in rows]}
        except Exception:
            pass
    if not path.exists():
        return {"subscribers": []}
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, list):
        return {"subscribers": data}
    if "subscribers" not in data:
        data["subscribers"] = []
    return data


def save_subscribers(data: dict[str, Any], path: Path = TELEGRAM_SUBSCRIBERS_PATH) -> None:
    ensure_dirs()
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def upsert_subscriber(chat: dict[str, Any], path: Path = TELEGRAM_SUBSCRIBERS_PATH) -> dict[str, Any]:
    chat_id = str(chat.get("id", "")).strip()
    if not chat_id:
        raise ValueError("chat id missing")

    data = load_subscribers(path)
    existing = next((entry for entry in data["subscribers"] if str(entry.get("chat_id")) == chat_id), None)
    if existing is None:
        existing = {"chat_id": chat_id, "created_at": _now()}
        data["subscribers"].append(existing)

    existing.update(
        {
            "active": True,
            "updated_at": _now(),
            "type": chat.get("type", ""),
            "username": chat.get("username", ""),
            "first_name": chat.get("first_name", ""),
            "last_name": chat.get("last_name", ""),
            "editions": existing.get("editions") or ["first-light", "midday-note", "night-read"],
            "muted_topics": existing.get("muted_topics") or [],
        }
    )
    remote = store()
    if path == TELEGRAM_SUBSCRIBERS_PATH and remote.enabled:
        try:
            remote.upsert("telegram_subscribers", [local_to_remote(existing)])
        except Exception:
            pass
    save_subscribers(data, path)
    return existing


def deactivate_subscriber(chat_id: str | int, path: Path = TELEGRAM_SUBSCRIBERS_PATH) -> bool:
    target = str(chat_id).strip()
    data = load_subscribers(path)
    changed = False
    for entry in data["subscribers"]:
        if str(entry.get("chat_id")) == target:
            entry["active"] = False
            entry["updated_at"] = _now()
            changed = True
            break
    if changed:
        remote = store()
        if path == TELEGRAM_SUBSCRIBERS_PATH and remote.enabled:
            try:
                remote.patch(
                    "telegram_subscribers",
                    query={"chat_id": f"eq.{target}"},
                    values={"active": False, "updated_at": _now()},
                )
            except Exception:
                pass
        save_subscribers(data, path)
    return changed


def update_subscriber_preferences(
    chat_id: str | int,
    *,
    editions: list[str] | None = None,
    muted_topics: list[str] | None = None,
    path: Path = TELEGRAM_SUBSCRIBERS_PATH,
) -> bool:
    target = str(chat_id).strip()
    data = load_subscribers(path)
    changed = False
    for entry in data["subscribers"]:
        if str(entry.get("chat_id")) != target:
            continue
        if editions is not None:
            entry["editions"] = editions
        if muted_topics is not None:
            entry["muted_topics"] = muted_topics
        entry["updated_at"] = _now()
        changed = True
        remote = store()
        if path == TELEGRAM_SUBSCRIBERS_PATH and remote.enabled:
            try:
                remote.upsert("telegram_subscribers", [local_to_remote(entry)])
            except Exception:
                pass
        break
    if changed:
        save_subscribers(data, path)
    return changed


def active_chat_ids(path: Path = TELEGRAM_SUBSCRIBERS_PATH, brief_kind: str | None = None) -> list[str]:
    data = load_subscribers(path)
    ids: list[str] = []
    seen = set()
    for entry in data["subscribers"]:
        chat_id = str(entry.get("chat_id", "")).strip()
        if not chat_id or not entry.get("active", True) or chat_id in seen:
            continue
        editions = entry.get("editions") or ["first-light", "midday-note", "night-read"]
        if brief_kind and brief_kind not in editions:
            continue
        seen.add(chat_id)
        ids.append(chat_id)
    return ids


def known_chat_ids(path: Path = TELEGRAM_SUBSCRIBERS_PATH) -> set[str]:
    data = load_subscribers(path)
    return {str(entry.get("chat_id", "")).strip() for entry in data["subscribers"] if entry.get("chat_id")}


def local_to_remote(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "chat_id": str(entry.get("chat_id", "")),
        "active": bool(entry.get("active", True)),
        "type": entry.get("type", ""),
        "username": entry.get("username", ""),
        "first_name": entry.get("first_name", ""),
        "last_name": entry.get("last_name", ""),
        "editions": entry.get("editions") or ["first-light", "midday-note", "night-read"],
        "muted_topics": entry.get("muted_topics") or [],
        "created_at": entry.get("created_at") or _now(),
        "updated_at": entry.get("updated_at") or _now(),
    }


def remote_to_local(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "chat_id": str(row.get("chat_id", "")),
        "active": bool(row.get("active", True)),
        "type": row.get("type", ""),
        "username": row.get("username", ""),
        "first_name": row.get("first_name", ""),
        "last_name": row.get("last_name", ""),
        "editions": row.get("editions") or ["first-light", "midday-note", "night-read"],
        "muted_topics": row.get("muted_topics") or [],
        "created_at": row.get("created_at", ""),
        "updated_at": row.get("updated_at", ""),
    }


def sync_local_subscribers_to_remote(path: Path = TELEGRAM_SUBSCRIBERS_PATH) -> int:
    remote = store()
    if not remote.enabled or not path.exists():
        return 0
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return 0
    data = raw if isinstance(raw, dict) else {"subscribers": raw}
    remote_rows = {}
    try:
        for row in remote.select("telegram_subscribers", query={"select": "*"}):
            remote_rows[str(row.get("chat_id", ""))] = row
    except Exception:
        remote_rows = {}
    rows = []
    for entry in data.get("subscribers", []):
        if not entry.get("chat_id"):
            continue
        local_row = local_to_remote(entry)
        remote_row = remote_rows.get(local_row["chat_id"])
        if remote_row and str(remote_row.get("updated_at", "")) > str(local_row.get("updated_at", "")):
            rows.append(remote_row)
        else:
            rows.append(local_row)
    if not rows:
        return 0
    try:
        remote.upsert("telegram_subscribers", rows)
    except Exception:
        return 0
    return len(rows)
