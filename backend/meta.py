"""Meta: how heroes and items do in real matches, and who won or lost from the last balance patch.

Numbers come from deadlock-api.com (public match analytics, millions of matches). Every
REFRESH_SECONDS the live collector asks for:

  - heroes over the last 7 days and the 7 days before, for all ranks and for Oracle+;
  - the 7 days before the newest balance patch (backend.balance) and the time since it;
  - items over the last 7 days and the 7 days before.

The result goes to Firebase (/live/meta), which the «Мета» page reads. Two post drafts go to
the Telegram chat, once each: «who won from patch N» 48 hours after a balance patch, and
«meta of the week» on Monday morning.

    python -m backend.meta            # compute once and print
"""
import asyncio
import html
import json
import logging
import math
import sys
import time

import httpx

from . import config, telegram
from .patches import plural

log = logging.getLogger("zipline.meta")
API = "https://api.deadlock-api.com/v1/analytics"
DAY = 86_400
WINDOW = 7 * DAY
REFRESH_SECONDS = 3 * 3600
FIREBASE_PATH = "live/meta"
STATE = config.ROOT / "data" / "meta_state.json"  # which posts went out; local, not in git
HEROES = config.ROOT / "data" / "heroes" / "index.json"
ITEMS = config.ROOT / "data" / "items" / "index.json"
PATCHES = config.ROOT / "data" / "patches" / "index.json"
HIGH_RANK = 81  # average badge of the match: Oracle 1 and above
MIN_HERO_MATCHES = {"all": 1500, "high": 400}
MIN_ITEM_MATCHES = 20_000
PATCH_MOVERS = 5  # winners and losers shown for a patch (page, picture, post)
PATCH_POST_AFTER = 48 * 3600
PATCH_POST_UNTIL = 96 * 3600  # an older patch is no news: skip its post
WEEKLY_POST = (0, 7)  # Monday, 07:00 UTC = 10:00 Moscow


def murmur2(key: str, seed: int = 0x31415926) -> int:
    """Source 2 ids of items and abilities: MurmurHash2 of the class name."""
    data, m = key.encode(), 0x5BD1E995
    h = (seed ^ len(data)) & 0xFFFFFFFF
    i = 0
    while len(data) - i >= 4:
        k = (int.from_bytes(data[i:i + 4], "little") * m) & 0xFFFFFFFF
        k = ((k ^ (k >> 24)) * m) & 0xFFFFFFFF
        h = ((h * m) & 0xFFFFFFFF) ^ k
        i += 4
    rest = data[i:]
    if len(rest) == 3:
        h ^= rest[2] << 16
    if len(rest) >= 2:
        h ^= rest[1] << 8
    if rest:
        h = ((h ^ rest[0]) * m) & 0xFFFFFFFF
    h = ((h ^ (h >> 13)) * m) & 0xFFFFFFFF
    return h ^ (h >> 15)


def read_json(path, default):
    try:
        return json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return default


async def fetch(client: httpx.AsyncClient, what: str, start: int, end: int, min_badge: int | None = None) -> list[dict]:
    params = {"min_unix_timestamp": start, "max_unix_timestamp": end}
    if min_badge:
        params["min_average_badge"] = min_badge
    resp = await client.get(f"{API}/{what}", params=params, timeout=60)
    resp.raise_for_status()
    return resp.json()


def hero_table(cur: list[dict], prev: list[dict], heroes: dict, min_matches: int) -> list[dict]:
    games = sum(h["matches"] for h in cur) / 12 or 1
    prev_games = sum(h["matches"] for h in prev) / 12 or 1
    before = {h["hero_id"]: h for h in prev}
    rows = []
    for h in cur:
        info = heroes.get(h["hero_id"])
        if not info or h["matches"] < min_matches:
            continue
        wr, pick = h["wins"] / h["matches"] * 100, h["matches"] / games * 100
        row = {"id": h["hero_id"], "key": info["key"], "name": info["name"], "icon": info.get("icon"),
               "matches": h["matches"], "wr": round(wr, 2), "pick": round(pick, 2)}
        if (p := before.get(h["hero_id"])) and p["matches"] >= min_matches:
            row["wrDelta"] = round(wr - p["wins"] / p["matches"] * 100, 2)
            row["pickDelta"] = round(pick - p["matches"] / prev_games * 100, 2)
        rows.append(row)
    return sorted(rows, key=lambda r: -r["wr"])


def item_table(cur: list[dict], prev: list[dict], items: dict) -> list[dict]:
    before = {i["item_id"]: i for i in prev}
    rows = []
    for i in cur:
        info = items.get(i["item_id"])
        if not info or i["matches"] < MIN_ITEM_MATCHES:
            continue
        wr = i["wins"] / i["matches"] * 100
        row = {"key": info["key"], "name": info["name"], "tier": info.get("tier"), "slot": info.get("slot"),
               "matches": i["matches"], "wr": round(wr, 2)}
        if (p := before.get(i["item_id"])) and p["matches"] >= MIN_ITEM_MATCHES:
            row["wrDelta"] = round(wr - p["wins"] / p["matches"] * 100, 2)
            row["usageDelta"] = round((i["matches"] / p["matches"] - 1) * 100, 1)
        rows.append(row)
    return sorted(rows, key=lambda r: -r["wr"])


def movers(rows: list[dict], n: int = 3, at_least: float = 0.5) -> tuple[list[dict], list[dict]]:
    moved = [r for r in rows if abs(r.get("wrDelta", 0)) >= at_least]
    up = sorted((r for r in moved if r["wrDelta"] > 0), key=lambda r: -r["wrDelta"])[:n]
    down = sorted((r for r in moved if r["wrDelta"] < 0), key=lambda r: r["wrDelta"])[:n]
    return up, down


def last_balance_patch() -> dict | None:
    builds = read_json(PATCHES, {}).get("builds", [])
    with_balance = [b for b in builds if (b.get("counts") or {}).get("balance")]
    return max(with_balance, key=lambda b: b["time"], default=None)


async def compute(client: httpx.AsyncClient) -> dict:
    heroes = {h["id"]: h for h in read_json(HEROES, {}).get("heroes", [])}
    item_list = read_json(ITEMS, {})
    item_list = item_list.get("items", []) if isinstance(item_list, dict) else item_list
    items = {murmur2(i["key"]): {"key": i["key"], "name": (i.get("name") or {}).get("en") if isinstance(i.get("name"), dict)
                                 else i.get("name") or i["key"], "tier": i.get("tier"), "slot": i.get("slot")}
             for i in item_list}
    now = int(time.time())
    week, prev_week = (now - WINDOW, now), (now - 2 * WINDOW, now - WINDOW)

    out = {"updatedAt": now * 1000, "window": {"start": week[0] * 1000, "end": week[1] * 1000},
           "source": "deadlock-api.com"}
    for rank, badge in (("all", None), ("high", HIGH_RANK)):
        cur = await fetch(client, "hero-stats", *week, badge)
        prev = await fetch(client, "hero-stats", *prev_week, badge)
        out[f"heroes_{rank}"] = hero_table(cur, prev, heroes, MIN_HERO_MATCHES[rank])
        out[f"games_{rank}"] = round(sum(h["matches"] for h in cur) / 12)

    if patch := last_balance_patch():
        start = int(patch["time"] / 1000)
        if now - start >= 6 * 3600:  # the first hours say little
            after = await fetch(client, "hero-stats", start, min(now, start + WINDOW))
            before = await fetch(client, "hero-stats", start - WINDOW, start)
            rows = hero_table(after, before, heroes, MIN_HERO_MATCHES["all"] // 3)
            up, down = movers(rows, PATCH_MOVERS)
            out["patch"] = {"build": patch["build"], "file": patch["file"], "time": patch["time"],
                            "hours": round((min(now, start + WINDOW) - start) / 3600),
                            "games": round(sum(h["matches"] for h in after) / 12),
                            "rows": rows, "winners": [r["key"] for r in up], "losers": [r["key"] for r in down]}

    cur = await fetch(client, "item-stats", *week)
    prev = await fetch(client, "item-stats", *prev_week)
    out["items"] = item_table(cur, prev, items)
    return out


# ── Telegram drafts ────────────────────────────────────

def games(n: int) -> str:
    return plural(n, "матч", "матча", "матчей").replace(str(n), f"{n:,}".replace(",", " "), 1)


def pct(x: float) -> str:
    return f"{x:.1f}%".replace(".", ",")


def delta(x: float) -> str:
    return ("+" if x > 0 else "−") + f"{abs(x):.1f}".replace(".", ",")


def patch_post(meta: dict) -> str:
    p = meta["patch"]
    by_key = {r["key"]: r for r in p["rows"]}
    lines = [f"📊 <b>Кто выиграл от патча {p['build']}</b>",
             f"{p['hours']} ч после патча · {games(p['games'])}", ""]
    for title, keys in (("🟢 Усилились", p["winners"]), ("🔴 Просели", p["losers"])):
        if keys:
            lines.append(f"<b>{title}</b>")
            lines += [f"{html.escape(by_key[k]['name'])} — {pct(by_key[k]['wr'])} ({delta(by_key[k]['wrDelta'])})" for k in keys]
            lines.append("")
    lines.append(f"Винрейт по всем рангам, сравнение с неделей до патча. Данные deadlock-api.com")
    lines.append(f'<a href="{html.escape(config.SITE_URL)}meta.html">Вся мета на сайте</a>')
    return "\n".join(lines)


def weekly_post(meta: dict) -> str:
    heroes = meta["heroes_all"]
    up, down = movers(heroes)
    popular = sorted(heroes, key=lambda r: -r["pick"])[:3]
    lines = ["📊 <b>Мета недели в Deadlock</b>", f"{games(meta['games_all'])} за 7 дней", "",
             "<b>🏆 Лучший винрейт</b>"]
    lines += [f"{i}. {html.escape(r['name'])} — {pct(r['wr'])}" for i, r in enumerate(heroes[:5], 1)]
    lines += ["", "<b>🔥 Самые популярные</b>"]
    lines += [f"{html.escape(r['name'])} — в {pct(r['pick'])} матчей" for r in popular]
    if up or down:
        lines += ["", "<b>📈 Изменения за неделю</b>"]
        lines += [f"🟢 {html.escape(r['name'])} {delta(r['wrDelta'])}" for r in up]
        lines += [f"🔴 {html.escape(r['name'])} {delta(r['wrDelta'])}" for r in down]
    high = meta.get("heroes_high") or []
    if high:
        lines += ["", "<b>👑 Oracle+</b>: " + ", ".join(f"{html.escape(r['name'])} {pct(r['wr'])}" for r in high[:3])]
    lines += ["", "Данные deadlock-api.com", f'<a href="{html.escape(config.SITE_URL)}meta.html">Вся мета на сайте</a>']
    return "\n".join(lines)


async def send_drafts(client: httpx.AsyncClient, meta: dict) -> None:
    if not telegram.enabled():
        return
    state = read_json(STATE, {})
    now = time.gmtime()
    age = (time.time() * 1000 - p["time"]) / 1000 if (p := meta.get("patch")) else 0
    if p and PATCH_POST_AFTER <= age < PATCH_POST_UNTIL and p["build"] not in state.get("patches", []):
        if await telegram.send(client, patch_post(meta)):
            state.setdefault("patches", []).append(p["build"])
    week = time.strftime("%G-W%V", now)
    if (now.tm_wday, now.tm_hour) >= WEEKLY_POST and now.tm_wday == WEEKLY_POST[0] and state.get("week") != week:
        if await telegram.send(client, weekly_post(meta)):
            state["week"] = week
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state), "utf-8")


async def run(client: httpx.AsyncClient, live_site=None, deadline: float = math.inf) -> None:
    while time.time() < deadline:
        try:
            meta = await compute(client)
            if live_site and live_site.enabled:
                await live_site.put(client, FIREBASE_PATH, meta)
            await send_drafts(client, meta)
            log.info("meta refreshed: %s heroes, %s items, patch %s", len(meta["heroes_all"]), len(meta["items"]),
                     (meta.get("patch") or {}).get("build"))
        except Exception:
            log.exception("meta refresh failed")
        await asyncio.sleep(max(0.0, min(REFRESH_SECONDS, deadline - time.time())))


async def _main() -> None:
    async with httpx.AsyncClient() as client:
        meta = await compute(client)
    print(f"{meta['games_all']} games; top 5:", [(r["name"], r["wr"], r.get("wrDelta")) for r in meta["heroes_all"][:5]])
    if "patch" in meta:
        print(f"patch {meta['patch']['build']} ({meta['patch']['hours']} h):", meta["patch"]["winners"], meta["patch"]["losers"])
    print("items:", [(r["name"], r["wr"]) for r in meta["items"][:5]])
    print("\n--- weekly draft ---\n" + weekly_post(meta))
    if "patch" in meta:
        print("\n--- patch draft ---\n" + patch_post(meta))


if __name__ == "__main__":
    asyncio.run(_main())
