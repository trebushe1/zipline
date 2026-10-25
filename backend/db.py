"""SQLite storage for developers and their Discord messages."""
import json
import sqlite3
import time
from contextlib import contextmanager

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS developers (
    user_id   TEXT PRIMARY KEY,
    name      TEXT NOT NULL,
    avatar    TEXT,
    tracked   INTEGER NOT NULL DEFAULT 1,
    is_demo   INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS messages (
    id                TEXT PRIMARY KEY,
    author_id         TEXT NOT NULL,
    guild_id          TEXT,
    channel_id        TEXT NOT NULL,
    channel_name      TEXT,
    parent_channel_id TEXT,
    thread_name       TEXT,
    time              INTEGER NOT NULL,
    edited_time       INTEGER,
    text              TEXT NOT NULL DEFAULT '',
    attachments       TEXT NOT NULL DEFAULT '[]',
    embeds            TEXT NOT NULL DEFAULT '[]',
    reply_to          TEXT,
    url               TEXT,
    first_seen        INTEGER NOT NULL,
    is_demo           INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS messages_time ON messages(time DESC);
CREATE INDEX IF NOT EXISTS messages_author ON messages(author_id, time DESC);
CREATE TABLE IF NOT EXISTS kv (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


def connect() -> sqlite3.Connection:
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


@contextmanager
def session():
    conn = connect()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init() -> None:
    with session() as conn:
        conn.executescript(SCHEMA)
        for dev in config.load_developers():
            conn.execute(
                "INSERT INTO developers(user_id, name) VALUES(?, ?) "
                "ON CONFLICT(user_id) DO NOTHING",
                (dev["user_id"], dev["name"]),
            )


def kv_get(conn, key, default=None):
    row = conn.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
    return json.loads(row["value"]) if row else default


def kv_set(conn, key, value) -> None:
    conn.execute(
        "INSERT INTO kv(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, json.dumps(value)),
    )


def tracked_ids(conn) -> list[str]:
    return [r["user_id"] for r in conn.execute(
        "SELECT user_id FROM developers WHERE tracked=1 AND is_demo=0")]


def upsert_message(conn, msg: dict) -> bool:
    """Insert or update a normalized message. Returns True if it was new."""
    exists = conn.execute("SELECT 1 FROM messages WHERE id=?", (msg["id"],)).fetchone()
    conn.execute(
        """
        INSERT INTO messages(id, author_id, guild_id, channel_id, channel_name, parent_channel_id,
                             thread_name, time, edited_time, text, attachments, embeds, reply_to,
                             url, first_seen, is_demo)
        VALUES(:id, :author_id, :guild_id, :channel_id, :channel_name, :parent_channel_id,
               :thread_name, :time, :edited_time, :text, :attachments, :embeds, :reply_to,
               :url, :first_seen, :is_demo)
        ON CONFLICT(id) DO UPDATE SET
            text=excluded.text, edited_time=excluded.edited_time,
            attachments=excluded.attachments, embeds=excluded.embeds,
            channel_name=COALESCE(excluded.channel_name, messages.channel_name),
            thread_name=COALESCE(excluded.thread_name, messages.thread_name)
        """,
        {
            **msg,
            "attachments": json.dumps(msg.get("attachments", [])),
            "embeds": json.dumps(msg.get("embeds", [])),
            "first_seen": int(time.time() * 1000),
            "is_demo": msg.get("is_demo", 0),
        },
    )
    return exists is None


def update_developer(conn, user_id: str, name: str | None, avatar: str | None) -> None:
    conn.execute(
        "UPDATE developers SET name=COALESCE(?, name), avatar=COALESCE(?, avatar) WHERE user_id=?",
        (name, avatar, user_id),
    )


def purge_demo(conn) -> None:
    conn.execute("DELETE FROM messages WHERE is_demo=1")
    conn.execute("DELETE FROM developers WHERE is_demo=1")
    kv_set(conn, "demo", False)
