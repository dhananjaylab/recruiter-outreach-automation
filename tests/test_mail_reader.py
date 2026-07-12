"""Tests for tracking/mail_reader.py's two MailReader implementations.

GmailOAuthMailReader: googleapiclient is mocked.
ImapMailReader: imaplib.IMAP4_SSL is mocked (this preserves the original
pre-refactor IMAP test coverage, now scoped to just the reader).
"""

from __future__ import annotations

import base64
from email.mime.text import MIMEText
from unittest.mock import MagicMock, patch

import pytest

from recruiter_outreach.auth.google_oauth import GoogleOAuthError
from recruiter_outreach.config import Settings
from recruiter_outreach.tracking.mail_reader import (
    GmailOAuthMailReader,
    ImapMailReader,
    build_mail_reader,
)


def _settings(tmp_path, **overrides) -> Settings:
    base = dict(
        EMAIL_USER="me@example.com",
        RESUME_LINK="https://example.com/cv",
        DB_PATH=str(tmp_path / "test.db"),
        REPORTS_DIR="reports",
    )
    base.update(overrides)
    return Settings(**base)


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii")


# ---------------------------------------------------------------------------
# GmailOAuthMailReader
# ---------------------------------------------------------------------------

class TestGmailOAuthMailReader:
    def test_raises_when_no_credentials(self, tmp_path):
        s = _settings(tmp_path)
        with patch(
            "recruiter_outreach.tracking.mail_reader.load_credentials",
            return_value=None,
        ):
            with pytest.raises(GoogleOAuthError):
                GmailOAuthMailReader(s)

    def _reader_with_mock_service(self, tmp_path):
        s = _settings(tmp_path)
        mock_creds = MagicMock()
        with patch(
            "recruiter_outreach.tracking.mail_reader.load_credentials",
            return_value=mock_creds,
        ), patch("recruiter_outreach.tracking.mail_reader.build") as mock_build:
            mock_service = MagicMock()
            mock_build.return_value = mock_service
            reader = GmailOAuthMailReader(s)
        return reader, reader._service

    def test_search_since_parses_plain_text_message(self, tmp_path):
        reader, service = self._reader_with_mock_service(tmp_path)
        service.users().messages().list().execute.return_value = {
            "messages": [{"id": "m1"}]
        }
        service.users().messages().get().execute.return_value = {
            "id": "m1",
            "payload": {
                "mimeType": "text/plain",
                "headers": [
                    {"name": "From", "value": "jane@corp.com"},
                    {"name": "Subject", "value": "Re: hello"},
                    {"name": "Message-ID", "value": "<abc@corp.com>"},
                ],
                "body": {"data": _b64("Thanks for reaching out!")},
            },
        }

        results = reader.search_since(since_days=7)

        assert len(results) == 1
        assert results[0].from_addr == "jane@corp.com"
        assert results[0].subject == "Re: hello"
        assert results[0].body == "Thanks for reaching out!"

    def test_search_since_parses_multipart_message(self, tmp_path):
        reader, service = self._reader_with_mock_service(tmp_path)
        service.users().messages().list().execute.return_value = {
            "messages": [{"id": "m2"}]
        }
        service.users().messages().get().execute.return_value = {
            "id": "m2",
            "payload": {
                "mimeType": "multipart/alternative",
                "headers": [
                    {"name": "From", "value": "bob@corp.com"},
                    {"name": "Subject", "value": "Re: opportunity"},
                ],
                "parts": [
                    {"mimeType": "text/html", "body": {"data": _b64("<p>hi</p>")}},
                    {"mimeType": "text/plain", "body": {"data": _b64("Plain text body")}},
                ],
            },
        }

        results = reader.search_since(since_days=7)
        assert results[0].body == "Plain text body"

    def test_search_since_returns_empty_on_no_messages(self, tmp_path):
        reader, service = self._reader_with_mock_service(tmp_path)
        service.users().messages().list().execute.return_value = {}
        assert reader.search_since(since_days=7) == []

    def test_search_since_handles_http_error_gracefully(self, tmp_path):
        from googleapiclient.errors import HttpError

        reader, service = self._reader_with_mock_service(tmp_path)
        resp = MagicMock(status=500)
        service.users().messages().list().execute.side_effect = HttpError(resp, b"err")

        results = reader.search_since(since_days=7)
        assert results == []


# ---------------------------------------------------------------------------
# ImapMailReader (legacy)
# ---------------------------------------------------------------------------

class TestImapMailReader:
    def _raw(self, subject: str, from_: str, body: str) -> bytes:
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"] = from_
        msg["To"] = "me@example.com"
        return msg.as_bytes()

    def test_search_since_parses_messages(self, tmp_path):
        s = _settings(tmp_path, EMAIL_PROVIDER="smtp", EMAIL_PASSWORD="app-pw")
        reader = ImapMailReader(s)

        raw = self._raw("Re: hi", "jane@corp.com", "Sounds good!")
        mock_imap = MagicMock()
        mock_imap.select.return_value = ("OK", [b""])
        mock_imap.search.return_value = ("OK", [b"1"])
        mock_imap.fetch.return_value = ("OK", [(None, raw)])

        with patch(
            "recruiter_outreach.tracking.mail_reader.imaplib.IMAP4_SSL",
            return_value=mock_imap,
        ):
            results = reader.search_since(since_days=7)

        assert len(results) == 1
        assert results[0].from_addr == "jane@corp.com"
        assert results[0].body == "Sounds good!"

    def test_search_since_returns_empty_on_search_failure(self, tmp_path):
        s = _settings(tmp_path, EMAIL_PROVIDER="smtp", EMAIL_PASSWORD="app-pw")
        reader = ImapMailReader(s)
        mock_imap = MagicMock()
        mock_imap.select.return_value = ("OK", [b""])
        mock_imap.search.return_value = ("NO", [b""])

        with patch(
            "recruiter_outreach.tracking.mail_reader.imaplib.IMAP4_SSL",
            return_value=mock_imap,
        ):
            results = reader.search_since(since_days=7)
        assert results == []


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

class TestBuildMailReader:
    def test_builds_gmail_reader_by_default(self, tmp_path):
        s = _settings(tmp_path)
        mock_creds = MagicMock()
        with patch(
            "recruiter_outreach.tracking.mail_reader.load_credentials",
            return_value=mock_creds,
        ), patch("recruiter_outreach.tracking.mail_reader.build"):
            reader = build_mail_reader(s)
        assert isinstance(reader, GmailOAuthMailReader)

    def test_builds_imap_reader_when_configured(self, tmp_path):
        s = _settings(tmp_path, MAIL_READER_PROVIDER="imap", EMAIL_PROVIDER="smtp", EMAIL_PASSWORD="pw")
        reader = build_mail_reader(s)
        assert isinstance(reader, ImapMailReader)
