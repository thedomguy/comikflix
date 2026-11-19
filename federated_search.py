"""Federated search: ask every enabled provider at once, cluster the results into one entry
per canonical title (matching alternate titles and romanizations, not just exact strings),
and report per-provider availability/chapter coverage so the user can pick a source.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from difflib import SequenceMatcher

import providers.registry as registry
import watchlist  # for norm_title(); a plain re-based helper, no heavy deps

CLUSTER_THRESHOLD = 0.72   # SequenceMatcher ratio above which two results are "the same title"
CHAPTER_LOOKUP_TOP_N = 3   # only probe get_chapters() for the top N clusters (keeps latency sane)


def _names(result) -> list[str]:
    return [result.title, *result.alt_titles]


def _best_ratio(a_names: list[str], b_names: list[str]) -> float:
    a_norm = [watchlist.norm_title(n) for n in a_names if n]
    b_norm = [watchlist.norm_title(n) for n in b_names if n]
    best = 0.0
    for a in a_norm:
        for b in b_norm:
            if not a or not b:
                continue
            if a == b:
                return 1.0
            best = max(best, SequenceMatcher(None, a, b).ratio())
    return best


def _search_one(provider, query: str, limit: int) -> tuple[str, list, str | None]:
    try:
        return provider.name, provider.search(query, limit), None
    except Exception as e:
        return provider.name, [], str(e) or e.__class__.__name__


def _cluster(results: list[tuple[str, object]]) -> list[dict]:
    clusters: list[dict] = []
    for provider_name, r in results:
        placed = False
        for cluster in clusters:
            if _best_ratio(_names(r), cluster["_names"]) >= CLUSTER_THRESHOLD:
                cluster["_names"].extend(_names(r))
                cluster["candidates"].append((provider_name, r))
                placed = True
                break
        if not placed:
            clusters.append({"_names": list(_names(r)), "candidates": [(provider_name, r)]})
    return clusters


def _chapter_coverage(provider_name: str, provider_id: str) -> dict:
    try:
        provider = registry.get(provider_name)
        chapters = provider.get_chapters(provider_id)
        return {"chapter_count": len(chapters), "latest_chapter": chapters[-1].number if chapters else None,
                "availability": "available" if chapters else "unavailable", "error": None}
    except Exception as e:
        return {"chapter_count": None, "latest_chapter": None, "availability": "unknown", "error": str(e)}


def search_all(
    query: str,
    provider_names: list[str] | None = None,
    *,
    limit_per_provider: int = 8,
    with_chapter_coverage: bool = True,
) -> dict:
    """Returns {"query", "providers_searched", "provider_errors", "results": [cluster, ...]}.
    Each cluster: {"title", "alt_titles", "candidates": [{provider, provider_id, title, url,
    cover_url, score, can_read_pages, chapter_count, latest_chapter, availability}]}."""
    query = (query or "").strip()
    if not query:
        return {"query": query, "providers_searched": [], "provider_errors": {}, "results": []}

    names = provider_names or list(registry.ENABLED_FOR_SEARCH)
    to_search = []
    for n in names:
        try:
            p = registry.get(n)
        except ValueError:
            continue
        if p.capabilities().can_search:
            to_search.append(p)

    errors: dict[str, str] = {}
    flat: list[tuple[str, object]] = []
    with ThreadPoolExecutor(max_workers=max(1, len(to_search))) as pool:
        for provider_name, hits, err in pool.map(lambda p: _search_one(p, query, limit_per_provider), to_search):
            if err:
                errors[provider_name] = err
            for h in hits:
                flat.append((provider_name, h))

    clusters = _cluster(flat)
    # Best candidate score per cluster decides lookup priority and output order.
    for c in clusters:
        c["best_score"] = max((r.score for _, r in c["candidates"]), default=0.0)
    clusters.sort(key=lambda c: -c["best_score"])

    lookup_targets = []
    if with_chapter_coverage:
        for c in clusters[:CHAPTER_LOOKUP_TOP_N]:
            for provider_name, r in c["candidates"]:
                lookup_targets.append((provider_name, r.provider_id))
    coverage: dict[tuple[str, str], dict] = {}
    if lookup_targets:
        with ThreadPoolExecutor(max_workers=max(1, len(lookup_targets))) as pool:
            for (provider_name, provider_id), cov in zip(
                lookup_targets, pool.map(lambda t: _chapter_coverage(*t), lookup_targets)
            ):
                coverage[(provider_name, provider_id)] = cov

    out = []
    for c in clusters:
        title = c["candidates"][0][1].title
        alt_titles = sorted({n for _, r in c["candidates"] for n in r.alt_titles if n and n != title})
        candidates = []
        for provider_name, r in c["candidates"]:
            cov = coverage.get((provider_name, r.provider_id), {})
            candidates.append({
                "provider": provider_name,
                "provider_id": r.provider_id,
                "title": r.title,
                "url": r.url,
                "cover_url": r.cover_url,
                "score": r.score,
                "can_read_pages": registry.get(provider_name).capabilities().can_read_pages,
                "chapter_count": cov.get("chapter_count"),
                "latest_chapter": cov.get("latest_chapter"),
                "availability": cov.get("availability", "unknown"),
                "coverage_error": cov.get("error"),
            })
        candidates.sort(key=lambda x: -x["score"])
        out.append({"title": title, "alt_titles": alt_titles, "candidates": candidates})

    return {
        "query": query,
        "providers_searched": [p.name for p in to_search],
        "provider_errors": errors,
        "results": out,
    }
