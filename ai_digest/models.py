from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class SourceItem:
    title: str
    url: str
    source: str
    published_at: str
    summary: str
    category: str
    trust: str
    source_group: str = "industry"
    source_priority: int = 50
    max_items: int = 4
    allow_expand: bool = False
    expanded_text: str = ""


@dataclass(frozen=True)
class BriefPackage:
    website_markdown: str
    telegram_text: str
    themes: list[str]
    top_story_ids: list[str]
    source_urls: list[str]
    provider: str
    writer_model: str
    helper_model: str
    used_fallback: bool


@dataclass(frozen=True)
class DigestResult:
    mode: str
    brief_kind: str
    digest_path: str
    digest_markdown: str
    telegram_text: str
    item_count: int
    shortlisted_count: int
    selected_count: int
    delivery_status: str
    generated_at: datetime
