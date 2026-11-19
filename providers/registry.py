"""The provider registry: one place that knows every ComicSourceProvider, used by federated
search, `/api/providers` diagnostics, and safe source-switching. Each provider is a singleton
instance (health is tracked per instance across calls).

Order matters for `for_slug`: mgeko/mangadex/comick are prefix-identified; Asura is the
fallback (its slugs predate this interface and carry no prefix).
"""
from __future__ import annotations

from .asura import AsuraProvider
from .base import ComicSourceProvider
from .comick import ComickProvider
from .mangadex import MangaDexProvider
from .mgeko import MgekoProvider

_PROVIDERS: list[ComicSourceProvider] = [
    MgekoProvider(),
    MangaDexProvider(),
    ComickProvider(),
    AsuraProvider(),  # fallback: must stay last
]
_BY_NAME = {p.name: p for p in _PROVIDERS}

ENABLED_FOR_SEARCH = ("asura", "mgeko", "mangadex")  # comick excluded: unverified, see comick.py


def list_providers() -> list[ComicSourceProvider]:
    return list(_PROVIDERS)


def get(name: str) -> ComicSourceProvider:
    try:
        return _BY_NAME[name]
    except KeyError:
        raise ValueError(f"unknown provider {name!r}; have {', '.join(_BY_NAME)}") from None


def for_slug(slug: str) -> ComicSourceProvider:
    """The provider that owns a ComikFlix series slug. Asura is the catch-all fallback."""
    for p in _PROVIDERS[:-1]:
        if p.owns_slug(slug):
            return p
    return _PROVIDERS[-1]


def diagnostics() -> list[dict]:
    """Capabilities + live health per provider — the coverage-matrix data source."""
    out = []
    for p in _PROVIDERS:
        caps = p.capabilities()
        health = p.health()
        out.append({
            "provider": p.name,
            "capabilities": {
                "can_search": caps.can_search,
                "can_read_pages": caps.can_read_pages,
                "can_check_updates": caps.can_check_updates,
                "requires_image_proxy": caps.requires_image_proxy,
                "languages": caps.languages,
                "rate_limit_notes": caps.rate_limit_notes,
                "terms_url": caps.terms_url,
                "status": caps.status,
                "notes": caps.notes,
            },
            "health": {
                "status": health.status,
                "last_ok_at": health.last_ok_at,
                "last_error": health.last_error,
                "last_checked_at": health.last_checked_at,
            },
        })
    return out
