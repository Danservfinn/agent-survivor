from __future__ import annotations

import os

from fastapi import Header, HTTPException


def require_operator(authorization: str | None = Header(default=None)) -> None:
    token = os.environ.get("OPERATOR_TOKEN", "").strip()
    if not token:
        raise HTTPException(status_code=503, detail="operator disabled")
    if authorization != f"Bearer {token}":
        raise HTTPException(status_code=401, detail="operator required")
