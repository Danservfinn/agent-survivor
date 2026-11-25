"""The site answers on one public address; its former address redirects there."""

from __future__ import annotations

import asyncio
import unittest

from starlette.requests import Request
from starlette.responses import Response

from backend import api


def _request(host: str, path: str, query: str = "", method: str = "GET") -> Request:
    return Request({"type": "http", "method": method, "path": path, "query_string": query.encode(), "headers": [(b"host", host.encode())]})


def _through(request: Request) -> Response:
    async def call_next(_request):
        return Response("served", status_code=200)

    return asyncio.run(api._redirect_to_public_host(request, call_next))


class PublicHostTest(unittest.TestCase):
    def test_a_former_address_redirects_to_the_same_page(self) -> None:
        # any other kurult.ai name, which covers the address the site had before 2026-11-25
        response = _through(_request("former.kurult.ai", "/report/20261125-1/data/report.json", "k=abc"))
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response.headers["location"], "https://agentsurvivor.kurult.ai/report/20261125-1/data/report.json?k=abc")

    def test_the_public_address_and_local_calls_are_served(self) -> None:
        for host in ("agentsurvivor.kurult.ai", "127.0.0.1:18800", "localhost"):
            self.assertEqual(_through(_request(host, "/api/methodology")).status_code, 200)

    def test_stripes_webhook_and_health_are_never_redirected(self) -> None:
        for path in ("/api/entries/stripe-webhook", "/health"):
            self.assertEqual(_through(_request("old.kurult.ai", path, method="POST")).status_code, 200)


if __name__ == "__main__":
    unittest.main()
