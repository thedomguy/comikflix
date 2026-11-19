"""MangaDex as a third chapter source, read the same way as Asura/mgeko: a slug-prefixed
series id ("mangadex-<uuid>") maps to the public api.mangadex.org REST API.

MangaDex's Acceptable Usage Policy (api.mangadex.org/docs, verified 2026-11-20) requires:
  - crediting MangaDex and the scanlation group, honoring group takedown requests
  - no ads/paid services on top of their content
  - a real User-Agent (no spoofing), no `Via` header
  - page images are NEVER hotlinked to end users: callers must proxy them
This module never exposes a raw mangadex.org/uploads.mangadex.org URL to the reader; page
`src` values are always our own `/api/mangadex/image/<chapterId>/<quality>/<filename>` proxy
path (see server.py's `_send_mangadex_image`, which resolves the real, short-lived at-home
URL per request). No account is linked, so chapter "mark as read" is not reported back to
MangaDex (it needs OAuth2 user auth) — our own `progress` table is the only read-tracking.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from difflib import SequenceMatcher
from urllib.parse import urlparse

API = "https://api.mangadex.org"
UPLOADS = "https://uploads.mangadex.org"
PREFIX = "mangadex-"
USER_AGENT = "ComikFlix/1.0 (personal single-user reader; +https://github.com/)"
FEED_LANGS = ("en",)
FEED_PAGE_SIZE = 200
AT_HOME_TTL = 5 * 60  # seconds; at-home tokens last longer, but we re-check this often anyway

STATUS_MAP = {
    "ongoing": "ongoing", "completed": "completed", "hiatus": "hiatus", "cancelled": "dropped",
}

# ---- rate limiting: ~5 req/s global, 40/min for /at-home/server (MangaDex's documented caps) --

_lock = threading.Lock()
_recent: list[float] = []
_at_home_recent: list[float] = []


def _throttle(bucket: list[float], max_per_window: int, window: float) -> None:
    with _lock:
        now = time.monotonic()
        while bucket and now - bucket[0] > window:
            bucket.pop(0)
        if len(bucket) >= max_per_window:
            wait = window - (now - bucket[0])
        else:
            wait = 0.0
        bucket.append(now + max(wait, 0.0))
    if wait > 0:
        time.sleep(wait)


def _get(path: str, params: dict | None = None, *, timeout: float = 20.0) -> dict:
    _throttle(_recent, 5, 1.0)
    url = API + path
    if params:
        url += "?" + urllib.parse.urlencode(params, doseq=True)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")[:300]
        except Exception:
            pass
        raise RuntimeError(f"MangaDex HTTP {e.code}: {body or e.reason}") from e


def is_mangadex(value: str) -> bool:
    value = (value or "").strip().lower()
    return value.startswith(PREFIX) or "mangadex.org" in value


def parse_slug(value: str) -> str:
    """A stored slug ("mangadex-<uuid>") or any mangadex.org/title/<uuid>[/...] URL -> our slug."""
    value = value.strip()
    if value.lower().startswith(PREFIX):
        mid = value[len(PREFIX):].lower()
    else:
        parts = urlparse(value if "//" in value else "//" + value).path.strip("/").split("/")
        mid = None
        for key in ("title", "manga"):
            if key in parts and parts.index(key) + 1 < len(parts):
                mid = parts[parts.index(key) + 1].lower()
                break
        if not mid:
            raise ValueError("Use a mangadex.org/title/<uuid> link, not a chapter link")
    if not _looks_like_uuid(mid):
        raise ValueError("Invalid MangaDex series link (expected a UUID)")
    return PREFIX + mid


def _looks_like_uuid(value: str) -> bool:
    parts = value.split("-")
    return len(parts) == 5 and [len(p) for p in parts] == [8, 4, 4, 4, 12] and all(
        c in "0123456789abcdef-" for c in value
    )


def _site_id(slug: str) -> str:
    return slug[len(PREFIX):] if slug.lower().startswith(PREFIX) else slug


def _localized(d: dict | None, *, default: str = "") -> str:
    if not isinstance(d, dict) or not d:
        return default
    for lang in ("en", "en-us"):
        if d.get(lang):
            return d[lang]
    return next(iter(d.values()), default)


def search(query: str, limit: int = 8) -> list[dict]:
    """Title search via GET /manga?title=; scored like the other sources for comparison."""
    query = (query or "").strip()
    if not query:
        return []
    data = _get("/manga", {
        "title": query,
        "limit": min(limit * 3, 30),
        "includes[]": "cover_art",
        "order[relevance]": "desc",
    })
    words = [w.lower() for w in query.split() if w]
    out = []
    for m in data.get("data") or []:
        attrs = m.get("attributes") or {}
        title = _localized(attrs.get("title")) or next(iter((attrs.get("title") or {}).values()), "")
        alts = [_localized(a) for a in (attrs.get("altTitles") or []) if _localized(a)]
        names = [title, *alts]
        name_words = " ".join(names).lower()
        overlap = sum(1 for w in words if w in name_words) / max(len(words), 1)
        ratio = max((SequenceMatcher(None, query.lower(), n.lower()).ratio() for n in names if n), default=0.0)
        cover_file = None
        for rel in m.get("relationships") or []:
            if rel.get("type") == "cover_art":
                cover_file = (rel.get("attributes") or {}).get("fileName")
        out.append({
            "provider": "mangadex",
            "source": "mangadex",
            "slug": PREFIX + m["id"],
            "provider_id": m["id"],
            "title": title or "(untitled)",
            "alt_titles": alts,
            "url": f"https://mangadex.org/title/{m['id']}",
            "cover_url": f"{UPLOADS}/covers/{m['id']}/{cover_file}" if cover_file else None,
            "score": round(0.6 * overlap + 0.4 * ratio, 3),
        })
    return sorted(out, key=lambda r: -r["score"])[:limit]


def _label(number) -> str:
    try:
        n = float(number)
    except (TypeError, ValueError):
        return str(number)
    return str(int(n)) if n.is_integer() else str(n)


def _fetch_feed(manga_id: str) -> list[dict]:
    chapters, offset, total = [], 0, None
    while total is None or offset < total:
        data = _get(f"/manga/{manga_id}/feed", {
            "translatedLanguage[]": list(FEED_LANGS),
            "order[chapter]": "asc",
            "limit": FEED_PAGE_SIZE,
            "offset": offset,
            "includes[]": "scanlation_group",
            "contentRating[]": ["safe", "suggestive", "erotica"],
        })
        total = data.get("total", 0)
        chapters.extend(data.get("data") or [])
        offset += FEED_PAGE_SIZE
        if not data.get("data"):
            break
    return chapters


def fetch_series_info(slug: str) -> dict:
    """Same shape as ingest.fetch_series_info / mgeko.fetch_series_info."""
    manga_id = _site_id(slug)
    data = _get(f"/manga/{manga_id}", {"includes[]": ["cover_art", "author", "artist"]})
    m = data.get("data")
    if not m:
        raise RuntimeError("series not found on MangaDex")
    attrs = m.get("attributes") or {}
    title = _localized(attrs.get("title")) or next(iter((attrs.get("title") or {}).values()), None)
    if not title:
        raise RuntimeError("series has no title on MangaDex")
    alts = [_localized(a) for a in (attrs.get("altTitles") or []) if _localized(a)]
    cover_file = author = artist = None
    for rel in m.get("relationships") or []:
        rtype = rel.get("type")
        if rtype == "cover_art":
            cover_file = (rel.get("attributes") or {}).get("fileName")
        elif rtype == "author" and not author:
            author = (rel.get("attributes") or {}).get("name")
        elif rtype == "artist" and not artist:
            artist = (rel.get("attributes") or {}).get("name")

    raw_chapters = _fetch_feed(manga_id)
    chapters, seen = [], set()
    for c in raw_chapters:
        cattrs = c.get("attributes") or {}
        if cattrs.get("externalUrl"):
            continue  # not actually hosted/readable on MangaDex
        number = cattrs.get("chapter")
        if number is None or number == "":
            continue
        number = _label(number)
        if number in seen:
            continue
        seen.add(number)
        chapters.append({
            "number": number,
            "url": c["id"],  # the chapter UUID; stored as chapters.source_url (see ingest.py)
            "published_at": cattrs.get("publishAt"),
            "page_count": cattrs.get("pages"),
            "locked": False,
        })
    if not chapters:
        raise RuntimeError("series has no readable chapters on MangaDex (no translatedLanguage=en feed)")
    chapters.sort(key=lambda c: float(c["number"]))

    return {
        "slug": PREFIX + manga_id,
        "moved_from": None,
        "source_url": f"https://mangadex.org/title/{manga_id}",
        "title": title,
        "description": _localized(attrs.get("description")),
        "alt_titles": alts,
        "cover_url": f"{UPLOADS}/covers/{manga_id}/{cover_file}" if cover_file else None,
        "rating": None,
        "bookmarks": None,
        "status": STATUS_MAP.get((attrs.get("status") or "").lower()),
        "type": "Manga" if (attrs.get("originalLanguage") == "ja") else None,
        "author": author,
        "artist": artist,
        "genres": [
            _localized((t.get("attributes") or {}).get("name"))
            for t in attrs.get("tags") or []
            if (t.get("attributes") or {}).get("group") == "genre"
        ],
        "chapters": chapters,
    }


# ---- chapter pages: at-home server, proxied (never hotlinked) ------------------------------

_at_home_cache: dict[str, tuple[float, str, str]] = {}  # chapter_id -> (fetched_at, base_url, hash)
_at_home_lock = threading.Lock()


def _fetch_at_home(chapter_id: str) -> dict:
    """Raw GET /at-home/server/{id}, throttled to stay under MangaDex's documented 40/min
    on this endpoint — shared by every caller (page listing, image proxy) so the budget is
    tracked in one place."""
    _throttle(_at_home_recent, 38, 60.0)
    return _get(f"/at-home/server/{chapter_id}")


def _at_home(chapter_id: str) -> tuple[str, str]:
    """(baseUrl, hash) for a chapter, cached briefly; re-fetched as needed per MangaDex's
    own guidance (the token can expire or be invalidated by a reported failed delivery)."""
    with _at_home_lock:
        cached = _at_home_cache.get(chapter_id)
        if cached and time.time() - cached[0] < AT_HOME_TTL:
            return cached[1], cached[2]
    data = _fetch_at_home(chapter_id)
    chapter = data.get("chapter") or {}
    base_url, chash = data.get("baseUrl"), chapter.get("hash")
    if not base_url or not chash:
        raise RuntimeError("MangaDex did not return an at-home server for this chapter")
    with _at_home_lock:
        _at_home_cache[chapter_id] = (time.time(), base_url, chash)
    return base_url, chash


def extract_pages(chapter_id: str) -> list[dict]:
    """Page list for one chapter (our own proxy URLs, in page order)."""
    chapter_id = chapter_id.strip()
    if chapter_id.startswith("http"):
        parts = urlparse(chapter_id).path.strip("/").split("/")
        if "chapter" in parts:
            chapter_id = parts[parts.index("chapter") + 1]
    data = _fetch_at_home(chapter_id)  # also primes/validates the chapter id
    chapter = data.get("chapter") or {}
    filenames = chapter.get("data") or []
    if not filenames:
        raise RuntimeError("no pages found for this chapter on MangaDex")
    base_url, chash = data.get("baseUrl"), chapter.get("hash")
    if base_url and chash:
        with _at_home_lock:
            _at_home_cache[chapter_id] = (time.time(), base_url, chash)
    return [
        {
            "page_index": i,
            "src": f"/api/mangadex/image/{chapter_id}/data/{fn}",
            "aspect_ratio": None,
            "alt": None,
        }
        for i, fn in enumerate(filenames)
    ]


def image_url(chapter_id: str, quality: str, filename: str) -> str:
    """The real (short-lived) MangaDex CDN URL for one page; server.py fetches it server-side
    and streams the bytes back — the browser never sees this URL (no hotlinking)."""
    base_url, chash = _at_home(chapter_id)
    quality = quality if quality in ("data", "data-saver") else "data"
    return f"{base_url}/{quality}/{chash}/{filename}"


def fetch_image(chapter_id: str, quality: str, filename: str, timeout: float = 20.0) -> tuple[bytes, str]:
    url = image_url(chapter_id, quality, filename)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        ctype = resp.headers.get_content_type() or "image/jpeg"
        return resp.read(), ctype
