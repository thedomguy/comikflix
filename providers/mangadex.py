"""MangaDex adapter: wraps mangadex.py (the real api.mangadex.org client)."""
from __future__ import annotations

import mangadex as mangadex_api

from .asura import _to_series_info
from .base import ComicSourceProvider
from .types import PageInfo, ProviderCapabilities, SearchResult, SeriesInfo


class MangaDexProvider(ComicSourceProvider):
    name = "mangadex"

    def owns_slug(self, slug: str) -> bool:
        return mangadex_api.is_mangadex(slug)

    def parse_slug(self, value: str) -> str:
        return mangadex_api.parse_slug(value)

    def search(self, query: str, limit: int = 8) -> list[SearchResult]:
        hits = self.call(mangadex_api.search, query, limit)
        return [
            SearchResult(
                provider="mangadex", provider_id=h["provider_id"], title=h["title"], url=h.get("url"),
                cover_url=h.get("cover_url"), alt_titles=h.get("alt_titles") or [], score=h.get("score", 0.0),
            )
            for h in hits
        ]

    def get_series(self, provider_id: str) -> SeriesInfo:
        info = self.call(mangadex_api.fetch_series_info, provider_id)
        return _to_series_info("mangadex", info)

    def get_chapter_pages(self, provider_id: str, chapter_id: str) -> list[PageInfo]:
        import ingest  # for _chapter_source_url (stored chapter UUID lookup)

        def run():
            url = ingest._chapter_source_url(provider_id, chapter_id)
            return mangadex_api.extract_pages(url)

        pages = self.call(run)
        return [
            PageInfo(page_index=p["page_index"], src=p["src"], aspect_ratio=p.get("aspect_ratio"), alt=p.get("alt"))
            for p in pages
        ]

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            can_search=True, can_read_pages=True, can_check_updates=True,
            requires_image_proxy=True, languages=["en"],
            rate_limit_notes="~5 req/s global, 40/min on /at-home/server (enforced in mangadex.py).",
            terms_url="https://api.mangadex.org/docs/",
            status="available",
            notes=(
                "Documented public API. Page images are proxied server-side (never hotlinked), "
                "per MangaDex's Acceptable Usage Policy. No MangaDex account is linked, so "
                "chapter read-reporting back to MangaDex is not supported (needs their OAuth2)."
            ),
        )
