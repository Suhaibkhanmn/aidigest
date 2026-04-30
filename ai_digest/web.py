import json
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .config import ROOT, env_value, load_app_config
from .memory import list_digests
from .pipeline import DigestPipeline
from .telegram_bot import handle_update, telegram_token, webhook_secret


STATIC_DIR = ROOT / "web"


def run_server(host: str = "127.0.0.1", port: int = 8765) -> None:
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"AI Digest running at http://{host}:{port}")
    server.serve_forever()


class Handler(BaseHTTPRequestHandler):
    def do_HEAD(self) -> None:
        if urlparse(self.path).path == "/":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            return
        self.send_error(404)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            return self.send_file(STATIC_DIR / "index.html", "text/html")
        if parsed.path == "/styles.css":
            return self.send_file(STATIC_DIR / "styles.css", "text/css")
        if parsed.path == "/app.js":
            return self.send_file(STATIC_DIR / "app.js", "application/javascript")
        if parsed.path == "/api/digests":
            return self.send_json({"digests": list_digests()})
        if parsed.path == "/api/public-config":
            return self.send_json(public_config())
        if parsed.path == "/api/preview-sources":
            items = DigestPipeline().preview_sources()[:50]
            return self.send_json(
                {
                    "items": [
                        {
                            "title": item.title,
                            "source": item.source,
                            "source_group": item.source_group,
                            "url": item.url,
                            "published_at": item.published_at,
                            "category": item.category,
                        }
                        for item in items
                    ]
                }
            )
        return self.send_error(404)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/telegram/webhook":
            return self.handle_telegram_webhook()
        if parsed.path == "/api/run":
            query = parse_qs(parsed.query)
            mode = query.get("mode", ["normal"])[0]
            if mode != "normal":
                mode = "normal"
            brief_kind = query.get("brief", [None])[0]
            allow_delivery = query.get("send", ["false"])[0] == "true"
            try:
                result = DigestPipeline().run(mode=mode, brief_kind=brief_kind, allow_delivery=allow_delivery)
            except Exception as exc:
                return self.send_json({"error": str(exc)}, status=500)
            return self.send_json(
                {
                    "mode": result.mode,
                    "brief_kind": result.brief_kind,
                    "digest_path": result.digest_path,
                    "digest_markdown": result.digest_markdown,
                    "telegram_text": result.telegram_text,
                    "item_count": result.item_count,
                    "shortlisted_count": result.shortlisted_count,
                    "selected_count": result.selected_count,
                    "delivery_status": result.delivery_status,
                }
            )
        return self.send_error(404)

    def handle_telegram_webhook(self) -> None:
        configured_secret = webhook_secret()
        if configured_secret:
            received_secret = self.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
            if received_secret != configured_secret:
                return self.send_json({"ok": False, "error": "unauthorized"}, status=401)

        bot_token = telegram_token()
        if not bot_token:
            return self.send_json({"ok": False, "error": "telegram token not configured"}, status=500)

        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            content_length = 0

        try:
            payload = self.rfile.read(content_length).decode("utf-8") if content_length else "{}"
            update = json.loads(payload)
            handled = handle_update(bot_token, update)
        except Exception as exc:
            return self.send_json({"ok": False, "error": str(exc)}, status=500)

        return self.send_json({"ok": True, "handled": handled})

    def send_file(self, path: Path, content_type: str) -> None:
        if not path.exists():
            return self.send_error(404)
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, data: dict, status: int = 200) -> None:
        payload = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args) -> None:
        return


def public_config() -> dict:
    telegram = load_app_config().get("telegram", {})
    username = env_value(telegram.get("bot_username_env", "AI_DIGEST_TELEGRAM_BOT_USERNAME")).lstrip("@")
    if not username:
        username = telegram_username_from_api(telegram)
    return {
        "telegram_bot_username": username,
        "telegram_bot_url": f"https://t.me/{username}" if username else "",
    }


def telegram_username_from_api(telegram_config: dict) -> str:
    token = env_value(telegram_config.get("bot_token_env", "AI_DIGEST_TELEGRAM_BOT_TOKEN"))
    if not token:
        return ""
    try:
        request = urllib.request.Request(f"https://api.telegram.org/bot{token}/getMe")
        with urllib.request.urlopen(request, timeout=8) as response:
            data = json.loads(response.read().decode("utf-8"))
    except Exception:
        return ""
    result = data.get("result", {}) if isinstance(data, dict) else {}
    return str(result.get("username", "")).lstrip("@")
