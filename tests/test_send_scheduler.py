"""Tests for SendWindowAdvisor (delivery/send_scheduler.py)."""

from __future__ import annotations

from datetime import datetime

from recruiter_outreach.config import Settings
from recruiter_outreach.delivery.send_scheduler import SendWindowAdvisor


def _settings(tmp_path, **overrides) -> Settings:
    base = dict(
        EMAIL_USER="me@example.com",
        RESUME_LINK="https://example.com/cv",
        DB_PATH=str(tmp_path / "test.db"),
        REPORTS_DIR="reports",
    )
    base.update(overrides)
    return Settings(**base)


class TestSendWindowAdvisor:
    def test_disabled_always_optimal(self, tmp_path):
        s = _settings(tmp_path, SEND_WINDOW_ENABLED=False)
        advisor = SendWindowAdvisor(s)
        status = advisor.check(when=datetime(2026, 7, 13, 3, 0))  # Monday 3am
        assert status.is_optimal is True
        assert status.enforced is False

    def test_tuesday_morning_is_optimal(self, tmp_path):
        s = _settings(tmp_path, SEND_WINDOW_ENABLED=True, SEND_WINDOW_DAYS="Tue,Wed,Thu",
                       SEND_WINDOW_START_HOUR=8, SEND_WINDOW_END_HOUR=11)
        advisor = SendWindowAdvisor(s)
        tuesday_9am = datetime(2026, 7, 14, 9, 0)  # a Tuesday
        assert tuesday_9am.weekday() == 1
        status = advisor.check(when=tuesday_9am)
        assert status.is_optimal is True

    def test_monday_is_not_optimal(self, tmp_path):
        s = _settings(tmp_path, SEND_WINDOW_ENABLED=True, SEND_WINDOW_DAYS="Tue,Wed,Thu")
        advisor = SendWindowAdvisor(s)
        monday_9am = datetime(2026, 7, 13, 9, 0)
        assert monday_9am.weekday() == 0
        status = advisor.check(when=monday_9am)
        assert status.is_optimal is False
        assert "days" in status.reason.lower()

    def test_evening_on_optimal_day_is_not_optimal(self, tmp_path):
        s = _settings(tmp_path, SEND_WINDOW_ENABLED=True, SEND_WINDOW_DAYS="Tue,Wed,Thu",
                       SEND_WINDOW_START_HOUR=8, SEND_WINDOW_END_HOUR=11)
        advisor = SendWindowAdvisor(s)
        tuesday_8pm = datetime(2026, 7, 14, 20, 0)
        status = advisor.check(when=tuesday_8pm)
        assert status.is_optimal is False
        assert "hours" in status.reason.lower()

    def test_enforced_flag_reflects_settings(self, tmp_path):
        s = _settings(tmp_path, SEND_WINDOW_ENABLED=True, SEND_WINDOW_ENFORCE=True)
        advisor = SendWindowAdvisor(s)
        status = advisor.check(when=datetime(2026, 7, 14, 9, 0))
        assert status.enforced is True

    def test_window_description_lists_configured_days(self, tmp_path):
        s = _settings(tmp_path, SEND_WINDOW_DAYS="Tue,Wed,Thu", SEND_WINDOW_START_HOUR=8, SEND_WINDOW_END_HOUR=11)
        advisor = SendWindowAdvisor(s)
        desc = advisor.window_description
        assert "Tue" in desc and "Thu" in desc
        assert "08:00" in desc and "11:00" in desc
