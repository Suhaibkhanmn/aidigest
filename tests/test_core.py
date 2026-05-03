import unittest
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ai_digest.dedupe import dedupe_items
from ai_digest.editions import edition_label, normalize_edition
from ai_digest.models import SourceItem
from ai_digest.pipeline import (
    daily_items,
    diversify_selected_items,
    is_misleading_risk_item,
    is_noise_item,
    select_digest_items,
    suppress_recent_items,
)
from ai_digest.subscribers import active_chat_ids, deactivate_subscriber, upsert_subscriber
from ai_digest import telegram_bot


def item(
    title: str,
    source: str = "Source",
    category: str = "ai_lab",
    source_group: str = "labs",
    hours_old: int = 1,
) -> SourceItem:
    published = (datetime.now(timezone.utc) - timedelta(hours=hours_old)).isoformat()
    return SourceItem(
        title=title,
        url=f"https://example.com/{title.lower().replace(' ', '-')}",
        source=source,
        published_at=published,
        summary="summary",
        category=category,
        trust="high",
        source_group=source_group,
    )


class CoreTests(unittest.TestCase):
    def test_dedupe_removes_same_url(self):
        first = item("Agent update")
        second = SourceItem(**{**first.__dict__, "title": "Agent update copy"})
        self.assertEqual(len(dedupe_items([first, second])), 1)

    def test_daily_items_filters_old_entries(self):
        fresh = item("Fresh", hours_old=2)
        old = item("Old", hours_old=100)
        self.assertEqual(daily_items([old, fresh], hours=48), [fresh])

    def test_selection_limits_research_domination(self):
        research = [item(f"Paper {idx}", source="arXiv", category="research", source_group="research") for idx in range(20)]
        labs = [item(f"Lab {idx}", source="OpenAI", category="ai_lab", source_group="labs") for idx in range(3)]
        selected = select_digest_items(research + labs, max_items=10)
        groups = [entry.source_group for entry in selected]
        self.assertIn("labs", groups)
        self.assertLessEqual(groups.count("research"), 2)

    def test_noise_item_detects_event_titles(self):
        event_item = item("Grab a ticket today for our AI summit", source="TechCrunch", source_group="industry")
        self.assertTrue(is_noise_item(event_item))

    def test_diversify_interleaves_sources(self):
        openai = [item(f"OpenAI {idx}", source="OpenAI", source_group="labs") for idx in range(3)]
        tech = [item(f"Tech {idx}", source="TechCrunch", source_group="industry") for idx in range(2)]
        mixed = diversify_selected_items(openai + tech)
        self.assertEqual([entry.source for entry in mixed[:3]], ["OpenAI", "TechCrunch", "OpenAI"])

    def test_old_edition_names_are_aliases(self):
        self.assertEqual(normalize_edition("morning"), "first-light")
        self.assertEqual(normalize_edition("midday"), "midday-note")
        self.assertEqual(normalize_edition("evening"), "night-read")
        self.assertEqual(edition_label("night-read"), "Edition 3")

    def test_noise_item_filters_gaming_updates(self):
        gaming = item("GeForce NOW levels up game discovery with Xbox Game Pass", source="NVIDIA Newsroom")
        self.assertTrue(is_noise_item(gaming))

    def test_telegram_subscriber_lifecycle(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "subscribers.json"
            upsert_subscriber({"id": 123, "type": "private", "username": "reader"}, path)
            self.assertEqual(active_chat_ids(path), ["123"])
            self.assertEqual(active_chat_ids(path, brief_kind="night-read"), ["123"])
            self.assertEqual(active_chat_ids(path, brief_kind="unknown"), [])
            self.assertTrue(deactivate_subscriber(123, path))
            self.assertEqual(active_chat_ids(path), [])

    def test_selection_caps_single_source(self):
        openai = [item(f"OpenAI story {idx}", source="OpenAI", source_group="labs") for idx in range(6)]
        tech = [item(f"Tech story {idx}", source="TechCrunch", source_group="industry") for idx in range(4)]
        selected = select_digest_items(openai + tech, max_items=6)
        self.assertLessEqual([entry.source for entry in selected].count("OpenAI"), 2)

    def test_misleading_risk_filters_low_trust_leaks(self):
        base = item("Rumor says a model could launch next week")
        leak = SourceItem(**{**base.__dict__, "trust": "low"})
        self.assertTrue(is_misleading_risk_item(leak))

    def test_telegram_webhook_update_uses_message_handler(self):
        calls = []
        original = telegram_bot.handle_message
        telegram_bot.handle_message = lambda token, message: calls.append((token, message))
        try:
            handled = telegram_bot.handle_update("token", {"message": {"chat": {"id": 123}, "text": "/start"}})
        finally:
            telegram_bot.handle_message = original
        self.assertTrue(handled)
        self.assertEqual(calls[0][0], "token")
        self.assertEqual(calls[0][1]["text"], "/start")

    def test_suppress_recent_items_filters_previous_issue_urls(self):
        old_story = item("AI-generated actors and scripts are now ineligible for Oscars", source="TechCrunch")
        new_story = item("New model release for developers", source="OpenAI", source_group="labs")
        memory = [{"source_urls": [old_story.url], "source_titles": [old_story.title]}]
        fresh = suppress_recent_items([old_story, new_story], memory, min_items=1)
        self.assertEqual(fresh, [new_story])


if __name__ == "__main__":
    unittest.main()
