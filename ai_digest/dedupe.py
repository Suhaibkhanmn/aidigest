import re
from hashlib import sha1

from .models import SourceItem


def dedupe_items(items: list[SourceItem]) -> list[SourceItem]:
    seen: set[str] = set()
    unique: list[SourceItem] = []
    for item in items:
        key = story_key(item)
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def story_key(item: SourceItem) -> str:
    normalized_url = item.url.lower().split("?")[0].rstrip("/")
    normalized_title = re.sub(r"[^a-z0-9]+", " ", item.title.lower()).strip()
    basis = normalized_url or normalized_title
    return sha1(basis.encode("utf-8")).hexdigest()[:16]


def story_slug(item: SourceItem) -> str:
    title = re.sub(r"[^a-z0-9]+", "-", item.title.lower()).strip("-")
    return title[:80] or story_key(item)
