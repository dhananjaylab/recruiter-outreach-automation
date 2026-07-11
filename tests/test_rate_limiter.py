import threading
import time

from recruiter_outreach.delivery.rate_limiter import RateLimiter


def test_allows_calls_up_to_limit():
    rl = RateLimiter(calls_per_period=3, period=60)
    for _ in range(3):
        rl.wait()
    assert len(rl.timestamps) == 3


def test_blocks_and_waits_when_limit_exceeded():
    rl = RateLimiter(calls_per_period=1, period=1)
    rl.wait()
    start = time.time()
    rl.wait()
    elapsed = time.time() - start
    assert elapsed >= 0.8


def test_thread_safety_no_lost_updates():
    rl = RateLimiter(calls_per_period=1000, period=60)

    def hammer():
        for _ in range(50):
            rl.wait()

    threads = [threading.Thread(target=hammer) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(rl.timestamps) == 500
