"""One collection run for static hosting (GitHub Actions + GitHub Pages).

Loads data/archive.json into a throwaway SQLite database, polls Discord for new
developer messages, writes the archive back and builds the site into site/.

    python -m backend.static_build            # poll once, then build
    python -m backend.static_build --no-poll  # only rebuild the site from the archive
"""
import asyncio
import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

# Work on a temporary database; the JSON archive is the source of truth here.
os.environ.setdefault("ZIPLINE_DB", str(Path(tempfile.mkdtemp()) / "zipline.db"))

import httpx  # noqa: E402

from . import config, db, reddit  # noqa: E402
from .discord_collector import DiscordCollector  # noqa: E402

ARCHIVE = config.ROOT / "data" / "archive.json"
SITE = config.ROOT / "site"
PATCHES = config.ROOT / "data" / "patches"
HEROES = config.ROOT / "data" / "heroes"
ITEMS = config.ROOT / "data" / "items"
MESSAGE_FIELDS = ("id", "author_id", "guild_id", "channel_id", "channel_name", "parent_channel_id",
                  "thread_name", "time", "edited_time", "text", "attachments", "embeds", "reply_to", "url",
                  "text_ru", "embeds_ru")


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


def site_messages(limit: int | None = None) -> list[dict]:
    """Messages in the shape the site renders, newest first."""
    with db.session() as conn:
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
        msg["embeds_ru"] = json.loads(msg["embeds_ru"]) if msg["embeds_ru"] else None
        messages.append(msg | {"name": r["name"], "avatar": r["avatar"], "is_demo": False})
        if limit and len(messages) >= limit:
            break
    return messages


def export(last_poll: dict) -> None:
    with db.session() as conn:
        authors = [dict(r) for r in conn.execute(
            "SELECT user_id, name, avatar, tracked FROM developers WHERE is_demo=0")]
    devs = [{k: a[k] for k in ("user_id", "name", "avatar")} for a in authors if a["tracked"]]
    messages = site_messages()

    archive_messages = [{k: m[k] for k in MESSAGE_FIELDS} for m in messages]
    ARCHIVE.parent.mkdir(parents=True, exist_ok=True)
    ARCHIVE.write_text(json.dumps({"lastPoll": last_poll, "developers": authors, "messages": archive_messages},
                                  ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    if SITE.exists():
        shutil.rmtree(SITE)
    shutil.copytree(config.ROOT / "frontend", SITE)
    bust_cache(SITE / "index.html", ("styles.css", "app.js"))
    bust_cache(SITE / "changes.html", ("styles.css", "changes.js"))
    bust_cache(SITE / "heroes.html", ("styles.css", "heroes.js"))
    bust_cache(SITE / "reddit.html", ("styles.css", "reddit.js"))
    bust_cache(SITE / "items.html", ("styles.css", "items.js"))
    (SITE / "reddit.json").write_text(json.dumps(reddit_snapshot(), ensure_ascii=False), encoding="utf-8")
    if PATCHES.exists():
        shutil.copytree(PATCHES, SITE / "patches")
        # Where the changes page listens for builds that are newer than this deploy.
        (SITE / "patches" / "realtime.json").write_text(
            json.dumps({"realtimeUrl": config.FIREBASE_DB_URL or None}), encoding="utf-8")
    if HEROES.exists():
        # Models come from the «heroes» release in the deploy (backend.heroes --fetch).
        shutil.copytree(HEROES, SITE / "heroes", ignore=shutil.ignore_patterns("deps.json", "models"))
    if ITEMS.exists():
        shutil.copytree(ITEMS, SITE / "items", ignore=shutil.ignore_patterns("deps.json", "loc_ru.json"))
    status = {"demo": False, "collector": "live", "ingest": False,
              "lastPoll": last_poll, "pollSeconds": None,
              "realtimeUrl": config.FIREBASE_DB_URL or None}
    (SITE / "data.json").write_text(json.dumps(
        {"generatedAt": int(time.time() * 1000), "status": status,
         "developers": devs, "messages": messages}, ensure_ascii=False), encoding="utf-8")
    (SITE / ".nojekyll").touch()


def reddit_snapshot() -> dict:
    """The Reddit page's data as of the build; open pages then refresh it from Firebase.

    The live collector keeps data/reddit.json on its runner; a plain deploy has only Firebase.
    """
    snapshots = []
    if reddit.FILE.exists():
        snapshots.append(json.loads(reddit.FILE.read_text(encoding="utf-8")))
    if config.FIREBASE_DB_URL:
        try:
            resp = httpx.get(f"{config.FIREBASE_DB_URL}/{reddit.PAGE_PATH}.json", timeout=20)
            if resp.status_code == 200 and resp.json():
                snapshots.append(resp.json())
        except httpx.HTTPError as exc:
            print(f"::warning::Reddit snapshot from Firebase failed: {exc}")
    newest = max(snapshots, key=lambda s: s.get("updatedAt", 0), default={"posts": []})
    return reddit.page(newest) | {"realtimeUrl": config.FIREBASE_DB_URL or None}


def bust_cache(page: Path, assets: tuple[str, ...]) -> None:
    """Point the page at content-hashed asset URLs.

    GitHub Pages lets browsers cache files for 10 minutes, so without this a
    deploy can pair the new HTML with a stale stylesheet or script.
    """
    html = page.read_text(encoding="utf-8")
    for name in assets:
        digest = hashlib.sha1((page.parent / name).read_bytes()).hexdigest()[:10]
        html = html.replace(f'"{name}"', f'"{name}?v={digest}"')
    page.write_text(html, encoding="utf-8")


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
