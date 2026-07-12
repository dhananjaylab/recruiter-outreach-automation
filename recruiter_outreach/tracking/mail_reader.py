# FILE: recruiter_outreach/tracking/mail_reader.py

"""
MailReader — the provider-agnostic interface InboxTracker scans through
to find bounces, replies, and unsubscribe requests.

Same Dependency Inversion pattern as delivery/transport.py: InboxTracker
knows nothing about IMAP vs the Gmail API, it just asks its MailReader
for "every message since N days ago" and applies bounce/reply/unsubscribe
heuristics to the normalised RawEmailMessage objects it gets back.

  - GmailOAuthMailReader (default) — uses the same OAuth2 grant as
    GmailOAuthTransport (gmail.readonly + gmail.modify scopes), so
    there's one credential to manage for both sending and tracking.
  - ImapMailReader — legacy fallback wrapping the original imaplib logic,
    used when MAIL_READER_PROVIDER=imap (e.g. non-Gmail mailboxes).
"""

from __future__ import annotations

import base64
import email as email_lib
import imaplib
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta
from email.header import decode_header

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from recruiter_outreach.auth.google_oauth import GoogleOAuthError, load_credentials
from recruiter_outreach.config import Settings

logger = logging.getLogger(__name__)


@dataclass
class RawEmailMessage:
    """Provider-neutral view of one inbox message, enough for bounce/
    reply/unsubscribe detection without either reader leaking its
    transport-specific message format into InboxTracker."""

    message_id: str
    from_addr: str
    subject: str
    body: str


class MailReader(ABC):
    @abstractmethod
    def search_since(self, since_days: int, limit: int = 200) -> list[RawEmailMessage]:
        ...


def _decode_header_value(value: str | None) -> str:
    if not value:
        return ""
    parts = decode_header(value)
    return "".join(
        (p.decode(enc or "utf-8", errors="replace") if isinstance(p, bytes) else p)
        for p, enc in parts
    )


def _get_body_text(msg) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                try:
                    return part.get_payload(decode=True).decode(
                        part.get_content_charset() or "utf-8", errors="replace"
                    )
                except Exception:
                    continue
        return ""
    try:
        return msg.get_payload(decode=True).decode(
            msg.get_content_charset() or "utf-8", errors="replace"
        )
    except Exception:
        return ""


class ImapMailReader(MailReader):
    """Legacy fallback (MAIL_READER_PROVIDER=imap). Requires
    EMAIL_PASSWORD (an app password) and IMAP_SERVER/IMAP_PORT."""

    def __init__(self, settings: Settings):
        self.settings = settings

    def _connect(self) -> imaplib.IMAP4_SSL:
        conn = imaplib.IMAP4_SSL(self.settings.imap_server, self.settings.imap_port)
        conn.login(self.settings.email_user, self.settings.email_password)
        return conn

    def search_since(self, since_days: int, limit: int = 200) -> list[RawEmailMessage]:
        dt = datetime.now() - timedelta(days=since_days)
        imap_date = dt.strftime("%d-%b-%Y")

        results: list[RawEmailMessage] = []
        conn = self._connect()
        try:
            conn.select("INBOX")
            status, data = conn.search(None, f'(SINCE "{imap_date}")')
            if status != "OK":
                logger.warning("IMAP search failed.")
                return results

            msg_ids = data[0].split()[-limit:]
            for msg_id in msg_ids:
                status, msg_data = conn.fetch(msg_id, "(RFC822)")
                if status != "OK" or not msg_data or not msg_data[0]:
                    continue
                raw = msg_data[0][1]
                msg = email_lib.message_from_bytes(raw)
                results.append(
                    RawEmailMessage(
                        message_id=msg.get("Message-ID", ""),
                        from_addr=_decode_header_value(msg.get("From", "")),
                        subject=_decode_header_value(msg.get("Subject", "")),
                        body=_get_body_text(msg),
                    )
                )
        finally:
            try:
                conn.logout()
            except Exception:
                pass

        return results


class GmailOAuthMailReader(MailReader):
    """Default. Uses the Gmail API list+get with a `newer_than:Nd` query —
    no IMAP login, no app password, same OAuth grant as
    GmailOAuthTransport."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._credentials = load_credentials(settings)
        if self._credentials is None:
            raise GoogleOAuthError(
                "No valid Google OAuth credentials found. Connect a Gmail "
                "account first (GET /auth/google/login)."
            )
        self._service = build("gmail", "v1", credentials=self._credentials, cache_discovery=False)

    def search_since(self, since_days: int, limit: int = 200) -> list[RawEmailMessage]:
        query = f"newer_than:{max(1, since_days)}d"
        results: list[RawEmailMessage] = []
        try:
            resp = self._service.users().messages().list(
                userId="me", q=query, maxResults=limit,
            ).execute()
            message_refs = resp.get("messages", [])

            for ref in message_refs:
                full = self._service.users().messages().get(
                    userId="me", id=ref["id"], format="full",
                ).execute()
                results.append(self._parse_message(full))
        except HttpError as exc:
            logger.warning("Gmail API inbox scan failed: %s", exc)

        return results

    @staticmethod
    def _parse_message(full: dict) -> RawEmailMessage:
        headers = {h["name"].lower(): h["value"] for h in full.get("payload", {}).get("headers", [])}
        body = GmailOAuthMailReader._extract_body(full.get("payload", {}))
        return RawEmailMessage(
            message_id=headers.get("message-id", full.get("id", "")),
            from_addr=headers.get("from", ""),
            subject=headers.get("subject", ""),
            body=body,
        )

    @staticmethod
    def _extract_body(payload: dict) -> str:
        def _decode_part(data: str) -> str:
            padded = data + "=" * (-len(data) % 4)
            return base64.urlsafe_b64decode(padded).decode("utf-8", errors="replace")

        if payload.get("mimeType") == "text/plain" and payload.get("body", {}).get("data"):
            return _decode_part(payload["body"]["data"])

        for part in payload.get("parts", []) or []:
            if part.get("mimeType") == "text/plain" and part.get("body", {}).get("data"):
                return _decode_part(part["body"]["data"])
        for part in payload.get("parts", []) or []:
            nested = GmailOAuthMailReader._extract_body(part)
            if nested:
                return nested
        return ""


def build_mail_reader(settings: Settings) -> MailReader:
    if settings.mail_reader_provider == "gmail_oauth":
        return GmailOAuthMailReader(settings)
    if settings.mail_reader_provider == "imap":
        return ImapMailReader(settings)
    raise ValueError(f"Unknown MAIL_READER_PROVIDER: {settings.mail_reader_provider!r}")
