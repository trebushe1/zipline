"""Notices a new Deadlock build within half a minute and processes it.

The live collector, which runs all the time, watches Steam's public app info: its
change number moves whenever Valve updates the app, including new builds. Then the
patch pipeline runs: on our server (PATCH_PIPELINE=local) right here, in GitHub
Actions otherwise. The pipeline checks the depot itself and does nothing if the
change was not a new build. On the server it also runs every FALLBACK_EVERY seconds,
in case the public app info is late or down.
"""
import asyncio
import logging
import os
import subprocess
import time

import httpx

from . import config, telegram

log = logging.getLogger("zipline.steam_watch")
INFO_URL = "https://api.steamcmd.net/v1/info/1422450"
EVERY = 20  # seconds between checks
COOLDOWN = 120  # at most one pipeline start per 2 minutes; Valve often pushes several changes in a row
FALLBACK_EVERY = 1800
LOCAL = os.environ.get("PATCH_PIPELINE") == "local"


async def change_number(client: httpx.AsyncClient) -> int | None:
    try:
        resp = await client.get(INFO_URL, timeout=20)
        return int(resp.json()["data"]["1422450"]["_change_number"])
    except Exception as exc:
        log.warning("steam app info: %s", exc)
        return None


def start_patch_pipeline() -> str | None:
    """Runs the pipeline; returns a problem report when it failed."""
    if LOCAL:
        # The script holds a lock, so a run that overlaps a running one just waits its turn.
        res = subprocess.run(["scripts/patch_pipeline.sh"], cwd=config.ROOT, capture_output=True, text=True)
        tail = "\n".join((res.stdout + res.stderr).strip().splitlines()[-15:])
        (log.info if res.returncode == 0 else log.error)("patch pipeline exit %s:\n%s", res.returncode, tail)
        return None if res.returncode == 0 else f"⚠️ zipline: патч-конвейер упал (код {res.returncode}).\n\n{tail[-3000:]}"
    if os.environ.get("PUBLISH") != "1":
        log.info("would start the patch workflow (PUBLISH is not 1)")
        return
    res = subprocess.run(["gh", "workflow", "run", "patches.yml"], cwd=config.ROOT, capture_output=True, text=True)
    if res.returncode != 0:
        log.error("patch workflow start failed: %s", res.stderr.strip())
    return None


async def run(client: httpx.AsyncClient, deadline: float) -> None:
    last = await change_number(client)
    started_at = 0.0
    pending = LOCAL  # on the server, check right after a restart
    fallback_at = time.time() + FALLBACK_EVERY
    while time.time() < deadline:
        await asyncio.sleep(EVERY)
        current = await change_number(client)
        if current is not None and last is not None and current != last:
            log.info("Deadlock app info changed: %s -> %s", last, current)
            pending = True
        if current is not None:
            last = current
        if LOCAL and time.time() >= fallback_at:
            pending = True
        if pending and time.time() - started_at >= COOLDOWN:
            started_at, pending = time.time(), False
            fallback_at = started_at + FALLBACK_EVERY
            problem = await asyncio.to_thread(start_patch_pipeline)
            if problem:
                await telegram.send_admin(client, problem)
