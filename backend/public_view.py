from __future__ import annotations

from copy import deepcopy
from typing import Any

PRIVATE_KEYS = {
    "inner_thought",
    "private_memory",
    "private_signal",
    "raw_prompt",
    "prompt",
    "system_prompt",
    "outsider_private",
}


def public_payload(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: public_payload(item)
            for key, item in value.items()
            if key not in PRIVATE_KEYS
        }
    if isinstance(value, list):
        return [public_payload(item) for item in value]
    return deepcopy(value)
