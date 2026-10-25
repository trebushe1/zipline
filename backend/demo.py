"""Demo data so the site has something to show before the collector is configured.

Authors are fictional on purpose: no words are put in real developers' mouths.
Everything here is flagged is_demo and removed after the first successful real poll.
"""
import time

from . import db

DEVS = [
    ("demo-ashby", "Ashby (демо)"),
    ("demo-mira", "Mira (демо)"),
    ("demo-teddy", "Teddy (демо)"),
]

# (minutes ago, author, channel, thread, text, extras)
MESSAGES = [
    (14, "demo-ashby", "deadlock-chat", None,
     "Client update going out shortly with a fix for heroes occasionally getting stuck on the zipline transfer at mid boss.", {}),
    (52, "demo-mira", "deadlock-chat", "Matchmaking feedback megathread",
     "Thanks for the reports. We're looking at queue times in the lower brackets this week.", {}),
    (180, "demo-ashby", "deadlock-chat", None,
     "Backend update to fix the issue where some players couldn't reconnect after a disconnect.", {}),
    (60 * 7, "demo-teddy", "bug-reports", "Shop UI overlapping on 21:9",
     "Repro'd on our side, fix is in the next client build.", {}),
    (60 * 20, "demo-mira", "deadlock-chat", None,
     "Reminder that the experimental branch rotates weekly — feedback there goes straight into our notes.",
     {"embeds": [{"title": "Experimental branch notes", "description": "What's being tested this week and how to opt in.",
                  "url": "https://store.steampowered.com/app/1422450/Deadlock/", "image": None}]}),
    (60 * 27, "demo-ashby", "deadlock-chat", None,
     "We're aware of the souls counter desync after the latest patch. Should be fixed in the next couple of hours.", {}),
    (60 * 30, "demo-ashby", "deadlock-chat", None,
     "Fixed.\nAlso shipped a small update to the Urn path visuals.", {}),
    (60 * 50, "demo-teddy", "bug-reports", None,
     "If you're crashing on launch after the update, please verify game files and send us the dump from the crash folder.",
     {"attachments": [{"url": "#", "name": "crash_dump_instructions.txt", "type": "text/plain"}]}),
    (60 * 75, "demo-mira", "deadlock-chat", "Hero ideas & feedback",
     "Not ready to talk about the next heroes yet, but we read these threads.", {}),
    (60 * 100, "demo-ashby", "deadlock-chat", None,
     "Patch notes for today's update are up on the forums.", {}),
    (60 * 130, "demo-teddy", "deadlock-chat", None,
     "Server maintenance in about 30 minutes, should be short.", {}),
    (60 * 170, "demo-mira", "bug-reports", "Ability bug megathread",
     "Collected these and passed them on, thanks everyone.", {}),
    (60 * 220, "demo-ashby", "deadlock-chat", None,
     "Matchmaking changes went live — let us know how your games feel over the weekend.", {}),
]


def seed() -> None:
    with db.session() as conn:
        if db.kv_get(conn, "demo_seeded"):
            return
        has_real = conn.execute("SELECT 1 FROM messages WHERE is_demo=0 LIMIT 1").fetchone()
        if has_real:
            return
        for user_id, name in DEVS:
            conn.execute(
                "INSERT OR IGNORE INTO developers(user_id, name, is_demo) VALUES(?, ?, 1)", (user_id, name))
        now = int(time.time() * 1000)
        for i, (minutes, author, channel, thread, text, extra) in enumerate(MESSAGES):
            ts = now - minutes * 60_000
            db.upsert_message(conn, {
                "id": f"demo-{i:03d}",
                "author_id": author,
                "guild_id": None,
                "channel_id": f"demo-{channel}",
                "channel_name": channel,
                "parent_channel_id": None,
                "thread_name": thread,
                "time": ts,
                "edited_time": None,
                "text": text,
                "attachments": extra.get("attachments", []),
                "embeds": extra.get("embeds", []),
                "reply_to": None,
                "url": None,
                "is_demo": 1,
            })
        db.kv_set(conn, "demo", True)
        db.kv_set(conn, "demo_seeded", True)
