"""Pushes the newest messages to Firebase Realtime Database.

Open pages subscribe to /live/latest over server-sent events, so a new developer
message appears on the site within a second or two, without waiting for a deploy.
"""
import asyncio
import json
import logging
import time

import httpx
from google.auth.transport.requests import Request
from google.oauth2 import service_account

from . import config

log = logging.getLogger("zipline.realtime")
SCOPES = ["https://www.googleapis.com/auth/firebase.database",
          "https://www.googleapis.com/auth/userinfo.email"]
LATEST = 20


class Realtime:
    def __init__(self) -> None:
        self.creds = None
        if config.FIREBASE_DB_URL and config.FIREBASE_SERVICE_ACCOUNT:
            self.creds = service_account.Credentials.from_service_account_info(
                json.loads(config.FIREBASE_SERVICE_ACCOUNT), scopes=SCOPES)

    @property
    def enabled(self) -> bool:
        return self.creds is not None

    async def _token(self) -> str:
        if not self.creds.valid:
            await asyncio.to_thread(self.creds.refresh, Request())
        return self.creds.token

    async def push(self, client: httpx.AsyncClient, messages: list[dict]) -> None:
        """Replaces /live/latest with the given messages (newest first)."""
        await self.put(client, "live/latest", {"updatedAt": int(time.time() * 1000), "messages": messages[:LATEST]})

    async def put(self, client: httpx.AsyncClient, path: str, data) -> None:
        try:
            resp = await client.put(f"{config.FIREBASE_DB_URL}/{path}.json",
                                    headers={"Authorization": f"Bearer {await self._token()}"},
                                    json=data, timeout=30)
            if resp.status_code != 200:
                log.error("firebase put %s failed: %s %s", path, resp.status_code, resp.text[:200])
        except Exception:
            log.exception("firebase put %s failed", path)

    async def get(self, client: httpx.AsyncClient, path: str):
        """Reads any path (the service account is not bound by the public read rules)."""
        try:
            resp = await client.get(f"{config.FIREBASE_DB_URL}/{path}.json",
                                    headers={"Authorization": f"Bearer {await self._token()}"}, timeout=30)
            resp.raise_for_status()
            return resp.json()
        except Exception:
            log.exception("firebase get %s failed", path)
            return None
