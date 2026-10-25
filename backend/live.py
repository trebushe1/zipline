"""Live collector: near-instant tracking of developer messages.

Busy channels (and threads where developers posted recently) are read directly
every FAST_SECONDS, so new messages show up within seconds — no waiting for the
search index. A guild search every SEARCH_SECONDS catches everything else.
New messages go to Telegram immediately; the archive is then committed and the
site redeployed (only when running in GitHub Actions with PUBLISH=1).

    python -m backend.live
"""
import asyncio
import logging
import os
import subprocess
import time

import httpx

from . import static_build  # sets up the temporary database; must come first
from . import config, db, telegram
from .discord_collector import DiscordCollector, avatar_url, normalize

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("zipline.live")

DAY = 86_400_000
MAX_HOT_CHANNELS = 4
MAX_HOT_THREADS = 8
PUBLISH_DEBOUNCE = 15  # seconds to gather a burst of messages into one deploy


def now_ms() -> int:
    return int(time.time() * 1000)


def hot_channels() -> list[str]:
    with db.session() as conn:
        rows = conn.execute("""
            SELECT channel_id FROM messages
            WHERE is_demo = 0 AND parent_channel_id IS NULL AND time > ?
            GROUP BY channel_id ORDER BY COUNT(*) DESC LIMIT ?
        """, (now_ms() - 60 * DAY, MAX_HOT_CHANNELS)).fetchall()
    return list(dict.fromkeys([r["channel_id"] for r in rows] + config.HOT_CHANNELS))


def hot_threads() -> list[str]:
    with db.session() as conn:
        rows = conn.execute("""
            SELECT channel_id FROM messages
            WHERE is_demo = 0 AND parent_channel_id IS NOT NULL AND time > ?
            GROUP BY channel_id ORDER BY MAX(time) DESC LIMIT ?
        """, (now_ms() - 3 * DAY, MAX_HOT_THREADS)).fetchall()
    return [r["channel_id"] for r in rows]


class LiveCollector(DiscordCollector):
    async def latest_id(self, channel_id: str) -> str | None:
        msgs = await self._get(f"/channels/{channel_id}/messages", {"limit": 1})
        return msgs[0]["id"] if msgs else None

    async def read_channel(self, channel_id: str, after: str, tracked: set[str]) -> tuple[str, list[dict]]:
        """Returns the new cursor and the developer messages posted after `after`."""
        new_rows = []
        while True:
            msgs = await self._get(f"/channels/{channel_id}/messages", {"after": after, "limit": 100})
            if not msgs:
                return after, new_rows
            after = max(msgs, key=lambda m: int(m["id"]))["id"]
            with db.session() as conn:
                for raw in msgs:
                    if raw["author"]["id"] not in tracked:
                        continue
                    db.update_developer(conn, raw["author"]["id"],
                                        raw["author"].get("global_name") or raw["author"].get("username"),
                                        avatar_url(raw["author"]))
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
    deployed = run("gh", "workflow", "run", "deploy.yml")
    if deployed.returncode != 0:
        log.error("deploy trigger failed: %s", deployed.stderr.strip())


async def main() -> None:
    db.init()
    static_build.import_archive()
    collector = LiveCollector()
    tg = httpx.AsyncClient()
    tracked = set()
    deadline = time.time() + config.RUN_SECONDS if config.RUN_SECONDS else float("inf")
    cursors: dict[str, str] = {}
    last_search = last_threads = 0.0
    unpublished = 0
    first_unpublished_at = 0.0
    log.info("live collector started: fast=%ss search=%ss telegram=%s",
             config.FAST_SECONDS, config.SEARCH_SECONDS, telegram.enabled())

    async def handle(rows: list[dict]) -> None:
        nonlocal unpublished, first_unpublished_at
        for row in sorted(rows, key=lambda r: r["time"]):
            name = author_name(row["author_id"])
            log.info("NEW %s in #%s: %s", name, row.get("channel_name"), row["text"][:80])
            if telegram.enabled():
                await telegram.send(tg, telegram.format_message(row, name))
        if rows:
            first_unpublished_at = first_unpublished_at or time.time()
            unpublished += len(rows)

    async def add_cursor(channel_id: str) -> None:
        try:
            latest = await collector.latest_id(channel_id)
            cursors[channel_id] = latest or "0"
        except httpx.HTTPStatusError as exc:
            log.warning("cannot read channel %s (%s), skipping", channel_id, exc.response.status_code)

    try:
        while time.time() < deadline:
            started = time.time()
            with db.session() as conn:
                tracked = set(db.tracked_ids(conn))

            # Search: catches threads/channels we don't poll directly, and gaps between runs.
            if started - last_search >= config.SEARCH_SECONDS:
                last_search = started
                try:
                    await handle(await collector.poll_once())
                except Exception:
                    log.exception("search failed")

            # Refresh the set of fast-polled channels every 10 minutes.
            if started - last_threads >= 600:
                last_threads = started
                for channel_id in hot_channels() + hot_threads():
                    if channel_id not in cursors:
                        await add_cursor(channel_id)
                log.info("fast-polling %d channels/threads", len(cursors))

            for channel_id, after in list(cursors.items()):
                try:
                    cursors[channel_id], rows = await collector.read_channel(channel_id, after, tracked)
                    await handle(rows)
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code in (403, 404):
                        log.warning("lost access to %s, dropping", channel_id)
                        cursors.pop(channel_id, None)
                    else:
                        log.warning("channel %s: %s", channel_id, exc)
                except Exception as exc:
                    log.warning("channel %s: %s", channel_id, exc)

            if unpublished and time.time() - first_unpublished_at >= PUBLISH_DEBOUNCE:
                publish({"ok": True, "at": now_ms(), "new": unpublished, "error": None})
                log.info("published %d new messages", unpublished)
                unpublished, first_unpublished_at = 0, 0.0

            await asyncio.sleep(max(0.5, config.FAST_SECONDS - (time.time() - started)))
    finally:
        if unpublished:
            publish({"ok": True, "at": now_ms(), "new": unpublished, "error": None})
        await collector.client.aclose()
        await tg.aclose()
        log.info("live collector stopped")


if __name__ == "__main__":
    asyncio.run(main())
