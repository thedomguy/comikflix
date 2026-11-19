#!/usr/bin/env python3
"""Custom numbered-image galleries as single-chapter comics.

For sources with no series/chapter listing to scrape (e.g. a direct CDN image-set
link), given a URL template with one {n} page-number placeholder and a page range,
this builds one series with one chapter whose pages point straight at the CDN —
same as asura/mgeko chapters (no download, no R2 upload). Defaults to the private
vault since that's the usual reason to add one by hand.

Usage:
    ./custom_gallery.py --title "..." --url-template "https://host/.../{n}.jpg" \
        --start 1 --end 240 --pad 3
"""
from __future__ import annotations

import argparse
import re
from datetime import datetime, timezone

import db
import db_ingest
import imgsize

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,120}$")


def slugify(title: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return f"custom-{base}"[:120]


def add_gallery(
    title: str,
    url_template: str,
    start: int,
    end: int,
    *,
    slug: str | None = None,
    pad: int = 0,
    private: bool = True,
    status: str = "completed",
) -> str:
    """url_template takes one {n} placeholder for the page number."""
    slug = slug or slugify(title)
    if not SLUG_RE.match(slug):
        raise ValueError(f"invalid slug: {slug}")
    if end < start:
        raise ValueError("end must be >= start")

    def fmt(n: int) -> str:
        return url_template.format(n=str(n).zfill(pad) if pad else str(n))

    pages_meta = [
        {"page_index": i, "src": fmt(n), "aspect_ratio": None, "alt": None}
        for i, n in enumerate(range(start, end + 1))
    ]
    imgsize.fill_aspects(pages_meta)

    db_ingest.upsert_series(
        {
            "slug": slug,
            "title": title,
            "description": None,
            "genres": [],
            "author": None,
            "artist": None,
            "type": None,
            "status": status,
            "rating": None,
            "bookmarks": None,
            "alt_titles": [],
            "source_url": None,
            "chapters": [],
        },
        cover_url=pages_meta[0]["src"] if pages_meta else None,
    )

    pages = [
        {
            "page_index": p["page_index"],
            "r2_key": None,
            "public_url": None,
            "cdn_url": p["src"],
            "aspect_ratio": p.get("aspect_ratio"),
            "alt": p.get("alt"),
        }
        for p in pages_meta
    ]
    db_ingest.mark_chapter_ready(
        slug, "1",
        page_count=len(pages), size_bytes=0,
        source_url=fmt(start),
        published_at=datetime.now(timezone.utc).isoformat(),
        pages=pages,
    )
    db.patch_series(slug, {"private": private, "auto_check": "off"})
    return slug


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--title", required=True)
    ap.add_argument("--slug", help="defaults to a slug built from the title")
    ap.add_argument("--url-template", required=True, help="image URL with {n} where the page number goes")
    ap.add_argument("--start", type=int, required=True)
    ap.add_argument("--end", type=int, required=True)
    ap.add_argument("--pad", type=int, default=0, help="zero-pad the page number to this width")
    ap.add_argument("--public", action="store_true", help="skip the private vault, add to the main library")
    args = ap.parse_args()
    slug = add_gallery(
        args.title, args.url_template, args.start, args.end,
        slug=args.slug, pad=args.pad, private=not args.public,
    )
    print(slug)


if __name__ == "__main__":
    main()
