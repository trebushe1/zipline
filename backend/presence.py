"""Discord online status of the tracked developers.

Presence is only available over the gateway (the websocket the Discord app keeps
open). We connect as an invisible session, ask for the developers' presences
every POLL_SECONDS (slowly, to stay gentle) and also apply PRESENCE_UPDATE events when
Discord sends them.
"""
import asyncio
import json
import logging
import ssl
import time
from collections.abc import Awaitable, Callable

import certifi
import websockets

from . import config

log = logging.getLogger("zipline.presence")
GATEWAY = "wss://gateway.discord.gg/?v=10&encoding=json"
POLL_SECONDS = 120
STATUSES = {"online", "idle", "dnd", "offline"}


def now_ms() -> int:
    return int(time.time() * 1000)


class PresenceWatcher:
    """Keeps `self.state` = {user_id: {"status", "since", "lastOnline"}} up to date."""

    def __init__(self, user_ids: Callable[[], list[str]],
                 on_change: Callable[[dict], Awaitable[None]], state: dict | None = None) -> None:
        self.user_ids = user_ids
        self.on_change = on_change
        self.state: dict[str, dict] = state or {}
        self.ssl = ssl.create_default_context(cafile=certifi.where())
        self.last_push = 0.0

    async def _apply(self, statuses: dict[str, str]) -> None:
        changed = False
        now = now_ms()
        for user_id, status in statuses.items():
            status = status if status in STATUSES else "offline"
            entry = self.state.get(user_id, {})
            if entry.get("status") != status:
                entry = {**entry, "status": status, "since": now}
                changed = True
            if status != "offline":
                entry["lastOnline"] = now
            self.state[user_id] = entry
        # Push on every change, and at least once a minute so the site can tell fresh data from stale.
        if changed or time.time() - self.last_push >= 60:
            self.last_push = time.time()
            await self.on_change(self.state)

    async def run_forever(self) -> None:
        # Reconnect slowly: every new login is one more signal to Discord.
        delay = 60
        while True:
            try:
                await self._session()
                delay = 60
            except websockets.ConnectionClosed as exc:
                if exc.rcvd and exc.rcvd.code == 4004:  # token revoked: retrying only adds failed logins
                    log.error("presence: Discord rejected the token, stopping")
                    return
                log.warning("presence gateway dropped: %s", exc)
            except Exception as exc:  # network blips, gateway restarts
                log.warning("presence gateway dropped: %s", exc)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 900)

    async def _session(self) -> None:
        async with websockets.connect(GATEWAY, max_size=None, ssl=self.ssl) as ws:
            hello = json.loads(await ws.recv())
            interval = hello["d"]["heartbeat_interval"] / 1000
            seq = None

            async def send(op: int, d) -> None:
                await ws.send(json.dumps({"op": op, "d": d}))

            async def heartbeat() -> None:
                while True:
                    await asyncio.sleep(interval)
                    await send(1, seq)

            async def poll() -> None:
                while True:
                    await send(8, {"guild_id": config.DISCORD_GUILD_ID, "user_ids": self.user_ids(),
                                   "presences": True, "limit": 0})
                    await asyncio.sleep(POLL_SECONDS)

            await send(2, {
                "token": config.DISCORD_TOKEN,
                "properties": {"os": "Mac OS X", "browser": "Chrome", "device": ""},
                # Invisible: the collector account never shows up as online.
                "presence": {"status": "invisible", "since": 0, "activities": [], "afk": False},
                "compress": False,
            })
            tasks = [asyncio.create_task(heartbeat())]
            try:
                async for raw in ws:
                    msg = json.loads(raw)
                    seq = msg.get("s") or seq
                    op, event, data = msg["op"], msg.get("t"), msg.get("d")
                    if op in (7, 9):  # reconnect requested / invalid session
                        log.info("gateway asked to reconnect (op %s)", op)
                        return
                    if event == "READY":
                        log.info("presence gateway ready")
                        tasks.append(asyncio.create_task(poll()))
                        # Ask for live updates too; polling covers it if Discord ignores this.
                        await send(14, {"guild_id": config.DISCORD_GUILD_ID, "members": self.user_ids()})
                    elif event == "GUILD_MEMBERS_CHUNK":
                        found = {p["user"]["id"]: p.get("status", "offline") for p in data.get("presences", [])}
                        requested = {m["user"]["id"] for m in data.get("members", [])}
                        # No presence in the chunk means offline.
                        await self._apply({uid: found.get(uid, "offline") for uid in requested})
                    elif event == "PRESENCE_UPDATE" and data["user"]["id"] in set(self.user_ids()):
                        await self._apply({data["user"]["id"]: data.get("status", "offline")})
            finally:
                for task in tasks:
                    task.cancel()
