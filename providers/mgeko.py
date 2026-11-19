"""mgeko.cc adapter: wraps mgeko.py's existing (unchanged) scrape functions."""
from __future__ import annotations

import mgeko as mgeko_site

from .asura import _to_series_info
from .base import ComicSourceProvider
from .types import PageInfo, ProviderCapabilities, SearchResult, SeriesInfo


class MgekoProvider(ComicSourceProvider):
    name = "mgeko"

    def owns_slug(self, slug: str) -> bool:
        return mgeko_site.is_mgeko(slug)

    def parse_slug(self, value: str) -> str:
        return mgeko_site.parse_slug(value)

    def search(self, query: str, limit: int = 8) -> list[SearchResult]:
        hits = self.call(mgeko_site.search, query, limit)
        return [
            SearchResult(
                provider="mgeko", provider_id=h["slug"], title=h["title"], url=h.get("url"),
                score=h.get("score", 0.0),
            )
            for h in hits
        ]

    def get_series(self, provider_id: str) -> SeriesInfo:
        info = self.call(mgeko_site.fetch_series_info, provider_id)
        return _to_series_info("mgeko", info)

    def get_chapter_pages(self, provider_id: str, chapter_id: str) -> list[PageInfo]:
        import ingest  # for _chapter_source_url/_extract_pages (dispatch stays there)

        def run():
            url = ingest._chapter_source_url(provider_id, chapter_id)
            return ingest._extract_pages(provider_id, url)

        pages = self.call(run)
        return [
            PageInfo(page_index=p["page_index"], src=p["src"], aspect_ratio=p.get("aspect_ratio"), alt=p.get("alt"))
            for p in pages
        ]

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            can_search=True, can_read_pages=True, can_check_updates=True,
            requires_image_proxy=False, languages=["en"],
            rate_limit_notes="Polite scraping only; see auto_check.py's per-provider pacing.",
            terms_url=None, status="available",
            notes="Reads the public series/chapter pages directly (no documented API).",
        )
