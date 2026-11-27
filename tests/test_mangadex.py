"""MangaDex rate limits and the at-home lookup shared by a chapter's page requests.

Run: .venv/bin/python -m unittest discover tests
"""
from __future__ import annotations

import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import mangadex  # noqa: E402

CH = "12345678-1234-1234-1234-123456789abc"


class Throttle(unittest.TestCase):
    def test_concurrent_callers_stay_under_the_limit(self):
        bucket: list[float] = []
        sent: list[float] = []
        lock = threading.Lock()

        def call():
            mangadex._throttle(bucket, 5, 0.5)
            with lock:
                sent.append(time.monotonic())

        threads = [threading.Thread(target=call) for _ in range(12)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        sent.sort()
        busiest = max(sum(1 for y in sent if x <= y < x + 0.5 - 0.02) for x in sent)
        self.assertLessEqual(busiest, 5)


class AtHome(unittest.TestCase):
    def setUp(self):
        mangadex._at_home_cache.clear()

    def test_one_lookup_for_a_burst_of_page_requests(self):
        calls = []

        def fake(chapter_id):
            calls.append(chapter_id)
            time.sleep(0.05)
            return {"baseUrl": "https://node.example", "chapter": {"hash": "h", "data": []}}

        with mock.patch.object(mangadex, "_fetch_at_home", side_effect=fake):
            threads = [threading.Thread(target=mangadex.image_url, args=(CH, "data", f"{i}.png")) for i in range(10)]
            [t.start() for t in threads]
            [t.join() for t in threads]
        self.assertEqual(calls, [CH])


class Covers(unittest.TestCase):
    def test_raw_cover_urls_become_proxy_paths(self):
        self.assertEqual(
            mangadex.proxied_cover(f"https://uploads.mangadex.org/covers/{CH}/a.jpg"),
            f"/api/mangadex/cover/{CH}/a.jpg",
        )
        self.assertEqual(mangadex.proxied_cover("https://cdn.asurascans.com/x.webp"), "https://cdn.asurascans.com/x.webp")

    def test_bad_cover_paths_are_refused(self):
        for manga_id, name in ((CH, "../x"), ("not-a-uuid", "a.jpg"), (CH, "a/b.jpg")):
            with self.assertRaises(ValueError):
                mangadex.fetch_cover(manga_id, name)


if __name__ == "__main__":
    unittest.main()
