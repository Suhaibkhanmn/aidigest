from datetime import datetime


EDITION_LABELS = {
    "first-light": "Edition 1",
    "midday-note": "Edition 2",
    "night-read": "Edition 3",
}

EDITION_ALIASES = {
    "morning": "first-light",
    "midday": "midday-note",
    "afternoon": "midday-note",
    "evening": "night-read",
    "night": "night-read",
}

EDITION_SCHEDULE = [
    ("first-light", "08:00"),
    ("midday-note", "15:30"),
    ("night-read", "00:30"),
]


def normalize_edition(value: str | None) -> str:
    if not value:
        return ""
    cleaned = value.strip().lower().replace("_", "-")
    return EDITION_ALIASES.get(cleaned, cleaned)


def edition_label(value: str | None) -> str:
    edition = normalize_edition(value)
    return EDITION_LABELS.get(edition, "Edition 3")


def infer_edition(now: datetime) -> str:
    local = now.astimezone()
    minutes = local.hour * 60 + local.minute
    if 8 * 60 <= minutes < (15 * 60 + 30):
        return "first-light"
    if minutes >= 15 * 60 + 30:
        return "midday-note"
    return "night-read"
