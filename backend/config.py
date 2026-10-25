"""Settings loaded from environment and an optional .env file in the project root."""
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv(ROOT / ".env")

DB_PATH = Path(os.environ.get("ZIPLINE_DB", ROOT / "data" / "zipline.db"))

# Official Deadlock Discord server.
DISCORD_GUILD_ID = os.environ.get("DISCORD_GUILD_ID", "1231286446472691825")
# Token of an account that is a member of the server. Empty = collector disabled.
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN", "")
POLL_SECONDS = int(os.environ.get("POLL_SECONDS", "90"))
BACKFILL_PAGES = int(os.environ.get("BACKFILL_PAGES", "8"))

# Live collector (backend/live.py): fast polling of busy channels + periodic search.
FAST_SECONDS = float(os.environ.get("FAST_SECONDS", "5"))
SEARCH_SECONDS = float(os.environ.get("SEARCH_SECONDS", "60"))
RUN_SECONDS = int(os.environ.get("RUN_SECONDS", "0"))  # 0 = run forever
# Extra channel IDs to poll fast, comma-separated (busy dev channels are picked automatically).
HOT_CHANNELS = [c for c in os.environ.get("HOT_CHANNELS", "").split(",") if c]

# Telegram notifications about new developer messages. Empty = disabled.
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# Shared secret for POST /api/ingest. Empty = endpoint disabled.
INGEST_SECRET = os.environ.get("INGEST_SECRET", "")

DEVELOPERS_FILE = ROOT / "backend" / "developers.json"


def load_developers() -> list[dict]:
    return json.loads(DEVELOPERS_FILE.read_text(encoding="utf-8"))
