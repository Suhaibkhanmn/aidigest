from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from .config import TELEGRAM_DIR, TELEGRAM_OFFSET_PATH, env_value, load_app_config
from .delivery import send_telegram_message
from .memory import list_digests
from .subscribers import deactivate_subscriber, update_subscriber_preferences, upsert_subscriber


WELCOME_TEXT = """You're subscribed to AI Digest.

New issues arrive here three times a day:
02:30 UTC
10:00 UTC
19:00 UTC

To stop receiving them, send /stop."""


HELP_TEXT = """You're subscribed to AI Digest.

New issues arrive here three times a day:
02:30 UTC
10:00 UTC
19:00 UTC

To stop receiving them, send /stop."""


START_TEXT = "Send /start to subscribe to AI Digest."


def telegram_token() -> str:
    config = load_app_config().get("telegram", {})
    return env_value(config.get("bot_token_env", ""))


def api_call(bot_token: str, method: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    url = f"https://api.telegram.org/bot{bot_token}/{method}"
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method="POST" if payload is not None else "GET")
    with urllib.request.urlopen(request, timeout=65) as response:
        return json.loads(response.read().decode("utf-8"))


def webhook_secret() -> str:
    return env_value("AI_DIGEST_TELEGRAM_WEBHOOK_SECRET")


def public_base_url() -> str:
    return env_value("AI_DIGEST_PUBLIC_BASE_URL").rstrip("/")


def webhook_path() -> str:
    secret = webhook_secret()
    if secret:
        return "/telegram/webhook"
    return "/telegram/webhook"


def webhook_url() -> str:
    base_url = public_base_url()
    if not base_url:
        raise RuntimeError("AI_DIGEST_PUBLIC_BASE_URL is not configured")
    return f"{base_url}{webhook_path()}"


def set_webhook() -> dict[str, Any]:
    bot_token = telegram_token()
    if not bot_token:
        raise RuntimeError("AI_DIGEST_TELEGRAM_BOT_TOKEN is not configured")
    payload: dict[str, Any] = {
        "url": webhook_url(),
        "allowed_updates": ["message"],
        "drop_pending_updates": True,
    }
    secret = webhook_secret()
    if secret:
        payload["secret_token"] = secret
    return api_call(bot_token, "setWebhook", payload)


def delete_webhook() -> dict[str, Any]:
    bot_token = telegram_token()
    if not bot_token:
        raise RuntimeError("AI_DIGEST_TELEGRAM_BOT_TOKEN is not configured")
    return api_call(bot_token, "deleteWebhook", {"drop_pending_updates": True})


def handle_update(bot_token: str, update: dict[str, Any]) -> bool:
    message = update.get("message")
    if not isinstance(message, dict):
        return False
    handle_message(bot_token, message)
    return True


def load_offset(path: Path = TELEGRAM_OFFSET_PATH) -> int:
    if not path.exists():
        return 0
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return int(data.get("offset", 0))
    except Exception:
        return 0


def save_offset(offset: int, path: Path = TELEGRAM_OFFSET_PATH) -> None:
    TELEGRAM_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"offset": offset}, indent=2), encoding="utf-8")


def latest_telegram_text() -> str:
    digests = list_digests()
    for digest in digests:
        telegram_content = digest.get("telegram_content", "")
        if telegram_content:
            return telegram_content
    return "No saved Telegram issue yet. Generate a digest first."


def handle_message(bot_token: str, message: dict[str, Any]) -> None:
    chat = message.get("chat", {})
    chat_id = chat.get("id", "")
    text = str(message.get("text", "")).strip()
    command = text.split()[0].split("@")[0].lower() if text else ""

    if command == "/start":
        upsert_subscriber(chat)
        send_telegram_message(bot_token, chat_id, WELCOME_TEXT)
        return

    if command == "/stop":
        deactivate_subscriber(chat_id)
        send_telegram_message(bot_token, chat_id, "Done. You will not receive AI Digest updates anymore.")
        return

    if command == "/latest":
        send_telegram_message(bot_token, chat_id, latest_telegram_text())
        return

    if command == "/all":
        upsert_subscriber(chat)
        update_subscriber_preferences(chat_id, editions=["first-light", "midday-note", "night-read"])
        send_telegram_message(bot_token, chat_id, "Done. You will receive all three daily editions.")
        return

    if command in {"/firstlight", "/first-light"}:
        upsert_subscriber(chat)
        update_subscriber_preferences(chat_id, editions=["first-light"])
        send_telegram_message(bot_token, chat_id, "Done. You will receive only the 02:30 UTC edition.")
        return

    if command in {"/midday", "/middaynote", "/midday-note"}:
        upsert_subscriber(chat)
        update_subscriber_preferences(chat_id, editions=["midday-note"])
        send_telegram_message(bot_token, chat_id, "Done. You will receive only the 10:00 UTC edition.")
        return

    if command in {"/night", "/nightread", "/night-read"}:
        upsert_subscriber(chat)
        update_subscriber_preferences(chat_id, editions=["night-read"])
        send_telegram_message(bot_token, chat_id, "Done. You will receive only the 19:00 UTC edition.")
        return

    if command == "/help":
        send_telegram_message(bot_token, chat_id, HELP_TEXT)
        return

    send_telegram_message(bot_token, chat_id, HELP_TEXT if is_subscribed(chat_id) else START_TEXT)


def poll_once(bot_token: str, *, timeout: int = 30) -> int:
    offset = load_offset()
    params = urllib.parse.urlencode({"timeout": timeout, "offset": offset, "allowed_updates": json.dumps(["message"])})
    data = api_call(bot_token, f"getUpdates?{params}")
    updates = data.get("result", [])
    next_offset = offset
    for update in updates:
        next_offset = max(next_offset, int(update.get("update_id", 0)) + 1)
        handle_update(bot_token, update)
    if next_offset != offset:
        save_offset(next_offset)
    return len(updates)


def is_subscribed(chat_id: str | int) -> bool:
    from .subscribers import active_chat_ids

    return str(chat_id) in set(active_chat_ids())


def run_polling(*, sleep_seconds: float = 1.0) -> None:
    bot_token = telegram_token()
    if not bot_token:
        raise RuntimeError("AI_DIGEST_TELEGRAM_BOT_TOKEN is not configured")
    print("Telegram bot polling started. Press Ctrl+C to stop.")
    while True:
        try:
            poll_once(bot_token)
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            print(f"Telegram polling error: {exc}")
            time.sleep(5)
        time.sleep(sleep_seconds)
