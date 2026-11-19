"""Safe source switching: point a canonical comic at a different provider without duplicating
it, reusing the merge logic already built for Asura's own slug redirects (db.move_series) and
the matching watchlist merge (watchlist.move, called from inside move_series).
"""
from __future__ import annotations

import db


class SwitchError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def switch_source(comic_id: str, series_value: str) -> dict:
    """Make `series_value` (any slug/URL ingest.parse_slug accepts — mgeko, MangaDex, or an
    Asura slug) the active source for `comic_id`. Ingests it first if it isn't in the library
    yet, then carries progress/rating/watchlist across via db.move_series + watchlist.move."""
    import ingest  # heavy deps (Pillow, R2 client); only needed when actually switching

    comic = db.get_comic(comic_id)
    if not comic:
        raise SwitchError("unknown comic", 404)

    try:
        new_slug = ingest.parse_slug(str(series_value))
    except ValueError as e:
        raise SwitchError(str(e)) from e

    old_slug = comic["primary_slug"]
    if new_slug == old_slug:
        return comic

    if not db.series_exists(new_slug):
        try:
            info = ingest.fetch_series_info(new_slug)
        except Exception as e:
            raise SwitchError(f"Could not read {series_value}: {ingest._error_text(e)}", 502) from e
        ingest.save_series_info(info)  # also lazily ensures a comics/comic_sources row
        new_slug = info["slug"]

    other = db.get_comic_by_slug(new_slug)
    if other and other["id"] != comic_id:
        db.merge_comics(keep_id=comic_id, other_id=other["id"])

    if old_slug:
        db.move_series(old_slug, new_slug)  # merges chapters/pages/progress/watchlist; drops old_slug

    db.set_comic_primary(comic_id, new_slug)
    db.set_source_active(comic_id, new_slug, True)
    if old_slug:
        db.set_source_active(comic_id, old_slug, False)
    return db.get_comic(comic_id)
