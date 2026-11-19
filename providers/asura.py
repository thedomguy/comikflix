"""Asura Scans adapter: wraps ingest.py's existing (unchanged) scrape functions."""
from __future__ import annotations

from .base import ComicSourceProvider
from .types import ChapterInfo, PageInfo, ProviderCapabilities, SearchResult, SeriesInfo


class AsuraProvider(ComicSourceProvider):
    name = "asura"

    def owns_slug(self, slug: str) -> bool:
        import mangadex
        import mgeko

        return not mgeko.is_mgeko(slug) and not mangadex.is_mangadex(slug)

    def parse_slug(self, value: str) -> str:
        import ingest

        return ingest.parse_slug(value)

    def search(self, query: str, limit: int = 8) -> list[SearchResult]:
        import ingest

        def run():
            return ingest.catalog_search(query, limit)

        hits = self.call(run)
        return [
            SearchResult(
                provider="asura", provider_id=h["slug"], title=h["title"], url=h.get("url"),
                score=h.get("score", 0.0),
            )
            for h in hits
        ]

    def get_series(self, provider_id: str) -> SeriesInfo:
        import ingest

        def run():
            return ingest.fetch_series_info(provider_id)

        info = self.call(run)
        return _to_series_info("asura", info)

    def get_chapter_pages(self, provider_id: str, chapter_id: str) -> list[PageInfo]:
        import ingest

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
            notes="Reads the public comic/chapter pages directly (no documented API).",
        )


def _to_series_info(provider: str, info: dict) -> SeriesInfo:
    return SeriesInfo(
        provider=provider,
        provider_id=info["slug"],
        slug=info["slug"],
        title=info.get("title") or "",
        source_url=info.get("source_url"),
        description=info.get("description") or "",
        alt_titles=info.get("alt_titles") or [],
        cover_url=info.get("cover_url"),
        rating=info.get("rating"),
        bookmarks=info.get("bookmarks"),
        status=info.get("status"),
        type=info.get("type"),
        author=info.get("author"),
        artist=info.get("artist"),
        genres=info.get("genres") or [],
        chapters=[
            ChapterInfo(
                number=str(c["number"]), url=c.get("url"), published_at=c.get("published_at"),
                page_count=c.get("page_count"), locked=bool(c.get("locked")),
            )
            for c in info.get("chapters") or []
            if c.get("number") is not None
        ],
        moved_from=info.get("moved_from"),
    )
