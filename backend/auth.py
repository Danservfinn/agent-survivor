from __future__ import annotations

import hmac
import os

from fastapi import Header, HTTPException


def require_operator(authorization: str | None = Header(default=None)) -> None:
    token = os.environ.get("OPERATOR_TOKEN", "").strip()
    if not token:
        raise HTTPException(status_code=503, detail="operator disabled")
    if not hmac.compare_digest((authorization or "").encode(), f"Bearer {token}".encode()):
        raise HTTPException(status_code=401, detail="operator required")
