# FILE: src/utils/ratelimiter.py

import time

class RateLimiter:
    """
    RateLimiter class to control the rate of API calls.
    """
    def __init__(self, calls_per_period, period):
        self.calls_per_period = max(1, calls_per_period) # Ensure at least 1
        self.period = max(1, period) # Ensure at least 1 second
        self.timestamps = []
        self.logger = logging.getLogger(__name__) # Use standard logger

    def wait(self):
        """
        Waits if necessary to not exceed the rate limit.
        """
        now = time.time()
        # Remove timestamps older than the period
        self.timestamps = [t for t in self.timestamps if t > (now - self.period)]
        
        if len(self.timestamps) >= self.calls_per_period:
            # Time since the first call in the window
            time_since_first_call = now - self.timestamps[0]
            # Time to wait is the remainder of the period
            sleep_time = self.period - time_since_first_call
            
            if sleep_time > 0:
                self.logger.warning(f"Rate limit reached. Sleeping for {sleep_time:.2f} seconds.")
                time.sleep(sleep_time)
            
            # Recalculate 'now' after sleeping
            now = time.time()
        
        self.timestamps.append(now)