"""Tests for delivery/factory.py (build_transport) and the SmtpTransport
adapter in delivery/smtp_client.py — the legacy provider path, which
sender.py tests bypass entirely by injecting a FakeTransport directly."""

from __future__ import annotations

import smtplib
from email.mime.text import MIMEText
from unittest.mock import MagicMock, patch

import pytest

from recruiter_outreach.config import Settings
from recruiter_outreach.delivery.factory import build_transport
from recruiter_outreach.delivery.gmail_oauth_client import GmailOAuthTransport
from recruiter_outreach.delivery.smtp_client import SmtpConnectionPool, SmtpTransport
from recruiter_outreach.delivery.transport import TransportError, TransportPermanentError


def _settings(tmp_path, **overrides) -> Settings:
    base = dict(
        EMAIL_USER="me@example.com",
        RESUME_LINK="https://example.com/cv",
        DB_PATH=str(tmp_path / "test.db"),
        REPORTS_DIR="reports",
    )
    base.update(overrides)
    return Settings(**base)


class TestBuildTransport:
    def test_builds_gmail_oauth_transport_by_default(self, tmp_path):
        s = _settings(tmp_path)
        with patch(
            "recruiter_outreach.delivery.gmail_oauth_client.load_credentials",
            return_value=MagicMock(),
        ):
            transport = build_transport(s)
        assert isinstance(transport, GmailOAuthTransport)

    def test_builds_smtp_transport_when_configured(self, tmp_path):
        s = _settings(tmp_path, EMAIL_PROVIDER="smtp", EMAIL_PASSWORD="app-pw")
        transport = build_transport(s)
        assert isinstance(transport, SmtpTransport)

    def test_raises_when_smtp_missing_password(self, tmp_path):
        # Settings itself already validates this, but exercise the factory's
        # own defensive check in case it's ever constructed a different way.
        s = _settings(tmp_path, EMAIL_PROVIDER="smtp", EMAIL_PASSWORD="app-pw")
        object.__setattr__(s, "email_password", None)
        with pytest.raises(ValueError, match="EMAIL_PASSWORD"):
            build_transport(s)


class TestSmtpTransport:
    def test_send_sets_from_and_to_and_returns_message_id(self):
        pool = MagicMock(spec=SmtpConnectionPool)
        mock_conn = MagicMock()
        pool.get.return_value = mock_conn
        transport = SmtpTransport(pool)

        msg = MIMEText("hello")
        msg["Message-ID"] = "<abc@corp.com>"
        result = transport.send("me@example.com", "jane@corp.com", msg)

        assert result == "<abc@corp.com>"
        assert msg["From"] == "me@example.com"
        assert msg["To"] == "jane@corp.com"
        mock_conn.sendmail.assert_called_once()

    def test_recipients_refused_raises_permanent_error(self):
        pool = MagicMock(spec=SmtpConnectionPool)
        mock_conn = MagicMock()
        mock_conn.sendmail.side_effect = smtplib.SMTPRecipientsRefused({"j@corp.com": (550, b"no")})
        pool.get.return_value = mock_conn
        transport = SmtpTransport(pool)

        with pytest.raises(TransportPermanentError):
            transport.send("me@example.com", "jane@corp.com", MIMEText("x"))

    def test_disconnect_raises_transport_error_and_invalidates_pool(self):
        pool = MagicMock(spec=SmtpConnectionPool)
        mock_conn = MagicMock()
        mock_conn.sendmail.side_effect = smtplib.SMTPServerDisconnected("gone")
        pool.get.return_value = mock_conn
        transport = SmtpTransport(pool)

        with pytest.raises(TransportError):
            transport.send("me@example.com", "jane@corp.com", MIMEText("x"))
        pool.invalidate.assert_called_once()

    def test_close_thread_delegates_to_pool(self):
        pool = MagicMock(spec=SmtpConnectionPool)
        transport = SmtpTransport(pool)
        transport.close_thread()
        pool.close_current_thread.assert_called_once()


class TestSmtpConnectionPool:
    def test_get_opens_and_authenticates_new_connection(self):
        pool = SmtpConnectionPool("smtp.example.com", 587, "me@example.com", "secret")
        with patch("recruiter_outreach.delivery.smtp_client.smtplib.SMTP") as MockSMTP:
            mock_conn = MagicMock()
            MockSMTP.return_value = mock_conn
            conn = pool.get()

        assert conn is mock_conn
        mock_conn.starttls.assert_called_once()
        mock_conn.login.assert_called_once_with("me@example.com", "secret")

    def test_get_reuses_healthy_connection(self):
        pool = SmtpConnectionPool("smtp.example.com", 587, "me@example.com", "secret")
        with patch("recruiter_outreach.delivery.smtp_client.smtplib.SMTP") as MockSMTP:
            mock_conn = MagicMock()
            MockSMTP.return_value = mock_conn
            first = pool.get()
            second = pool.get()

        assert first is second
        MockSMTP.assert_called_once()  # only opened once

    def test_invalidate_forces_reconnect(self):
        pool = SmtpConnectionPool("smtp.example.com", 587, "me@example.com", "secret")
        with patch("recruiter_outreach.delivery.smtp_client.smtplib.SMTP") as MockSMTP:
            MockSMTP.side_effect = [MagicMock(), MagicMock()]
            first = pool.get()
            pool.invalidate()
            second = pool.get()

        assert first is not second
        assert MockSMTP.call_count == 2

    def test_close_current_thread_quits_and_clears(self):
        pool = SmtpConnectionPool("smtp.example.com", 587, "me@example.com", "secret")
        with patch("recruiter_outreach.delivery.smtp_client.smtplib.SMTP") as MockSMTP:
            mock_conn = MagicMock()
            MockSMTP.return_value = mock_conn
            pool.get()
            pool.close_current_thread()

        mock_conn.quit.assert_called_once()
        assert pool._local.smtp is None
