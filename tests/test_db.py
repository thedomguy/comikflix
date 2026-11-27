"""Progress ordering, bulk imports, and comic identity across slug moves.

Run: .venv/bin/python -m unittest discover tests
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db  # noqa: E402
import db_ingest  # noqa: E402
import ingest  # noqa: E402

LATER, EARLIER = 2_000_000_000_000, 1_900_000_000_000  # ms epochs


def info(slug: str, moved_from: str | None = None, chapters=("1", "2")) -> dict:
    return {
        "slug": slug, "moved_from": moved_from, "title": "Solo", "source_url": None, "cover_url": None,
        "chapters": [{"number": n, "published_at": None, "page_count": None, "locked": False} for n in chapters],
    }


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db.set_db_path(Path(self.tmp.name) / "t.db")
        db.init_schema()

    def tearDown(self):
        db.close_thread_conn()
        db.set_db_path(None)
        self.tmp.cleanup()


class Progress(Base):
    def test_older_write_keeps_newer_position_but_merges_read(self):
        db.put_progress_slug("s", {"chapter": "5", "frac": 0.8, "read": ["4"], "at": LATER})
        out = db.put_progress_slug("s", {"chapter": "2", "frac": 0.1, "read": ["1"], "at": EARLIER})
        self.assertEqual((out["chapter"], out["frac"], sorted(out["read"])), ("5", 0.8, ["1", "4"]))
        self.assertEqual(out["at"], LATER)
        self.assertEqual(out["read_at"], LATER)

    def test_newer_write_moves_position(self):
        db.put_progress_slug("s", {"chapter": "2", "frac": 0.1, "at": EARLIER})
        out = db.put_progress_slug("s", {"chapter": "3", "frac": 0.5, "at": LATER})
        self.assertEqual((out["chapter"], out["frac"]), ("3", 0.5))

    def test_import_replaces_even_when_older(self):
        db.put_progress_slug("s", {"chapter": "5", "frac": 0.8, "read": ["4", "5"], "at": LATER})
        out = db.put_progress_slug("s", {"chapter": "4", "frac": 0, "read": ["4"], "at": EARLIER}, reading=False)
        self.assertEqual((out["chapter"], out["read"]), ("4", ["4"]))
        self.assertEqual(out["read_at"], LATER)  # an import never counts as reading

    def test_bulk_import_is_not_reading(self):
        db.put_progress_all({"s": {"chapter": "1", "read": ["1"], "at": EARLIER}}, reading=False)
        self.assertIsNone(db.get_progress()["s"]["read_at"])


class ComicIdentity(Base):
    def test_asura_slug_move_keeps_one_comic(self):
        ingest.save_series_info(info("solo-aaaaaaaa"))
        ingest.save_series_info(info("solo-bbbbbbbb", moved_from="solo-aaaaaaaa"))
        comics = db.list_comics()
        self.assertEqual(len(comics), 1)
        self.assertEqual(comics[0]["primary_slug"], "solo-bbbbbbbb")
        self.assertEqual([s["series_slug"] for s in comics[0]["sources"]], ["solo-bbbbbbbb"])

    def test_move_onto_existing_series_folds_its_comic(self):
        ingest.save_series_info(info("solo-aaaaaaaa"))
        ingest.save_series_info(info("solo-bbbbbbbb"))
        keep = db.get_comic_by_slug("solo-aaaaaaaa")["id"]
        db.move_series("solo-aaaaaaaa", "solo-bbbbbbbb")
        comics = db.list_comics()
        self.assertEqual([c["id"] for c in comics], [keep])
        self.assertEqual(comics[0]["primary_slug"], "solo-bbbbbbbb")

    def test_backfill_drops_comics_with_no_series_left(self):
        ingest.save_series_info(info("solo-aaaaaaaa"))
        with db.transaction() as conn:  # what a pre-fix slug move left behind
            conn.execute("DELETE FROM series WHERE slug = 'solo-aaaaaaaa'")
        ingest.save_series_info(info("solo-bbbbbbbb"))
        db.backfill_comic_identities()
        self.assertEqual([c["primary_slug"] for c in db.list_comics()], ["solo-bbbbbbbb"])

    def test_provider_prefixes(self):
        self.assertEqual(db.provider_for_slug("custom-some-gallery"), "custom")
        self.assertEqual(db.provider_for_slug("mangadex-x"), "mangadex")
        self.assertEqual(db.provider_for_slug("solo-aaaaaaaa"), "asura")


class Http(unittest.TestCase):
    """The real handler with an API token configured (no PIN: the app itself stays open)."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        db.set_db_path(Path(cls.tmp.name) / "h.db")
        db.init_schema()
        import server

        cls.server_mod = server
        cls.env = mock.patch.dict(os.environ, {"COMIKFLIX_API_TOKEN": "tok"})
        cls.env.start()
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), partial(server.Handler, directory=str(server.HERE)))
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.env.stop()
        db.close_thread_conn()
        db.set_db_path(None)
        cls.tmp.cleanup()

    def call(self, method, path, body=None, headers=None):
        data = json.dumps(body).encode() if body is not None else None
        h = {"Content-Type": "application/json"} if body is not None else {}
        req = urllib.request.Request(self.base + path, data=data, method=method, headers={**h, **(headers or {})})
        try:
            with urllib.request.urlopen(req) as res:
                return res.status, json.loads(res.read())
        except urllib.error.HTTPError as e:
            with e:
                return e.code, json.loads(e.read())

    def test_bulk_progress_with_token_is_an_import(self):
        st, out = self.call("PUT", "/api/progress", {"bulk": {"chapter": "1", "read": ["1"]}},
                            headers={"Authorization": "Bearer tok"})
        self.assertEqual(st, 200)
        self.assertIsNone(out["bulk"]["read_at"])

    def test_non_ascii_token_is_rejected_not_crashed(self):
        st, out = self.call("GET", "/api/me", headers={"Authorization": "Bearer té"})
        self.assertEqual(st, 200)

    def test_library_labels_and_proxied_covers(self):
        slug = "mangadex-12345678-1234-1234-1234-123456789abc"
        db_ingest.upsert_series({**info(slug), "title": "MD"},
                                cover_url=f"https://uploads.mangadex.org/covers/{slug[9:]}/c.jpg")
        db_ingest.mark_chapter_ready(slug, "1", page_count=1, size_bytes=0,
                                     pages=[{"page_index": 0, "cdn_url": "/api/mangadex/image/x/data/1.png"}])
        s = next(s for s in self.server_mod.scan_library() if s["slug"] == slug)
        self.assertEqual(s["source"], "mangadex")
        self.assertEqual(s["poster"], f"/api/mangadex/cover/{slug[9:]}/c.jpg")


if __name__ == "__main__":
    unittest.main()
