"""One collection run for static hosting (GitHub Actions + GitHub Pages).

Loads data/archive.json into a throwaway SQLite database, polls Discord for new
developer messages, writes the archive back and builds the site into site/.

    python -m backend.static_build            # poll once, then build
    python -m backend.static_build --no-poll  # only rebuild the site from the archive
"""
import asyncio
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

# Work on a temporary database; the JSON archive is the source of truth here.
os.environ.setdefault("ZIPLINE_DB", str(Path(tempfile.mkdtemp()) / "zipline.db"))

from . import config, db  # noqa: E402
from .discord_collector import DiscordCollector  # noqa: E402

ARCHIVE = config.ROOT / "data" / "archive.json"
SITE = config.ROOT / "site"
MESSAGE_FIELDS = ("id", "author_id", "guild_id", "channel_id", "channel_name", "parent_channel_id",
                  "thread_name", "time", "edited_time", "text", "attachments", "embeds", "reply_to", "url")


def import_archive() -> dict:
    if not ARCHIVE.exists():
        return {}
    archive = json.loads(ARCHIVE.read_text(encoding="utf-8"))
    with db.session() as conn:
        for dev in archive.get("developers", []):
            db.ensure_author(conn, dev["user_id"], dev.get("name"), dev.get("avatar"), dev.get("tracked", 1))
        for msg in archive.get("messages", []):
            db.upsert_message(conn, msg)
    return archive


async def poll() -> dict:
    started = int(time.time() * 1000)
    if not config.DISCORD_TOKEN:
        return {"ok": False, "at": started, "new": 0, "error": "DISCORD_TOKEN is not set"}
    collector = DiscordCollector()
    try:
        new = len(await collector.poll_once())
        return {"ok": True, "at": started, "new": new, "error": None}
    except Exception as exc:
        return {"ok": False, "at": started, "new": 0, "error": str(exc)}
    finally:
        await collector.client.aclose()


def export(last_poll: dict) -> None:
    with db.session() as conn:
        authors = [dict(r) for r in conn.execute(
            "SELECT user_id, name, avatar, tracked FROM developers WHERE is_demo=0")]
        devs = [{k: a[k] for k in ("user_id", "name", "avatar")} for a in authors if a["tracked"]]
        rows = conn.execute("""
            SELECT m.*, d.name, d.avatar FROM messages m
            LEFT JOIN developers d ON d.user_id = m.author_id
            WHERE m.is_demo = 0 ORDER BY m.time DESC
        """).fetchall()
    messages = []
    for r in rows:
        msg = {k: r[k] for k in MESSAGE_FIELDS}
        if not db.in_tracked_channel(msg):
            continue
        msg["attachments"] = json.loads(msg["attachments"])
        msg["embeds"] = json.loads(msg["embeds"])
        messages.append(msg | {"name": r["name"], "avatar": r["avatar"], "is_demo": False})

    archive_messages = [{k: m[k] for k in MESSAGE_FIELDS} for m in messages]
    ARCHIVE.parent.mkdir(parents=True, exist_ok=True)
    ARCHIVE.write_text(json.dumps({"lastPoll": last_poll, "developers": authors, "messages": archive_messages},
                                  ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    if SITE.exists():
        shutil.rmtree(SITE)
    shutil.copytree(config.ROOT / "frontend", SITE)
    status = {"demo": False, "collector": "live", "ingest": False,
              "lastPoll": last_poll, "pollSeconds": None}
    (SITE / "data.json").write_text(json.dumps(
        {"generatedAt": int(time.time() * 1000), "status": status,
         "developers": devs, "messages": messages}, ensure_ascii=False), encoding="utf-8")
    (SITE / ".nojekyll").touch()


def main() -> int:
    db.init()
    archive = import_archive()
    if "--no-poll" in sys.argv:
        # Publish what the live collector already stored.
        export(archive.get("lastPoll") or {"ok": True, "at": int(time.time() * 1000), "new": 0, "error": None})
        return 0
    last_poll = asyncio.run(poll())
    export(last_poll)
    if last_poll["ok"]:
        print(f"poll ok: {last_poll['new']} new messages")
    else:
        # Annotate the run in the Actions UI, but still publish the site.
        print(f"::warning::Discord poll failed: {last_poll['error']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
