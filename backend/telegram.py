"""Telegram notifications about new developer messages."""
import html
import logging

import httpx

from . import config
from .translate import needs_translation

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
    original, translated = msg.get("text") or "", msg.get("text_ru") or ""
    embeds = msg.get("embeds") or []
    if embeds and not needs_translation(original):
        # Announcements are often "@everyone <link>" plus a card: show the card's text.
        embeds_ru = msg.get("embeds_ru") or []
        ru_of = lambda i, k: (embeds_ru[i] or {}).get(k) if i < len(embeds_ru) else None
        lead = original.strip()
        card = _embeds_text(embeds)
        card_ru = _embeds_text([{k: ru_of(i, k) or e.get(k) for k in ("title", "description")}
                                for i, e in enumerate(embeds)])
        original = "\n\n".join(filter(None, (lead, card)))
        translated = "\n\n".join(filter(None, (lead, card_ru))) if embeds_ru else ""
    room = LIMIT - len(head) - len(tail) - 80
    if translated and translated != original:
        # Russian first; the original stays one tap away in a collapsed quote.
        body = (f"\n\n<blockquote>{_fit(translated, room // 2)}</blockquote>"
                f"\n<blockquote expandable>🇬🇧 {_fit(original, room // 2)}</blockquote>")
    else:
        body = f"\n\n<blockquote>{_fit(original, room)}</blockquote>" if original.strip() else ""
    return head + body + tail


def _embeds_text(embeds: list[dict]) -> str:
    return "\n\n".join(filter(None, ("\n".join(filter(None, (e.get("title"), e.get("description")))) for e in embeds)))


def _fit(text: str, room: int) -> str:
    # Cut before escaping so an HTML entity is never split in half.
    if len(text) > room:
        text = text[:room].rsplit(" ", 1)[0] + "…"
    return html.escape(text)


async def send(client: httpx.AsyncClient, text: str) -> bool:
    resp = await client.post(
        f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage",
        json={"chat_id": config.TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML",
              "disable_web_page_preview": True},
        timeout=20,
    )
    if resp.status_code != 200:
        log.error("telegram send failed: %s %s", resp.status_code, resp.text[:200])
    return resp.status_code == 200
