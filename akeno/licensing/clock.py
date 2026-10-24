"""Trusted time for offline leases, robust against changing the PC's clock.

* Server time is learned at every online check (``offset`` = server time - local time).
* ``high_water`` is the latest trusted time ever seen. If the PC clock (corrected by the offset) is more than
  ``TOLERANCE`` behind it, the clock was turned back: a time-limited lease is then not trusted until the next
  online check. (Turning the clock forward only ends a lease early, and the next online check repairs that.)
* While AKENO runs, elapsed time comes from the monotonic clock, so changing the clock in the meantime has no
  effect at all. Reboots, sleep and time zones do not matter: every value is UTC seconds.

Lifetime certificates do not depend on time and are never affected.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

TOLERANCE = 10 * 60


@dataclass
class TrustedTime:
    now: float
    rolled_back: bool


class ClockGuard:
    def __init__(self, state: dict):
        self.state = state                       # the "clock" part of the saved license state (mutated in place)
        self._anchor: tuple[float, float] | None = None     # (trusted time, monotonic) of this run

    @property
    def offset(self) -> float:
        return float(self.state.get("offset", 0.0))

    @property
    def high_water(self) -> float:
        return float(self.state.get("high_water", 0.0))

    def now(self) -> TrustedTime:
        if self._anchor is not None:
            start, mono = self._anchor
            current = start + (time.monotonic() - mono)
            rolled = bool(self.state.get("rolled_back"))
        else:
            estimate = time.time() + self.offset
            rolled = estimate < self.high_water - TOLERANCE
            current = max(estimate, self.high_water)
            self._anchor = (current, time.monotonic())
            self.state["rolled_back"] = rolled
        if current > self.high_water:
            self.state["high_water"] = current
        return TrustedTime(current, rolled)

    def learn(self, server_time: float) -> None:
        """An online answer: the server's clock is the truth from now on."""
        self.state["offset"] = server_time - time.time()
        self.state["rolled_back"] = False
        self.state["high_water"] = max(self.high_water, server_time) if server_time >= self.high_water - TOLERANCE \
            else server_time
        self._anchor = (server_time, time.monotonic())
