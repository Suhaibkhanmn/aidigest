import argparse
import sys

from .editions import EDITION_LABELS, normalize_edition
from .pipeline import DigestPipeline
from .scheduler import run_scheduler
from .telegram_bot import run_polling
from .web import run_server
from .memory import sync_local_digests_to_remote
from .subscribers import sync_local_subscribers_to_remote


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    parser = argparse.ArgumentParser(description="AI Digest")
    subparsers = parser.add_subparsers(dest="command")

    run_parser = subparsers.add_parser("run", help="Run the digest pipeline")
    run_parser.add_argument("--mode", choices=["normal"], default="normal")
    run_parser.add_argument(
        "--brief",
        choices=["first-light", "midday-note", "night-read", "morning", "midday", "evening"],
        help="Edition to generate. Old morning/evening names still work as aliases.",
    )
    run_parser.add_argument("--send", action="store_true", help="Allow delivery adapters such as Telegram and email")

    subparsers.add_parser("preview-sources", help="Fetch and print source items")

    schedule_parser = subparsers.add_parser("schedule", help="Run the three local scheduled editions")
    schedule_parser.add_argument("--mode", choices=["normal"], default="normal")
    schedule_parser.add_argument("--no-send", action="store_true", help="Run on schedule without delivery")

    subparsers.add_parser("telegram-bot", help="Listen for Telegram /start, /stop, and /latest commands")
    subparsers.add_parser("sync-storage", help="Copy local subscribers and digests into configured cloud storage")

    serve_parser = subparsers.add_parser("serve", help="Start the local web app")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8765)

    args = parser.parse_args()

    if args.command == "run":
        result = DigestPipeline().run(mode=args.mode, brief_kind=normalize_edition(args.brief), allow_delivery=args.send)
        print(f"Digest generated: {result.digest_path}")
        print(f"Edition: {EDITION_LABELS.get(result.brief_kind, result.brief_kind)}")
        print(f"Items fetched: {result.item_count}")
        print(f"Shortlisted: {result.shortlisted_count}")
        print(f"Selected: {result.selected_count}")
        if result.delivery_status:
            print(f"Delivery: {result.delivery_status}")
        return

    if args.command == "schedule":
        run_scheduler(mode=args.mode, allow_delivery=not args.no_send)
        return

    if args.command == "telegram-bot":
        run_polling()
        return

    if args.command == "sync-storage":
        subscribers = sync_local_subscribers_to_remote()
        digests = sync_local_digests_to_remote()
        print(f"Synced subscribers: {subscribers}")
        print(f"Synced digests: {digests}")
        return

    if args.command == "preview-sources":
        result = DigestPipeline().preview_sources()
        for item in result[:20]:
            print(f"- [{item.source_group}/{item.source}] {item.title} ({item.url})")
        print(f"\nFetched {len(result)} items.")
        return

    run_server(host=getattr(args, "host", "127.0.0.1"), port=getattr(args, "port", 8765))
