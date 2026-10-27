"""Sliding-window rate limits kept in memory, per process.

Each service (API, website, admin website) runs one process, so the limits are exact. Failed sign-ins are also
counted in the database per account (``users.failed_logins``), which holds across restarts and processes.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

# name: (requests, seconds)
LIMITS: dict[str, tuple[int, int]] = {
    "login": (10, 60),
    "register": (5, 3600),
    "refresh": (60, 60),
    "redeem_ip": (30, 3600),
    "redeem_user": (10, 600),
    "reset_ip": (5, 3600),
    "reset_email": (3, 3600),
    "verify": (20, 3600),
    "validate": (120, 3600),
    "device": (30, 3600),
    "admin_codes": (30, 600),
    "default": (300, 60),
    "lan_ip": (120, 60),
    "lan_user": (90, 60),
    "lan_join": (10, 60),
}


class RateLimiter:
    def __init__(self):
        self._hits: dict[tuple[str, str], deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()
        self.enabled = True

    def hit(self, name: str, key: str) -> float | None:
        """Count one request; returns the seconds to wait when the limit is exceeded, else None."""
        if not self.enabled:
            return None
        limit, window = LIMITS.get(name, LIMITS["default"])
        now = time.monotonic()
        with self._lock:
            q = self._hits[(name, key)]
            while q and q[0] <= now - window:
                q.popleft()
            if len(q) >= limit:
                return max(1.0, q[0] + window - now)
            q.append(now)
            if len(self._hits) > 50_000:            # forget idle keys
                for k in [k for k, v in self._hits.items() if not v][:10_000]:
                    del self._hits[k]
        return None

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


LIMITER = RateLimiter()
