"""Plain data shapes shared by every ComicSourceProvider.

Kept as dicts-in/dicts-out at the edges (matching the rest of the codebase, which trades
scraped dicts around rather than classes) but typed here as dataclasses for the provider
interface itself, where structure matters more than JSON-friendliness.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SearchResult:
    provider: str            # "asura" | "mgeko" | "mangadex" | "comick"
    provider_id: str         # the provider's own id/slug (not a ComikFlix series slug)
    title: str
    url: str | None = None
    cover_url: str | None = None
    alt_titles: list[str] = field(default_factory=list)
    score: float = 0.0       # provider's own search-relevance score, 0..1


@dataclass
class ChapterInfo:
    number: str               # normalized label, e.g. "12", "12.5"
    url: str | None = None
    published_at: str | None = None
    page_count: int | None = None
    locked: bool = False


@dataclass
class SeriesInfo:
    provider: str
    provider_id: str
    slug: str                 # ComikFlix series slug this maps to (provider-prefixed)
    title: str
    source_url: str | None = None
    description: str = ""
    alt_titles: list[str] = field(default_factory=list)
    cover_url: str | None = None
    rating: float | None = None
    bookmarks: int | None = None
    status: str | None = None
    type: str | None = None
    author: str | None = None
    artist: str | None = None
    genres: list[str] = field(default_factory=list)
    chapters: list[ChapterInfo] = field(default_factory=list)
    moved_from: str | None = None


@dataclass
class PageInfo:
    page_index: int
    src: str                  # absolute (CDN) or our own proxy URL
    aspect_ratio: str | None = None
    alt: str | None = None


@dataclass
class ProviderCapabilities:
    can_search: bool = True
    can_read_pages: bool = True
    can_check_updates: bool = True
    requires_image_proxy: bool = False   # True: page images must never be hotlinked
    languages: list[str] = field(default_factory=lambda: ["en"])
    rate_limit_notes: str = ""
    terms_url: str | None = None
    status: str = "available"            # available | unverified | unavailable
    notes: str = ""                      # human-readable diagnostic (e.g. why unavailable)


@dataclass
class ProviderHealth:
    status: str = "unknown"              # ok | degraded | down | unknown
    last_ok_at: str | None = None
    last_error: str | None = None
    last_checked_at: str | None = None
