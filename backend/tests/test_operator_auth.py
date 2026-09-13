from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from backend.auth import require_operator


class OperatorAuthTest(unittest.TestCase):
    def test_missing_token_fails_closed(self) -> None:
        with patch.dict(os.environ, {"OPERATOR_TOKEN": ""}, clear=False):
            with self.assertRaises(HTTPException) as ctx:
                require_operator(authorization="Bearer anything")
        self.assertEqual(ctx.exception.status_code, 503)

    def test_wrong_bearer_is_unauthorized(self) -> None:
        with patch.dict(os.environ, {"OPERATOR_TOKEN": "secret-token"}, clear=False):
            with self.assertRaises(HTTPException) as ctx:
                require_operator(authorization="Bearer nope")
        self.assertEqual(ctx.exception.status_code, 401)

    def test_matching_bearer_allows_operator(self) -> None:
        with patch.dict(os.environ, {"OPERATOR_TOKEN": "secret-token"}, clear=False):
            require_operator(authorization="Bearer secret-token")


if __name__ == "__main__":
    unittest.main()
