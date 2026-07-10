# FILE: src/recruiter_outreach/delivery/warmup.py

"""
Domain / sender warm-up: gradually ramps the daily send cap for a new or
low-volume sender rather than sending at full volume from day one.

Sending a large batch from an unauthenticated/low-history address is one
of the fastest ways to get flagged as spam. This ramps linearly:

    cap(day) = min(start_cap + daily_increment * (day - 1), ceiling)

where day 1 is the first day this tool ever recorded a send.
"""

from __future__ import annotations

from datetime import date, datetime

from recruiter_outreach.db import Database

META_KEY = "warmup_start_date"


class WarmupScheduler:
    def __init__(
        self, db: Database, start_cap: int, daily_increment: int, ceiling: int, warmup_days: int,
    ):
        self.db = db
        self.start_cap = max(1, start_cap)
        self.daily_increment = max(0, daily_increment)
        self.ceiling = max(self.start_cap, ceiling)
        self.warmup_days = max(1, warmup_days)

    def _start_date(self) -> date:
        raw = self.db.get_meta(META_KEY)
        if raw is None:
            today_iso = date.today().isoformat()
            self.db.set_meta(META_KEY, today_iso)
            return date.today()
        return datetime.fromisoformat(raw).date()

    def today_cap(self) -> int:
        start = self._start_date()
        day_number = (date.today() - start).days + 1
        if day_number >= self.warmup_days:
            return self.ceiling
        cap = self.start_cap + self.daily_increment * (day_number - 1)
        return min(cap, self.ceiling)
