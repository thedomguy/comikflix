"""The shared ComicSourceProvider interface every source (Asura, mgeko, MangaDex, ...)
implements. Adapters for the two existing sources wrap their existing scrape modules
unchanged (see asura.py / mgeko.py); new sources (mangadex.py) implement it directly.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timezone
from threading import Lock

from .types import ChapterInfo, PageInfo, ProviderCapabilities, ProviderHealth, SearchResult, SeriesInfo


class ComicSourceProvider(ABC):
    """One comic source. `name` is the stable provider id stored in comic_sources.provider
    and used as the ComikFlix slug prefix for every series except the original (unprefixed)
    Asura slugs, which predate this interface."""

    name: str

    def __init__(self) -> None:
        self._health_lock = Lock()
        self._health = ProviderHealth()

    # ---- identity -----------------------------------------------------------------

    @abstractmethod
    def owns_slug(self, slug: str) -> bool:
        """True if `slug` (a ComikFlix series slug) belongs to this provider."""

    @abstractmethod
    def parse_slug(self, value: str) -> str:
        """Bare provider id, ComikFlix slug, or source URL -> ComikFlix series slug."""

    # ---- data -----------------------------------------------------------------------

    @abstractmethod
    def search(self, query: str, limit: int = 8) -> list[SearchResult]:
        ...

    @abstractmethod
    def get_series(self, provider_id: str) -> SeriesInfo:
        """Raises on failure (series not found, network error, ...)."""

    def get_chapters(self, provider_id: str) -> list[ChapterInfo]:
        """Default: chapters already present on get_series(); override if chapters need
        their own fetch (paginated feeds, etc.)."""
        return self.get_series(provider_id).chapters

    @abstractmethod
    def get_chapter_pages(self, provider_id: str, chapter_id: str) -> list[PageInfo]:
        ...

    def check_updates(self, provider_id: str, known_chapter_ids: set[str]) -> list[ChapterInfo]:
        """Chapters not already in `known_chapter_ids`. Default: diff get_chapters()."""
        return [c for c in self.get_chapters(provider_id) if c.number not in known_chapter_ids]

    # ---- diagnostics ------------------------------------------------------------------

    @abstractmethod
    def capabilities(self) -> ProviderCapabilities:
        ...

    def health(self) -> ProviderHealth:
        with self._health_lock:
            return ProviderHealth(**vars(self._health))

    def _record_ok(self) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._health_lock:
            self._health = ProviderHealth(status="ok", last_ok_at=now, last_error=None, last_checked_at=now)

    def _record_error(self, error: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._health_lock:
            self._health.status = "degraded" if self._health.last_ok_at else "down"
            self._health.last_error = error[:500]
            self._health.last_checked_at = now

    def call(self, fn, *args, **kwargs):
        """Run `fn` recording provider health; re-raises on failure."""
        try:
            result = fn(*args, **kwargs)
            self._record_ok()
            return result
        except Exception as e:
            self._record_error(str(e) or e.__class__.__name__)
            raise
