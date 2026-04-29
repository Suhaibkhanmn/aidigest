import email.utils
import html
import re
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Iterable

from .config import load_sources
from .models import SourceItem


ATOM_NS = "{http://www.w3.org/2005/Atom}"


def fetch_all_sources(timeout: int = 12) -> list[SourceItem]:
    items: list[SourceItem] = []
    for source in load_sources():
        if not source.get("enabled", True):
            continue
        source_type = source.get("type", "rss")
        if source_type in {"rss", "atom"}:
            items.extend(fetch_feed(source, timeout=timeout))
    return items


def fetch_feed(source: dict, timeout: int = 12) -> list[SourceItem]:
    url = source.get("url", "")
    if not url:
        return []

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "AI-Digest/0.2 (+local personal digest reader)",
            "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = response.read()
    except Exception:
        return []

    try:
        root = ET.fromstring(payload)
    except ET.ParseError:
        return []

    if root.tag.endswith("feed"):
        entries = parse_atom(root)
    else:
        entries = parse_rss(root)

    return [
        SourceItem(
            title=clean_text(entry.get("title", "")),
            url=entry.get("url", ""),
            source=source.get("name", "Unknown source"),
            published_at=normalize_date(entry.get("published_at", "")),
            summary=clean_text(entry.get("summary", "")),
            category=source.get("category", "general"),
            trust=source.get("trust", "medium"),
            source_group=source.get("source_group", source.get("category", "industry")),
            source_priority=int(source.get("source_priority", 50)),
            max_items=int(source.get("max_items", 4)),
            allow_expand=bool(source.get("allow_expand", False)),
        )
        for entry in entries
        if entry.get("title") and entry.get("url")
    ]


def parse_rss(root: ET.Element) -> Iterable[dict[str, str]]:
    for item in root.findall(".//item"):
        yield {
            "title": find_text(item, "title"),
            "url": find_text(item, "link") or find_text(item, "guid"),
            "published_at": find_text(item, "pubDate") or find_text(item, "published"),
            "summary": find_text(item, "description") or find_text(item, "summary"),
        }


def parse_atom(root: ET.Element) -> Iterable[dict[str, str]]:
    for entry in root.findall(f"{ATOM_NS}entry"):
        link = ""
        for link_el in entry.findall(f"{ATOM_NS}link"):
            if link_el.attrib.get("rel", "alternate") == "alternate":
                link = link_el.attrib.get("href", "")
                break
        yield {
            "title": find_text(entry, f"{ATOM_NS}title"),
            "url": link or find_text(entry, f"{ATOM_NS}id"),
            "published_at": find_text(entry, f"{ATOM_NS}published") or find_text(entry, f"{ATOM_NS}updated"),
            "summary": find_text(entry, f"{ATOM_NS}summary") or find_text(entry, f"{ATOM_NS}content"),
        }


def find_text(parent: ET.Element, tag: str) -> str:
    child = parent.find(tag)
    if child is None or child.text is None:
        return ""
    return child.text.strip()


def clean_text(value: str) -> str:
    text = html.unescape(value or "")
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def normalize_date(value: str) -> str:
    if not value:
        return ""
    try:
        parsed = email.utils.parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat()
    except Exception:
        pass
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc).isoformat()
    except Exception:
        return value


def maybe_expand_items(items: list[SourceItem], *, max_expand: int = 4, timeout: int = 12) -> list[SourceItem]:
    expanded: list[SourceItem] = []
    used = 0
    for item in items:
        if item.allow_expand and used < max_expand and should_expand(item):
            text = fetch_page_excerpt(item.url, timeout=timeout)
            if text:
                expanded.append(
                    SourceItem(
                        **{**item.__dict__, "expanded_text": text[:1400]}
                    )
                )
                used += 1
                continue
        expanded.append(item)
    return expanded


def should_expand(item: SourceItem) -> bool:
    if item.source_group in {"labs", "industry", "policy"} and len(item.summary) < 180:
        return True
    return False


class ExcerptParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self.skip_depth = 0

    def handle_data(self, data: str) -> None:
        if self.skip_depth:
            return
        text = " ".join(data.split())
        if len(text) > 40 and "function(" not in text and "{" not in text and "}" not in text:
            self.parts.append(text)

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in {"script", "style", "noscript"}:
            self.skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"} and self.skip_depth:
            self.skip_depth -= 1

    def excerpt(self) -> str:
        text = " ".join(self.parts[:12])
        text = re.sub(r"\s+", " ", text).strip()
        return text[:1800]


def fetch_page_excerpt(url: str, timeout: int = 12) -> str:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "AI-Digest/0.2 (+local personal digest reader)",
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = response.read().decode("utf-8", errors="ignore")
    except Exception:
        return ""
    parser = ExcerptParser()
    parser.feed(payload)
    return parser.excerpt()
