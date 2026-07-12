# FILE: recruiter_outreach/delivery/daily_governor.py

"""
DailySendGovernor — enforces a true per-calendar-day send cap.

Why this exists: WarmupScheduler.today_cap() computes "how many emails
should go out today" during warm-up, but the original design fed that
number straight into RateLimiter as calls-per-period (a *sliding window*,
default 60s). That smooths bursts within a single run, but a long-running
or repeatedly-invoked process could still send far more than the intended
daily volume, since the window resets every period indefinitely.

This governor instead asks the database "how many have actually been sent
today?" and refuses once the warm-up-computed cap is reached — the number
that actually protects sender reputation during a Gmail warm-up ramp,
per 2026 deliverability guidance (start ~10-20/day, ramp up gradually).
"""

from __future__ import annotations

from recruiter_outreach.db import Database
from recruiter_outreach.delivery.warmup import WarmupScheduler


class DailySendGovernor:
    def __init__(self, db: Database, warmup: WarmupScheduler | None, static_ceiling: int):
        self.db = db
        self.warmup = warmup
        self.static_ceiling = max(1, static_ceiling)

    def daily_cap(self) -> int:
        """Today's allowed volume: the warm-up ramp value while warming up,
        otherwise the configured ceiling."""
        return self.warmup.today_cap() if self.warmup else self.static_ceiling

    def can_send(self) -> bool:
        return self.db.sends_today_count() < self.daily_cap()

    def remaining_today(self) -> int:
        return max(0, self.daily_cap() - self.db.sends_today_count())
