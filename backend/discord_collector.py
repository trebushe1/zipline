"""Collects developer messages from the Deadlock Discord server.

Uses the guild message search (filtered by author) so that every channel and
thread is covered with a handful of requests per poll. Requires the token of an
account that is a member of the server.
"""
import asyncio
import logging
import time
from datetime import datetime

import httpx

from . import config, db

log = logging.getLogger("zipline.discord")
API = "https://discord.com/api/v10"
PAGE_SIZE = 25  # fixed by Discord for search results


def snowflake_time(snowflake: str) -> int:
    return (int(snowflake) >> 22) + 1420070400000


def _iso_ms(value: str | None) -> int | None:
    return int(datetime.fromisoformat(value).timestamp() * 1000) if value else None


def avatar_url(user: dict) -> str | None:
    if not user.get("avatar"):
        return None
    return f"https://cdn.discordapp.com/avatars/{user['id']}/{user['avatar']}.png?size=64"


def normalize(raw: dict, channels: dict[str, dict], guild_id: str) -> dict:
    """Turns a raw Discord message object into a storage row."""
    channel = channels.get(raw["channel_id"], {})
    parent_id = channel.get("parent_id") if channel.get("is_thread") else None
    parent = channels.get(parent_id, {}) if parent_id else {}
    return {
        "id": raw["id"],
        "author_id": raw["author"]["id"],
        "guild_id": guild_id,
        "channel_id": raw["channel_id"],
        "channel_name": parent.get("name") or channel.get("name"),
        "parent_channel_id": parent_id,
        "thread_name": channel.get("name") if parent_id else None,
        "time": _iso_ms(raw.get("timestamp")) or snowflake_time(raw["id"]),
        "edited_time": _iso_ms(raw.get("edited_timestamp")),
        "text": raw.get("content", ""),
        "attachments": [
            {
                "url": a["url"],
                "name": a.get("filename"),
                "type": a.get("content_type", ""),
                "width": a.get("width"),
                "height": a.get("height"),
            }
            for a in raw.get("attachments", [])
        ],
        "embeds": [
            {k: e.get(k) for k in ("title", "description", "url")}
            | {"image": (e.get("image") or e.get("thumbnail") or {}).get("url")}
            for e in raw.get("embeds", [])
            if e.get("title") or e.get("description")
        ],
        "reply_to": (raw.get("message_reference") or {}).get("message_id"),
        "url": f"https://discord.com/channels/{guild_id}/{raw['channel_id']}/{raw['id']}",
    }


class DiscordCollector:
    def __init__(self) -> None:
        self.guild_id = config.DISCORD_GUILD_ID
        self.channels: dict[str, dict] = {}
        self.channels_loaded_at = 0.0
        self.client = httpx.AsyncClient(
            base_url=API,
            headers={"Authorization": config.DISCORD_TOKEN},
            timeout=30,
        )

    async def _get(self, path: str, params=None):
        for _ in range(5):
            resp = await self.client.get(path, params=params)
            if resp.status_code == 429 or resp.status_code == 202:
                # 429 = rate limited, 202 = search index is still being built.
                wait = float(resp.json().get("retry_after", 5))
                await asyncio.sleep(max(wait, 1))
                continue
            resp.raise_for_status()
            return resp.json()
        raise RuntimeError(f"Discord kept refusing {path}")

    async def refresh_channels(self) -> None:
        if time.time() - self.channels_loaded_at < 3600:
            return
        for ch in await self._get(f"/guilds/{self.guild_id}/channels"):
            self.channels[ch["id"]] = {"name": ch.get("name"), "parent_id": ch.get("parent_id")}
        self.channels_loaded_at = time.time()

    async def search_page(self, author_ids: list[str], offset: int, min_id: str | None):
        params = [("author_id", a) for a in author_ids]
        params += [("sort_by", "timestamp"), ("sort_order", "desc"), ("offset", offset)]
        if min_id:
            params.append(("min_id", min_id))
        data = await self._get(f"/guilds/{self.guild_id}/messages/search", params)
        for th in data.get("threads", []):
            self.channels[th["id"]] = {"name": th.get("name"), "parent_id": th.get("parent_id"), "is_thread": True}
        hits = []
        for group in data.get("messages", []):
            hit = next((m for m in group if m.get("hit")), group[0] if group else None)
            if hit:
                hits.append(hit)
        return hits, data.get("total_results", 0)

    async def poll_once(self) -> list[dict]:
        """Fetches developer messages newer than the newest stored one. Returns the new rows."""
        await self.refresh_channels()
        with db.session() as conn:
            authors = db.tracked_ids(conn)
            newest = conn.execute(
                "SELECT MAX(CAST(id AS INTEGER)) AS id FROM messages WHERE is_demo=0").fetchone()["id"]
        min_id = str(newest) if newest else None
        # First run: backfill a limited history. Later runs: everything newer than min_id.
        max_pages = config.BACKFILL_PAGES if min_id is None else 40

        new_rows = []
        for page in range(max_pages):
            hits, total = await self.search_page(authors, page * PAGE_SIZE, min_id)
            if not hits:
                break
            with db.session() as conn:
                for raw in hits:
                    db.update_developer(conn, raw["author"]["id"],
                                        raw["author"].get("global_name") or raw["author"].get("username"),
                                        avatar_url(raw["author"]))
                    row = normalize(raw, self.channels, self.guild_id)
                    if db.upsert_message(conn, row):
                        new_rows.append(row)
            if (page + 1) * PAGE_SIZE >= total:
                break
            await asyncio.sleep(1.5)
        return new_rows

    async def run_forever(self) -> None:
        log.info("Discord collector started, polling every %ss", config.POLL_SECONDS)
        while True:
            started = int(time.time() * 1000)
            try:
                new = len(await self.poll_once())
                with db.session() as conn:
                    if db.kv_get(conn, "demo"):
                        db.purge_demo(conn)
                    db.kv_set(conn, "collector", {"ok": True, "at": started, "new": new, "error": None})
                log.info("poll ok, %d new messages", new)
            except Exception as exc:  # keep polling through transient failures
                log.exception("poll failed")
                with db.session() as conn:
                    db.kv_set(conn, "collector", {"ok": False, "at": started, "new": 0, "error": str(exc)})
            await asyncio.sleep(config.POLL_SECONDS)
