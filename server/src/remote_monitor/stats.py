"""Small, lock-free-enough measurement helpers (frame rate, bit rate, timings)."""

from __future__ import annotations

import time
from collections import deque


class RateMeter:
    """Events per second over a sliding window (default 2 s)."""

    def __init__(self, window: float = 2.0):
        self.window = window
        self._events: deque[tuple[float, float]] = deque()
        self._sum = 0.0

    def tick(self, amount: float = 1.0) -> None:
        now = time.perf_counter()
        self._events.append((now, amount))
        self._sum += amount
        self._trim(now)

    def _trim(self, now: float) -> None:
        while self._events and now - self._events[0][0] > self.window:
            _, a = self._events.popleft()
            self._sum -= a

    @property
    def rate(self) -> float:
        now = time.perf_counter()
        self._trim(now)
        if not self._events:
            return 0.0
        span = max(now - self._events[0][0], 1e-3)
        # window not yet full -> use observed span to avoid under-reporting at start-up
        return self._sum / max(min(self.window, span + 1e-9), 0.25)


class Ewma:
    """Exponentially weighted moving average (e.g. per-frame encode time in ms)."""

    def __init__(self, alpha: float = 0.1):
        self.alpha = alpha
        self.value = 0.0
        self._init = False

    def add(self, x: float) -> None:
        if not self._init:
            self.value, self._init = x, True
        else:
            self.value += self.alpha * (x - self.value)
