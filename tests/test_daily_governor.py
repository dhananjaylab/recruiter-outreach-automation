"""Tests for DailySendGovernor (delivery/daily_governor.py)."""

from __future__ import annotations

from unittest.mock import MagicMock

from recruiter_outreach.db import Database
from recruiter_outreach.delivery.daily_governor import DailySendGovernor


class TestDailySendGovernorNoWarmup:
    def test_can_send_true_when_under_static_ceiling(self, db: Database):
        gov = DailySendGovernor(db=db, warmup=None, static_ceiling=5)
        assert gov.can_send() is True
        assert gov.daily_cap() == 5

    def test_can_send_false_once_ceiling_reached(self, db: Database):
        gov = DailySendGovernor(db=db, warmup=None, static_ceiling=2)
        for i in range(2):
            db.record_send(
                email=f"r{i}@corp.com", name="R", company="Corp", role="",
                template_used="default.md", sequence_step=0, status="sent",
            )
        assert gov.can_send() is False

    def test_remaining_today_decreases_with_sends(self, db: Database):
        gov = DailySendGovernor(db=db, warmup=None, static_ceiling=10)
        assert gov.remaining_today() == 10
        db.record_send(
            email="a@corp.com", name="A", company="Corp", role="",
            template_used="default.md", sequence_step=0, status="sent",
        )
        assert gov.remaining_today() == 9

    def test_failed_and_skipped_sends_dont_count(self, db: Database):
        gov = DailySendGovernor(db=db, warmup=None, static_ceiling=5)
        db.record_send(
            email="a@corp.com", name="A", company="Corp", role="",
            template_used="default.md", sequence_step=0, status="failed",
        )
        assert gov.remaining_today() == 5


class TestDailySendGovernorWithWarmup:
    def test_uses_warmup_cap_when_warming_up(self, db: Database):
        mock_warmup = MagicMock()
        mock_warmup.today_cap.return_value = 3
        gov = DailySendGovernor(db=db, warmup=mock_warmup, static_ceiling=150)
        assert gov.daily_cap() == 3

    def test_can_send_respects_warmup_cap_not_static_ceiling(self, db: Database):
        mock_warmup = MagicMock()
        mock_warmup.today_cap.return_value = 1
        gov = DailySendGovernor(db=db, warmup=mock_warmup, static_ceiling=150)
        db.record_send(
            email="a@corp.com", name="A", company="Corp", role="",
            template_used="default.md", sequence_step=0, status="sent",
        )
        assert gov.can_send() is False  # hit warmup's cap of 1, even though static_ceiling=150
