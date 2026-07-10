# FILE: src/recruiter_outreach/delivery/rate_limiter.py

"""Thread-safe sliding-window rate limiter."""

from __future__ import annotations

import logging
import threading
import time

logger = logging.getLogger(__name__)


class RateLimiter:
    def __init__(self, calls_per_period: int, period: int):
        self.calls_per_period = max(1, calls_per_period)
        self.period = max(1, period)
        self.timestamps: list[float] = []
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            now = time.time()
            self.timestamps = [t for t in self.timestamps if t > (now - self.period)]

            if len(self.timestamps) >= self.calls_per_period:
                sleep_time = self.period - (now - self.timestamps[0])
                if sleep_time > 0:
                    logger.warning(
                        f"Rate limit reached ({self.calls_per_period} calls / "
                        f"{self.period}s). Sleeping {sleep_time:.2f}s."
                    )
                    self._lock.release()
                    try:
                        time.sleep(sleep_time)
                    finally:
                        self._lock.acquire()
                    now = time.time()
                    self.timestamps = [t for t in self.timestamps if t > (now - self.period)]

            self.timestamps.append(now)
