"""ComicSourceProvider adapters: one uniform interface (base.ComicSourceProvider) over
Asura Scans, mgeko.cc and MangaDex, plus a registry used by federated search, provider
diagnostics and safe source-switching. The existing per-source scrape modules at the top of
the repo (ingest.py's Asura code, mgeko.py, mangadex.py) are untouched; these adapters just
wrap them so new, source-spanning features have one shape to call.
"""
from .registry import diagnostics, for_slug, get, list_providers

__all__ = ["diagnostics", "for_slug", "get", "list_providers"]
