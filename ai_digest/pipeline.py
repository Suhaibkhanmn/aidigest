from datetime import datetime, timedelta, timezone

from .agent import DigestAgent
from .config import ensure_dirs, load_profile, mode_config
from .dedupe import dedupe_items
from .delivery import maybe_send_email, maybe_send_telegram
from .editions import edition_label, infer_edition, normalize_edition
from .llm import provider_for
from .memory import append_index, recent_memory, save_digest, save_telegram_message
from .models import DigestResult, SourceItem
from .sources import fetch_all_sources, maybe_expand_items


BRIEF_WINDOWS = {
    "first-light": 18,
    "midday-note": 18,
    "night-read": 30,
    "morning": 18,
    "evening": 30,
}


class DigestPipeline:
    def preview_sources(self) -> list[SourceItem]:
        return daily_items(dedupe_items(fetch_all_sources()), hours=72)

    def run(
        self,
        *,
        mode: str = "test",
        brief_kind: str | None = None,
        allow_delivery: bool = False,
    ) -> DigestResult:
        ensure_dirs()
        config = mode_config(mode)
        now = datetime.now(timezone.utc)
        brief_kind = normalize_edition(brief_kind) or infer_edition(now)
        date_label = now.astimezone().strftime("%B %d, %Y")
        date_slug = now.astimezone().strftime("%Y-%m-%d")

        fetched = fetch_all_sources()
        daily = daily_items(dedupe_items(fetched), hours=BRIEF_WINDOWS.get(brief_kind, 18))
        shortlisted = shortlist_items(daily, shortlist_limit=config.shortlist_limit)
        expanded = maybe_expand_items(shortlisted, max_expand=4)
        selected = select_digest_items(expanded, max_items=config.selected_limit)

        writer_llm = provider_for(config.provider, config.writer_model, config.api_key_env)
        helper_llm = provider_for(config.provider, config.helper_model, config.api_key_env)
        full_digest_url = f"http://127.0.0.1:8765/?issue={date_slug}-{brief_kind}"
        brief = DigestAgent(writer_llm, helper_llm).create_brief(
            mode=mode,
            brief_kind=brief_kind,
            date_label=date_label,
            items=selected,
            recent_memory=recent_memory(),
            profile=load_profile(),
            full_digest_url=full_digest_url,
        )

        digest_path = save_digest(brief.website_markdown, mode=mode, date_slug=date_slug, brief_kind=brief_kind)
        telegram_path = save_telegram_message(brief.telegram_text, mode=mode, date_slug=date_slug, brief_kind=brief_kind)
        append_index(
            {
                "date": date_slug,
                "brief_kind": brief_kind,
                "mode": mode,
                "themes": brief.themes,
                "story_ids": brief.top_story_ids,
                "source_urls": brief.source_urls,
                "digest_path": str(digest_path),
                "telegram_path": str(telegram_path),
                "provider": brief.provider,
                "writer_model": brief.writer_model,
                "helper_model": brief.helper_model,
                "used_fallback": brief.used_fallback,
            }
        )

        email_status = maybe_send_email(
            brief.website_markdown,
            subject=f"AI Digest - {edition_label(brief_kind)} - {date_label}",
            mode=mode,
            allow_delivery=allow_delivery or config.send_email_by_default,
        )
        telegram_status = maybe_send_telegram(
            brief.telegram_text,
            mode=mode,
            brief_kind=brief_kind,
            allow_delivery=allow_delivery or config.send_telegram_by_default,
        )
        return DigestResult(
            mode=mode,
            brief_kind=brief_kind,
            digest_path=str(digest_path),
            digest_markdown=brief.website_markdown,
            telegram_text=brief.telegram_text,
            item_count=len(fetched),
            shortlisted_count=len(shortlisted),
            selected_count=len(selected),
            delivery_status=f"{email_status}; {telegram_status}",
            generated_at=now.astimezone(),
        )


def infer_brief_kind(now: datetime) -> str:
    return infer_edition(now)


def daily_items(items: list[SourceItem], *, hours: int) -> list[SourceItem]:
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=hours)
    dated: list[tuple[datetime, SourceItem]] = []
    undated: list[SourceItem] = []
    for item in items:
        parsed = parse_item_date(item.published_at)
        if parsed is None:
            undated.append(item)
            continue
        if parsed >= cutoff:
            dated.append((parsed, item))
    dated.sort(key=lambda row: row[0], reverse=True)
    if dated:
        return [item for _, item in dated]
    return undated[:40]


def shortlist_items(items: list[SourceItem], *, shortlist_limit: int) -> list[SourceItem]:
    filtered = [item for item in items if not is_noise_item(item)]
    ranked = sorted(
        filtered,
        key=lambda item: (
            group_rank(item.source_group),
            trust_rank(item.trust),
            item.source_priority,
            item.source,
        ),
    )
    shortlisted: list[SourceItem] = []
    per_source: dict[str, int] = {}
    per_group: dict[str, int] = {}
    group_caps = {
        "labs": max(6, shortlist_limit // 5),
        "tools": max(6, shortlist_limit // 5),
        "industry": max(6, shortlist_limit // 5),
        "research": max(6, shortlist_limit // 4),
        "policy": 3,
    }
    for item in ranked:
        source_cap = item.max_items
        if per_source.get(item.source, 0) >= source_cap:
            continue
        if per_group.get(item.source_group, 0) >= group_caps.get(item.source_group, shortlist_limit):
            continue
        shortlisted.append(item)
        per_source[item.source] = per_source.get(item.source, 0) + 1
        per_group[item.source_group] = per_group.get(item.source_group, 0) + 1
        if len(shortlisted) >= shortlist_limit:
            break
    return shortlisted


def select_digest_items(items: list[SourceItem], *, max_items: int) -> list[SourceItem]:
    items = [item for item in items if not is_misleading_risk_item(item)]
    if len(items) <= max_items:
        return diversify_selected_items(enforce_source_cap(items, cap=2))
    ranked = sorted(
        items,
        key=lambda item: (
            group_rank(item.source_group),
            trust_rank(item.trust),
            item.source_priority,
            item.source,
        ),
    )
    selected: list[SourceItem] = []
    per_group: dict[str, int] = {}
    per_source: dict[str, int] = {}
    group_caps = {
        "labs": 3,
        "tools": 3,
        "industry": 3,
        "research": 2,
        "policy": 1,
    }
    for item in ranked:
        if per_source.get(item.source, 0) >= 2:
            continue
        if per_group.get(item.source_group, 0) >= group_caps.get(item.source_group, max_items):
            continue
        selected.append(item)
        per_group[item.source_group] = per_group.get(item.source_group, 0) + 1
        per_source[item.source] = per_source.get(item.source, 0) + 1
        if len(selected) >= max_items:
            return selected
    for item in ranked:
        if item in selected:
            continue
        if per_source.get(item.source, 0) >= 2:
            continue
        if item.source_group == "research" and per_group.get("research", 0) >= group_caps["research"]:
            continue
        selected.append(item)
        per_group[item.source_group] = per_group.get(item.source_group, 0) + 1
        per_source[item.source] = per_source.get(item.source, 0) + 1
        if len(selected) >= max_items:
            break
    return diversify_selected_items(selected)


def enforce_source_cap(items: list[SourceItem], *, cap: int) -> list[SourceItem]:
    result: list[SourceItem] = []
    per_source: dict[str, int] = {}
    for item in diversify_selected_items(items):
        if per_source.get(item.source, 0) >= cap:
            continue
        result.append(item)
        per_source[item.source] = per_source.get(item.source, 0) + 1
    return result


def diversify_selected_items(items: list[SourceItem]) -> list[SourceItem]:
    """Keep one source from quietly dominating fallback and analyst input order."""
    if not items:
        return items
    buckets: dict[str, list[SourceItem]] = {}
    for item in items:
        buckets.setdefault(item.source, []).append(item)
    ordered_sources = sorted(
        buckets,
        key=lambda source: (
            group_rank(buckets[source][0].source_group),
            buckets[source][0].source_priority,
            source,
        ),
    )
    result: list[SourceItem] = []
    while any(buckets.values()):
        for source in ordered_sources:
            if buckets[source]:
                result.append(buckets[source].pop(0))
    return result


def group_rank(source_group: str) -> int:
    ranking = {
        "labs": 0,
        "tools": 1,
        "industry": 2,
        "policy": 3,
        "research": 4,
    }
    return ranking.get(source_group, 5)


def trust_rank(trust: str) -> int:
    return {"high": 0, "medium": 1, "low": 2}.get(trust, 3)


def parse_item_date(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def is_noise_item(item: SourceItem) -> bool:
    title = item.title.lower()
    summary = (item.summary or "").lower()
    noise_phrases = [
        "grab a ticket",
        "strictlyvc",
        "event",
        "webinar",
        "podcast",
        "sponsor",
        "register now",
        "conference",
        "geforce now",
        "game pass",
        "ubisoft",
        "gaming",
        "spring astronomy day",
        "game discovery",
        "levels up game",
        "xbox game pass",
        "cloud gaming",
        "save up to",
        "tickets are going",
    ]
    if any(phrase in title for phrase in noise_phrases):
        return True
    if item.source == "NVIDIA Newsroom" and any(term in title for term in ["geforce", "gpu gaming", "game pass"]):
        return True
    if item.source_group == "labs" and "astronomy" in title and "ai" not in title:
        return True
    if item.source_group == "industry" and "raises $" in title and "ai" not in summary and "agent" not in summary:
        return True
    return False


def is_misleading_risk_item(item: SourceItem) -> bool:
    title = item.title.lower()
    summary = (item.summary or "").lower()
    speculative = ["rumor", "rumour", "leak", "leaked", "reportedly", "could launch", "might launch"]
    if item.trust == "low" and any(term in title or term in summary for term in speculative):
        return True
    if "?" in item.title and item.trust != "high":
        return True
    return False
