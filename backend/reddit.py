"""Reddit: the most interesting fresh posts of r/DeadlockTheGame (the «Reddit» page).

Sources
-------
* The official API, when REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET are set: real scores and
  comment counts, refreshed every minute.
* Otherwise the public RSS feeds. Reddit allows one anonymous request a minute and RSS has
  no numbers, so the feeds take turns: «top of the hour», «hot» and «top of the day» give
  each post's place by votes, and the subreddit's comment stream lets us count comments.

Ranking — «Пульс»
-----------------
A post is only compared with posts of about the same age (between age/1.6 and age×1.6).
That is what lets a 20-minute-old post with 30 upvotes beat a 10-hour-old one with 300:
it is far ahead of what posts usually have at 20 minutes.

  strength z  the post's place among same-age peers on a normal scale: 0 is typical,
              +1 beats ~84 % of them, +2 beats ~98 % (votes, plus comments);
  momentum    how much z grew over the last ~30 minutes;
  bonus       news: patch notes, updates, leaks, links to official sites;
  pulse       (z + momentum/2 + bonus + 0.6) × ½^(age / 8 h)

    python -m backend.reddit          # keep data/reddit.json fresh (local run)
    python -m backend.reddit --once   # a single request, then print the top
"""
import asyncio
import bisect
import html
import json
import logging
import math
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from statistics import NormalDist, median
from urllib.parse import urlsplit

import httpx

from . import config

log = logging.getLogger("zipline.reddit")

FILE = config.ROOT / "data" / "reddit.json"
STATE_PATH = "reddit/state"  # Firebase: the collector's full state, kept between live runs
PAGE_PATH = "live/reddit"    # Firebase: what the page reads (public)
WWW = "https://www.reddit.com"
OAUTH = "https://oauth.reddit.com"
ATOM = {"a": "http://www.w3.org/2005/Atom", "media": "http://search.yahoo.com/mrss/"}
HOUR = 3_600_000

KEEP_HOURS = 48       # older posts are forgotten
PAGE_POSTS = 150      # posts sent to the page: every post of the last FRESH_HOURS, the rest by pulse
FRESH_HOURS = 4
HALF_LIFE_HOURS = 8   # freshness halves every 8 hours
BAND = 1.6            # peers: posts between age/BAND and age×BAND
MIN_PEERS = 8
Z_CAP = 2.5
STATE_EVERY = 300     # seconds between saves of the full state to Firebase

# RSS: one anonymous request a minute, so the feeds take turns (each comes round every 3–6 min).
RSS_TURNS = ("hour", "comments", "hot", "hour", "comments", "day")
RSS_FEEDS = {"hour": "top/.rss?t=hour&limit=100", "hot": "hot/.rss?limit=100",
             "day": "top/.rss?t=day&limit=100", "comments": "comments/.rss?limit=100"}
# Ages (minutes) whose order a listing tells: «top of the hour» only knows the last hour, and so on.
WINDOWS = {"hour": (0, 60), "hot": (60, KEEP_HOURS * 60), "day": (60, 24 * 60)}
RANK_KEYS = {"hour": "zh", "hot": "zt", "day": "zd"}
# Firebase serves JSON uncompressed, so the page gets only what it shows (the link is built from the id).
PAGE_FIELDS = ("id", "title", "author", "created", "url", "domain", "kind", "thumb", "excerpt", "flair", "nsfw",
               "spoiler", "score", "comments", "ratio", "hour_rank", "hot_rank", "day_rank", "z", "mom", "pulse",
               "topic", "badges")

ERRORS = {401: "ключ Reddit API не подошёл", 403: "Reddit не пускает с этого адреса (403)",
          404: f"сабреддит r/{config.REDDIT_SUBREDDIT} не найден"}

VIDEO_HOSTS = ("v.redd.it", "youtube.com", "youtu.be", "twitch.tv", "streamable.com", "medal.tv",
               "outplayed.tv", "kick.com")
OFFICIAL = re.compile(r"(^|\.)(playdeadlock\.com|steampowered\.com|steamcommunity\.com|valvesoftware\.com)$")
# Titles that are news rather than talk: patches, reveals, leaks, what Valve said.
NEWS = re.compile(
    r"patch ?notes?|gameplay update|\bhotfix|\bchangelog|\bnew (map|mode)\b|\bnew hero:|\bleak(s|ed)?\b"
    r"|\bdatamin|\bannounce|\bofficial\b|\btrailer\b|\bteaser\b|\bhero (reveal|leak|teaser|trailer)"
    r"|\b(dev|devs|developers?|valve|yoshi) (said|says|confirm|respon|repl|post|tweet|announce|update)"
    r"|^\W*(update|patch)\b|\bupdate\b.*\b\d{1,2}[-/.]\d{1,2}\b", re.I)
ART = re.compile(r"\bart\b|fan ?art|[(\[]oc[)\]]|\bdrew\b|\bdrawing|\bsketch|\bcosplay|\bby @|\bcommission"
                 r"|\bpainting|\billustrat|\brender\b", re.I)
FLAIRS = [("news", re.compile(r"news|official|patch|dev|announce|leak", re.I)),
          ("clip", re.compile(r"clip|gameplay|video|highlight", re.I)),
          ("art", re.compile(r"art|cosplay|creation", re.I)),
          ("talk", re.compile(r"discussion|question|feedback|suggestion|help|guide", re.I))]

_NORMAL = NormalDist()


class RateLimited(Exception):
    pass


def now_ms() -> int:
    return int(time.time() * 1000)


def age_min(post: dict, now: int) -> float:
    return max(0.0, (now - post["created"]) / 60_000)


def _iso_ms(value: str) -> int:
    return int(datetime.fromisoformat(value).timestamp() * 1000)


def _host(url: str) -> str:
    return re.sub(r"^(www|m|old|new)\.", "", urlsplit(url).netloc.lower())


def _b36(n: int) -> str:
    digits = ""
    while n:
        n, r = divmod(n, 36)
        digits = "0123456789abcdefghijklmnopqrstuvwxyz"[r] + digits
    return digits or "0"


def _excerpt(text: str, limit: int = 280) -> str | None:
    text = re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n", text)).strip()
    if len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0] + "…"
    return text or None


def _html_text(fragment: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", re.sub(r"<br\s*/?>|</p>", "\n", fragment)))


def _kind(url: str, permalink: str) -> str:
    host = _host(url)
    if url.split("?")[0].rstrip("/") == permalink.rstrip("/") or (host == "reddit.com" and "/comments/" in url):
        return "text"
    if "/gallery/" in url:
        return "gallery"
    if any(host == h or host.endswith("." + h) for h in VIDEO_HOSTS):
        return "video"
    if host in ("i.redd.it", "i.imgur.com") or re.search(r"\.(jpe?g|png|gif|webp)$", urlsplit(url).path, re.I):
        return "image"
    return "link"


def from_entry(e: ET.Element) -> dict | None:
    """A post from an RSS (Atom) entry."""
    pid = e.findtext("a:id", "", ATOM).removeprefix("t3_")
    link = e.find("a:link", ATOM)
    published = e.findtext("a:published", "", ATOM)
    if not pid or link is None or not published:
        return None
    permalink = link.get("href", "")
    content = e.findtext("a:content", "", ATOM)
    target = re.search(r'<a href="([^"]+)">\[link\]</a>', content)
    url = html.unescape(target.group(1)) if target else permalink
    body = re.search(r'<div class="md">(.*?)</div>\s*<!-- SC_ON -->', content, re.S)
    thumb = e.find("media:thumbnail", ATOM)
    return {"id": pid, "title": e.findtext("a:title", "", ATOM).strip(),
            "author": e.findtext("a:author/a:name", "", ATOM).removeprefix("/u/") or None,
            "created": _iso_ms(published), "permalink": permalink, "url": url,
            "domain": _host(url), "kind": _kind(url, permalink),
            "thumb": thumb.get("url") if thumb is not None else None,
            "excerpt": _excerpt(_html_text(body.group(1))) if body else None}


def from_api(d: dict) -> dict | None:
    """A post from the API, or None if it was removed."""
    if d.get("removed_by_category"):
        return None
    permalink = WWW + d["permalink"]
    url = d.get("url_overridden_by_dest") or d.get("url") or permalink
    if url.startswith("/"):
        url = WWW + url
    kind = ("text" if d.get("is_self") else "video" if d.get("is_video") else "gallery" if d.get("is_gallery")
            else "image" if d.get("post_hint") == "image" else _kind(url, permalink))
    thumb = None
    images = (d.get("preview") or {}).get("images") or []
    if images:
        sizes = images[0].get("resolutions") or []
        thumb = (next((s for s in sizes if s["width"] >= 600), None) or (sizes[-1] if sizes else images[0]["source"]))["url"]
    elif str(d.get("thumbnail", "")).startswith("http"):
        thumb = d["thumbnail"]
    return {"id": d["id"], "title": d["title"].strip(), "author": d.get("author"),
            "created": int(d["created_utc"] * 1000), "permalink": permalink, "url": url,
            "domain": _host(url), "kind": kind, "thumb": thumb, "excerpt": _excerpt(d.get("selftext") or ""),
            "flair": d.get("link_flair_text"), "nsfw": bool(d.get("over_18")), "spoiler": bool(d.get("spoiler")),
            "score": d.get("score", 0), "comments": d.get("num_comments", 0), "ratio": d.get("upvote_ratio")}


# ── Ranking ────────────────────────────────────────────
def _clip(z: float) -> float:
    return max(-Z_CAP, min(Z_CAP, z))


def rank_z(value: float, values: list[float]) -> float:
    """Place of value among values (which include it) on a normal scale; ties share the middle."""
    below = sum(v < value for v in values)
    equal = sum(v == value for v in values)
    return _clip(_NORMAL.inv_cdf((below + equal / 2 + 0.5) / (len(values) + 1)))


def log_z(value: float, values: list[float]) -> float:
    """Robust z-score of log engagement: how far the post is above its peers' median."""
    logs = [math.log(max(v, 0) + 3) for v in values]
    mid = median(logs)
    spread = max(1.4826 * median(abs(x - mid) for x in logs), 0.35)
    return _clip((math.log(max(value, 0) + 3) - mid) / spread)


def peers(pool: list[dict], ages: list[float], age: float) -> list[dict]:
    """Posts of about the same age; pool is sorted by age and ages lines up with it."""
    lo, hi = bisect.bisect_left(ages, age / BAND), bisect.bisect_right(ages, age * BAND)
    if hi - lo < MIN_PEERS:  # too few that close: take the nearest ones around the post
        lo = max(0, min(bisect.bisect_left(ages, age) - MIN_PEERS // 2, len(pool) - MIN_PEERS))
        hi = min(len(pool), lo + MIN_PEERS)
    return pool[lo:hi]


def engagement(p: dict) -> float:
    return max(p.get("score") or 0, 0) + 2 * (p.get("comments") or 0)


def topic(p: dict) -> str:
    flair = p.get("flair") or ""
    for name, pattern in FLAIRS:
        if flair and pattern.search(flair):
            return name
    if OFFICIAL.search(p.get("domain") or "") or NEWS.search(p["title"]):
        return "news"
    if p["kind"] == "video":
        return "clip"
    if p["kind"] in ("image", "gallery") and ART.search(p["title"]):
        return "art"
    return "talk" if p["kind"] == "text" else "other"


def _votes_z(p: dict, age: float) -> float | None:
    """Strength by votes from the listings that still rank a post of this age."""
    if age < 60:
        return p.get("zh")
    known = [p[k] for k in (("zt", "zd") if age < 24 * 60 else ("zt",)) if p.get(k) is not None]
    return sum(known) / len(known) if known else p.get("zh")


def _rss_strength(p: dict, age: float, group: list[dict], counting_since: int) -> float:
    votes = _votes_z(p, age)
    comments = None
    counted = [q for q in group if q["created"] >= counting_since]
    if p["created"] >= counting_since and len(counted) >= 4:
        comments = rank_z(p.get("comments") or 0, [q.get("comments") or 0 for q in counted])
    if votes is None:
        return 0.6 * comments if comments is not None else 0.0
    return votes if comments is None else 0.7 * votes + 0.3 * comments


def _remember(p: dict, age: float, z: float) -> None:
    """Strength over time: every 10 minutes for the first 3 hours, then hourly, for a day."""
    hist = p.setdefault("hist", [])
    if age <= 24 * 60 and (not hist or age - hist[-1][0] >= (10 if age < 180 else 60)):
        hist.append([round(age), round(z * 100)])


def _momentum(p: dict, age: float, z: float) -> float:
    then = [h for a, h in p.get("hist", []) if age - 75 <= a <= age - 25]
    return z - then[-1] / 100 if then else 0.0


def score(posts: dict[str, dict], now: int, api: bool, counting_since: int) -> None:
    """Strength, momentum, pulse and badges of every post (see the module docstring)."""
    pool = sorted(posts.values(), key=lambda p: -p["created"])
    ages = [age_min(p, now) for p in pool]
    for p, age in zip(pool, ages):
        group = peers(pool, ages, age)
        if api:
            z = log_z(engagement(p), [engagement(q) for q in group])
        else:
            z = _rss_strength(p, age, group, counting_since)
        _remember(p, age, z)
        mom = _momentum(p, age, z)
        if z > 0 and age < 15:
            z *= (age + 5) / 20  # a few votes in the first minutes say little
        p["z"], p["mom"], p["topic"] = round(z, 2), round(mom, 2), topic(p)
        bonus = (0.6 if p["topic"] == "news" else 0) + (0.6 if OFFICIAL.search(p.get("domain") or "") else 0)
        p["pulse"] = round(max(z + mom / 2 + bonus + 0.6, 0.05) * 0.5 ** (age / 60 / HALF_LIFE_HOURS), 3)
        p["badges"] = [name for name, on in (
            ("new", age < 30),
            ("rising", age <= 240 and z >= 1.1 and (mom >= 0.3 or age <= 60)),
            ("hit", z >= 1.8),
        ) if on]


# ── Collector ──────────────────────────────────────────
class Feed:
    """Posts of the subreddit with their signals. Each step() makes one request (RSS) or one refresh (API)."""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self.client = client
        self.api = bool(config.REDDIT_CLIENT_ID and config.REDDIT_CLIENT_SECRET)
        self.posts: dict[str, dict] = {}
        self.comment_cursor = 0          # newest counted comment (its base-36 id as a number)
        self.counting_since = now_ms()   # comments are complete for posts created after this
        self.status: dict = {"ok": None, "at": None, "error": None}
        self.turn = 0
        self.next_at = 0.0               # earliest time of the next request
        self.token, self.token_until = "", 0.0
        self.refreshed_at = 0.0          # API: last refresh of posts that left the listings

    # State
    def restore(self, data: dict | None) -> None:
        if not data:
            return
        now = now_ms()
        self.posts = {p["id"]: p for p in data.get("posts") or [] if now - p["created"] < KEEP_HOURS * HOUR}
        self.comment_cursor = int(data.get("commentCursor") or "0", 36)
        self.counting_since = data.get("countingSince") or self.counting_since

    async def load(self, live_site=None) -> None:
        """Picks up where the previous run stopped: the local file or Firebase, whichever is newer."""
        local = json.loads(FILE.read_text(encoding="utf-8")) if FILE.exists() else None
        remote = await live_site.get(self.client, STATE_PATH) if live_site and live_site.enabled else None
        newest = max((d for d in (local, remote) if d), key=lambda d: d.get("updatedAt", 0), default=None)
        self.restore(newest)
        log.info("reddit: %s, %d posts restored", "API" if self.api else "RSS", len(self.posts))

    def state(self) -> dict:
        now = now_ms()
        hourly = [0] * 24  # posts per hour over the last day, oldest first
        for p in self.posts.values():
            if 0 <= (h := (now - p["created"]) // HOUR) < 24:
                hourly[23 - h] += 1
        return {"updatedAt": now, "subreddit": config.REDDIT_SUBREDDIT, "source": "api" if self.api else "rss",
                "status": self.status, "countingSince": self.counting_since,
                "commentCursor": _b36(self.comment_cursor), "stats": {"day": sum(hourly), "hourly": hourly},
                "posts": sorted(self.posts.values(), key=lambda p: -p.get("pulse", 0))}

    def page(self) -> dict:
        """What the page needs: every post of the last hours and the strongest of the rest."""
        return page(self.state())

    def save(self) -> None:
        FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state(), ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        os.replace(tmp, FILE)  # git add / the static build never see half a file

    # Requests
    async def _token(self) -> str:
        if time.time() < self.token_until:
            return self.token
        resp = await self.client.post(f"{WWW}/api/v1/access_token", data={"grant_type": "client_credentials"},
                                      auth=(config.REDDIT_CLIENT_ID, config.REDDIT_CLIENT_SECRET),
                                      headers={"User-Agent": config.REDDIT_USER_AGENT}, timeout=20)
        resp.raise_for_status()
        body = resp.json()
        self.token, self.token_until = body["access_token"], time.time() + body.get("expires_in", 3600) - 300
        return self.token

    async def _get(self, url: str, params: dict | None = None) -> httpx.Response:
        headers = {"User-Agent": config.REDDIT_USER_AGENT}
        if self.api:
            headers["Authorization"] = f"bearer {await self._token()}"
        resp = await self.client.get(url, params=params, headers=headers, timeout=20, follow_redirects=True)
        try:  # wait out Reddit's rate limit window once it is used up
            remaining = float(resp.headers.get("x-ratelimit-remaining", "1"))
            reset = float(resp.headers.get("x-ratelimit-reset", "60"))
            if remaining < 1 or resp.status_code == 429:
                self.next_at = max(self.next_at, time.time() + reset + 1)
        except ValueError:
            pass
        if resp.status_code == 429:
            raise RateLimited
        resp.raise_for_status()
        return resp

    async def step(self) -> None:
        started = time.time()
        try:
            await (self._api_refresh() if self.api else self._rss_turn())
            self.status = {"ok": True, "at": now_ms(), "error": None}
        except RateLimited:
            log.info("reddit: rate limited, waiting for the next window")
        except httpx.HTTPStatusError as exc:
            code = exc.response.status_code
            self.status = {"ok": False, "at": now_ms(), "error": ERRORS.get(code, f"Reddit ответил {code}")}
            if code in (401, 403):
                self.next_at = max(self.next_at, time.time() + 600)
            log.warning("reddit: %s", self.status["error"])
        except (httpx.HTTPError, ET.ParseError, KeyError, ValueError) as exc:
            self.status = {"ok": False, "at": now_ms(), "error": f"{type(exc).__name__}: {exc}"[:200]}
            log.warning("reddit: %s", self.status["error"])
        self.next_at = max(self.next_at, started + (60 if self.api else 30))
        now = now_ms()
        self.posts = {k: p for k, p in self.posts.items() if now - p["created"] < KEEP_HOURS * HOUR}
        score(self.posts, now, self.api, self.counting_since)

    def _merge(self, post: dict, now: int) -> dict:
        known = self.posts.get(post["id"])
        if known is None:
            self.posts[post["id"]] = known = post | {"first_seen": now}
            known.setdefault("comments", 0)
        else:
            known.update(post)
        return known

    async def _rss_turn(self) -> None:
        feed = RSS_TURNS[self.turn % len(RSS_TURNS)]
        resp = await self._get(f"{WWW}/r/{config.REDDIT_SUBREDDIT}/{RSS_FEEDS[feed]}")
        self.turn += 1
        entries = ET.fromstring(resp.content).findall("a:entry", ATOM)
        now = now_ms()
        if feed == "comments":
            return self._count_comments(entries)
        ids = []
        for e in entries:
            post = from_entry(e)
            if post and now - post["created"] < KEEP_HOURS * HOUR:
                ids.append(self._merge(post, now)["id"])
        self._ranks(feed, ids, now)

    def _ranks(self, feed: str, ids: list[str], now: int) -> None:
        """Turns a listing's order into the strength of every post it ranks, among same-age peers."""
        lo, hi = WINDOWS[feed]
        place = {pid: i + 1 for i, pid in enumerate(ids)}
        unlisted = len(ids) + 1  # posts missing from the listing tie below every listed one
        pool = sorted((p for p in self.posts.values() if lo <= age_min(p, now) < hi), key=lambda p: -p["created"])
        ages = [age_min(p, now) for p in pool]
        for p, age in zip(pool, ages):
            group = peers(pool, ages, age)
            p[RANK_KEYS[feed]] = round(rank_z(-place.get(p["id"], unlisted),
                                              [-place.get(q["id"], unlisted) for q in group]), 2)
        for p in self.posts.values():
            p[f"{feed}_rank"] = place.get(p["id"])

    def _count_comments(self, entries: list[ET.Element]) -> None:
        seen = []
        for e in entries:
            link = e.find("a:link", ATOM)
            match = re.search(r"/comments/(\w+)/", link.get("href", "") if link is not None else "")
            cid = e.findtext("a:id", "", ATOM).removeprefix("t1_")
            if match and cid:
                seen.append((int(cid, 36), match.group(1), e.findtext("a:author/a:name", "", ATOM),
                             e.findtext("a:updated", "", ATOM)))
        if not seen:
            return
        fresh = [c for c in seen if c[0] > self.comment_cursor]
        if not self.comment_cursor or (len(seen) >= 100 and len(fresh) == len(seen)):
            # First read, or more comments came in than one page holds: counts are only
            # complete for posts created after the oldest comment we can see.
            self.counting_since = min(_iso_ms(c[3]) for c in seen if c[3])
        for _, post_id, author, _ in fresh:
            post = self.posts.get(post_id)
            if post is not None and author != "/u/AutoModerator":
                post["comments"] = (post.get("comments") or 0) + 1
        self.comment_cursor = max(self.comment_cursor, *(c[0] for c in seen))

    async def _api_refresh(self) -> None:
        now = now_ms()
        listed = set()
        for listing in ("new", "hot"):
            resp = await self._get(f"{OAUTH}/r/{config.REDDIT_SUBREDDIT}/{listing}", {"limit": 100, "raw_json": 1})
            for child in resp.json()["data"]["children"]:
                listed.add(child["data"]["id"])
                self._take_api(child["data"], now)
        if time.time() - self.refreshed_at >= 600:  # posts that dropped out of both listings
            self.refreshed_at = time.time()
            rest = [pid for pid in self.posts if pid not in listed]
            for i in range(0, len(rest), 100):
                ids = ",".join("t3_" + pid for pid in rest[i:i + 100])
                resp = await self._get(f"{OAUTH}/api/info", {"id": ids, "raw_json": 1})
                for child in resp.json()["data"]["children"]:
                    self._take_api(child["data"], now)

    def _take_api(self, d: dict, now: int) -> None:
        post = from_api(d)
        if post is None:
            self.posts.pop(d["id"], None)
        elif now - post["created"] < KEEP_HOURS * HOUR:
            self._merge(post, now)


def page(state: dict) -> dict:
    """The page's share of the state: posts of the last FRESH_HOURS and the strongest older ones."""
    now = now_ms()
    posts = sorted(state.get("posts") or [], key=lambda p: -p.get("pulse", 0))
    fresh = [p for p in posts if now - p["created"] < FRESH_HOURS * HOUR][:PAGE_POSTS]
    older = [p for p in posts if now - p["created"] >= FRESH_HOURS * HOUR]
    keep = sorted(fresh + older[:PAGE_POSTS - len(fresh)], key=lambda p: -p.get("pulse", 0))
    return {k: v for k, v in state.items() if k != "commentCursor"} | {"posts": [
        {k: p[k] for k in PAGE_FIELDS if p.get(k) is not None and not (k == "url" and p["kind"] == "text")}
        for p in keep]}


async def run(feed: Feed, live_site=None, deadline: float = math.inf) -> None:
    """Polls Reddit until the deadline."""
    state_saved = time.time()
    try:
        while time.time() < deadline:
            await asyncio.sleep(max(0.0, min(feed.next_at, deadline) - time.time()))
            if time.time() >= deadline:
                break
            try:
                await feed.step()
                feed.save()
                if live_site and live_site.enabled:
                    await live_site.put(feed.client, PAGE_PATH, feed.page())
                    if time.time() - state_saved >= STATE_EVERY:
                        state_saved = time.time()
                        await live_site.put(feed.client, STATE_PATH, feed.state())
            except Exception:
                log.exception("reddit step failed")
                feed.next_at = max(feed.next_at, time.time() + 60)
    finally:
        if live_site and live_site.enabled:
            await live_site.put(feed.client, STATE_PATH, feed.state())


async def _main() -> None:
    async with httpx.AsyncClient() as client:
        feed = Feed(client)
        await feed.load()
        if "--once" not in sys.argv:
            return await run(feed)
        await feed.step()
        feed.save()
        print(f"{len(feed.posts)} posts, status {feed.status}")
        for p in sorted(feed.posts.values(), key=lambda p: -p["pulse"])[:20]:
            age = age_min(p, now_ms())
            print(f"{p['pulse']:5.2f}  z={p['z']:+.2f} mom={p['mom']:+.2f}  {age:5.0f}m  {p['topic']:5}  "
                  f"{' '.join(p['badges']):12}  {p['title'][:70]}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    asyncio.run(_main())
