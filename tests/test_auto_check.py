"""Auto-check scheduler: due selection, ended/off skip, modes, provider budget.

Run: .venv/bin/python -m unittest discover tests
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import auto_check as ac  # noqa: E402
import db  # noqa: E402
import ingest  # noqa: E402


def _iso_days_ago(days: int, hour: int = 12) -> str:
    d = datetime.now().astimezone().date() - timedelta(days=days)
    return datetime(d.year, d.month, d.day, hour, 0, tzinfo=timezone.utc).isoformat()


def seed_series(
    slug: str,
    *,
    status: str = "ongoing",
    auto_check: str = "ingest",
    release_day: int | None = None,
    last_checked_at: str | None = None,
    next_check_at: str | None = None,
    published_days_ago: int | None = 7,
) -> None:
    now = db._now()
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO series (slug, title, status, release_day, auto_check, last_checked_at, "
            "next_check_at, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (slug, slug, status, release_day, auto_check, last_checked_at, next_check_at, now, now),
        )
        if published_days_ago is not None:
            pub = _iso_days_ago(published_days_ago)
            conn.execute(
                "INSERT INTO chapters (series_slug, chapter_id, source_url, published_at, "
                "page_count, status) VALUES (?, '1', ?, ?, 10, 'ready')",
                (slug, f"https://example/{slug}/1", pub),
            )
    db.bump()


class AutoCheckBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db.set_db_path(Path(self.tmp.name) / "t.db")
        db.init_schema()
        ac.reset_for_tests()

    def tearDown(self):
        db.close_thread_conn()
        db.set_db_path(None)
        self.tmp.cleanup()
        ac.reset_for_tests()

    def row(self, slug: str):
        return db.get_conn().execute("SELECT * FROM series WHERE slug = ?", (slug,)).fetchone()


class DueSelection(AutoCheckBase):
    def test_ended_status_not_due(self):
        seed_series("done-series", status="completed", last_checked_at=None, published_days_ago=14)
        due, reason = ac.is_due(self.row("done-series"))
        self.assertFalse(due)
        self.assertEqual(reason, "ended")

    def test_dropped_not_due(self):
        seed_series("drop-series", status="dropped", last_checked_at=None)
        due, reason = ac.is_due(self.row("drop-series"))
        self.assertFalse(due)
        self.assertEqual(reason, "ended")

    def test_off_mode_excluded_from_candidates(self):
        seed_series("off-series", auto_check="off", last_checked_at=None)
        self.assertEqual(ac.list_due(), [])

    def test_never_checked_is_due(self):
        seed_series("fresh", last_checked_at=None, next_check_at=None, published_days_ago=3)
        due, reason = ac.is_due(self.row("fresh"))
        self.assertTrue(due)
        self.assertEqual(reason, "never_checked")

    def test_next_check_at_future_blocks(self):
        future = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
        seed_series("later", last_checked_at=_iso_days_ago(1), next_check_at=future)
        due, reason = ac.is_due(self.row("later"))
        self.assertFalse(due)
        self.assertEqual(reason, "scheduled_later")

    def test_next_check_at_elapsed_is_due(self):
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        seed_series("ready-now", last_checked_at=_iso_days_ago(1), next_check_at=past)
        due, reason = ac.is_due(self.row("ready-now"))
        self.assertTrue(due)
        self.assertEqual(reason, "scheduled")

    def test_overdue_unchecked_is_due(self):
        # Last chapter 8 days ago → next_expected ~1 day ago; never checked since then.
        seed_series(
            "overdue",
            release_day=None,
            last_checked_at=_iso_days_ago(10),
            next_check_at=None,
            published_days_ago=8,
        )
        due, reason = ac.is_due(self.row("overdue"))
        self.assertTrue(due)
        self.assertIn(reason, ("overdue_unchecked", "overdue_recheck", "overdue_never_checked"))

    def test_list_due_prefers_overdue_over_never(self):
        past = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        seed_series("aaa-never", last_checked_at=None, next_check_at=None, published_days_ago=2)
        seed_series(
            "zzz-overdue",
            last_checked_at=_iso_days_ago(10),
            next_check_at=past,
            published_days_ago=8,
        )
        due = ac.list_due(limit=5)
        self.assertTrue(due)
        self.assertEqual(due[0]["slug"], "zzz-overdue")


class PatchMode(AutoCheckBase):
    def test_patch_auto_check(self):
        seed_series("patch-me")
        out = db.patch_series("patch-me", {"auto_check": "check"})
        self.assertEqual(out["auto_check"], "check")
        self.assertEqual(self.row("patch-me")["auto_check"], "check")

    def test_patch_rejects_bad_mode(self):
        seed_series("bad-mode")
        with self.assertRaises(ValueError):
            db.patch_series("bad-mode", {"auto_check": "maybe"})


class Modes(AutoCheckBase):
    def _info(self, slug: str, numbers=("1", "2")):
        return {
            "slug": slug,
            "title": slug,
            "chapters": [
                {"number": n, "url": f"https://example/{slug}/{n}", "locked": False, "published_at": _iso_days_ago(1)}
                for n in numbers
            ],
            "cover_url": None,
            "status": "ongoing",
        }

    def test_check_mode_does_not_start_ingest(self):
        seed_series("check-only", auto_check="check", published_days_ago=7)
        manager = ingest.IngestManager()
        info = self._info("check-only", ("1", "2"))
        with mock.patch.object(ingest, "sync_series_metadata", return_value=info), \
             mock.patch.object(ingest, "chapters_needing_pages", return_value=["2"]), \
             mock.patch.object(manager, "start") as start:
            result = ac.run_one("check-only", "check", manager)
        start.assert_not_called()
        self.assertEqual(result["missing"], 1)
        self.assertNotIn("job_id", result)
        self.assertIsNone(self.row("check-only")["auto_check_error"])
        self.assertIsNotNone(self.row("check-only")["last_checked_at"])

    def test_ingest_mode_starts_job_when_missing(self):
        seed_series("do-ingest", auto_check="ingest", published_days_ago=7)
        manager = ingest.IngestManager()
        info = self._info("do-ingest", ("1", "2"))
        with mock.patch.object(ingest, "sync_series_metadata", return_value=info), \
             mock.patch.object(ingest, "chapters_needing_pages", return_value=["2"]), \
             mock.patch.object(ingest, "update_start_chapter", return_value=1.0), \
             mock.patch.object(manager, "start", return_value={"id": "abc123"}) as start:
            result = ac.run_one("do-ingest", "ingest", manager)
        start.assert_called_once()
        kwargs = start.call_args
        self.assertEqual(kwargs.kwargs.get("source") or kwargs[1].get("source"), "auto")
        self.assertEqual(result["job_id"], "abc123")


class Budget(AutoCheckBase):
    def test_provider_gap_blocks_second_check(self):
        self.assertTrue(ac._provider_ready("asura"))
        ac._mark_provider_used("asura")
        self.assertFalse(ac._provider_ready("asura"))

    def test_hour_budget(self):
        import time

        now = time.time()
        for _ in range(ac.MAX_CHECKS_PER_HOUR):
            ac._hour_stamps.append(now)
        self.assertFalse(ac._hour_budget_ok(now))
        ac._hour_stamps.clear()
        self.assertTrue(ac._hour_budget_ok(now))

    def test_tick_skips_when_manual_busy(self):
        manager = ingest.IngestManager()
        with mock.patch.object(manager, "has_manual_running", return_value=True):
            out = ac.tick(manager)
        self.assertEqual(out, [{"skipped": "manual_busy"}])


class Forecast(unittest.TestCase):
    def test_next_release_local_weekly(self):
        # Wednesday publish → next Wednesday (or release_day).
        wed = datetime(2026, 11, 4, 15, 0, tzinfo=timezone.utc)  # Wednesday
        out = ac.next_release_local(None, [wed.isoformat()])
        self.assertIsNotNone(out)
        self.assertEqual(out["release_day"], 2)
        self.assertEqual(out["next_expected"], "2026-11-11")


if __name__ == "__main__":
    unittest.main()
