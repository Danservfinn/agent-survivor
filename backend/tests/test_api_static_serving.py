from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import os

from fastapi import HTTPException
from starlette.requests import Request

from backend import api


def _request(headers: dict[str, str] | None = None) -> Request:
    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    return Request({"type": "http", "method": "GET", "path": "/", "headers": raw, "query_string": b""})


class ApiStaticServingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.frontend_dist = Path(self.temp_dir.name) / "out"
        self.frontend_dist.mkdir()
        (self.frontend_dist / "index.html").write_text("<main>Agent Survivor</main>", encoding="utf-8")
        (self.frontend_dist / "benchmark.html").write_text("<main>Benchmark</main>", encoding="utf-8")
        nested = self.frontend_dist / "arena"
        nested.mkdir()
        (nested / "index.html").write_text("<main>Arena</main>", encoding="utf-8")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_resolves_frontend_files_and_falls_back_to_index(self) -> None:
        with patch.object(api, "FRONTEND_DIST", self.frontend_dist):
            self.assertEqual(api._resolve_frontend_file("").name, "index.html")
            self.assertEqual(api._resolve_frontend_file("benchmark").name, "benchmark.html")
            self.assertEqual(api._resolve_frontend_file("arena").name, "index.html")

    def test_unknown_route_is_a_404_not_the_home_page(self) -> None:
        (self.frontend_dist / "404.html").write_text("<main>Not found</main>", encoding="utf-8")
        with patch.object(api, "FRONTEND_DIST", self.frontend_dist):
            self.assertIsNone(api._resolve_frontend_file("unknown-route"))
            response = api.serve_frontend(_request(), "unknown-route")
            self.assertEqual(response.status_code, 404)
            self.assertEqual(Path(response.path).name, "404.html")
            self.assertEqual(api.serve_frontend(_request(), "").status_code, 200)

    def test_pages_revalidate_and_answer_304_on_matching_etag(self) -> None:
        (self.frontend_dist / "history.txt").write_text("rsc payload", encoding="utf-8")
        with patch.object(api, "FRONTEND_DIST", self.frontend_dist):
            first = api.serve_frontend(_request(), "")
            self.assertEqual(first.status_code, 200)
            self.assertEqual(first.headers["cache-control"], "no-cache")
            self.assertEqual(api.serve_frontend(_request(), "history.txt").headers["cache-control"], "no-cache")

            again = api.serve_frontend(_request({"If-None-Match": first.headers["etag"]}), "")
            self.assertEqual(again.status_code, 304)
            stale = api.serve_frontend(_request({"If-None-Match": '"not-this-build"'}), "")
            self.assertEqual(stale.status_code, 200)

    def test_not_found_page_is_never_cached(self) -> None:
        (self.frontend_dist / "404.html").write_text("<main>Not found</main>", encoding="utf-8")
        with patch.object(api, "FRONTEND_DIST", self.frontend_dist):
            response = api.serve_frontend(_request(), "nope")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.headers["cache-control"], "no-cache")

    def test_root_files_cache_for_an_hour(self) -> None:
        (self.frontend_dist / "favicon.ico").write_bytes(b"icon")
        with patch.object(api, "FRONTEND_DIST", self.frontend_dist):
            response = api.serve_frontend(_request(), "favicon.ico")
        self.assertEqual(response.headers["cache-control"], "public, max-age=3600")

    def test_next_assets_are_immutable(self) -> None:
        chunk = self.frontend_dist / "chunk-abc123.js"
        chunk.write_text("js", encoding="utf-8")
        static = api.ImmutableStaticFiles(directory=str(self.frontend_dist))
        response = static.file_response(chunk, os.stat(chunk), _request().scope)
        self.assertEqual(response.headers["cache-control"], "public, max-age=31536000, immutable")

    def test_rejects_api_media_and_unsafe_paths(self) -> None:
        with patch.object(api, "FRONTEND_DIST", self.frontend_dist):
            self.assertIsNone(api._resolve_frontend_file("api/state"))
            self.assertIsNone(api._resolve_frontend_file("media/voice/test.wav"))
            with self.assertRaises(HTTPException):
                api._resolve_frontend_file("../secret")

    def test_returns_none_when_no_frontend_build_exists(self) -> None:
        missing_dist = Path(self.temp_dir.name) / "missing"
        with patch.object(api, "FRONTEND_DIST", missing_dist):
            self.assertIsNone(api._resolve_frontend_file("benchmark"))

    def test_mounts_next_assets_and_voice_media(self) -> None:
        mounted_paths = {route.path for route in api.app.routes}

        self.assertIn("/_next", mounted_paths)
        self.assertIn("/media/voice", mounted_paths)


if __name__ == "__main__":
    unittest.main()
