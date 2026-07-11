"""Tests for OutreachManager (delivery/sender.py).

Real SMTP is not called — SmtpConnectionPool and RateLimiter are patched
so every test runs in milliseconds with no network dependency.
"""

from __future__ import annotations

import smtplib
from unittest.mock import MagicMock, patch

import pytest

from recruiter_outreach.config import Settings
from recruiter_outreach.db import Database
from recruiter_outreach.delivery.sender import OutreachManager
from recruiter_outreach.reporting.report import RunReport


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _settings(tmp_path) -> Settings:
    """Minimal valid Settings object (no .env file needed)."""
    return Settings(
        EMAIL_USER="sender@example.com",
        EMAIL_PASSWORD="secret",
        SENDER_NAME="Test Sender",
        RESUME_LINK="https://example.com/resume.pdf",
        EMAIL_TEMPLATE_DIR=str(tmp_path / "templates"),
        WARMUP_ENABLED=False,
        VERIFY_MX=False,
        VERIFY_SMTP_RCPT=False,
        LLM_PERSONALIZATION_ENABLED=False,
        DB_PATH=str(tmp_path / "test.db"),
        REPORTS_DIR=str(tmp_path / "reports"),
    )


def _make_manager(tmp_path, db: Database) -> OutreachManager:
    """Build a fully-mocked OutreachManager."""
    td = tmp_path / "templates"
    td.mkdir(exist_ok=True)
    (td / "default.md").write_text(
        "Hi {recruiter_name} at {company_name}. {opening_line}{resume_line} -{sender_name}"
    )
    s = _settings(tmp_path)
    with patch("recruiter_outreach.delivery.sender.SmtpConnectionPool"):
        mgr = OutreachManager(settings=s, db=db, sequence_step=0)
    mgr.rate_limiter = MagicMock()          # never actually sleep
    mgr.rate_limiter.wait = MagicMock()
    return mgr


# ---------------------------------------------------------------------------
# Pre-send check skips
# ---------------------------------------------------------------------------

class TestPreSendChecks:
    def test_skips_invalid_email_format(self, tmp_path, db):
        mgr = _make_manager(tmp_path, db)
        mgr.send_outreach_email("not-an-email", "HR", "Acme")
        assert len(mgr.report.skips) == 1
        assert mgr.report.skips[0][1] == "invalid_email_format"

    def test_skips_suppressed_address(self, tmp_path, db):
        db.suppress("blocked@corp.com", reason="bounced")
        mgr = _make_manager(tmp_path, db)
        mgr.send_outreach_email("blocked@corp.com", "HR", "Acme")
        assert mgr.report.skips[0][1] == "suppressed"

    def test_skips_already_sent(self, tmp_path, db):
        db.record_send(
            email="jane@corp.com", name="Jane", company="Corp", role="",
            template_used="default.md", sequence_step=0, status="sent",
        )
        mgr = _make_manager(tmp_path, db)
        mgr.send_outreach_email("jane@corp.com", "Jane", "Corp")
        assert mgr.report.skips[0][1] == "duplicate"

    def test_skips_missing_mx_when_enabled(self, tmp_path, db):
        td = tmp_path / "templates"
        td.mkdir(exist_ok=True)
        (td / "default.md").write_text(
            "Hi {recruiter_name} at {company_name}. {opening_line}{resume_line} -{sender_name}"
        )
        s = _settings(tmp_path)
        s = s.model_copy(update={"verify_mx": True})
        with patch("recruiter_outreach.delivery.sender.SmtpConnectionPool"), \
             patch("recruiter_outreach.delivery.sender.has_mx_record", return_value=False):
            mgr = OutreachManager(settings=s, db=db, sequence_step=0)
            mgr.rate_limiter = MagicMock()
            mgr.send_outreach_email("jane@no-mx.com", "Jane", "Corp")
        assert mgr.report.skips[0][1] == "no_mx_record"


# ---------------------------------------------------------------------------
# Successful send
# ---------------------------------------------------------------------------

class TestSuccessfulSend:
    def test_records_success_in_report_and_db(self, tmp_path, db):
        mgr = _make_manager(tmp_path, db)
        mock_conn = MagicMock()
        mgr.smtp_pool.get = MagicMock(return_value=mock_conn)

        mgr.send_outreach_email("jane@corp.com", "Jane", "Acme")

        assert len(mgr.report.successes) == 1
        assert mgr.report.successes[0] == "jane@corp.com"
        assert db.already_sent("jane@corp.com", 0)
        mock_conn.sendmail.assert_called_once()

    def test_rate_limiter_called_before_send(self, tmp_path, db):
        call_order: list[str] = []
        mgr = _make_manager(tmp_path, db)

        def mark_wait():
            call_order.append("wait")

        def mark_send(*a, **kw):
            call_order.append("send")

        mgr.rate_limiter.wait = MagicMock(side_effect=mark_wait)
        mock_conn = MagicMock()
        mock_conn.sendmail = MagicMock(side_effect=mark_send)
        mgr.smtp_pool.get = MagicMock(return_value=mock_conn)

        mgr.send_outreach_email("jane@corp.com", "Jane", "Acme")
        assert call_order == ["wait", "send"]

    def test_subject_prefixed_for_followup(self, tmp_path, db):
        import email as _email
        from email.header import decode_header as _dh

        td = tmp_path / "templates"
        td.mkdir(exist_ok=True)
        (td / "default.md").write_text(
            "Hi {recruiter_name} at {company_name}. {opening_line}{resume_line} -{sender_name}"
        )
        (td / "followup_1.md").write_text(
            "Following up {recruiter_name} at {company_name}. {resume_line} -{sender_name}"
        )
        s = _settings(tmp_path)
        with patch("recruiter_outreach.delivery.sender.SmtpConnectionPool"):
            mgr = OutreachManager(settings=s, db=db, sequence_step=1)
        mgr.rate_limiter = MagicMock()

        captured: list[str] = []
        mock_conn = MagicMock()
        mock_conn.sendmail = MagicMock(
            side_effect=lambda fr, to, msg: captured.append(msg)
        )
        mgr.smtp_pool.get = MagicMock(return_value=mock_conn)
        mgr.send_outreach_email("jane@corp.com", "Jane", "Acme")

        assert captured
        parsed = _email.message_from_string(captured[0])
        subject_parts = _dh(parsed["Subject"])
        subject = "".join(
            p.decode(enc or "utf-8") if isinstance(p, bytes) else p
            for p, enc in subject_parts
        )
        assert "Following up:" in subject


# ---------------------------------------------------------------------------
# Retry / failure paths
# ---------------------------------------------------------------------------

class TestRetryAndFailure:
    def test_retries_on_smtp_disconnect(self, tmp_path, db):
        mgr = _make_manager(tmp_path, db)
        mock_conn = MagicMock()
        call_count = {"n": 0}

        def flaky_send(*a, **kw):
            call_count["n"] += 1
            if call_count["n"] < 3:
                raise smtplib.SMTPServerDisconnected("gone")

        mock_conn.sendmail = MagicMock(side_effect=flaky_send)
        mgr.smtp_pool.get = MagicMock(return_value=mock_conn)
        mgr.max_retries = 3

        with patch("time.sleep"):            # don't actually sleep during tests
            mgr.send_outreach_email("jane@corp.com", "Jane", "Acme")

        assert call_count["n"] == 3
        assert len(mgr.report.successes) == 1

    def test_records_failure_after_max_retries_exceeded(self, tmp_path, db):
        mgr = _make_manager(tmp_path, db)
        mock_conn = MagicMock()
        mock_conn.sendmail = MagicMock(
            side_effect=smtplib.SMTPServerDisconnected("always gone")
        )
        mgr.smtp_pool.get = MagicMock(return_value=mock_conn)
        mgr.max_retries = 2

        with patch("time.sleep"):
            mgr.send_outreach_email("jane@corp.com", "Jane", "Acme")

        assert len(mgr.report.failures) == 1
        assert mgr.report.failures[0][1] == "max_retries_exceeded"

    def test_recipient_refused_not_retried(self, tmp_path, db):
        mgr = _make_manager(tmp_path, db)
        mock_conn = MagicMock()
        mock_conn.sendmail = MagicMock(
            side_effect=smtplib.SMTPRecipientsRefused({"jane@corp.com": (550, b"No")})
        )
        mgr.smtp_pool.get = MagicMock(return_value=mock_conn)
        mgr.max_retries = 3

        mgr.send_outreach_email("jane@corp.com", "Jane", "Acme")

        assert mock_conn.sendmail.call_count == 1   # no retry on hard rejection
        assert mgr.report.failures[0][1] == "recipient_refused"


# ---------------------------------------------------------------------------
# Concurrent dispatch
# ---------------------------------------------------------------------------

class TestConcurrentDispatch:
    def test_all_recruiters_attempted(self, tmp_path, db):
        mgr = _make_manager(tmp_path, db)
        mock_conn = MagicMock()
        mgr.smtp_pool.get = MagicMock(return_value=mock_conn)
        mgr.smtp_pool.close_current_thread = MagicMock()

        recruiters = [
            {"Email": f"r{i}@corp.com", "Name": f"R{i}", "Company": "Corp", "Role": ""}
            for i in range(5)
        ]
        report: RunReport = mgr.send_emails_concurrently(recruiters)
        assert len(report.successes) == 5

    def test_empty_recruiter_list_returns_empty_report(self, tmp_path, db):
        mgr = _make_manager(tmp_path, db)
        report = mgr.send_emails_concurrently([])
        assert report.successes == []
        assert report.failures == []
