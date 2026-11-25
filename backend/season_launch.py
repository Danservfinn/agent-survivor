"""The one lock around "check that no season runs, then start one".

Both the public "run a season" button (backend/api.py) and the paid-entry
dispatcher (backend/entry_queue.py) start the same detached runner. They must
share one lock, or a request landing between one path's check and its launch
could start two seasons at once.
"""

from __future__ import annotations

import threading

LAUNCH_LOCK = threading.Lock()
