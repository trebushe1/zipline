"""Pushes the newest build to Firebase, so the changes page shows it at once.

Open changes pages listen to /live/patches over server-sent events, the same way the
developer feed listens to /live/latest. The site deploy that follows a minute later
brings the same data as static files. Summaries go as JSON strings: their keys (file
paths in "media") are not valid Firebase keys.

    python -m backend.patches_live
"""
import asyncio
import json
import sys
import time

import httpx

from . import realtime
from .patches import OUT


async def main() -> int:
    live = realtime.Realtime()
    if not live.enabled:
        print("Firebase is not configured: skipping the live build update")
        return 0
    index = json.loads((OUT / "index.json").read_text("utf-8"))
    newest = next((b for b in index["builds"] if b["size"] != "empty"), None)
    if not newest:
        return 0
    summary = (OUT / newest["file"]).read_text("utf-8")
    async with httpx.AsyncClient() as client:
        await live.put(client, "live/patches", {
            "updatedAt": int(time.time() * 1000),
            "index": json.dumps(index, ensure_ascii=False),
            "file": newest["file"],
            "summary": summary,
        })
    print(f"live: build {newest['build']} sent to Firebase")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
