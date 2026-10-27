"""Notices a new Deadlock build within half a minute and processes it.

The live collector, which runs all the time, watches Steam's public app info: its
change number moves whenever Valve updates the app, including new builds. Then the
patch pipeline runs: on our server (PATCH_PIPELINE=local) right here, in GitHub
Actions otherwise. The pipeline checks the depot itself and does nothing if the
change was not a new build. On the server it also runs every FALLBACK_EVERY seconds,
in case the public app info is late or down.

Separately, Valve's own client-version endpoint is checked every VERSION_EVERY
seconds: when the version goes up, a short "new version, details soon" alert goes
to Telegram right away and the pipeline starts without waiting for the cooldown.
"""
import asyncio
import json
import logging
import os
import subprocess
import time

import httpx

from . import config, telegram

log = logging.getLogger("zipline.steam_watch")
INFO_URL = "https://api.steamcmd.net/v1/info/1422450"
VERSION_URL = "https://api.steampowered.com/IGCVersion_1422450/GetClientVersion/v1"
VERSION_EVERY = 10  # seconds; Valve's endpoint, cheap and public
EVERY = 20  # seconds between checks
COOLDOWN = 120  # at most one pipeline start per 2 minutes; Valve often pushes several changes in a row
FALLBACK_EVERY = 1800
LOCAL = os.environ.get("PATCH_PIPELINE") == "local"
ANNOUNCED = config.ROOT / "data" / "announced_version"  # local state, not in git


async def change_number(client: httpx.AsyncClient) -> int | None:
    try:
        resp = await client.get(INFO_URL, timeout=20)
        return int(resp.json()["data"]["1422450"]["_change_number"])
    except Exception as exc:
        log.warning("steam app info: %s", exc)
        return None


async def client_version(client: httpx.AsyncClient) -> int | None:
    try:
        resp = await client.get(VERSION_URL, timeout=15)
        return int(resp.json()["result"]["active_version"])
    except Exception as exc:
        log.warning("steam client version: %s", exc)
        return None


def last_announced() -> int:
    """Newest version we already told about: the tracker's newest build or an earlier instant alert."""
    known = 0
    try:
        builds = json.loads((config.ROOT / "data" / "patches" / "index.json").read_text("utf-8"))["builds"]
        known = max(b["build"] for b in builds)
    except Exception:
        pass
    try:
        known = max(known, int(ANNOUNCED.read_text().strip()))
    except Exception:
        pass
    return known


async def announce(client: httpx.AsyncClient, version: int, previous: int) -> None:
    log.info("new Deadlock client version %s (was %s)", version, previous)
    ANNOUNCED.parent.mkdir(parents=True, exist_ok=True)
    ANNOUNCED.write_text(str(version))
    if telegram.enabled():
        await telegram.send(client, f"⚡ <b>Вышла новая версия Deadlock — {version}</b> (была {previous}).\n"
                                    "Разбираю изменения, подробная сводка придёт через 1–2 минуты.")


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
    urgent = False  # a new client version: start the pipeline without the cooldown
    fallback_at = time.time() + FALLBACK_EVERY

    async def watch_version() -> None:
        # Its own task, so the alert is not held back by a pipeline run in progress.
        nonlocal pending, urgent
        announced = last_announced()
        while time.time() < deadline:
            version = await client_version(client)
            if version and announced and version > announced:
                await announce(client, version, announced)
                announced, pending, urgent = version, True, True
            elif version and not announced:
                announced = version
            await asyncio.sleep(VERSION_EVERY)

    version_task = asyncio.create_task(watch_version())
    try:
        while time.time() < deadline:
            # Short steps, so a new version found by watch_version starts the pipeline quickly.
            for _ in range(EVERY):
                if urgent:
                    break
                await asyncio.sleep(1)
            current = await change_number(client)
            if current is not None and last is not None and current != last:
                log.info("Deadlock app info changed: %s -> %s", last, current)
                pending = True
            if current is not None:
                last = current
            if LOCAL and time.time() >= fallback_at:
                pending = True
            if pending and (urgent or time.time() - started_at >= COOLDOWN):
                started_at, pending, urgent = time.time(), False, False
                fallback_at = started_at + FALLBACK_EVERY
                problem = await asyncio.to_thread(start_patch_pipeline)
                if problem:
                    await telegram.send_admin(client, problem)
    finally:
        version_task.cancel()
