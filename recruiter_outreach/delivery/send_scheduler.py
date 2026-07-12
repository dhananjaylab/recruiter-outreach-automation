# FILE: recruiter_outreach/delivery/send_scheduler.py

"""
SendWindowAdvisor — flags whether "now" is inside the send window that
2026 cold-outreach research consistently identifies as best for open and
reply rates: Tuesday-Thursday, mid-morning. Monday sends compete with a
full inbox from the weekend; Friday afternoon and evenings get the lowest
engagement.

We don't have reliable per-recipient timezone data (a CSV of recruiter
emails rarely includes it), so this is evaluated against server-local
time and surfaced as *advisory* by default — the human approving a send
in the UI sees a clear signal and can decide, rather than the tool
silently queuing emails for later without them noticing. Set
SEND_WINDOW_ENFORCE=true to make it a hard skip instead.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from recruiter_outreach.config import Settings

_DAY_ABBREVIATIONS = {"Mon": 0, "Tue": 1, "Wed": 2, "Thu": 3, "Fri": 4, "Sat": 5, "Sun": 6}


@dataclass
class SendWindowStatus:
    is_optimal: bool
    enforced: bool
    reason: str
    window_description: str


class SendWindowAdvisor:
    def __init__(self, settings: Settings):
        self.enabled = settings.send_window_enabled
        self.enforce = settings.send_window_enforce
        self.start_hour = settings.send_window_start_hour
        self.end_hour = settings.send_window_end_hour
        self.allowed_days = {
            _DAY_ABBREVIATIONS[d.strip()]
            for d in settings.send_window_days.split(",")
            if d.strip() in _DAY_ABBREVIATIONS
        }

    @property
    def window_description(self) -> str:
        day_names = ", ".join(
            name for name, idx in sorted(_DAY_ABBREVIATIONS.items(), key=lambda kv: kv[1])
            if idx in self.allowed_days
        )
        return f"{day_names}, {self.start_hour:02d}:00-{self.end_hour:02d}:00 (server-local)"

    def check(self, when: datetime | None = None) -> SendWindowStatus:
        when = when or datetime.now()

        if not self.enabled:
            return SendWindowStatus(
                is_optimal=True, enforced=False,
                reason="Send-window advisory disabled.",
                window_description=self.window_description,
            )

        in_day = when.weekday() in self.allowed_days
        in_hour = self.start_hour <= when.hour < self.end_hour
        is_optimal = in_day and in_hour

        if is_optimal:
            reason = "Within the optimal send window."
        elif not in_day:
            reason = "Outside the best days (weekends/Mon/Fri see lower engagement)."
        else:
            reason = "Outside the best hours (early morning / evening see lower open rates)."

        return SendWindowStatus(
            is_optimal=is_optimal,
            enforced=self.enforce,
            reason=reason,
            window_description=self.window_description,
        )
