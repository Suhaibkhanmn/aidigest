import json
import smtplib
import urllib.parse
import urllib.request
from email.message import EmailMessage

from .config import env_value, load_app_config
from .subscribers import active_chat_ids, known_chat_ids


def maybe_send_email(markdown: str, *, subject: str, mode: str, allow_delivery: bool) -> str:
    config = load_app_config().get("email", {})
    if not allow_delivery:
        return "email skipped"
    if mode != "normal":
        return "email skipped for test mode"
    if not config.get("enabled", False):
        return "email disabled"

    host = config.get("smtp_host", "")
    port = int(config.get("smtp_port", 587))
    username = env_value(config.get("username_env", ""))
    password = env_value(config.get("password_env", ""))
    sender = config.get("from", username)
    recipient = config.get("to", "")
    if not all([host, username, password, sender, recipient]):
        return "email not configured"

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = sender
    message["To"] = recipient
    message.set_content(markdown)

    try:
        with smtplib.SMTP(host, port, timeout=30) as smtp:
            smtp.starttls()
            smtp.login(username, password)
            smtp.send_message(message)
    except Exception as exc:
        return f"email failed: {exc}"
    return "email sent"


def maybe_send_telegram(message: str, *, mode: str, allow_delivery: bool, brief_kind: str | None = None) -> str:
    config = load_app_config().get("telegram", {})
    if not allow_delivery:
        return "telegram skipped"
    if mode != "normal":
        return "telegram skipped for test mode"
    if not config.get("enabled", False):
        return "telegram disabled"

    bot_token = env_value(config.get("bot_token_env", ""))
    fallback_chat_id = env_value(config.get("chat_id_env", ""))
    chat_ids = active_chat_ids(brief_kind=brief_kind)
    known_ids = known_chat_ids()
    if fallback_chat_id and fallback_chat_id not in chat_ids and fallback_chat_id not in known_ids:
        chat_ids.append(fallback_chat_id)
    if not bot_token or not chat_ids:
        return "telegram not configured"

    sent = 0
    failed: list[str] = []
    for chat_id in chat_ids:
        try:
            send_telegram_message(bot_token, chat_id, message)
            sent += 1
        except Exception as exc:
            failed.append(f"{chat_id}: {exc}")
    if failed and sent:
        return f"telegram sent to {sent}, failed {len(failed)}"
    if failed:
        return f"telegram failed: {'; '.join(failed[:3])}"
    return f"telegram sent to {sent}"


def send_telegram_message(bot_token: str, chat_id: str | int, message: str) -> None:
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "disable_web_page_preview": False,
    }
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        response.read()
