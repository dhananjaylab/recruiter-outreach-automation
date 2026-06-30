# FILE: src/utils/ratelimiter.py

import logging
import threading
import time


class RateLimiter:
    """
    Thread-safe RateLimiter using a sliding window algorithm.

    Fixes applied vs original:
    - Added missing `import logging` (caused NameError at runtime)
    - Added threading.Lock to protect self.timestamps from race conditions
      when multiple threads call wait() concurrently via ThreadPoolExecutor
    """

    def __init__(self, calls_per_period: int, period: int):
        self.calls_per_period = max(1, calls_per_period)
        self.period = max(1, period)
        self.timestamps: list[float] = []
        self._lock = threading.Lock()
        self.logger = logging.getLogger(__name__)

    def wait(self):
        """
        Blocks the calling thread until it is safe to proceed under the rate limit.
        Thread-safe: multiple threads can call this concurrently without
        corrupting the sliding window timestamp list.
        """
        with self._lock:
            now = time.time()
            # Evict timestamps outside the current sliding window
            self.timestamps = [t for t in self.timestamps if t > (now - self.period)]

            if len(self.timestamps) >= self.calls_per_period:
                # Wait until the oldest timestamp in the window expires
                sleep_time = self.period - (now - self.timestamps[0])
                if sleep_time > 0:
                    self.logger.warning(
                        f"Rate limit reached ({self.calls_per_period} calls / "
                        f"{self.period}s). Sleeping {sleep_time:.2f}s."
                    )
                    # Release lock while sleeping so other threads aren't frozen
                    self._lock.release()
                    try:
                        time.sleep(sleep_time)
                    finally:
                        self._lock.acquire()
                    now = time.time()
                    # Re-evict after sleep
                    self.timestamps = [t for t in self.timestamps if t > (now - self.period)]

            self.timestamps.append(now)
