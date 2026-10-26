"""zipline — Deadlock news tracker. API + static frontend."""
import asyncio
import hmac
import json
import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.staticfiles import StaticFiles

from . import config, db, demo
from .discord_collector import DiscordCollector, avatar_url, normalize

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
DAY = 86_400_000


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init()
    tasks = []
    if config.DISCORD_TOKEN:
        tasks.append(asyncio.create_task(DiscordCollector().run_forever()))
    else:
        demo.seed()
    yield
    for t in tasks:
        t.cancel()


app = FastAPI(title="zipline", lifespan=lifespan)


def _row_to_message(row) -> dict:
    msg = dict(row)
    msg["attachments"] = json.loads(msg["attachments"])
    msg["embeds"] = json.loads(msg["embeds"])
    msg["is_demo"] = bool(msg["is_demo"])
    return msg


@app.get("/api/dev-messages")
def dev_messages(
    q: str = "",
    authors: str = "",
    channel: str = "",
    before: int | None = Query(None, description="Return messages older than this time (ms)"),
    after: int | None = Query(None, description="Return messages newer than this time (ms)"),
    limit: int = Query(30, ge=1, le=100),
):
    where, args = [], []
    if q.strip():
        where.append("(m.text LIKE ? OR m.thread_name LIKE ?)")
        args += [f"%{q.strip()}%"] * 2
    if authors:
        ids = [a for a in authors.split(",") if a]
        where.append(f"m.author_id IN ({','.join('?' * len(ids))})")
        args += ids
    if channel:
        where.append("m.channel_name = ?")
        args.append(channel)
    if before:
        where.append("m.time < ?")
        args.append(before)
    if after:
        where.append("m.time > ?")
        args.append(after)
    sql = f"""
        SELECT m.*, d.name, d.avatar FROM messages m
        LEFT JOIN developers d ON d.user_id = m.author_id
        {'WHERE ' + ' AND '.join(where) if where else ''}
        ORDER BY m.time DESC LIMIT ?
    """
    with db.session() as conn:
        rows = conn.execute(sql, args + [limit + 1]).fetchall()
    return {
        "messages": [_row_to_message(r) for r in rows[:limit]],
        "hasMore": len(rows) > limit,
    }


@app.get("/api/developers")
def developers():
    since = int(time.time() * 1000) - 7 * DAY
    with db.session() as conn:
        rows = conn.execute("""
            SELECT d.user_id, d.name, d.avatar, d.is_demo,
                   COUNT(m.id) AS total,
                   SUM(CASE WHEN m.time > ? THEN 1 ELSE 0 END) AS week,
                   MAX(m.time) AS last_time
            FROM developers d LEFT JOIN messages m ON m.author_id = d.user_id
            WHERE d.tracked = 1
            GROUP BY d.user_id
            ORDER BY last_time IS NULL, last_time DESC, d.name COLLATE NOCASE
        """, (since,)).fetchall()
    return [dict(r) | {"is_demo": bool(r["is_demo"])} for r in rows]


@app.get("/api/channels")
def channels():
    with db.session() as conn:
        rows = conn.execute("""
            SELECT channel_name AS name, COUNT(*) AS total FROM messages
            WHERE channel_name IS NOT NULL GROUP BY channel_name ORDER BY total DESC
        """).fetchall()
    return [dict(r) for r in rows]


@app.get("/api/dev-stats")
def dev_stats():
    now = int(time.time() * 1000)
    with db.session() as conn:
        one = lambda sql, *a: conn.execute(sql, a).fetchone()[0]
        per_day = conn.execute("""
            SELECT CAST((? - time) / ? AS INTEGER) AS days_ago, COUNT(*) AS n
            FROM messages WHERE time > ? GROUP BY days_ago
        """, (now, DAY, now - 30 * DAY)).fetchall()
        top = conn.execute("""
            SELECT d.name, COUNT(*) AS n FROM messages m JOIN developers d ON d.user_id = m.author_id
            WHERE m.time > ? GROUP BY m.author_id ORDER BY n DESC LIMIT 1
        """, (now - 7 * DAY,)).fetchone()
        daily = [0] * 30
        for r in per_day:
            if 0 <= r["days_ago"] < 30:
                daily[29 - r["days_ago"]] = r["n"]
        return {
            "total": one("SELECT COUNT(*) FROM messages"),
            "day": one("SELECT COUNT(*) FROM messages WHERE time > ?", now - DAY),
            "week": one("SELECT COUNT(*) FROM messages WHERE time > ?", now - 7 * DAY),
            "lastTime": one("SELECT MAX(time) FROM messages"),
            "topWeek": dict(top) if top else None,
            "daily": daily,
        }


@app.get("/api/status")
def status():
    with db.session() as conn:
        return {
            "now": int(time.time() * 1000),
            "demo": bool(db.kv_get(conn, "demo", False)),
            "collector": "discord" if config.DISCORD_TOKEN else None,
            "ingest": bool(config.INGEST_SECRET),
            "lastPoll": db.kv_get(conn, "collector"),
            "pollSeconds": config.POLL_SECONDS,
        }


@app.post("/api/ingest")
def ingest(payload: dict, x_ingest_secret: str = Header("")):
    """Accepts raw Discord message objects pushed by an external bridge or bot.

    Body: {"messages": [<discord message>...], "channels": {"<id>": {"name": ..., "parent_id": ..., "is_thread": bool}}}
    """
    if not config.INGEST_SECRET or not hmac.compare_digest(x_ingest_secret, config.INGEST_SECRET):
        raise HTTPException(403, "ingest disabled or wrong secret")
    channels_map = payload.get("channels", {})
    new = 0
    with db.session() as conn:
        tracked = set(db.tracked_ids(conn))
        for raw in payload.get("messages", []):
            if raw["author"]["id"] not in tracked:
                continue
            db.update_developer(conn, raw["author"]["id"],
                                raw["author"].get("global_name") or raw["author"].get("username"),
                                avatar_url(raw["author"]))
            new += db.upsert_message(conn, normalize(raw, channels_map, raw.get("guild_id") or config.DISCORD_GUILD_ID))
        if new and db.kv_get(conn, "demo"):
            db.purge_demo(conn)
    return {"accepted": new}


# Patch summaries written by `python -m backend.patches`; the static build copies them the same way.
(config.ROOT / "data" / "patches").mkdir(parents=True, exist_ok=True)
app.mount("/patches", StaticFiles(directory=config.ROOT / "data" / "patches"), name="patches")
# Hero list and icons from `python -m backend.heroes`, models from its --fetch into data/heroes/models.
(config.ROOT / "data" / "heroes").mkdir(parents=True, exist_ok=True)
app.mount("/heroes", StaticFiles(directory=config.ROOT / "data" / "heroes"), name="heroes")
app.mount("/", StaticFiles(directory=config.ROOT / "frontend", html=True), name="frontend")
