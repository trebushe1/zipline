"""Notices a new Deadlock build within a minute and starts the patch workflow.

GitHub runs scheduled workflows late or skips them (every 20 minutes turned into
every 5-6 hours), so the live collector, which runs all the time, watches Steam's
public app info: its change number moves whenever Valve updates the app, including
new builds. The patch workflow then checks the depot itself and does nothing if the
change was not a new build.
"""
import asyncio
import logging
import os
import subprocess
import time

import httpx

from . import config

log = logging.getLogger("zipline.steam_watch")
INFO_URL = "https://api.steamcmd.net/v1/info/1422450"
EVERY = 60  # seconds between checks
COOLDOWN = 300  # at most one workflow start per 5 minutes; Valve often pushes several changes in a row


async def change_number(client: httpx.AsyncClient) -> int | None:
    try:
        resp = await client.get(INFO_URL, timeout=20)
        return int(resp.json()["data"]["1422450"]["_change_number"])
    except Exception as exc:
        log.warning("steam app info: %s", exc)
        return None


def start_patch_workflow() -> None:
    if os.environ.get("PUBLISH") != "1":
        log.info("would start the patch workflow (PUBLISH is not 1)")
        return
    res = subprocess.run(["gh", "workflow", "run", "patches.yml"], cwd=config.ROOT, capture_output=True, text=True)
    if res.returncode != 0:
        log.error("patch workflow start failed: %s", res.stderr.strip())


async def run(client: httpx.AsyncClient, deadline: float) -> None:
    last = await change_number(client)
    started_at = 0.0
    pending = False
    while time.time() < deadline:
        await asyncio.sleep(EVERY)
        current = await change_number(client)
        if current is not None and last is not None and current != last:
            log.info("Deadlock app info changed: %s -> %s", last, current)
            pending = True
        if current is not None:
            last = current
        if pending and time.time() - started_at >= COOLDOWN:
            await asyncio.to_thread(start_patch_workflow)
            started_at, pending = time.time(), False
