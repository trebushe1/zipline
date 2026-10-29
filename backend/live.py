"""Live collector: near-instant tracking of developer messages.

The tracked channels (#deadlock-chat, #announcements) are read directly every
FAST_SECONDS, so new messages show up within seconds — no waiting for the search
index. Threads of those channels where developers posted recently are read every
few ticks, and a guild search every SEARCH_SECONDS catches new threads.
New messages go to Telegram and Firebase (open pages update instantly) right
away; the archive is then committed and the site redeployed (only when running
in GitHub Actions with PUBLISH=1). Alongside, backend/reddit.py keeps the Reddit
page's feed fresh in Firebase.

    python -m backend.live
"""
import asyncio
import logging
import os
import subprocess
import time

import httpx

from . import static_build  # sets up the temporary database; must come first
from . import config, db, meta, presence, realtime, reddit, steam_watch, telegram, translate
from .discord_collector import DiscordCollector, avatar_url, normalize

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("zipline.live")

DAY = 86_400_000
MAX_HOT_THREADS = 5
THREAD_EVERY = 3  # threads are read every N ticks
BACKFILL_EVERY = 8  # seconds between translations of older messages (the free translator dislikes bursts)
PUBLISH_DEBOUNCE = 5  # seconds to gather a burst of messages into one deploy


def now_ms() -> int:
    return int(time.time() * 1000)


def hot_threads() -> list[str]:
    with db.session() as conn:
        rows = conn.execute("""
            SELECT channel_id FROM messages
            WHERE is_demo = 0 AND parent_channel_id IN ({}) AND time > ?
            GROUP BY channel_id ORDER BY MAX(time) DESC LIMIT ?
        """.format(",".join("?" * len(config.TRACKED_CHANNELS))),
            (*config.TRACKED_CHANNELS, now_ms() - 3 * DAY, MAX_HOT_THREADS)).fetchall()
    return [r["channel_id"] for r in rows]


class LiveCollector(DiscordCollector):
    async def latest_id(self, channel_id: str) -> str | None:
        msgs = await self._get(f"/channels/{channel_id}/messages", {"limit": 1})
        return msgs[0]["id"] if msgs else None

    async def read_channel(self, channel_id: str, after: str, tracked: set[str],
                           open_channel: bool = False) -> tuple[str, list[dict]]:
        """Returns the new cursor and the developer messages posted after `after`.

        In an open channel (announcements) every author counts.
        """
        new_rows = []
        while True:
            msgs = await self._get(f"/channels/{channel_id}/messages", {"after": after, "limit": 100})
            if not msgs:
                return after, new_rows
            after = max(msgs, key=lambda m: int(m["id"]))["id"]
            with db.session() as conn:
                for raw in msgs:
                    author = raw["author"]
                    name = author.get("global_name") or author.get("username")
                    if author["id"] in tracked:
                        db.update_developer(conn, author["id"], name, avatar_url(author))
                    elif open_channel:
                        db.ensure_author(conn, author["id"], name, avatar_url(author))
                    else:
                        continue
                    row = normalize(raw, self.channels, self.guild_id)
                    if db.upsert_message(conn, row):
                        new_rows.append(row)
            if len(msgs) < 100:
                return after, new_rows


def author_name(author_id: str) -> str:
    with db.session() as conn:
        row = conn.execute("SELECT name FROM developers WHERE user_id=?", (author_id,)).fetchone()
    return row["name"] if row else "Разработчик"


def publish(last_poll: dict) -> None:
    static_build.export(last_poll)
    if os.environ.get("PUBLISH") != "1":
        return
    run = lambda *cmd: subprocess.run(cmd, cwd=config.ROOT, capture_output=True, text=True)
    run("git", "add", "data/archive.json")
    if run("git", "diff", "--cached", "--quiet").returncode == 0:
        return
    run("git", "commit", "-m", "data: new developer messages")
    run("git", "pull", "--rebase", "-q")
    pushed = run("git", "push", "-q")
    if pushed.returncode != 0:
        log.error("git push failed: %s", pushed.stderr.strip())
        return
    # On our server the push (deploy key) starts deploy.yml by itself; in GitHub Actions it does not.
    if not os.environ.get("GITHUB_ACTIONS"):
        return
    deployed = run("gh", "workflow", "run", "deploy.yml")
    if deployed.returncode != 0:
        log.error("deploy trigger failed: %s", deployed.stderr.strip())


async def main() -> None:
    db.init()
    static_build.import_archive()
    collector = LiveCollector()
    tg = httpx.AsyncClient()  # shared by Telegram and Firebase
    live_site = realtime.Realtime()
    tracked = set()
    deadline = time.time() + config.RUN_SECONDS if config.RUN_SECONDS else float("inf")
    cursors: dict[str, str] = {}  # tracked channels, read every tick
    thread_cursors: dict[str, str] = {}  # recent dev threads, read every THREAD_EVERY ticks
    tick = 0
    last_search = last_threads = 0.0
    unpublished = 0
    first_unpublished_at = 0.0
    publish_task: asyncio.Task | None = None
    log.info("live collector started: fast=%ss search=%ss telegram=%s firebase=%s",
             config.FAST_SECONDS, config.SEARCH_SECONDS, telegram.enabled(), live_site.enabled)

    # Reddit runs on its own clock (one request a minute without API keys); its failures stay its own.
    reddit_feed = reddit.Feed(tg)
    await reddit_feed.load(live_site)
    reddit_task = asyncio.create_task(reddit.run(reddit_feed, live_site, deadline))
    # Hero and item stats from deadlock-api.com every few hours, for the «Мета» page and post drafts.
    meta_task = asyncio.create_task(meta.run(tg, live_site, deadline))
    # New Deadlock builds: start the patch workflow as soon as Steam's app info changes.
    steam_task = asyncio.create_task(steam_watch.run(tg, deadline))

    # Discord online status of the developers, for the dots in the site's developer list.
    with db.session() as conn:
        dev_ids = db.tracked_ids(conn)

    async def presence_changed(state: dict) -> None:
        if live_site.enabled:
            await live_site.put(tg, "live/presence", {"updatedAt": now_ms(), "users": state})

    saved = await live_site.get(tg, "live/presence") if live_site.enabled else None
    watcher = presence.PresenceWatcher(lambda: dev_ids, presence_changed, (saved or {}).get("users"))
    presence_task = asyncio.create_task(watcher.run_forever()) if config.DISCORD_TOKEN else None

    async def notify_telegram(rows: list[dict]) -> None:
        for row in sorted(rows, key=lambda r: r["time"]):
            name = author_name(row["author_id"])
            log.info("NEW %s in #%s: %s", name, row.get("channel_name"), row["text"][:80])
            if telegram.enabled():
                await telegram.send(tg, telegram.format_message(row, name))

    attempted: set[str] = set()  # messages already sent to the translator in this run
    backfilled = 0
    last_backfill_publish = last_backfill_at = time.time()

    async def translate_rows(rows: list[dict]) -> None:
        # Short timeout: a slow translator must not hold the notification back for long.
        results = await asyncio.gather(*(
            translate.translate_message(tg, r["text"], r["embeds"], timeout=3) for r in rows))
        with db.session() as conn:
            for row, (text_ru, embeds_ru) in zip(rows, results):
                row["text_ru"], row["embeds_ru"] = text_ru, embeds_ru
                db.set_translation(conn, row["id"], text_ru, embeds_ru)
                if translate.available():
                    attempted.add(row["id"])  # otherwise the backfill retries it after the pause

    async def backfill_translations() -> bool:
        """Translates one older message. Returns False once nothing is left."""
        nonlocal backfilled
        with db.session() as conn:
            todo = db.untranslated(conn, 1, attempted)
        for msg in todo:
            attempted.add(msg["id"])
            text_ru, embeds_ru = await translate.translate_message(tg, msg["text"], msg["embeds"])
            if not translate.available():
                attempted.discard(msg["id"])  # rate limited: try again after the pause
                return True
            if text_ru or embeds_ru:
                with db.session() as conn:
                    db.set_translation(conn, msg["id"], text_ru, embeds_ru)
                backfilled += 1
        return bool(todo)

    async def handle(rows: list[dict]) -> None:
        nonlocal unpublished, first_unpublished_at
        if not rows:
            return
        await translate_rows(rows)
        jobs = [notify_telegram(rows)]
        if live_site.enabled:
            jobs.append(live_site.push(tg, static_build.site_messages(realtime.LATEST)))
        await asyncio.gather(*jobs)
        first_unpublished_at = first_unpublished_at or time.time()
        unpublished += len(rows)

    token_dead = False

    async def token_rejected(exc: Exception) -> bool:
        """Discord answered 401: the token was revoked. Stop using it and tell the owner once."""
        nonlocal token_dead
        if not (isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 401):
            return False
        if not token_dead:
            token_dead = True
            log.error("Discord rejected the token: Discord polling stopped until a new token is set")
            if presence_task:
                presence_task.cancel()
            await telegram.send_admin(tg, "⚠️ zipline: Discord отклонил токен (401). Сбор сообщений и статусов "
                                          "разработчиков остановлен — нужен новый токен. Steam и Reddit работают.")
        return True

    async def add_cursor(target: dict, channel_id: str) -> None:
        try:
            latest = await collector.latest_id(channel_id)
            target[channel_id] = latest or "0"
        except httpx.HTTPStatusError as exc:
            if await token_rejected(exc):
                return
            log.warning("cannot read channel %s (%s), skipping", channel_id, exc.response.status_code)

    async def read_all(target: dict, tracked: set[str]) -> None:
        for channel_id, after in list(target.items()):
            parent = collector.channels.get(channel_id, {}).get("parent_id")
            is_open = channel_id in config.OPEN_CHANNELS or parent in config.OPEN_CHANNELS
            try:
                target[channel_id], rows = await collector.read_channel(channel_id, after, tracked, is_open)
                await handle(rows)
            except httpx.HTTPStatusError as exc:
                if await token_rejected(exc):
                    return
                if exc.response.status_code in (403, 404):
                    log.warning("lost access to %s, dropping", channel_id)
                    target.pop(channel_id, None)
                else:
                    log.warning("channel %s: %s", channel_id, exc)
            except Exception as exc:
                log.warning("channel %s: %s", channel_id, exc)

    if live_site.enabled:
        await live_site.push(tg, static_build.site_messages(realtime.LATEST))

    try:
        while time.time() < deadline:
            started = time.time()
            with db.session() as conn:
                tracked = set(db.tracked_ids(conn))

            # Search: catches threads/channels we don't poll directly, and gaps between runs.
            if not token_dead and started - last_search >= config.SEARCH_SECONDS:
                last_search = started
                try:
                    await handle(await collector.poll_once())
                except Exception as exc:
                    if not await token_rejected(exc):
                        log.exception("search failed")

            # Refresh the set of polled channels and threads every 10 minutes.
            if not token_dead and started - last_threads >= 600:
                last_threads = started
                for channel_id in config.TRACKED_CHANNELS:
                    if channel_id not in cursors:
                        await add_cursor(cursors, channel_id)
                for channel_id in hot_threads():
                    if channel_id not in thread_cursors:
                        await add_cursor(thread_cursors, channel_id)
                log.info("polling %d channels and %d threads", len(cursors), len(thread_cursors))

            if not token_dead:
                await read_all(cursors, tracked)
            if not token_dead and tick % THREAD_EVERY == 0:
                await read_all(thread_cursors, tracked)
            tick += 1

            # Older messages without a translation are translated gradually and
            # published in one batch, not one deploy per message.
            more = True
            if translate.available() and time.time() - last_backfill_at >= BACKFILL_EVERY:
                last_backfill_at = time.time()
                more = await backfill_translations()
            if backfilled and (not more or time.time() - last_backfill_publish >= 300):
                log.info("translated %d older messages", backfilled)
                unpublished += backfilled
                first_unpublished_at = first_unpublished_at or 1.0
                backfilled, last_backfill_publish = 0, time.time()
                if live_site.enabled:
                    await live_site.push(tg, static_build.site_messages(realtime.LATEST))

            publishing = publish_task is not None and not publish_task.done()
            if unpublished and not publishing and time.time() - first_unpublished_at >= PUBLISH_DEBOUNCE:
                # git + deploy take a few seconds; run them in the background and keep polling.
                publish_task = asyncio.create_task(asyncio.to_thread(
                    publish, {"ok": True, "at": now_ms(), "new": unpublished, "error": None}))
                log.info("publishing %d new messages", unpublished)
                unpublished, first_unpublished_at = 0, 0.0

            await asyncio.sleep(max(0.5, config.FAST_SECONDS - (time.time() - started)))
    finally:
        steam_task.cancel()
        meta_task.cancel()
        if presence_task:
            presence_task.cancel()
        if not reddit_task.done() and time.time() < deadline:
            reddit_task.cancel()  # stopped early: no need to finish the current wait
        try:
            await reddit_task
        except asyncio.CancelledError:
            pass
        except Exception:
            log.exception("reddit collector failed")
        if publish_task:
            await publish_task
        if unpublished:
            publish({"ok": True, "at": now_ms(), "new": unpublished, "error": None})
        await collector.client.aclose()
        await tg.aclose()
        log.info("live collector stopped")


if __name__ == "__main__":
    asyncio.run(main())
