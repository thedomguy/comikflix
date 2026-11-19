"""Background library auto-check: polite, release-day-aware updates for ongoing series.

Modes (series.auto_check):
  off     — never scheduled
  check   — series-page metadata + chapter list only (pending chapters appear in the UI)
  ingest  — check, then start IngestManager for missing chapter page URLs (default)

The scheduler runs in-process (daemon thread). It prefers series whose next chapter is
due or overdue, spaces requests per provider, and backs off on errors so Asura/Mgeko
are not hammered.
"""
from __future__ import annotations

import random
import sqlite3
import threading
import time
import urllib.error
from datetime import date, datetime, timedelta, timezone
from typing import TYPE_CHECKING

import db
import ingest
import providers.registry as registry

if TYPE_CHECKING:
    from ingest import IngestManager

ENDED = frozenset(("completed", "dropped"))
TICK_SECONDS = 120
PROVIDER_GAP_SECONDS = 50
MAX_CHECKS_PER_HOUR = 24
OVERDUE_RECHECK_HOURS = 22
OVERDUE_GRACE_DAYS = 3
NEVER_CHECKED_BATCH = 1  # trickle first-time checks
JITTER_SECONDS = (5 * 60, 3 * 60 * 60)  # 5m … 3h after a scheduled window

_lock = threading.Lock()
_started = False
_provider_last: dict[str, float] = {}
_provider_pause_until: dict[str, float] = {}
_hour_stamps: list[float] = []
_fail_counts: dict[str, int] = {}


def provider_for(slug: str) -> str:
    return registry.for_slug(slug).name


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def next_release_local(release_day: int | None, published_ats: list[str | None]) -> dict | None:
    """Same rule as server.next_release (weekly; local calendar day for next_expected)."""
    stamps = []
    for d in published_ats:
        try:
            stamps.append(datetime.fromisoformat(d.replace("Z", "+00:00")).astimezone())
        except (AttributeError, ValueError, TypeError):
            pass
    if not stamps:
        return None
    last = max(stamps)
    day = last.weekday() if release_day is None else int(release_day)
    due = last.date() + timedelta(days=(day - last.weekday() - 1) % 7 + 1)
    return {"release_day": day, "last_published": last.isoformat(), "next_expected": due.isoformat()}


def _candidate_rows() -> list[sqlite3.Row]:
    return db.get_conn().execute(
        "SELECT slug, status, release_day, auto_check, last_checked_at, next_check_at, auto_check_error "
        "FROM series WHERE COALESCE(auto_check, 'ingest') != 'off' "
        "ORDER BY title COLLATE NOCASE, slug"
    ).fetchall()


def _published_ats(slug: str) -> list[str | None]:
    return [
        r["published_at"]
        for r in db.get_conn().execute(
            "SELECT published_at FROM chapters WHERE series_slug = ?", (slug,)
        ).fetchall()
    ]


def schedule_after_success(slug: str, info: dict | None, *, had_new: bool) -> str:
    """ISO timestamp for the next allowed check after a successful run."""
    now = _now_utc()
    jitter = timedelta(seconds=random.randint(*JITTER_SECONDS))
    release_day = None
    published = []
    row = db.get_conn().execute(
        "SELECT release_day FROM series WHERE slug = ?", (slug,)
    ).fetchone()
    if row:
        release_day = row["release_day"]
    if info:
        published = [c.get("published_at") for c in (info.get("chapters") or [])]
        slug = info.get("slug") or slug
    else:
        published = _published_ats(slug)
    forecast = next_release_local(release_day, published)
    if forecast:
        expected = date.fromisoformat(forecast["next_expected"])
        today = datetime.now().astimezone().date()
        overdue_days = (today - expected).days
        if had_new or overdue_days < 0:
            # New chapter landed (or still early): wait for the new expected day.
            target = datetime.combine(expected, datetime.min.time()).astimezone()
            if target <= now.astimezone():
                target = now + timedelta(hours=OVERDUE_RECHECK_HOURS)
            return (target + jitter).astimezone(timezone.utc).isoformat()
        if overdue_days <= OVERDUE_GRACE_DAYS:
            # Soft rechecks for a few days after the expected release.
            return (now + timedelta(hours=OVERDUE_RECHECK_HOURS) + jitter).isoformat()
        # Past the grace window with nothing new: next weekly window.
        nxt = expected + timedelta(days=7)
        target = datetime.combine(nxt, datetime.min.time()).astimezone()
        return (max(target, now.astimezone()) + jitter).astimezone(timezone.utc).isoformat()
    # No publish dates: check again in a week.
    return (now + timedelta(days=7) + jitter).isoformat()


def schedule_after_failure(slug: str, status: int | None = None) -> str:
    """Exponential backoff for a series; longer for 403/429."""
    n = _fail_counts.get(slug, 0)
    base = 30 * 60  # 30 minutes
    if status in (403, 429):
        base = 2 * 60 * 60
    delay = min(base * (2 ** min(n, 4)), 24 * 60 * 60)
    delay += random.randint(60, 15 * 60)
    return (_now_utc() + timedelta(seconds=delay)).isoformat()


def is_due(row, *, now: datetime | None = None, today: date | None = None) -> tuple[bool, str]:
    """Whether this series should be checked. Returns (due, reason)."""
    now = now or _now_utc()
    today = today or datetime.now().astimezone().date()
    status = (row["status"] or "ongoing").lower()
    if status in ENDED:
        return False, "ended"
    mode = (row["auto_check"] or "ingest").lower()
    if mode == "off":
        return False, "off"

    next_at = _parse_iso(row["next_check_at"] if "next_check_at" in row.keys() else None)
    if next_at and next_at > now:
        return False, "scheduled_later"
    if next_at and next_at <= now:
        return True, "scheduled"

    last = _parse_iso(row["last_checked_at"] if "last_checked_at" in row.keys() else None)
    if last is None:
        return True, "never_checked"

    forecast = next_release_local(row["release_day"], _published_ats(row["slug"]))
    if not forecast:
        return False, "no_forecast"

    expected = date.fromisoformat(forecast["next_expected"])
    if expected > today:
        return False, "not_yet_due"

    # Due or overdue release window.
    if last is None:
        return True, "overdue_never_checked"
    last_local = last.astimezone().date()
    if last_local < expected:
        return True, "overdue_unchecked"
    overdue_days = (today - expected).days
    if overdue_days <= OVERDUE_GRACE_DAYS:
        # Recheck periodically during the grace window.
        if (now - last) >= timedelta(hours=OVERDUE_RECHECK_HOURS):
            return True, "overdue_recheck"
        return False, "grace_wait"
    return False, "past_grace"


def list_due(limit: int = 5) -> list[dict]:
    """Due series for the next tick, prioritized: overdue first, then never-checked trickle."""
    overdue: list[dict] = []
    never: list[dict] = []
    for row in _candidate_rows():
        due, reason = is_due(row)
        if not due:
            continue
        item = {
            "slug": row["slug"],
            "auto_check": (row["auto_check"] or "ingest").lower(),
            "status": row["status"],
            "provider": provider_for(row["slug"]),
            "reason": reason,
        }
        if reason == "never_checked":
            never.append(item)
        else:
            overdue.append(item)
    out = overdue[:limit]
    if len(out) < limit:
        out.extend(never[: min(NEVER_CHECKED_BATCH, limit - len(out))])
    return out


def _hour_budget_ok(now_ts: float | None = None) -> bool:
    now_ts = now_ts or time.time()
    cutoff = now_ts - 3600
    while _hour_stamps and _hour_stamps[0] < cutoff:
        _hour_stamps.pop(0)
    return len(_hour_stamps) < MAX_CHECKS_PER_HOUR


def _provider_ready(provider: str, now_ts: float | None = None) -> bool:
    now_ts = now_ts or time.time()
    pause = _provider_pause_until.get(provider, 0)
    if now_ts < pause:
        return False
    last = _provider_last.get(provider, 0)
    return (now_ts - last) >= PROVIDER_GAP_SECONDS


def _mark_provider_used(provider: str, *, hard_pause: float = 0) -> None:
    now_ts = time.time()
    _provider_last[provider] = now_ts
    _hour_stamps.append(now_ts)
    if hard_pause > 0:
        _provider_pause_until[provider] = max(_provider_pause_until.get(provider, 0), now_ts + hard_pause)


def _http_status(exc: BaseException) -> int | None:
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code
    text = str(exc)
    for code in (429, 403, 503, 502):
        if str(code) in text:
            return code
    return None


def run_one(slug: str, mode: str, manager: IngestManager) -> dict:
    """Run a single auto-check. Returns a small result dict for tests/logging."""
    mode = (mode or "ingest").lower()
    if mode == "off":
        return {"slug": slug, "skipped": "off"}
    if manager.is_running(slug):
        return {"slug": slug, "skipped": "already_running"}

    provider = provider_for(slug)
    result: dict = {"slug": slug, "mode": mode, "provider": provider}
    try:
        info = ingest.sync_series_metadata(slug)
        slug = info.get("slug") or slug
        result["title"] = info.get("title")
        missing = ingest.chapters_needing_pages(info)
        result["missing"] = len(missing)
        had_new = bool(missing)
        started_job = None
        if mode == "ingest" and missing:
            start = ingest.update_start_chapter(slug)
            try:
                started_job = manager.start(
                    slug,
                    start_chapter=start,
                    source="auto",
                    tags=["auto-check"],
                    info=info,  # avoid a second series-page hit
                )
            except ValueError as e:
                # Race: another job started; metadata was still saved.
                result["ingest_skipped"] = str(e)
        next_at = schedule_after_success(slug, info, had_new=had_new)
        db.record_auto_check(slug, next_check_at=next_at, error=None)
        _fail_counts.pop(slug, None)
        result["next_check_at"] = next_at
        if started_job:
            result["job_id"] = started_job.get("id")
        return result
    except Exception as e:
        status = _http_status(e)
        _fail_counts[slug] = _fail_counts.get(slug, 0) + 1
        next_at = schedule_after_failure(slug, status)
        err = ingest._error_text(e) if hasattr(ingest, "_error_text") else str(e)
        db.record_auto_check(slug, next_check_at=next_at, error=err[:500])
        if status in (403, 429):
            _mark_provider_used(provider, hard_pause=30 * 60)
        result["error"] = err
        result["next_check_at"] = next_at
        return result


def tick(manager: IngestManager) -> list[dict]:
    """One scheduler pass: at most one series-page fetch when budgets allow."""
    if manager.has_manual_running():
        return [{"skipped": "manual_busy"}]
    if not _hour_budget_ok():
        return [{"skipped": "hour_budget"}]

    due = list_due(limit=5)
    if not due:
        return []

    results = []
    for item in due:
        provider = item["provider"]
        if not _provider_ready(provider):
            continue
        if manager.is_running(item["slug"]):
            continue
        _mark_provider_used(provider)
        results.append(run_one(item["slug"], item["auto_check"], manager))
        break  # one series-page fetch per tick
    return results


def _loop(manager: IngestManager, stop: threading.Event) -> None:
    # Stagger the first tick so boot warm-up / catalog fetch settle first.
    stop.wait(45)
    while not stop.is_set():
        try:
            tick(manager)
        except Exception as e:
            print(f"auto-check tick failed: {e}")
        stop.wait(TICK_SECONDS)


def start(manager: IngestManager) -> None:
    """Start the background auto-check loop once per process."""
    global _started
    with _lock:
        if _started:
            return
        _started = True
        stop = threading.Event()
        threading.Thread(target=_loop, args=(manager, stop), name="auto-check", daemon=True).start()


def reset_for_tests() -> None:
    """Clear in-memory budget/backoff state (unit tests only)."""
    global _started
    with _lock:
        _started = False
        _provider_last.clear()
        _provider_pause_until.clear()
        _hour_stamps.clear()
        _fail_counts.clear()
