# ComikFlix provider architecture

## Overview

Every comic source implements `providers.base.ComicSourceProvider`:
`search`, `get_series`, `get_chapters`, `get_chapter_pages`, `check_updates`,
`capabilities()`, `health()`. Adapters in `providers/` wrap the existing, unmodified scrape
code (`ingest.py`'s Asura functions, `mgeko.py`, `mangadex.py`) rather than reimplementing it,
so the refactor is purely a dispatch change — see "What changed" below.

`providers.registry` holds one instance per provider and answers `for_slug()` (which provider
owns a ComikFlix series slug), `diagnostics()` (capabilities + live health — this is what
`GET /api/providers` returns and what the matrix below is generated from), and the enabled
list for search (`asura`, `mgeko`, `mangadex` — **not** `comick`, see below).

`federated_search.search_all(query)` runs every enabled provider's `search()` concurrently,
clusters results into one entry per canonical title (matching alternate titles/romanizations
via `watchlist.norm_title()` + `SequenceMatcher`, not just exact strings), and — for the top 3
clusters only, to keep latency reasonable — calls `get_chapters()` per candidate for real
chapter-count/availability, never reporting a provider as "has it" from search metadata alone.

Canonical identity is additive: new `comics` / `comic_sources` tables sit alongside the
existing `series` table (which stays the key for chapters/pages/progress/watchlist, unchanged).
Every series gets a `comics` row lazily (`db._ensure_comic`, hooked into `db.upsert_series`) and
via a one-time startup backfill (`db.backfill_comic_identities()`). Switching a comic's active
source (`providers/switch.py:switch_source`) ingests the new source if needed, then reuses
`db.move_series()` + `watchlist.move()` — the same merge logic Asura's own slug-redirects
already relied on — to carry chapters/progress/rating/watchlist across without duplicating
the comic.

## Capability matrix

| Provider | Search | Read chapters | Check updates | Image delivery | Status |
|---|---|---|---|---|---|
| Asura Scans | Yes (see limitation below) | Yes | Yes | Direct CDN (unchanged) | available |
| mgeko.cc | Yes | Yes | Yes | Direct CDN (unchanged) | available |
| MangaDex | Yes (documented API) | Yes, English only | Yes | **Proxied** (`/api/mangadex/image/...`); never hotlinked | available |
| Comick | No | No | No | n/a | **unverified — not implemented** |

## Comick: evaluated, not implemented

Per the task's own condition ("implement only if current API access, terms, and
chapter-reading capabilities are verified; do not use undocumented authentication bypasses"):
a fetch of `https://comick.io/` this session returned **HTTP 403**, no published API
documentation was found, and no Terms of Service addressing third-party/API use was found.
The only known way to search or read chapters from Comick is an undocumented, commonly
reverse-engineered API (often referenced as `api.comick.fun`) — exactly what the task says not
to rely on. `providers/comick.py` registers it as `status: "unverified"` so it shows up
truthfully in diagnostics instead of silently missing. Re-evaluate if Comick ever publishes an
official API or ToS.

## MangaDex specifics

- Public, documented REST API (`api.mangadex.org`), verified this session against its
  Acceptable Usage Policy: free for non-commercial, ad-free, crediting use — fits a personal
  single-user reader. Required: a real `User-Agent` (set in `mangadex.py`), no `Via` header,
  ~5 req/s global and 40/min on `/at-home/server` (enforced by `mangadex._throttle`, which hands
  each caller its own send slot so concurrent callers are spread out). A chapter's page requests
  share one `/at-home/server` lookup (`mangadex._at_home`), so a cold cache costs one call, not
  one per page.
- **Images are never hotlinked.** Every page URL ComikFlix stores/serves is our own
  `/api/mangadex/image/<chapterId>/<quality>/<filename>` path; `server.py`'s
  `_send_mangadex_image` resolves the real, short-lived MangaDex CDN URL server-side on each
  request (cached 5 minutes per chapter) and streams the bytes. Covers go the same way, through
  `/api/mangadex/cover/<mangaId>/<filename>` (raw `uploads.mangadex.org` cover URLs stored earlier
  are rewritten when served). The browser's `<img>` tag only ever talks to ComikFlix's own origin.
- **Unsupported capability (explicit):** no MangaDex account is linked, so chapter
  "mark as read" is never reported back to MangaDex (`POST /chapter/{id}/read` needs their
  OAuth2 user auth, out of scope). ComikFlix's own `progress` table is unaffected — reading
  progress inside the app works exactly as it does for Asura/mgeko.
- **Unsupported capability (explicit):** chapters with no numeric `attributes.chapter` (oneshots,
  specials) are skipped, not ingested — the existing `chapters` table sorts by
  `CAST(chapter_id AS REAL)`, which a non-numeric id would break for every provider. Fixing
  this would mean changing that sort key across the whole app; out of scope for this pass.
- Only the `en` translated-language feed is read (`FEED_LANGS`). Scanlation-group attribution
  (required by MangaDex's policy when a UI shows chapters) is fetched (`includes[]=scanlation_group`)
  but **not yet surfaced in the frontend** — another explicit, documented gap for a personal
  single-user reader; add it before giving this app to anyone else.

## Asura Scans: a pre-existing search limitation, found while building the coverage matrix

`ingest.catalog_search()` (used by `/api/catalog` and federated search) scores titles against
a cached list of slugs scraped from `asurascans.com/comics`. Fetching that page live today
returns **only ~112 titles, all sharing the exact same slug hash suffix** — strong evidence the
site now serves just the first page of a client-rendered/paginated catalogue to a plain HTML
fetch. None of the 7 acceptance titles were in that page, so Asura returned zero search hits
for all of them in this session's run — **not** because Asura doesn't host them, but because
its catalogue search is currently blind beyond that first page. This is unrelated to this
change (the scrape code was not modified) and was true before this work started. Direct import
by URL/slug (unaffected by this) still works normally if you have an Asura link.

## Federated search coverage matrix (acceptance titles, live data gathered this session)

Generated by calling `federated_search.search_all(title)` for real against Asura, mgeko and
MangaDex — chapter counts are from each provider's actual chapter feed, not metadata guesses.

| Title searched | Found on | Chapters (readable) | Notes |
|---|---|---|---|
| Memoir of the God of War | mgeko, MangaDex | mgeko: 256 (readable); MangaDex: 3 separate entries (1, 18, 108 ch., all readable) | Not found on Asura (see limitation above). Clustered correctly with its Korean/alt titles. |
| Memoir of the King of War | mgeko, MangaDex | mgeko: 256 (readable); MangaDex: 1 ch. readable + 1 entry with no English chapters | Same cluster as above. |
| Jeonwangjeongi | mgeko, MangaDex | mgeko: 256 (readable); MangaDex: 1 ch. readable | Correctly clustered to "Memoir of the King of War" via alt-title match — the romanization case this feature targets. |
| Castle | mgeko, MangaDex | mgeko: 122 listed / 120 readable; MangaDex: 2 entries readable (4 and 6 ch.), 3 entries with no English chapters | Not found on Asura. MangaDex has several fragmented entries for this title. |
| The Boxer | mgeko, MangaDex | mgeko: 134 listed / 135 latest (readable); MangaDex: 10 of 21 ch. readable in English | Not found on Asura. |
| Barbarian Quest | mgeko, MangaDex | mgeko: 100 (readable); MangaDex: entry exists, 0 English chapters | Not found on Asura. |
| Kagurabachi | mgeko, MangaDex | mgeko: 139 (readable); MangaDex: 2 entries, 0 English chapters each | Not found on Asura. |

**Reading is supported** everywhere a chapter count is listed as "readable" above — this was
confirmed by actually calling each provider's chapter feed (`get_chapters`), not by assuming
search metadata means the chapters exist, per the task's explicit instruction. Entries marked
"0 English chapters" exist on MangaDex but currently have no translated-language=`en` release,
so `can_read_pages` is `True` (the provider supports reading in general) but nothing is
currently available to actually read for that specific MangaDex entry.

## What changed vs. what didn't

- **Unchanged, byte-for-byte:** `asura_chapter_pages.py`, `mgeko.py`'s scraping internals,
  `watchlist.py`'s merge logic, `r2.py`, `sync_library.py`, the shape of `series`/`chapters`/
  `pages`/`progress`/`watchlist`.
- **Mechanically generalized** (same behavior, one more branch): `ingest.py`'s four dispatch
  points (`parse_slug`, `fetch_series_info`, `_chapter_source_url`, `_extract_pages`) and
  `auto_check.provider_for()`.
- **New:** `mangadex.py` (the API client), `providers/` (the interface + adapters + registry +
  switch), `federated_search.py`, and additive-only `server.py`/`db.py` changes (new routes,
  new tables, nothing existing changed shape).
- **Already got this for free:** incremental updates, retries and resumable jobs for MangaDex
  — `ingest.IngestManager` is provider-agnostic once the four dispatch points above handle it,
  so MangaDex series use the exact same background-job machinery (cancel/retry/history) Asura
  and mgeko already had.
- **Deliberately out of scope (v1):** simultaneous multi-provider reading for one comic (this
  version has one *active* source at a time, switchable); non-numeric "special" chapters from
  any provider; Comick; MangaDex read-reporting and scanlation-group UI credit; a connector for
  the external Jarvis MCP server (a separate repo — see "Jarvis MCP integration" below).

## Jarvis MCP integration

The Jarvis MCP server that exposes `add_comic`/`list_comics`/`check_comic_updates`/etc. tools
is a separate service (not in this repo) that calls this API with a bearer token
(`Authorization: Bearer <COMIKFLIX_API_TOKEN>`, see `auth.py`). This change adds the API
surface a future Jarvis tool update would call — `GET /api/providers`,
`GET /api/search/federated`, `POST /api/comics/<id>/switch-source` — but does not (and cannot,
from this repo) add new tools to Jarvis itself.

## New API endpoints

- `GET /api/providers` — capabilities + live health per provider (the matrix above, live).
- `GET /api/search/federated?q=<title>&providers=asura,mgeko,mangadex` — clustered results.
- `GET /api/comics` — every canonical comic and its source mappings.
- `POST /api/comics/<id>/switch-source` — body `{"series": "<slug or URL>"}`.
- `GET /api/mangadex/image/<chapterId>/<quality>/<filename>` — the page proxy (quality: `data` or
  `data-saver`).
- `GET /api/mangadex/cover/<mangaId>/<filename>` — the cover proxy.
- `/api/catalog?source=mangadex&q=...` — alongside the existing `source=mgeko`.

## Database migration

Two new, additive tables (`CREATE TABLE IF NOT EXISTS`, nothing existing is altered):

```sql
CREATE TABLE IF NOT EXISTS comics (
    id TEXT PRIMARY KEY,
    primary_slug TEXT,
    title TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS comic_sources (
    id TEXT PRIMARY KEY,
    comic_id TEXT NOT NULL REFERENCES comics(id) ON DELETE CASCADE,
    provider TEXT NOT NULL,
    provider_series_id TEXT NOT NULL,
    series_slug TEXT,
    url TEXT,
    language TEXT,
    chapter_count INTEGER,
    availability TEXT NOT NULL DEFAULT 'unknown',
    is_active INTEGER NOT NULL DEFAULT 0,
    last_checked_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (provider, provider_series_id)
);
CREATE INDEX IF NOT EXISTS idx_comic_sources_comic ON comic_sources(comic_id);
CREATE INDEX IF NOT EXISTS idx_comic_sources_slug ON comic_sources(series_slug);
```

They run via `db.init_schema()` (same lazy, idempotent pattern every existing migration in
`db.py` uses) plus a one-time `db.backfill_comic_identities()` call added to `server.main()`.
**This SQL was shown to the user and confirmed before being applied to the live database.**

## Testing

`python3 -m unittest discover tests` runs the unit tests (temp databases, no network). They're
not run on the VPS; run them on a development machine.
