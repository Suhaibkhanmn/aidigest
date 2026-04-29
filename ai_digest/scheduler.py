import time
from datetime import datetime
from zoneinfo import ZoneInfo

from .editions import EDITION_SCHEDULE, edition_label
from .pipeline import DigestPipeline


IST = ZoneInfo("Asia/Kolkata")


def run_scheduler(*, mode: str = "normal", allow_delivery: bool = True) -> None:
    """Simple local scheduler for the three daily editions."""
    print("AI Digest scheduler running on Asia/Kolkata time.")
    for edition, wall_time in EDITION_SCHEDULE:
        print(f"- {edition_label(edition)} at {wall_time}")

    completed: set[tuple[str, str]] = set()
    while True:
        now = datetime.now(IST)
        today = now.date().isoformat()
        current = now.strftime("%H:%M")
        for edition, wall_time in EDITION_SCHEDULE:
            key = (today, edition)
            if current == wall_time and key not in completed:
                print(f"Running {edition_label(edition)} at {now.strftime('%Y-%m-%d %H:%M %Z')}")
                try:
                    result = DigestPipeline().run(mode=mode, brief_kind=edition, allow_delivery=allow_delivery)
                    print(f"Saved {result.digest_path}; delivery: {result.delivery_status}")
                except Exception as exc:
                    print(f"{edition_label(edition)} failed: {exc}")
                completed.add(key)

        # Keep the in-memory guard small across days.
        completed = {key for key in completed if key[0] == today}
        time.sleep(20)
