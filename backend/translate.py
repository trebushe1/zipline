"""English → Russian translation of developer messages.

Uses Google Translate's free web endpoint (no key). Hero names, game terms,
links, mentions and code are swapped for ⟦n⟧ placeholders before translating —
the translator leaves those alone — and restored afterwards, so "warden" stays
Warden instead of becoming «начальник».
"""
import json
import logging
import re
import time

import httpx

from . import config

log = logging.getLogger("zipline.translate")
ENDPOINT = "https://translate.googleapis.com/translate_a/single"
CHUNK = 4000
BACKOFF_SECONDS = 600  # Google answers bursts with a captcha redirect; wait it out
_blocked_until = 0.0

# Hero names that are also everyday words are kept only when capitalized.
AMBIGUOUS = {"seven", "silver", "pocket", "haze", "lash", "ivy", "wraith", "mirage", "paradox", "shiv",
             "rem", "graves", "drifter", "mo", "harrow", "talon", "geist", "rat king", "mm"}
GAME_TERMS = ["Soul Urn", "Urn", "Patron", "Walker", "Walkers", "Guardian", "Guardians", "Mid Boss",
              "Rejuvenator", "Sinner's Sacrifice", "Street Brawl", "MM"]


def _hero_names() -> list[str]:
    path = config.ROOT / "data" / "heroes" / "index.json"
    names = []
    if path.exists():
        names = [h["name"] for h in json.loads(path.read_text(encoding="utf-8")).get("heroes", [])]
    aliases = []
    for name in names:
        parts = [p for p in re.split(r"\s+|&", name) if p and p.lower() not in ("the", "lady", "grey", "deadman")]
        aliases += parts if len(parts) > 1 or name.startswith("The ") else []
    return names + aliases


def _term_patterns() -> tuple[re.Pattern, re.Pattern]:
    terms = sorted(set(_hero_names() + GAME_TERMS), key=len, reverse=True)
    loose = [t for t in terms if t.lower() not in AMBIGUOUS]
    strict = [t for t in terms if t.lower() in AMBIGUOUS]
    alt = lambda ts: "|".join(re.escape(t) for t in ts) or r"(?!x)x"
    return (re.compile(rf"\b(?:{alt(loose)})\b", re.I), re.compile(rf"\b(?:{alt(strict)})\b"))


LOOSE_TERMS, STRICT_TERMS = _term_patterns()
TOKENS = re.compile(r"```.*?```|`[^`\n]+`|<a?:\w+:\d+>|<[@#][!&]?\d+>|@everyone|@here|https?://\S+", re.S)


def _protect(text: str) -> tuple[str, list[str]]:
    saved: list[str] = []

    def keep(m: re.Match, capitalize: bool = False) -> str:
        word = m.group(0)
        if capitalize and word.islower():
            word = word[0].upper() + word[1:]  # "warden" → "Warden"
        saved.append(word)
        return f"⟦{len(saved) - 1}⟧"

    text = TOKENS.sub(keep, text)
    for pattern in (LOOSE_TERMS, STRICT_TERMS):
        text = pattern.sub(lambda m: keep(m, capitalize=True), text)
    return text, saved


def _restore(text: str, saved: list[str]) -> str:
    return re.sub(r"⟦\s*(\d+)\s*⟧", lambda m: saved[int(m.group(1))] if int(m.group(1)) < len(saved) else m.group(0), text)


def available() -> bool:
    return time.time() >= _blocked_until


def needs_translation(text: str | None) -> bool:
    if not text or not re.search(r"[A-Za-z]{2}", text):
        return False
    stripped = TOKENS.sub("", text)
    letters = re.findall(r"[A-Za-zА-Яа-яЁё]", stripped)
    latin = sum(c.isascii() for c in letters)
    return latin >= 3 and latin / max(len(letters), 1) > 0.5


async def to_russian(client: httpx.AsyncClient, text: str | None, timeout: float = 6) -> str | None:
    """Returns the Russian translation, or None if not needed or it failed."""
    global _blocked_until
    if not needs_translation(text) or time.time() < _blocked_until:
        return None
    protected, saved = _protect(text)
    out = []
    try:
        for start in range(0, len(protected), CHUNK):
            resp = await client.post(
                ENDPOINT,
                params={"client": "gtx", "sl": "auto", "tl": "ru", "dt": "t"},
                data={"q": protected[start:start + CHUNK]},
                timeout=timeout,
            )
            if resp.status_code in (302, 429):
                _blocked_until = time.time() + BACKOFF_SECONDS
                log.warning("translator is rate limiting us, pausing for %ss", BACKOFF_SECONDS)
                return None
            resp.raise_for_status()
            out.append("".join(seg[0] for seg in resp.json()[0] if seg and seg[0]))
    except Exception as exc:
        log.warning("translation failed: %s", exc)
        return None
    return _restore("".join(out), saved)


async def translate_message(client: httpx.AsyncClient, text: str | None, embeds: list[dict] | None,
                            timeout: float = 6) -> tuple[str | None, list[dict] | None]:
    """Returns (text_ru, embeds_ru); embeds_ru lines up with embeds by index."""
    text_ru = await to_russian(client, text, timeout)
    embeds_ru = []
    for embed in embeds or []:
        embeds_ru.append({"title": await to_russian(client, embed.get("title"), timeout),
                          "description": await to_russian(client, embed.get("description"), timeout)})
    return text_ru, (embeds_ru if any(e["title"] or e["description"] for e in embeds_ru) else None)
