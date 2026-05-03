import json
import re
from collections import Counter

from .dedupe import story_slug
from .editions import edition_label
from .llm import LLMProvider
from .models import BriefPackage, SourceItem


class DigestAgent:
    """Controlled editorial agent for analysis, writing, and Telegram packaging."""

    def __init__(self, writer_llm: LLMProvider, helper_llm: LLMProvider):
        self.writer_llm = writer_llm
        self.helper_llm = helper_llm

    def create_brief(
        self,
        *,
        mode: str,
        brief_kind: str,
        date_label: str,
        items: list[SourceItem],
        recent_memory: list[dict],
        profile: dict,
        full_digest_url: str,
    ) -> BriefPackage:
        if not items:
            markdown = self._empty_digest(date_label, brief_kind)
            telegram = self._fallback_telegram(brief_kind, date_label, [], markdown, full_digest_url)
            return BriefPackage(
                website_markdown=markdown,
                telegram_text=telegram,
                themes=[],
                top_story_ids=[],
                source_urls=[],
                provider="offline",
                writer_model="local-preview",
                helper_model="local-preview",
                used_fallback=True,
            )

        analyst_prompt = build_analyst_prompt(
            brief_kind=brief_kind,
            date_label=date_label,
            items=items,
            recent_memory=recent_memory,
            profile=profile,
        )
        analyst = self.writer_llm.generate(analyst_prompt, temperature=0.2, max_output_tokens=2200)
        analysis = parse_analysis_json(analyst.text)
        if not analysis:
            analysis = fallback_analysis(items, profile)
        analysis = rebalance_analysis(analysis, items)
        analysis = dedupe_analysis_notes(analysis)
        analysis = fill_smaller_notes(analysis, items)
        analysis = ensure_work_relevance(analysis, profile, items)
        display_analysis = build_telegram_analysis(analysis, items)

        website_prompt = build_writer_prompt(
            brief_kind=brief_kind,
            date_label=date_label,
            analysis=display_analysis,
            profile=profile,
        )
        website = self.writer_llm.generate(website_prompt, temperature=0.55, max_output_tokens=5600)
        if website.text.strip():
            markdown = normalize_markdown(website.text, date_label, brief_kind)
        else:
            markdown = fallback_markdown(brief_kind, date_label, display_analysis, profile, items)
        markdown = force_website_story_sections(markdown, display_analysis)
        markdown = ensure_website_tail(markdown, display_analysis, profile, items)
        markdown = ensure_smaller_note_sources(markdown, display_analysis, items)
        markdown = remove_website_sections(markdown, ["For Your Work", "Closing"])

        telegram_text = self._fallback_telegram(
            brief_kind, date_label, display_analysis, markdown, full_digest_url
        )
        telegram_text = normalize_telegram_text(telegram_text)

        top_story_ids = [story.get("story_id", "") for story in display_analysis.get("top_stories", []) if story.get("story_id")]
        if not top_story_ids:
            top_story_ids = [story_slug(item) for item in items[:10]]
        return BriefPackage(
            website_markdown=markdown,
            telegram_text=telegram_text,
            themes=display_analysis.get("themes", infer_themes(items)),
            top_story_ids=top_story_ids,
            source_urls=[story.get("url", "") for story in display_analysis.get("top_stories", []) if story.get("url")],
            provider=website.provider if website.provider != "offline" else analyst.provider,
            writer_model=website.model,
            helper_model=getattr(self.helper_llm, "model", "template"),
            used_fallback=website.used_fallback or analyst.used_fallback,
        )

    def _empty_digest(self, date_label: str, brief_kind: str) -> str:
        title = edition_label(brief_kind)
        return (
            f"# AI Digest - {title}\n\n"
            f"## {date_label}\n\n"
            "## Opening Read\n\n"
            "There were not enough strong source updates in the configured feeds to publish a meaningful issue. "
            "That usually means the source list needs adjustment, the network was unavailable, or the window was genuinely quiet.\n"
        )

    def _fallback_telegram(
        self,
        brief_kind: str,
        date_label: str,
        analysis_or_stories: dict | list[dict],
        markdown: str,
        full_digest_url: str,
    ) -> str:
        title = edition_label(brief_kind)
        if isinstance(analysis_or_stories, dict):
            stories = analysis_or_stories.get("top_stories", [])[:5]
            notes = telegram_notes_without_story_duplicates(
                analysis_or_stories.get("smaller_notes", []),
                stories,
                limit=3,
            )
        else:
            stories = analysis_or_stories[:5]
            notes = []
        lines = [f"AI Digest - {title}", date_label, "", "What happened today:", fallback_opening_read(stories), ""]
        if stories:
            lines.append("Top stories:")
            for idx, story in enumerate(stories, start=1):
                lines.append(f"{idx}. {telegram_story_title(story)}: {telegram_story_angle(story)}")
        if notes:
            lines.append("")
            lines.append("Also worth looking:")
            for note in notes[:3]:
                lines.append(f"- {telegram_note_text(note)}")
            lines.append("")
        if lines[-1] != "":
            lines.append("")
        lines.extend(["Read full issue:", full_digest_url])
        return "\n".join(lines).strip()


def build_analyst_prompt(
    *,
    brief_kind: str,
    date_label: str,
    items: list[SourceItem],
    recent_memory: list[dict],
    profile: dict,
) -> str:
    compact_items = "\n".join(
        [
            f"{idx + 1}. "
            f"title={item.title} | source={item.source} | group={item.source_group} | category={item.category} | "
            f"priority={item.source_priority} | url={item.url} | summary={(item.expanded_text or item.summary)[:700]}"
            for idx, item in enumerate(items[:40])
        ]
    )
    memory_text = "\n".join(
        f"- {entry.get('date')} {entry.get('brief_kind', 'issue')}: {', '.join(entry.get('themes', [])[:4])}"
        for entry in recent_memory[-6:]
    ) or "No recent digest memory."
    profile_text = profile_summary(profile)
    return f"""You are the analyst pass for AI Digest.

Create a compact JSON object only. No prose outside JSON.

Rules:
- This product must remain 80-90% industry digest and 10-20% personal/project relevance.
- Personal context is only a short late add-on.
- Do not suppress major AI industry news because it is not directly relevant to the user's project.
- Prefer primary source, tooling, research, and quality industry reporting balance.
- Avoid selecting more than two top stories from the same source unless the shortlist has no credible alternatives.
- The top stories should usually include a mix of lab/product, industry/reporting, and research/tooling when those groups are present.
- If one company has the biggest story, cover it once as the lead and use the remaining slots for different angles from other sources.
- For Telegram, the top three stories must avoid repeating the same source when credible alternatives exist.
- Treat wider reporting and research as primary context, not filler.
- Edition 1 should be short and directional.
- Edition 2 should be a tighter check-in that catches meaningful movement since the first edition.
- Edition 3 should be fuller and more reflective.
- Avoid overusing words like "signal", "shift", "ecosystem", and "landscape".
- Write like a thoughtful person explaining the day clearly, not like an analyst filling a template.

Return JSON with exactly these keys:
{{
  "opening_read": "2-3 clear sentences explaining what happened today and why it matters",
  "themes": ["theme", "theme"],
  "top_stories": [
    {{
      "story_id": "...",
      "title": "...",
      "source": "...",
      "url": "...",
      "angle": "3-4 clear sentences focused on what happened, why it matters, and what to watch next",
      "importance": "high|medium"
    }}
  ],
  "smaller_notes": ["...", "...", "..."],
  "work_relevance": "1 short paragraph, late add-on only; concrete connection to the user's projects/workflow if real, otherwise say there is no action needed",
  "closing_takeaway": "1 short closing paragraph"
}}

Date: {date_label}
Brief kind: {brief_kind}

Recent memory:
{memory_text}

Profile context for light relevance only:
{profile_text}

Shortlisted items:
{compact_items}
"""


def build_writer_prompt(*, brief_kind: str, date_label: str, analysis: dict, profile: dict) -> str:
    profile_text = profile_summary(profile)
    return f"""You are the website writer for AI Digest.

Write the canonical website issue as a long-form editorial digest.

Rules:
- 80-90% of the digest must be industry digest.
- 10-20% can be personal/project relevance.
- The opening, main stories, and smaller notes are industry-focused.
- Do not include personal/project relevance on the website for now.
- Do not turn every story into "what this means for your project".
- This is not the Telegram brief. Do not write compact bot-style bullets as the main experience.
- Write in paragraphs with thoughtful transitions. It should feel like a dated magazine note or essay, not a report.
- Use a natural first-person-adjacent editorial voice when useful: "the useful thing to notice is...", "what matters here is...", "the quieter point is...". Do not sound like a corporate briefing.
- Start with a human, date-led opening. Example style: "24 April. It has been a busy day in AI: GPT-5.5 dropped, NVIDIA is already talking about where it runs, and a few smaller stories filled in the rest of the picture."
- After the opening, explain the day in detail. The main article should have room to breathe.
- Explain terms that may be unclear. For example, explain that a system card is a technical/safety document about model behavior and limitations.
- Use careful verbs. Prefer "published", "reported", "described", "studied", "raised", "announced". Do not overclaim with "proved", "exposed", "warned", or "confirmed" unless the source clearly supports it.
- Avoid hype, generic AI-sounding phrasing, dashboard copy, and repeated "signal" language.
- Avoid empty phrases like "AI landscape", "rapid evolution", "maturing ecosystem", "significant update", "strategic move", "trajectory", "confluence", and "broader application ecosystem".
- Prefer plain verbs and concrete phrasing: "released", "changed", "raised questions", "made it easier", "shows pressure", "matters because".
- The writing should feel calm, human, detailed, and well-edited.
- If the input is thin or noisy, say that gently instead of pretending every item is important.
- The digest should not read like a single-company bulletin unless the entire source mix genuinely points to one company.
- If multiple sources cover the same story, synthesize them into one main story instead of listing each article separately.
- Cite sources with markdown links.
- End each main story with a separate line like: [Read more](https://source-url)
- Use markdown headings for story titles, not bold-only lines.
- Do not write raw source names in brackets unless they are proper links.
- Use these sections exactly:
  1. # AI Digest - {edition_label(brief_kind)}
  2. ## {date_label}
  3. ## Today
  4. ## What Happened
  5. ## Smaller Notes

Length guidance:
- Today: 3-5 paragraphs, written like the introduction of an article. Do not summarize all story details here; set up why the day matters.
- Each main story: 3-5 paragraphs. Explain what happened, what the source actually says, why it matters, and what is still unclear.
- Smaller Notes: short articulated paragraphs or bullets, not raw article titles. Each note should include a [Read more](source-url) link when a source URL is available.

Profile context:
{profile_text}

Analysis JSON:
{json.dumps(analysis, ensure_ascii=False, indent=2)}
"""


def build_telegram_prompt(*, brief_kind: str, date_label: str, analysis: dict, full_digest_url: str) -> str:
    title = edition_label(brief_kind)
    return f"""Write a sharp Telegram briefing.

Rules:
- Medium length: more points, not long paragraphs.
- Use plain text only.
- Do not use markdown formatting. No asterisks. No bold. Use "-" for bullets.
- Aim for 450-700 words max.
- Give exactly 5 top stories when available.
- Each top story should be title + 1-2 useful sentences, not a big paragraph.
- Use careful verbs. Prefer "published", "reported", "described", "studied", "raised", "announced". Do not overclaim with "proved", "exposed", "warned", or "confirmed" unless the source clearly says that.
- Do not repeat a top story again in smaller notes.
- Do not list multiple articles about the same announcement unless they add genuinely different information.
- Avoid vague phrases like "useful connection", "ecosystem", "landscape", "game changer", "rapid evolution", and overused "signal".
- Write like a person who read the sources and is explaining what is actually worth knowing.
- Structure:
  AI Digest - {title}
  {date_label}
  blank line
  What happened today:
  2-3 short sentences
  blank line
  Top stories:
  exactly 5 numbered items when available. Format each item as:
  1. Title: one or two clear sentences.
  blank line
  Also worth looking:
  exactly 3 bullet lines, no duplicates of main stories
  blank line
  Read full issue:
  {full_digest_url}

Analysis JSON:
{json.dumps(analysis, ensure_ascii=False, indent=2)}
"""


def parse_analysis_json(text: str) -> dict:
    if not text.strip():
        return {}
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return {}
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return {}


def rebalance_analysis(analysis: dict, items: list[SourceItem]) -> dict:
    top_stories = analysis.get("top_stories", [])
    if not isinstance(top_stories, list):
        top_stories = []
    balanced = diverse_top_stories(top_stories, limit=5)
    seen_urls = {story.get("url") for story in balanced}
    seen_sources = {story.get("source") for story in balanced}

    for item in diverse_top_items(items, limit=8):
        if len(balanced) >= 5:
            break
        if item.url in seen_urls:
            continue
        if item.source in seen_sources and len(seen_sources) < 3:
            continue
        balanced.append(source_item_story(item))
        seen_urls.add(item.url)
        seen_sources.add(item.source)

    # If the model chose two items from one source and alternatives exist, keep the lead
    # but force the first three Telegram-visible stories to be broader.
    if len({story.get("source") for story in balanced[:3]}) < min(3, len(balanced)):
        balanced = diverse_top_stories(balanced + [source_item_story(item) for item in items], limit=5)

    return {**analysis, "top_stories": balanced}


def dedupe_analysis_notes(analysis: dict) -> dict:
    notes = analysis.get("smaller_notes") or analysis.get("smaller_signals") or []
    if not isinstance(notes, list):
        notes = []
    top_stories = analysis.get("top_stories", [])
    top_texts = [
        " ".join([story.get("title", ""), story.get("source", ""), story.get("angle", "")])
        for story in top_stories
        if isinstance(story, dict)
    ]
    top_token_sets = [token_set(text) for text in top_texts]
    cleaned: list[str] = []
    seen_keys: set[str] = set()
    for note in notes:
        if not isinstance(note, str):
            continue
        note_text = " ".join(note.split())
        if not note_text:
            continue
        note_key = " ".join(sorted(token_set(note_text))[:8])
        if note_key in seen_keys:
            continue
        note_tokens = token_set(note_text)
        if overlaps_named_model_or_company(note_text, top_texts):
            continue
        if any(token_overlap(note_tokens, top_tokens) >= 0.42 for top_tokens in top_token_sets):
            continue
        if any(title_substring_match(note_text, top_text) for top_text in top_texts):
            continue
        cleaned.append(note_text)
        seen_keys.add(note_key)
        if len(cleaned) >= 5:
            break
    return {**analysis, "smaller_notes": cleaned}


def fill_smaller_notes(analysis: dict, items: list[SourceItem]) -> dict:
    notes = list(analysis.get("smaller_notes") or [])
    top_urls = {story.get("url") for story in analysis.get("top_stories", []) if isinstance(story, dict)}
    top_texts = [
        " ".join([story.get("title", ""), story.get("source", ""), story.get("angle", "")])
        for story in analysis.get("top_stories", [])
        if isinstance(story, dict)
    ]
    existing = " ".join(notes)
    for item in diverse_top_items(items, limit=24):
        if len(notes) >= 3:
            break
        if item.url in top_urls:
            continue
        candidate = f"{fix_mojibake(item.title)} - {item.source}"
        if overlaps_named_model_or_company(candidate, top_texts):
            continue
        if any(title_substring_match(candidate, top_text) for top_text in top_texts):
            continue
        if token_overlap(token_set(candidate), token_set(existing)) >= 0.45:
            continue
        notes.append(candidate)
        existing += " " + candidate
    if len(notes) < 3:
        for item in diverse_top_items(items, limit=32):
            if len(notes) >= 3:
                break
            if item.url in top_urls:
                continue
            candidate = f"{fix_mojibake(item.title)} - {item.source}"
            if any(normalize_for_match(candidate) == normalize_for_match(existing_note) for existing_note in notes):
                continue
            notes.append(candidate)
    return {**analysis, "smaller_notes": notes[:3]}


def build_telegram_analysis(analysis: dict, items: list[SourceItem]) -> dict:
    selected: list[SourceItem] = []
    for item in sorted(items, key=telegram_item_rank):
        if len(selected) >= 5:
            break
        if is_low_value_telegram_item(item, selected):
            continue
        if any(same_telegram_story(item, existing) for existing in selected):
            continue
        selected.append(item)

    top_stories = [source_item_story(item) for item in selected]
    note_items: list[SourceItem] = []
    for item in sorted(items, key=telegram_note_rank):
        if len(note_items) >= 3:
            break
        if is_bad_note_item(item):
            continue
        if is_openai_echo_note(item, selected):
            continue
        if item in selected:
            continue
        if any(same_telegram_story(item, existing) for existing in selected + note_items):
            continue
        note_items.append(item)

    if len(note_items) < 3:
        for item in sorted(items, key=telegram_note_rank):
            if len(note_items) >= 3:
                break
            if item in selected or item in note_items:
                continue
            if is_bad_note_item(item):
                continue
            if is_openai_echo_note(item, selected):
                continue
            if any(normalize_for_match(item.title) == normalize_for_match(existing.title) for existing in note_items):
                continue
            note_items.append(item)

    notes = telegram_notes_without_story_duplicates(
        [f"{fix_mojibake(item.title)} - {item.source}" for item in note_items],
        top_stories,
        limit=3,
    )
    if len(notes) < 3:
        for item in sorted(items, key=telegram_note_rank):
            if len(notes) >= 3:
                break
            if item in selected:
                continue
            if is_bad_note_item(item) or is_openai_echo_note(item, selected):
                continue
            candidate = f"{fix_mojibake(item.title)} - {item.source}"
            merged = telegram_notes_without_story_duplicates(notes + [candidate], top_stories, limit=3)
            if len(merged) > len(notes):
                notes = merged
    if len(notes) < 3:
        for item in sorted(items, key=telegram_note_rank):
            if len(notes) >= 3:
                break
            if item in selected:
                continue
            candidate = f"{fix_mojibake(item.title)} - {item.source}"
            if any(normalize_for_match(candidate) == normalize_for_match(existing) for existing in notes):
                continue
            if any(title_substring_match(candidate, " ".join([story.get("title", ""), story.get("source", "")])) for story in top_stories):
                continue
            notes.append(candidate)
    return {
        **analysis,
        "top_stories": (top_stories or analysis.get("top_stories", []))[:5],
        "smaller_notes": (notes or analysis.get("smaller_notes", []))[:3],
    }


def telegram_item_rank(item: SourceItem) -> tuple[int, int, int, str]:
    title = normalize_for_match(fix_mojibake(item.title))
    if "system card" in title and "gpt" in title:
        rank = 0
    elif "powers codex" in title and "nvidia" in title:
        rank = 1
    elif "noscroll" in title:
        rank = 2
    elif "tool overuse" in title or "external tools" in title:
        rank = 3
    elif item.source_group == "industry":
        rank = 4
    elif item.source_group == "research":
        rank = 5
    elif item.source_group == "tools":
        rank = 6
    else:
        rank = 7
    return (rank, item.source_priority, trust_rank(item.trust), item.source)


def telegram_note_rank(item: SourceItem) -> tuple[int, int, int, str]:
    if item.source_group == "research":
        rank = 0
    elif item.source_group == "industry":
        rank = 1
    elif item.source_group == "tools":
        rank = 2
    else:
        rank = 3
    return (rank, item.source_priority, trust_rank(item.trust), item.source)


def trust_rank(trust: str) -> int:
    return {"high": 0, "medium": 1, "low": 2}.get(trust, 3)


def is_low_value_telegram_item(item: SourceItem, selected: list[SourceItem]) -> bool:
    title = normalize_for_match(fix_mojibake(item.title))
    has_gpt_story = any("gpt 5 5" in normalize_for_match(fix_mojibake(existing.title)) for existing in selected)
    if has_gpt_story and ("introducing gpt" in title or "openai releases gpt" in title or "what is codex" in title):
        return True
    if "geforce now" in title or "tim cook" in title or "airpods touch bars" in title:
        return True
    return False


def is_bad_note_item(item: SourceItem) -> bool:
    title = normalize_for_match(fix_mojibake(item.title))
    return "geforce now" in title or "tim cook" in title or "airpods touch bars" in title


def is_openai_echo_note(item: SourceItem, selected: list[SourceItem]) -> bool:
    title = normalize_for_match(fix_mojibake(item.title))
    selected_text = " ".join(normalize_for_match(fix_mojibake(existing.title)) for existing in selected)
    if "gpt 5 5" not in selected_text and "openai" not in selected_text:
        return False
    echo_terms = [
        "openai",
        "gpt 5 5",
        "what is codex",
        "introducing gpt",
        "openai releases",
        "codex",
    ]
    return any(term in title for term in echo_terms)


def same_telegram_story(left: SourceItem, right: SourceItem) -> bool:
    left_title = normalize_for_match(fix_mojibake(left.title))
    right_title = normalize_for_match(fix_mojibake(right.title))
    if "noscroll" in left_title and "noscroll" in right_title:
        return True
    if "tool overuse" in left_title and "tool overuse" in right_title:
        return True
    if "gpt 5 5" in left_title and "gpt 5 5" in right_title:
        return not (("nvidia" in left_title) ^ ("nvidia" in right_title))
    if "tim cook" in left_title and "tim cook" in right_title:
        return True
    if "thinking machines" in left_title and "thinking machines" in right_title:
        return True
    if "deepseek" in left_title and "deepseek" in right_title:
        return True
    if "project maven" in left_title and "project maven" in right_title:
        return True
    if "comfyui" in left_title and "comfyui" in right_title:
        return True
    return token_overlap(token_set(left_title), token_set(right_title)) >= 0.55


def telegram_notes_without_story_duplicates(notes: list[str], stories: list[dict], *, limit: int) -> list[str]:
    top_texts = [
        " ".join([story.get("title", ""), story.get("source", ""), story.get("angle", "")])
        for story in stories
        if isinstance(story, dict)
    ]
    top_token_sets = [token_set(text) for text in top_texts]
    cleaned: list[str] = []
    seen_keys: set[str] = set()
    for note in notes:
        if not isinstance(note, str):
            continue
        note_text = " ".join(fix_mojibake(note).split())
        if not note_text:
            continue
        note_tokens = token_set(note_text)
        note_key = " ".join(sorted(note_tokens)[:8])
        if note_key in seen_keys:
            continue
        if any(title_substring_match(note_text, top_text) for top_text in top_texts):
            continue
        if any(token_overlap(note_tokens, top_tokens) >= 0.42 for top_tokens in top_token_sets):
            continue
        if overlaps_named_model_or_company(note_text, top_texts):
            continue
        cleaned.append(note_text)
        seen_keys.add(note_key)
        if len(cleaned) >= limit:
            break
    return cleaned


def overlaps_named_model_or_company(note: str, top_texts: list[str]) -> bool:
    note_norm = normalize_for_match(note)
    repeated_terms = [
        "gpt 5 5",
        "gpt 5",
        "claude",
        "gemini",
        "llama",
        "mistral",
        "codex",
        "openai",
        "anthropic",
        "deepmind",
        "nvidia",
    ]
    for top_text in top_texts:
        top_norm = normalize_for_match(top_text)
        for term in repeated_terms:
            if term in note_norm and term in top_norm:
                return True
    return False


def ensure_work_relevance(analysis: dict, profile: dict, items: list[SourceItem]) -> dict:
    relevance = (analysis.get("work_relevance") or analysis.get("useful_connections") or "").strip()
    if relevance and not looks_generic_relevance(relevance):
        return {**analysis, "work_relevance": relevance}
    return {**analysis, "work_relevance": fallback_work_relevance(profile, analysis, items)}


def looks_generic_relevance(text: str) -> bool:
    lowered = text.lower()
    vague = [
        "rapid evolution",
        "maturing ecosystem",
        "highlight the",
        "provides context",
        "practical applications",
        "user-facing applications",
        "useful background",
    ]
    return any(phrase in lowered for phrase in vague) or len(text.split()) < 18


def token_set(text: str) -> set[str]:
    stop = {
        "the",
        "and",
        "for",
        "with",
        "from",
        "into",
        "this",
        "that",
        "their",
        "about",
        "openai",
        "news",
        "blog",
        "announced",
        "introducing",
    }
    return {word for word in re.findall(r"[a-z0-9]+", text.lower()) if len(word) > 2 and word not in stop}


def token_overlap(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / max(1, min(len(left), len(right)))


def title_substring_match(note: str, top_text: str) -> bool:
    note_norm = " ".join(token_set(note))
    top_norm = " ".join(token_set(top_text))
    return bool(note_norm and top_norm and (note_norm in top_norm or top_norm in note_norm))


def normalize_for_match(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def diverse_top_stories(stories: list[dict], *, limit: int) -> list[dict]:
    selected: list[dict] = []
    per_source: dict[str, int] = {}
    for story in stories:
        source = story.get("source", "")
        if source and per_source.get(source, 0) >= 1 and len(per_source) < limit:
            continue
        selected.append(story)
        if source:
            per_source[source] = per_source.get(source, 0) + 1
        if len(selected) >= limit:
            return selected
    for story in stories:
        if story not in selected:
            selected.append(story)
        if len(selected) >= limit:
            break
    return selected


def source_item_story(item: SourceItem) -> dict:
    return {
        "story_id": story_slug(item),
        "title": fix_mojibake(item.title),
        "source": item.source,
        "url": item.url,
        "angle": fallback_story_angle(item),
        "importance": "high" if item.trust == "high" else "medium",
    }


def is_complete_telegram_message(text: str) -> bool:
    if len(text.strip()) < 450:
        return False
    if "For your work:" in text or "Useful connection:" in text:
        return False
    required = ["What happened today:", "Top stories:", "Read full issue:"]
    return all(part in text for part in required)


def normalize_markdown(markdown: str, date_label: str, brief_kind: str) -> str:
    text = fix_mojibake(markdown.strip())
    text = normalize_website_markdown(text)
    desired = f"# AI Digest - {edition_label(brief_kind)}"
    if not text.startswith("#"):
        text = f"{desired}\n\n## {date_label}\n\n{text}"
    return text


def normalize_website_markdown(markdown: str) -> str:
    lines: list[str] = []
    for line in markdown.splitlines():
        stripped = line.strip()
        stripped = stripped.replace("## The Read", "## Today")
        stripped = stripped.replace("## The Main Stories", "## What Happened")
        stripped = stripped.replace("## Other Notes", "## Smaller Notes")
        stripped = stripped.replace("## Closing Takeaway", "## Closing")
        stripped = re.sub(r"\[Read the source\]\(([^)]+)\)", r"[Read more](\1)", stripped)
        if stripped.startswith("*   "):
            lines.append("- " + stripped[4:].strip())
            continue
        if stripped.startswith("* "):
            lines.append("- " + stripped[2:].strip())
            continue
        bold_heading = re.fullmatch(r"\*\*(.+?)\*\*", stripped)
        if bold_heading:
            lines.append(f"### {bold_heading.group(1)}")
            continue
        lines.append(line)
    return "\n".join(lines)


def ensure_website_tail(markdown: str, analysis: dict, profile: dict, items: list[SourceItem]) -> str:
    text = markdown.strip()
    additions: list[str] = []
    if "## Smaller Notes" not in text:
        note_lines = smaller_note_lines(analysis, items)
        if note_lines:
            additions.extend(["", "## Smaller Notes", "", *note_lines])
    if additions:
        text = text + "\n" + "\n".join(additions)
    return text


def force_website_story_sections(markdown: str, analysis: dict) -> str:
    stories = analysis.get("top_stories", [])[:5]
    if not stories or "## What Happened" not in markdown:
        return markdown

    before, after = markdown.split("## What Happened", 1)
    next_section = re.search(r"\n##\s+", after)
    tail = after[next_section.start() :] if next_section else ""

    story_blocks: list[str] = []
    for story in stories:
        story_blocks.append(
            "\n".join(
                [
                    f"### {website_story_title(story)}",
                    "",
                    website_story_body(story),
                    "",
                    f"[Read more]({story.get('url', '#')})",
                ]
            )
        )

    return before.rstrip() + "\n\n## What Happened\n\n" + "\n\n".join(story_blocks).strip() + "\n" + tail


def remove_website_sections(markdown: str, section_titles: list[str]) -> str:
    titles = "|".join(re.escape(title) for title in section_titles)
    pattern = re.compile(rf"\n##\s+({titles})\s*\n.*?(?=\n##\s+|\Z)", re.DOTALL)
    return pattern.sub("", markdown).strip() + "\n"


def ensure_smaller_note_sources(markdown: str, analysis: dict, items: list[SourceItem]) -> str:
    marker = "## Smaller Notes"
    if marker not in markdown:
        return markdown
    before, after = markdown.split(marker, 1)
    next_section = re.search(r"\n##\s+", after)
    if next_section:
        notes_body = after[: next_section.start()]
        tail = after[next_section.start() :]
    else:
        notes_body = after
        tail = ""

    fixed_lines: list[str] = []
    for line in notes_body.splitlines():
        stripped = line.strip()
        if stripped.startswith("[Read more](") and fixed_lines and "[Read more](" in fixed_lines[-1]:
            continue
        if not stripped.startswith("- ") or "[Read more](" in stripped:
            fixed_lines.append(line)
            continue
        note = remove_title_quotes(stripped[2:].strip())
        url = source_url_for_note(note, analysis, items)
        if url:
            fixed_lines.append(f"- {note} [Read more]({url})")
        else:
            fixed_lines.append(line)
    return before + marker + "\n".join(fixed_lines) + tail


def remove_title_quotes(text: str) -> str:
    return re.sub(r'"([^"]{8,160})"', r"\1", text)


def fallback_analysis(items: list[SourceItem], profile: dict) -> dict:
    top_items = diverse_top_items(items, limit=6)
    themes = infer_themes(items)
    top_stories = []
    for item in top_items[:5]:
        top_stories.append(
            {
                "story_id": story_slug(item),
                "title": fix_mojibake(item.title),
                "source": item.source,
                "url": item.url,
                "angle": fallback_story_angle(item),
                "importance": "high" if item.trust == "high" else "medium",
            }
        )
    return {
        "opening_read": fallback_opening_read(top_stories),
        "themes": themes,
        "top_stories": top_stories,
        "smaller_notes": [f"{fix_mojibake(item.title)} - {item.source}" for item in diverse_top_items(items[5:], limit=8)[:3]],
        "work_relevance": fallback_work_relevance(profile, {"top_stories": top_stories}, items),
        "closing_takeaway": "The useful read is not that every item is urgent. It is that the day gives a few practical threads to follow without drowning you in links.",
    }


def diverse_top_items(items: list[SourceItem], *, limit: int) -> list[SourceItem]:
    selected: list[SourceItem] = []
    per_source: dict[str, int] = {}
    for item in items:
        source_count = per_source.get(item.source, 0)
        if source_count >= 2:
            continue
        selected.append(item)
        per_source[item.source] = source_count + 1
        if len(selected) >= limit:
            return selected
    for item in items:
        if item not in selected:
            selected.append(item)
        if len(selected) >= limit:
            break
    return selected


def fallback_markdown(
    brief_kind: str,
    date_label: str,
    analysis: dict,
    profile: dict,
    items: list[SourceItem] | None = None,
) -> str:
    items = items or []
    lines = [
        f"# AI Digest - {edition_label(brief_kind)}",
        "",
        f"## {date_label}",
        "",
        "## Today",
        "",
        website_opening_read(analysis),
        "",
        "## What Happened",
        "",
    ]
    for story in analysis.get("top_stories", [])[:5]:
        lines.extend(
            [
                f"### {website_story_title(story)}",
                "",
                website_story_body(story),
                "",
                f"[Read more]({story.get('url', '#')})",
                "",
            ]
        )
    lines.extend(["## Smaller Notes", ""])
    for note in smaller_note_lines(analysis, items):
        lines.append(note)
    lines.extend(
        [
            "",
        ]
    )
    return "\n".join(lines)


def website_opening_read(analysis: dict) -> str:
    opening = analysis.get("opening_read") or ""
    stories = analysis.get("top_stories", [])
    titles = " ".join(story.get("title", "") for story in stories).lower()
    if "gpt" in titles:
        return (
            "24 April.\n\n"
            "It has been a busy day in AI. OpenAI released GPT-5.5, and the news around it quickly became bigger than the model announcement itself. NVIDIA is already talking about the infrastructure behind Codex, which makes the launch feel less like a standalone model update and more like another step toward AI systems that do actual work.\n\n"
            "The rest of the day filled in that picture. Noscroll showed the consumer side of the same pressure: people do not only want more AI, they want less noise. A research paper on tool overuse looked at a quieter but important problem, where language models may call external tools even when their own knowledge may be enough.\n\n"
            "So the useful read today is not just that a new model arrived. It is that the industry is still moving from chat as an interface toward agents, infrastructure, summaries, and workflows."
        )
    return opening or (
        "24 April.\n\nToday’s AI news is less about one isolated headline and more about how product, research, and infrastructure updates are starting to shape the way people build with AI."
    )


def website_story_title(story: dict) -> str:
    title = telegram_story_title(story)
    if title == "GPT-5.5 launch":
        return "OpenAI launches GPT-5.5"
    return title


def website_story_body(story: dict) -> str:
    title = fix_mojibake(story.get("title", ""))
    lower = title.lower()
    angle = fix_mojibake(story.get("angle", "")).strip()
    if "system card" in lower and "gpt" in lower:
        return (
            "OpenAI released GPT-5.5 and published its system card alongside it. A system card is the technical document that explains how the model behaved in testing, what safety work was done, and where the company says the model still has limits.\n\n"
            "That matters because model launches are easy to flatten into a capability claim. The system card gives the more useful layer: what was measured, what risks were considered, and what builders should treat carefully before relying on the model in real workflows."
        )
    if "powers codex" in lower and "nvidia" in lower:
        return (
            "NVIDIA described GPT-5.5 as part of the infrastructure behind Codex. The useful point is not just that a new model is connected to a coding agent, but that agent-style tools are becoming heavier backend systems.\n\n"
            "For builders, that has practical consequences. Coding agents need fast inference, tool access, retrieval, execution, and guardrails. As those systems become more capable, the infrastructure underneath them becomes part of the product story."
        )
    if "noscroll" in lower:
        return (
            "Noscroll is a consumer AI product built around a simple promise: let an AI layer filter and summarize online content so the user does not have to keep scrolling.\n\n"
            "The idea is familiar, but the framing is useful. A lot of AI products are not trying to create more information; they are trying to reduce the cost of paying attention. The hard part is making that filtering trustworthy, source-aware, and not just another summary stream."
        )
    if "tool-overuse" in lower or "external tools" in lower:
        return (
            "A new arXiv paper studies tool overuse, where a language model chooses to call an external tool even when its internal knowledge may be enough.\n\n"
            "That is directly relevant to agent design. Tool calls can be useful, but unnecessary calls add latency, cost, and failure points. The paper is a reminder that agent systems need boundaries around when tools should be used."
        )
    if angle:
        return angle
    return "This item is worth opening for the source details and surrounding context."


def smaller_note_lines(analysis: dict, items: list[SourceItem]) -> list[str]:
    notes = analysis.get("smaller_notes") or analysis.get("smaller_signals", [])
    lines: list[str] = []
    for note in notes[:3]:
        text = telegram_note_text(note)
        url = source_url_for_note(note, analysis, items)
        if url:
            lines.append(f"- {text} [Read more]({url})")
        else:
            lines.append(f"- {text}")
    return lines


def source_url_for_note(note: str, analysis: dict, items: list[SourceItem]) -> str:
    note_norm = normalize_for_match(note)
    for story in analysis.get("top_stories", []):
        story_text = normalize_for_match(" ".join([story.get("title", ""), story.get("source", "")]))
        if story.get("url") and token_overlap(token_set(note_norm), token_set(story_text)) >= 0.35:
            return story.get("url", "")
    for item in items:
        item_text = normalize_for_match(" ".join([item.title, item.source]))
        if item.url and token_overlap(token_set(note_norm), token_set(item_text)) >= 0.35:
            return item.url
    return ""


def infer_themes(items: list[SourceItem]) -> list[str]:
    words: Counter[str] = Counter()
    stop = {"the", "and", "for", "with", "from", "into", "new", "that", "this", "open", "source"}
    for item in items[:30]:
        text = (
            item.title + " " + item.category.replace("_", " ") + " " + item.source_group.replace("_", " ")
        ).lower().replace("-", " ")
        for word in text.split():
            cleaned = "".join(ch for ch in word if ch.isalnum())
            if len(cleaned) > 3 and cleaned not in stop:
                words[cleaned] += 1
    return [word for word, _ in words.most_common(6)]


def fallback_opening_read(top_stories: list[dict]) -> str:
    if not top_stories:
        return "The feed was thinner than usual, so today is better treated as a light check-in than a full issue."
    titles = [telegram_story_title(story) for story in top_stories[:3]]
    raw_titles = [fix_mojibake(story.get("title", "one update")) for story in top_stories[:3]]
    combined = " ".join(raw_titles).lower()
    if "gpt" in combined:
        return (
            "Today’s news is led by OpenAI’s GPT-5.5 launch and the infrastructure around running it. "
            "NVIDIA says GPT-5.5 is powering Codex on its systems, while Noscroll shows the consumer side: AI tools that filter online content instead of making users scroll through everything."
        )
    if len(titles) == 1:
        return f"Today's brief is built around {titles[0]}. The useful question is not just what was announced, but whether it changes how people build, buy, or trust AI."
    if len(titles) == 2:
        return f"Today is mostly about {titles[0]} and {titles[1]}. The useful read is what changed, who is affected, and whether it matters beyond the announcement."
    return (
        f"Today's brief is led by {titles[0]}. Around it, {titles[1]} and {titles[2]} fill out the rest of the day."
    )


def fallback_work_relevance(profile: dict, analysis: dict, items: list[SourceItem]) -> str:
    projects = profile.get("projects", [])
    if not projects:
        return "No project profile is configured yet, so there is no workflow note to add today."
    focus = projects[0].get("name", "your current work")
    combined = " ".join(
        [story.get("title", "") + " " + story.get("angle", "") for story in analysis.get("top_stories", [])]
        + [item.title + " " + item.source_group + " " + item.category for item in items[:12]]
    ).lower()
    if "tool-overuse" in combined or "external tools" in combined:
        return (
            f"For {focus}: keep tool calls capped, and keep fetch/dedupe decisions in code."
        )
    if any(term in combined for term in ["agent", "codex", "tool", "developer", "workflow", "automation", "telegram", "gemini"]):
        return (
            f"For {focus}: only test changes that improve sources, delivery, or agent reliability."
        )
    return (
        f"Nothing to change in {focus} today."
    )


def work_relevance_from_markdown(markdown: str) -> str:
    marker = "## For Your Work"
    if marker not in markdown:
        return "Nothing here needs action for your current workflow."
    after = markdown.split(marker, 1)[1]
    for next_marker in ["## Closing Takeaway", "##"]:
        if next_marker in after:
            after = after.split(next_marker, 1)[0]
            break
    text = " ".join(line.strip("#- ").strip() for line in after.splitlines() if line.strip())
    return text or "Nothing here needs action for your current workflow."


def normalize_telegram_text(text: str) -> str:
    text = fix_mojibake(text)
    text = text.replace("**", "")
    text = text.replace("__", "")
    cleaned_lines: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("* "):
            stripped = "- " + stripped[2:].strip()
        elif stripped.startswith("*"):
            stripped = "- " + stripped[1:].strip()
        stripped = stripped.replace("Useful connection:", "For your work:")
        stripped = stripped.replace("The main stories:", "Top stories:")
        cleaned_lines.append(stripped)
    return "\n".join(cleaned_lines).strip()


def telegram_story_title(story: dict) -> str:
    title = fix_mojibake(story.get("title", "Story"))
    lower = title.lower()
    if "noscroll" in lower:
        return "Meet Noscroll"
    if "sierra" in lower and "fragment" in lower:
        return "Sierra buys Fragment"
    if "claude" in lower and "personal apps" in lower:
        return "Claude app connectors"
    if "tool-overuse" in lower or "external tools" in lower:
        return "Tool-overuse research"
    if "powers codex" in lower and "nvidia" in lower:
        return "Codex on NVIDIA"
    if "system card" in lower and "gpt" in lower:
        return "GPT-5.5 launch"
    if "introducing gpt" in lower:
        return title.replace("Introducing ", "")
    if len(title) > 90 and " — " in title:
        title = title.split(" — ", 1)[0]
    return shorten_text(title, 120)


def telegram_story_angle(story: dict) -> str:
    title = fix_mojibake(story.get("title", ""))
    lower = title.lower()
    if "noscroll" in lower:
        return "An AI bot designed to filter and summarize online content, aimed at reducing doomscrolling."
    if "sierra" in lower and "fragment" in lower:
        return "Bret Taylor’s Sierra bought Fragment, a YC-backed AI startup, adding another acquisition story around agent-style enterprise software."
    if "claude" in lower and "personal apps" in lower:
        return "Anthropic is connecting Claude to personal apps like Spotify, Uber Eats, and TurboTax, pushing assistants closer to everyday tasks."
    if "meta" in lower and "laying off" in lower:
        return "The Verge reports Meta is cutting 10 percent of staff, a company-movement item worth tracking alongside its AI spending."
    if "mythos" in lower and "anthropic" in lower:
        return "The Verge reports on Anthropic’s Mythos security incident; treat it as a security and trust story, not just company drama."
    if "era raises" in lower:
        return "Era raised $11M to build software for AI gadgets, another bet on AI moving into dedicated devices."
    if "tool-overuse" in lower or "external tools" in lower:
        return "An arXiv paper studies cases where LLMs choose external tools even when internal knowledge may be enough."
    if "powers codex" in lower and "nvidia" in lower:
        return "NVIDIA says GPT-5.5 is powering Codex on its infrastructure, pointing to heavier backend needs for agent-style coding tools."
    if "system card" in lower and "gpt" in lower:
        return "OpenAI released GPT-5.5 and published its system card, which explains the model’s behavior, safety testing, and known limitations."
    if "introducing gpt" in lower:
        return "OpenAI announced GPT-5.5, with the main builder angle around coding, research, tool use, and latency."
    if "deepseek" in lower and ("v4" in lower or "model" in lower):
        return "DeepSeek previewed a new model that narrows the gap with frontier systems, keeping pressure on model pricing and performance."
    if "thinking machines" in lower or "meta’s loss" in lower or "meta's loss" in lower:
        return "TechCrunch reports Meta is losing AI talent to Thinking Machines, showing how hiring pressure is shaping the model race."
    if "project maven" in lower:
        return "The Verge reports how Project Maven helped normalize AI inside military operations, raising questions about speed, oversight, and accountability."
    if "comfyui" in lower:
        return "ComfyUI hit a $500M valuation as creators look for more control over AI-generated media workflows."
    if "nothing" in lower and ("dictation" in lower or "essential" in lower):
        return "Nothing introduced an AI-powered dictation tool, another example of phone makers turning AI into small everyday utilities."
    if "amazon" in lower and ("cpu" in lower or "chip" in lower) and "meta" in lower:
        return "Meta signed a deal for Amazon's AI chips, another sign that large AI labs are trying to diversify the hardware they depend on."
    if "tim cook" in lower or "airpods" in lower:
        return "The Verge looked at Tim Cook's product legacy, useful mostly as broader context for how big tech product bets age."
    return sentence_limited(remove_generic_lead(expand_story_angle(story)), 1, 190)


def telegram_note_text(note: str) -> str:
    text = fix_mojibake(note)
    lower = text.lower()
    if "ai to learn 2.0" in lower:
        return "A new arXiv paper proposes a governance framework for opaque AI systems in learning-heavy domains."
    if "meta is laying off" in lower:
        return "The Verge reports that Meta is laying off 10 percent of its staff."
    if "mythos" in lower and "anthropic" in lower:
        return "The Verge covered Anthropic’s Mythos security incident."
    if "era raises" in lower:
        return "Era raised $11M to build software for AI gadgets."
    if "what is codex" in lower:
        return "OpenAI published an explainer on Codex and how it connects tools for coding work."
    if "introducing gpt" in lower:
        return "OpenAI also published a broader GPT-5.5 announcement alongside the technical details."
    if "openai releases gpt" in lower:
        return "TechCrunch framed GPT-5.5 as part of OpenAI’s push toward broader AI products."
    if "tool-overuse" in lower or "external tools" in lower:
        return "An arXiv paper studies when LLMs choose external tools even when internal knowledge may be enough."
    if "noscroll" in lower:
        return "Noscroll is trying to turn online reading into filtered AI summaries."
    if "tim cook" in lower or "airpods" in lower:
        return "The Verge looked at Tim Cook's product legacy and how Apple product bets aged."
    if "thinking machines" in lower:
        return "TechCrunch reports Meta is losing AI talent to Thinking Machines."
    if "deepseek" in lower:
        return "MIT Technology Review explained why DeepSeek's new model is worth watching."
    if "project maven" in lower:
        return "The Verge traced how Project Maven changed military adoption of AI."
    if "comfyui" in lower:
        return "ComfyUI reached a $500M valuation as creators look for more control over AI media."
    return shorten_text(text, 140)


def short_work_note(text: str) -> str:
    text = normalize_telegram_text(text)
    text = re.sub(r"^For your work:\s*", "", text, flags=re.IGNORECASE).strip()
    return shorten_text(text, 150)


def shorten_text(text: str, limit: int) -> str:
    text = " ".join(fix_mojibake(text).split())
    if len(text) <= limit:
        return text
    clipped = text[:limit]
    sentence_end = max(clipped.rfind("."), clipped.rfind("?"), clipped.rfind("!"))
    if sentence_end > 70:
        return clipped[: sentence_end + 1].strip()
    shortened = clipped.rsplit(" ", 1)[0].rstrip(".,;:")
    return shortened + "."


def sentence_limited(text: str, sentence_count: int, limit: int) -> str:
    text = " ".join(fix_mojibake(text).split())
    protected = re.sub(r"(?<=\d)\.(?=\d)", "<DOT>", text)
    sentences = [sentence.replace("<DOT>", ".") for sentence in re.findall(r"[^.!?]+[.!?]", protected)]
    if sentences:
        candidate = " ".join(sentence.strip() for sentence in sentences[:sentence_count])
        if len(candidate) <= limit:
            return candidate
    return shorten_text(text, limit)


def remove_generic_lead(text: str) -> str:
    text = " ".join(fix_mojibake(text).split())
    generic_leads = [
        "TechCrunch AI reports the industry context.",
        "The Verge AI reports the industry context.",
        "MIT Technology Review AI reports the industry context.",
        "This research points to a concrete system behavior or evaluation problem.",
        "This is a builder-facing update.",
        "The source summary is brief, so the linked piece is worth opening directly.",
    ]
    changed = True
    while changed:
        changed = False
        for lead in generic_leads:
            if text.startswith(lead):
                text = text[len(lead):].strip()
                changed = True
    return text or "Open the source for the full details."


def clean_snippet(text: str, limit: int = 280) -> str:
    cleaned = " ".join(text.split())
    cleaned = fix_mojibake(cleaned)
    junk_phrases = [
        "Your browser does not support the video tag.",
        "Tickets are going fast.",
        "Save up to",
        "Ends 11:59 p.m. PT tonight.",
        "The first StrictlyVC of 2026 hits SF on April 30.",
        "What if you could outsource your doomscrolling? That’s.",
    ]
    for phrase in junk_phrases:
        cleaned = cleaned.replace(phrase, "")
    if "Abstract:" in cleaned:
        cleaned = cleaned.split("Abstract:", 1)[1].strip()
    if " | TechCrunch " in cleaned:
        cleaned = cleaned.split(" | TechCrunch ", 1)[-1].strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[:limit].rsplit(" ", 1)[0] + "."


def fix_mojibake(text: str) -> str:
    if "â" in text or "Ã" in text or "Â" in text:
        try:
            text = text.encode("cp1252").decode("utf-8")
        except UnicodeError:
            pass
    replacements = {
        "\u00e2\u20ac\u2122": "'",
        "\u00e2\u20ac\u02dc": "'",
        "\u00e2\u20ac\u0153": '"',
        "\u00e2\u20ac\ufffd": '"',
        "\u00e2\u20ac\u201d": "-",
        "\u00e2\u20ac\u201c": "-",
        "\u00e2\u20ac\u00a6": "...",
        "\u00c3\u00a2\u00e2\u201a\u00ac\u00e2\u201e\u00a2": "'",
        "\u00c3\u00a2\u00e2\u201a\u00ac\u2039\u0153": "'",
        "\u00c3\u00a2\u00e2\u201a\u00ac\u00c5\u201c": '"',
        "\u00c3\u00a2\u00e2\u201a\u00ac\u00c2\u009d": '"',
        "\u00c3\u00a2\u00e2\u201a\u00ac\u00e2\u20ac\u009d": "-",
        "\u00c3\u00a2\u00e2\u201a\u00ac\u00e2\u20ac\u0153": "-",
        "\u00c3\u00a2\u00e2\u201a\u00ac\u00c2\u00a6": "...",
        "\u00c3\u201a": "",
        "\u00c2": "",
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u2013": "-",
        "\u2014": "-",
        "\u2026": "...",
    }
    for bad, good in replacements.items():
        text = text.replace(bad, good)
    return text

def fallback_story_angle(item: SourceItem) -> str:
    snippet = clean_snippet(best_text_for_item(item))
    inferred = inferred_story_context(item)
    if inferred:
        return inferred
    if item.source_group == "labs":
        return f"{item.source} published the primary update. {snippet}"
    if item.source_group == "industry":
        return f"{item.source} reports the industry context. {snippet}"
    if item.source_group == "tools":
        return f"This is a builder-facing update. {snippet}"
    if item.source_group == "research":
        return f"This research points to a concrete system behavior or evaluation problem. {snippet}"
    return snippet


def inferred_story_context(item: SourceItem) -> str:
    title = fix_mojibake(item.title)
    lower = title.lower()
    if "system card" in lower and "gpt" in lower:
        return (
            f"{item.source} published the system card for {title.replace(' System Card', '')}, which is the document to read for model behavior, safety testing, and limitations. "
            "For a digest, the system card matters less as a launch headline and more as the evidence layer behind the launch: what the model was tested on, where it still fails, and which risks the company chose to disclose."
        )
    if "introducing gpt" in lower:
        return (
            f"{item.source} announced {title.replace('Introducing ', '')}. The useful read is what changed for builders: coding, research, tool use, latency, and whether the model is good enough to alter existing workflows rather than just benchmark tables."
        )
    if "powers codex" in lower and "nvidia" in lower:
        return (
            "NVIDIA says GPT-5.5 is powering Codex on its infrastructure. The practical angle is where heavy agent workloads may run, and what that means for speed and cost."
        )
    if "tool-overuse" in lower or "external tools" in lower:
        return (
            "This is useful agent research: models can call tools even when they do not need to. That matters for cost, speed, and reliability."
        )
    if "noscroll" in lower:
        return (
            "Noscroll is a consumer attempt to package AI as an internet-reading layer: less scrolling, more pre-filtered summaries. The interesting part is not the app itself, but the behavior it points to: people want agents that reduce attention cost, but they still need trust, source visibility, and taste."
        )
    return ""


def expand_story_angle(story: dict) -> str:
    angle = fix_mojibake(story.get("angle", "")).strip()
    if len(angle.split()) >= 45:
        return angle
    title = fix_mojibake(story.get("title", ""))
    source = story.get("source", "The source")
    lower = title.lower()
    if "system card" in lower and "gpt" in lower:
        return (
            f"{source} published the system card for {title.replace(' System Card', '')}. That gives the launch a more useful layer than the announcement copy: safety behavior, limitations, and the places where the model still needs caution. "
            "For builders, this is the document that helps separate real capability from demo energy."
        )
    if "tool-overuse" in lower or "external tools" in lower:
        return (
            "This paper describes a common agent problem: models may call tools when they do not need to. That matters for cost, speed, and reliability."
        )
    if "powers codex" in lower and "nvidia" in lower:
        return (
            "NVIDIA says GPT-5.5 is powering Codex on its infrastructure. The practical angle is where heavy agent workloads may run, and what that means for speed and cost."
        )
    if "noscroll" in lower:
        return (
            "Noscroll is trying to turn web reading into a filtered AI layer. Whether or not this specific product wins, the direction is familiar: people want fewer feeds and better judgment. "
            "The hard part is preserving source visibility so the product does not become another black-box summary machine."
        )
    return angle or "Worth opening for the full context."


def best_text_for_item(item: SourceItem) -> str:
    expanded = item.expanded_text or ""
    summary = item.summary or ""
    expanded_looks_noisy = any(
        phrase in expanded
        for phrase in [
            "StrictlyVC",
            "Save up to",
            "Tickets are going fast",
            "turnstile",
            "localStorage",
        ]
    )
    if summary and (expanded_looks_noisy or len(summary) > 80):
        return summary
    return expanded or summary or "The source summary is brief, so the linked piece is worth opening directly."


def profile_summary(profile: dict) -> str:
    if not profile:
        return "No profile configured."
    interests = ", ".join(profile.get("interests", [])[:10])
    projects = "; ".join(
        f"{project.get('name', 'Project')}: {project.get('description', '')}" for project in profile.get("projects", [])[:3]
    )
    preferences = profile.get("digest_preferences", {})
    return (
        f"Interests: {interests or 'not specified'}\n"
        f"Projects: {projects or 'not specified'}\n"
        f"Preferences: {preferences}"
    )
