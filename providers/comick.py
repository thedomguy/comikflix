"""Comick — evaluated, not implemented.

Per the task's own condition ("implement only if current API access, terms, and
chapter-reading capabilities are verified; do not use undocumented authentication bypasses"):
this session fetched https://comick.io/ and got HTTP 403 on a normal client request, found no
published API documentation and no Terms of Service page addressing third-party/API use. The
only way to search or read chapters from Comick right now would be to reverse-engineer its
internal frontend API (commonly referenced online as api.comick.fun, but undocumented and
unconfirmed) — exactly the "undocumented auth bypass" the task says not to rely on.

So this adapter exists only so Comick shows up in diagnostics/the coverage matrix as
"unverified", not silently missing; it never makes a network call.
"""
from __future__ import annotations

from .base import ComicSourceProvider
from .types import ChapterInfo, PageInfo, ProviderCapabilities, SearchResult, SeriesInfo

UNAVAILABLE_REASON = (
    "Not implemented: comick.io returned HTTP 403 to a normal client fetch, publishes no "
    "documented public API and no Terms of Service covering third-party/API access. Only an "
    "undocumented, reverse-engineered API is known to exist, which the task explicitly "
    "disallows relying on. Re-evaluate if Comick publishes an official API/ToS."
)


class ComickProvider(ComicSourceProvider):
    name = "comick"

    def owns_slug(self, slug: str) -> bool:
        return slug.lower().startswith("comick-")

    def parse_slug(self, value: str) -> str:
        raise NotImplementedError(UNAVAILABLE_REASON)

    def search(self, query: str, limit: int = 8) -> list[SearchResult]:
        return []

    def get_series(self, provider_id: str) -> SeriesInfo:
        raise NotImplementedError(UNAVAILABLE_REASON)

    def get_chapter_pages(self, provider_id: str, chapter_id: str) -> list[PageInfo]:
        raise NotImplementedError(UNAVAILABLE_REASON)

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            can_search=False, can_read_pages=False, can_check_updates=False,
            requires_image_proxy=True, languages=[],
            rate_limit_notes="unknown (no documented API)",
            terms_url=None, status="unverified", notes=UNAVAILABLE_REASON,
        )
