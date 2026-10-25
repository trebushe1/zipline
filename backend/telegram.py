"""Telegram notifications about new developer messages."""
import html
import logging

import httpx

from . import config

log = logging.getLogger("zipline.telegram")
LIMIT = 4096


def enabled() -> bool:
    return bool(config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID)


def format_message(msg: dict, author: str) -> str:
    where = f"#{msg.get('channel_name') or 'канал'}"
    if msg.get("thread_name"):
        where += f" › {msg['thread_name']}"
    head = f"💬 <b>{html.escape(author)}</b> · {html.escape(where)}"
    links = []
    if msg.get("url"):
        links.append(f'<a href="{html.escape(msg["url"])}">Открыть в Discord</a>')
    for a in msg.get("attachments", []):
        links.append(f'<a href="{html.escape(a["url"])}">📎 {html.escape(a.get("name") or "вложение")}</a>')
    tail = "\n\n" + " · ".join(links) if links else ""
    text = html.escape(msg.get("text") or "")
    room = LIMIT - len(head) - len(tail) - 40
    if len(text) > room:
        text = text[:room].rsplit(" ", 1)[0] + "…"
    body = f"\n\n<blockquote>{text}</blockquote>" if text else ""
    return head + body + tail


async def send(client: httpx.AsyncClient, text: str) -> None:
    resp = await client.post(
        f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage",
        json={"chat_id": config.TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML",
              "disable_web_page_preview": True},
        timeout=20,
    )
    if resp.status_code != 200:
        log.error("telegram send failed: %s %s", resp.status_code, resp.text[:200])
