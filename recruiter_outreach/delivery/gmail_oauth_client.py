# FILE: recruiter_outreach/delivery/gmail_oauth_client.py

"""
GmailOAuthTransport — sends outreach emails via the Gmail API instead of
raw SMTP. Requires a one-time OAuth2 grant (see auth/google_oauth.py);
after that, the access token silently refreshes for as long as the
refresh token stays valid.

Why the Gmail API over SMTP+password:
  - No app password stored on disk or in .env.
  - Google's own send path — it's already authenticated as "you", which
    plays better with Gmail's abuse/reputation systems than a bare SMTP
    login from a script.
  - The same OAuth grant covers both sending (gmail.send) and inbox
    scanning (gmail.readonly/modify) for bounce/reply tracking, so there's
    one credential to manage instead of an SMTP password + IMAP password.
"""

from __future__ import annotations

import base64
import logging
import threading
from email.message import Message

from googleapiclient.discovery import Resource, build
from googleapiclient.errors import HttpError

from recruiter_outreach.auth.google_oauth import GoogleOAuthError, load_credentials
from recruiter_outreach.config import Settings
from recruiter_outreach.delivery.transport import (
    EmailTransport,
    TransportError,
    TransportPermanentError,
)

logger = logging.getLogger(__name__)

# Gmail API HTTP error codes that mean "this address/request will never
# succeed, don't retry" vs transient errors worth retrying.
_PERMANENT_STATUS_CODES = {400, 403, 404}


class GmailOAuthTransport(EmailTransport):
    """Thread-safe: googleapiclient Resource objects are NOT thread-safe,
    so each thread gets its own `build()`'d service via threading.local(),
    same pattern as the old SmtpConnectionPool but without a live socket
    to keep alive — just a lightweight HTTP client wrapper."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._credentials = load_credentials(settings)
        if self._credentials is None:
            raise GoogleOAuthError(
                "No valid Google OAuth credentials found. Connect a Gmail "
                "account first (GET /auth/google/login), or run "
                "EMAIL_PROVIDER=smtp if you don't want to use OAuth."
            )
        self._local = threading.local()

    def _service(self) -> Resource:
        service = getattr(self._local, "service", None)
        if service is None:
            service = build("gmail", "v1", credentials=self._credentials, cache_discovery=False)
            self._local.service = service
        return service

    def send(self, from_addr: str, to_addr: str, message: Message) -> str:
        if "From" not in message:
            message["From"] = from_addr
        if "To" not in message:
            message["To"] = to_addr

        raw = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")
        body = {"raw": raw}

        try:
            result = self._service().users().messages().send(userId="me", body=body).execute()
            return result.get("id", "")
        except HttpError as exc:
            status = getattr(exc.resp, "status", None)
            logger.warning("Gmail API send failed for %s (status=%s): %s", to_addr, status, exc)
            if status in _PERMANENT_STATUS_CODES:
                raise TransportPermanentError(str(exc)) from exc
            raise TransportError(str(exc)) from exc
        except Exception as exc:  # noqa: BLE001 - network/transport errors, all retryable
            logger.warning("Gmail API send failed for %s: %s", to_addr, exc)
            raise TransportError(str(exc)) from exc

    def close_thread(self) -> None:
        self._local.service = None
