"""Tests for OutreachManager (delivery/sender.py).

A FakeTransport (implementing EmailTransport) is injected directly via the
constructor's `transport=` parameter — no real SMTP or Gmail API is ever
touched, and no provider-specific mocking (SmtpConnectionPool / Gmail
service objects) leaks into these tests, since OutreachManager only ever
depends on the EmailTransport interface.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from recruiter_outreach.config import Settings
from recruiter_outreach.db import Database
from recruiter_outreach.delivery.sender import OutreachManager
from recruiter_outreach.delivery.transport import (
    EmailTransport,
    ProgressEvent,
    TransportError,
    TransportPermanentError,
)
from recruiter_outreach.reporting.report import RunReport


# ---------------------------------------------------------------------------
# Fake transport
# ---------------------------------------------------------------------------

class FakeTransport(EmailTransport):
    """Records every send() call; can be scripted to raise per-call."""

    def __init__(self):
        self.sent: list[tuple[str, str]] = []
        self.side_effects: list[Exception | None] = []  # consumed in order
        self.closed_threads = 0

    def send(self, from_addr, to_addr, message):
        effect = self.side_effects.pop(0) if self.side_effects else None
        if effect is not None:
            raise effect
        self.sent.append((from_addr, to_addr))
        return "fake-message-id"

    def close_thread(self):
        self.closed_threads += 1


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _settings(tmp_path, **overrides) -> Settings:
    """Minimal valid Settings object (no .env file needed)."""
    base = dict(
        EMAIL_USER="sender@example.com",
        SENDER_NAME="Test Sender",
        RESUME_LINK="https://example.com/resume.pdf",
        EMAIL_TEMPLATE_DIR=str(tmp_path / "templates"),
        WARMUP_ENABLED=False,
        DAILY_SEND_CAP_ENABLED=False,
        SEND_WINDOW_ENABLED=False,
        VERIFY_MX=False,
        VERIFY_SMTP_RCPT=False,
        LLM_PERSONALIZATION_ENABLED=False,
        DB_PATH=str(tmp_path / "test.db"),
        REPORTS_DIR=str(tmp_path / "reports"),
    )
    base.update(overrides)
    return Settings(**base)


def _write_default_template(tmp_path) -> None:
    td = tmp_path / "templates"
    td.mkdir(exist_ok=True)
    (td / "default.md").write_text(
        "Hi {recruiter_name} at {company_name}. {opening_line}{resume_line} -{sender_name}"
    )


def _make_manager(tmp_path, db: Database, **setting_overrides) -> tuple[OutreachManager, FakeTransport]:
    _write_default_template(tmp_path)
    s = _settings(tmp_path, **setting_overrides)
    fake = FakeTransport()
    mgr = OutreachManager(settings=s, db=db, sequence_step=0, transport=fake)
    mgr.rate_limiter = MagicMock()
    mgr.rate_limiter.wait = MagicMock()
    return mgr, fake


# ---------------------------------------------------------------------------
# Pre-send check skips
# ---------------------------------------------------------------------------

class TestPreSendChecks:
    def test_skips_invalid_email_format(self, tmp_path, db):
        mgr, _ = _make_manager(tmp_path, db)
        mgr.send_outreach_email("not-an-email", "HR", "Acme")
        assert len(mgr.report.skips) == 1
        assert mgr.report.skips[0][1] == "invalid_email_format"

    def test_skips_suppressed_address(self, tmp_path, db):
        db.suppress("blocked@corp.com", reason="bounced")
        mgr, _ = _make_manager(tmp_path, db)
        mgr.send_outreach_email("blocked@corp.com", "HR", "Acme")
        assert mgr.report.skips[0][1] == "suppressed"

    def test_skips_already_sent(self, tmp_path, db):
        db.record_send(
            email="jane@corp.com", name="Jane", company="Corp", role="",
            template_used="default.md", sequence_step=0, status="sent",
        )
        mgr, _ = _make_manager(tmp_path, db)
        mgr.send_outreach_email("jane@corp.com", "Jane", "Corp")
        assert mgr.report.skips[0][1] == "duplicate"

    def test_skips_missing_mx_when_enabled(self, tmp_path, db):
        _write_default_template(tmp_path)
        s = _settings(tmp_path, VERIFY_MX=True)
        fake = FakeTransport()
        with patch("recruiter_outreach.delivery.sender.has_mx_record", return_value=False):
            mgr = OutreachManager(settings=s, db=db, sequence_step=0, transport=fake)
            mgr.rate_limiter = MagicMock()
            mgr.send_outreach_email("jane@no-mx.com", "Jane", "Corp")
        assert mgr.report.skips[0][1] == "no_mx_record"

    def test_skips_when_daily_cap_reached(self, tmp_path, db):
        mgr, fake = _make_manager(tmp_path, db, DAILY_SEND_CAP_ENABLED=True, WARMUP_ENABLED=False)
        mgr.daily_governor.can_send = MagicMock(return_value=False)
        mgr.send_outreach_email("jane@corp.com", "Jane", "Corp")
        assert mgr.report.skips[0][1] == "daily_cap_reached"
        assert fake.sent == []


# ---------------------------------------------------------------------------
# Successful send
# ---------------------------------------------------------------------------

class TestSuccessfulSend:
    def test_records_success_in_report_and_db(self, tmp_path, db):
        mgr, fake = _make_manager(tmp_path, db)
        mgr.send_outreach_email("jane@corp.com", "Jane", "Acme")

        assert len(mgr.report.successes) == 1
        assert mgr.report.successes[0] == "jane@corp.com"
        assert db.already_sent("jane@corp.com", 0)
        assert fake.sent == [("sender@example.com", "jane@corp.com")]

    def test_rate_limiter_called_before_send(self, tmp_path, db):
        call_order: list[str] = []
        mgr, fake = _make_manager(tmp_path, db)

        mgr.rate_limiter.wait = MagicMock(side_effect=lambda: call_order.append("wait"))
        original_send = fake.send

        def tracking_send(*a, **kw):
            call_order.append("send")
            return original_send(*a, **kw)

        fake.send = tracking_send
        mgr.send_outreach_email("jane@corp.com", "Jane", "Acme")
        assert call_order == ["wait", "send"]

    def test_subject_prefixed_for_followup_without_embedded_subject(self, tmp_path, db):
        td = tmp_path / "templates"
        td.mkdir(exist_ok=True)
        (td / "default.md").write_text(
            "Hi {recruiter_name} at {company_name}. {opening_line}{resume_line} -{sender_name}"
        )
        (td / "followup_1.md").write_text(
            "Following up {recruiter_name} at {company_name}. {resume_line} -{sender_name}"
        )
        s = _settings(tmp_path)
        fake = FakeTransport()
        captured: list = []
        fake.send = lambda from_addr, to_addr, message: captured.append(message) or "id"

        mgr = OutreachManager(settings=s, db=db, sequence_step=1, transport=fake)
        mgr.rate_limiter = MagicMock()
        mgr.send_outreach_email("jane@corp.com", "Jane", "Acme")

        assert captured
        assert "Following up:" in captured[0]["Subject"]

    def test_embedded_subject_line_used_verbatim(self, tmp_path, db):
        td = tmp_path / "templates"
        td.mkdir(exist_ok=True)
        (td / "default.md").write_text(
            "Subject: Quick note for {company_name}\n\n"
            "Hi {recruiter_name}. {opening_line}{resume_line} -{sender_name}"
        )
        s = _settings(tmp_path)
        fake = FakeTransport()
        captured: list = []
        fake.send = lambda from_addr, to_addr, message: captured.append(message) or "id"

        mgr = OutreachManager(settings=s, db=db, sequence_step=0, transport=fake)
        mgr.rate_limiter = MagicMock()
        mgr.send_outreach_email("jane@corp.com", "Jane", "Acme")

        assert captured[0]["Subject"] == "Quick note for Acme"

    def test_progress_events_emitted_started_then_sent(self, tmp_path, db):
        _write_default_template(tmp_path)
        s = _settings(tmp_path)
        fake = FakeTransport()
        events: list[ProgressEvent] = []
        mgr = OutreachManager(settings=s, db=db, transport=fake, on_event=events.append)
        mgr.rate_limiter = MagicMock()

        mgr.send_outreach_email("jane@corp.com", "Jane", "Acme", index=1, total=1)

        statuses = [e.status for e in events]
        assert statuses == ["started", "sent"]
        assert events[-1].index == 1 and events[-1].total == 1


# ---------------------------------------------------------------------------
# Retry / failure paths
# ---------------------------------------------------------------------------

class TestRetryAndFailure:
    def test_retries_on_transport_error(self, tmp_path, db):
        mgr, fake = _make_manager(tmp_path, db)
        fake.side_effects = [TransportError("gone"), TransportError("gone"), None]
        mgr.max_retries = 3

        with patch("time.sleep"):
            mgr.send_outreach_email("jane@corp.com", "Jane", "Acme")

        assert len(mgr.report.successes) == 1
        assert len(fake.sent) == 1  # only the final successful attempt recorded a "sent"

    def test_records_failure_after_max_retries_exceeded(self, tmp_path, db):
        mgr, fake = _make_manager(tmp_path, db)
        fake.side_effects = [TransportError("x")] * 10
        mgr.max_retries = 2

        with patch("time.sleep"):
            mgr.send_outreach_email("jane@corp.com", "Jane", "Acme")

        assert len(mgr.report.failures) == 1
        assert mgr.report.failures[0][1] == "max_retries_exceeded"

    def test_permanent_error_not_retried(self, tmp_path, db):
        mgr, fake = _make_manager(tmp_path, db)
        fake.side_effects = [TransportPermanentError("rejected")]
        mgr.max_retries = 3

        mgr.send_outreach_email("jane@corp.com", "Jane", "Acme")

        assert fake.sent == []  # never succeeded
        assert mgr.report.failures[0][1] == "recipient_refused"

    def test_failed_event_emitted_on_permanent_error(self, tmp_path, db):
        _write_default_template(tmp_path)
        s = _settings(tmp_path)
        fake = FakeTransport()
        fake.side_effects = [TransportPermanentError("rejected")]
        events: list[ProgressEvent] = []
        mgr = OutreachManager(settings=s, db=db, transport=fake, on_event=events.append)
        mgr.rate_limiter = MagicMock()

        mgr.send_outreach_email("jane@corp.com", "Jane", "Acme")

        assert [e.status for e in events] == ["started", "failed"]
        assert events[-1].reason == "recipient_refused"


# ---------------------------------------------------------------------------
# Concurrent dispatch
# ---------------------------------------------------------------------------

class TestConcurrentDispatch:
    def test_all_recruiters_attempted(self, tmp_path, db):
        mgr, fake = _make_manager(tmp_path, db)

        recruiters = [
            {"Email": f"r{i}@corp.com", "Name": f"R{i}", "Company": "Corp", "Role": ""}
            for i in range(5)
        ]
        report: RunReport = mgr.send_emails_concurrently(recruiters)
        assert len(report.successes) == 5
        assert len(fake.sent) == 5

    def test_empty_recruiter_list_returns_empty_report(self, tmp_path, db):
        mgr, _ = _make_manager(tmp_path, db)
        report = mgr.send_emails_concurrently([])
        assert report.successes == []
        assert report.failures == []

    def test_run_complete_event_emitted_with_summary(self, tmp_path, db):
        _write_default_template(tmp_path)
        s = _settings(tmp_path)
        fake = FakeTransport()
        events: list[ProgressEvent] = []
        mgr = OutreachManager(settings=s, db=db, transport=fake, on_event=events.append)
        mgr.rate_limiter = MagicMock()

        mgr.send_emails_concurrently([{"Email": "a@corp.com", "Name": "A", "Company": "Corp"}])

        assert events[-1].status == "run_complete"
        assert events[-1].extra["sent"] == 1

    def test_send_window_enforcement_blocks_entire_run(self, tmp_path, db):
        _write_default_template(tmp_path)
        s = _settings(tmp_path, SEND_WINDOW_ENABLED=True, SEND_WINDOW_ENFORCE=True)
        fake = FakeTransport()
        mgr = OutreachManager(settings=s, db=db, transport=fake)
        mgr.rate_limiter = MagicMock()
        mgr.send_window.check = MagicMock(
            return_value=type(
                "S", (), {"is_optimal": False, "reason": "off-hours", "window_description": "Tue-Thu"}
            )()
        )

        report = mgr.send_emails_concurrently(
            [{"Email": "a@corp.com", "Name": "A", "Company": "Corp"}]
        )
        assert report.successes == []
        assert fake.sent == []

    def test_scenario_routes_to_scenario_template(self, tmp_path, db):
        td = tmp_path / "templates"
        td.mkdir(exist_ok=True)
        (td / "default.md").write_text("Default body {recruiter_name} {opening_line}{resume_line}{sender_name}{company_name}")
        (td / "referral.md").write_text(
            "Subject: Referred by a mutual contact\n\n"
            "Hi {recruiter_name}, {opening_line}{resume_line} -{sender_name} {company_name}"
        )
        s = _settings(tmp_path)
        fake = FakeTransport()
        captured: list = []
        fake.send = lambda from_addr, to_addr, message: captured.append(message) or "id"
        mgr = OutreachManager(settings=s, db=db, transport=fake)
        mgr.rate_limiter = MagicMock()

        mgr.send_outreach_email("jane@corp.com", "Jane", "Acme", scenario="referral")

        assert captured[0]["Subject"] == "Referred by a mutual contact"
