import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
DIGEST_DIR = DATA_DIR / "digests"
MEMORY_DIR = DATA_DIR / "memory"
TELEGRAM_DIR = DATA_DIR / "telegram"
TELEGRAM_SUBSCRIBERS_PATH = TELEGRAM_DIR / "subscribers.json"
TELEGRAM_OFFSET_PATH = TELEGRAM_DIR / "bot_offset.json"


@dataclass(frozen=True)
class ModeConfig:
    name: str
    provider: str
    writer_model: str
    helper_model: str
    api_key_env: str
    shortlist_limit: int
    selected_limit: int
    send_email_by_default: bool
    send_telegram_by_default: bool
    output_prefix: str


def ensure_dirs() -> None:
    DIGEST_DIR.mkdir(parents=True, exist_ok=True)
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    TELEGRAM_DIR.mkdir(parents=True, exist_ok=True)


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def load_app_config() -> dict[str, Any]:
    return load_json(ROOT / "config.json", {})


def load_sources() -> list[dict[str, Any]]:
    return load_json(ROOT / "sources.json", [])


def load_profile() -> dict[str, Any]:
    return load_json(ROOT / "profile.json", {})


def load_dotenv() -> None:
    path = ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def mode_config(mode: str) -> ModeConfig:
    config = load_app_config()
    modes = config.get("modes", {})
    selected = modes.get(mode, modes.get("normal", {}))
    return ModeConfig(
        name=mode,
        provider=selected.get("provider", "offline"),
        writer_model=selected.get("writer_model", selected.get("model", "local-preview")),
        helper_model=selected.get("helper_model", selected.get("model", "local-preview")),
        api_key_env=selected.get("api_key_env", ""),
        shortlist_limit=int(selected.get("shortlist_limit", 36)),
        selected_limit=int(selected.get("selected_limit", 10)),
        send_email_by_default=bool(selected.get("send_email_by_default", False)),
        send_telegram_by_default=bool(selected.get("send_telegram_by_default", False)),
        output_prefix=selected.get("output_prefix", mode),
    )


def env_value(name: str) -> str:
    load_dotenv()
    return os.environ.get(name, "").strip()
