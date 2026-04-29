import json
from datetime import datetime, timezone
from pathlib import Path

from .config import DIGEST_DIR, MEMORY_DIR, TELEGRAM_DIR, ensure_dirs
from .storage import store, utc_timestamp


INDEX_PATH = MEMORY_DIR / "daily_index.jsonl"


def recent_memory(limit: int = 8) -> list[dict]:
    if not INDEX_PATH.exists():
        return []
    rows: list[dict] = []
    with INDEX_PATH.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows[-limit:]


def save_digest(markdown: str, *, mode: str, date_slug: str, brief_kind: str) -> Path:
    ensure_dirs()
    filename = f"{date_slug}-{brief_kind}.md" if mode == "normal" else f"{date_slug}-{brief_kind}-{mode}.md"
    path = DIGEST_DIR / filename
    path.write_text(markdown, encoding="utf-8")
    upsert_remote_digest(
        name=filename,
        content=markdown,
        telegram_content=None,
        mode=mode,
        date_slug=date_slug,
        brief_kind=brief_kind,
        modified=utc_timestamp(),
    )
    return path


def save_telegram_message(message: str, *, mode: str, date_slug: str, brief_kind: str) -> Path:
    ensure_dirs()
    filename = f"{date_slug}-{brief_kind}.txt" if mode == "normal" else f"{date_slug}-{brief_kind}-{mode}.txt"
    path = TELEGRAM_DIR / filename
    path.write_text(message, encoding="utf-8")
    digest_filename = filename.replace(".txt", ".md")
    local_digest = DIGEST_DIR / digest_filename
    upsert_remote_digest(
        name=digest_filename,
        content=local_digest.read_text(encoding="utf-8") if local_digest.exists() else None,
        telegram_content=message,
        mode=mode,
        date_slug=date_slug,
        brief_kind=brief_kind,
        modified=utc_timestamp(),
    )
    return path


def append_index(entry: dict) -> None:
    ensure_dirs()
    entry = {**entry, "recorded_at": datetime.now(timezone.utc).isoformat()}
    with INDEX_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=True) + "\n")
    remote = store()
    if remote.enabled:
        try:
            remote.insert("daily_index", [{"entry": entry, "recorded_at": entry["recorded_at"]}])
        except Exception:
            pass


def list_digests() -> list[dict]:
    ensure_dirs()
    remote = remote_digests()
    if remote:
        return remote
    digests = []
    for path in sorted(DIGEST_DIR.glob("*.md"), key=lambda candidate: candidate.stat().st_mtime, reverse=True):
        related_telegram = TELEGRAM_DIR / (path.stem + ".txt")
        digests.append(
            {
                "name": path.name,
                "path": str(path),
                "modified": path.stat().st_mtime,
                "content": path.read_text(encoding="utf-8"),
                "telegram_path": str(related_telegram) if related_telegram.exists() else "",
                "telegram_content": related_telegram.read_text(encoding="utf-8") if related_telegram.exists() else "",
            }
        )
    return digests


def upsert_remote_digest(
    *,
    name: str,
    content: str | None,
    telegram_content: str | None,
    mode: str,
    date_slug: str,
    brief_kind: str,
    modified: float,
) -> None:
    remote = store()
    if not remote.enabled:
        return
    existing = remote_digests(name=name)
    row = {
        "name": name,
        "mode": mode,
        "date_slug": date_slug,
        "brief_kind": brief_kind,
        "modified": modified,
    }
    if content is not None:
        row["content"] = content
    elif existing:
        row["content"] = existing[0].get("content", "")
    if telegram_content is not None:
        row["telegram_content"] = telegram_content
    elif existing:
        row["telegram_content"] = existing[0].get("telegram_content", "")
    try:
        remote.upsert("digests", [row])
    except Exception:
        pass


def remote_digests(name: str | None = None) -> list[dict]:
    remote = store()
    if not remote.enabled:
        return []
    query = {"select": "*", "order": "modified.desc"}
    if name:
        query["name"] = f"eq.{name}"
    try:
        rows = remote.select("digests", query=query)
    except Exception:
        return []
    return [
        {
            "name": row.get("name", ""),
            "path": "",
            "modified": row.get("modified", 0),
            "content": row.get("content", ""),
            "telegram_path": "",
            "telegram_content": row.get("telegram_content", ""),
        }
        for row in rows
    ]


def sync_local_digests_to_remote() -> int:
    remote = store()
    if not remote.enabled:
        return 0
    ensure_dirs()
    count = 0
    for path in DIGEST_DIR.glob("*.md"):
        related_telegram = TELEGRAM_DIR / (path.stem + ".txt")
        parts = path.stem.split("-")
        date_slug = "-".join(parts[:3]) if len(parts) >= 3 else ""
        mode = "test" if path.stem.endswith("-test") else "normal"
        brief_parts = parts[3:-1] if mode == "test" else parts[3:]
        brief_kind = "-".join(brief_parts) if brief_parts else "night-read"
        upsert_remote_digest(
            name=path.name,
            content=path.read_text(encoding="utf-8"),
            telegram_content=related_telegram.read_text(encoding="utf-8") if related_telegram.exists() else "",
            mode=mode,
            date_slug=date_slug,
            brief_kind=brief_kind,
            modified=path.stat().st_mtime,
        )
        count += 1
    return count
