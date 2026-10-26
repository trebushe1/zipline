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

DEADLOCK_CHAT = "1231286447131328544"
ANNOUNCEMENTS = "1231311070786752579"


def _ids(name: str, default: str) -> list[str]:
    return [c for c in os.environ.get(name, default).split(",") if c]


# Only messages from these channels (and their threads) are collected.
TRACKED_CHANNELS = _ids("TRACKED_CHANNELS", f"{DEADLOCK_CHAT},{ANNOUNCEMENTS}")
# In these channels every message counts, not only the ones from developers.json.
OPEN_CHANNELS = _ids("OPEN_CHANNELS", ANNOUNCEMENTS)

# Live collector (backend/live.py): fast polling of tracked channels + periodic search for threads.
FAST_SECONDS = float(os.environ.get("FAST_SECONDS", "2"))
SEARCH_SECONDS = float(os.environ.get("SEARCH_SECONDS", "60"))
RUN_SECONDS = int(os.environ.get("RUN_SECONDS", "0"))  # 0 = run forever

# Telegram notifications about new developer messages. Empty = disabled.
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
# Public address of the site, for links in notifications.
SITE_URL = os.environ.get("SITE_URL", "https://trebushe1.github.io/zipline/").rstrip("/") + "/"

# Reddit page (backend/reddit.py). With API keys the scores come from the official API,
# without them from the public RSS feeds (one request a minute, no scores).
REDDIT_SUBREDDIT = os.environ.get("REDDIT_SUBREDDIT", "DeadlockTheGame")
REDDIT_CLIENT_ID = os.environ.get("REDDIT_CLIENT_ID", "")
REDDIT_CLIENT_SECRET = os.environ.get("REDDIT_CLIENT_SECRET", "")
REDDIT_USER_AGENT = os.environ.get("REDDIT_USER_AGENT", f"web:zipline:1.0 (+{SITE_URL})")

# Firebase Realtime Database: the live collector pushes the newest messages there and
# open pages receive them instantly. The URL is public; the service account JSON is secret.
FIREBASE_DB_URL = os.environ.get("FIREBASE_DB_URL", "").rstrip("/")
FIREBASE_SERVICE_ACCOUNT = os.environ.get("FIREBASE_SERVICE_ACCOUNT", "")

# Shared secret for POST /api/ingest. Empty = endpoint disabled.
INGEST_SECRET = os.environ.get("INGEST_SECRET", "")

DEVELOPERS_FILE = ROOT / "backend" / "developers.json"


def load_developers() -> list[dict]:
    return json.loads(DEVELOPERS_FILE.read_text(encoding="utf-8"))
