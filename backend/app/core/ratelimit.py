"""A small in-process rate limiter for the API (one API process per server).

Limits changes (POST/PUT/PATCH/DELETE) per client address, with a tighter limit for sending
email from the dashboard. Reads are not limited: the dashboard polls. Behind more than one API
process, put the same limits on the reverse proxy instead.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque


class SlidingWindow:
    def __init__(self, limit: int, seconds: float):
        self.limit, self.seconds = limit, seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            hits = self._hits[key]
            while hits and now - hits[0] > self.seconds:
                hits.popleft()
            if len(hits) >= self.limit:
                return False
            hits.append(now)
            if len(self._hits) > 10_000:  # forget idle clients
                for k in [k for k, v in self._hits.items() if not v]:
                    del self._hits[k]
            return True


CHANGES = SlidingWindow(limit=120, seconds=60)
SENDS = SlidingWindow(limit=20, seconds=60)
