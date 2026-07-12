"""Tests for GmailOAuthTransport (delivery/gmail_oauth_client.py).

load_credentials and googleapiclient.discovery.build are mocked — no
real Google API calls.
"""

from __future__ import annotations

from email.mime.text import MIMEText
from unittest.mock import MagicMock, patch

import pytest
from googleapiclient.errors import HttpError

from recruiter_outreach.auth.google_oauth import GoogleOAuthError
from recruiter_outreach.config import Settings
from recruiter_outreach.delivery.gmail_oauth_client import GmailOAuthTransport
from recruiter_outreach.delivery.transport import TransportError, TransportPermanentError


def _settings(tmp_path) -> Settings:
    return Settings(
        EMAIL_USER="me@example.com",
        RESUME_LINK="https://example.com/cv",
        DB_PATH=str(tmp_path / "test.db"),
        REPORTS_DIR="reports",
    )


def _http_error(status: int) -> HttpError:
    resp = MagicMock()
    resp.status = status
    return HttpError(resp, b"error body")


class TestConstruction:
    def test_raises_when_no_credentials(self, tmp_path):
        s = _settings(tmp_path)
        with patch(
            "recruiter_outreach.delivery.gmail_oauth_client.load_credentials",
            return_value=None,
        ):
            with pytest.raises(GoogleOAuthError):
                GmailOAuthTransport(s)


class TestSend:
    def _transport_with_mock_service(self, tmp_path):
        s = _settings(tmp_path)
        mock_creds = MagicMock()
        mock_service = MagicMock()
        with patch(
            "recruiter_outreach.delivery.gmail_oauth_client.load_credentials",
            return_value=mock_creds,
        ):
            transport = GmailOAuthTransport(s)
        # Seed the thread-local service directly so send() never calls the
        # real googleapiclient.discovery.build() with a mocked Credentials
        # object (which raises unrelated universe-domain errors).
        transport._local.service = mock_service
        return transport, mock_service

    def test_returns_message_id_on_success(self, tmp_path):
        transport, mock_service = self._transport_with_mock_service(tmp_path)
        mock_service.users().messages().send().execute.return_value = {"id": "abc123"}

        msg = MIMEText("hello")
        result = transport.send("me@example.com", "jane@corp.com", msg)

        assert result == "abc123"
        assert msg["From"] == "me@example.com"
        assert msg["To"] == "jane@corp.com"

    def test_raises_transport_permanent_error_on_400(self, tmp_path):
        transport, mock_service = self._transport_with_mock_service(tmp_path)
        mock_service.users().messages().send().execute.side_effect = _http_error(400)

        with pytest.raises(TransportPermanentError):
            transport.send("me@example.com", "bad@corp.com", MIMEText("x"))

    def test_raises_transport_error_on_500(self, tmp_path):
        transport, mock_service = self._transport_with_mock_service(tmp_path)
        mock_service.users().messages().send().execute.side_effect = _http_error(500)

        with pytest.raises(TransportError):
            transport.send("me@example.com", "jane@corp.com", MIMEText("x"))

    def test_raises_transport_error_on_generic_exception(self, tmp_path):
        transport, mock_service = self._transport_with_mock_service(tmp_path)
        mock_service.users().messages().send().execute.side_effect = ConnectionError("dns fail")

        with pytest.raises(TransportError):
            transport.send("me@example.com", "jane@corp.com", MIMEText("x"))

    def test_close_thread_resets_local_service(self, tmp_path):
        transport, _ = self._transport_with_mock_service(tmp_path)
        transport._local.service = "something"
        transport.close_thread()
        assert transport._local.service is None
