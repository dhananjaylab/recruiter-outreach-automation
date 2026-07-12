# FILE: recruiter_outreach/delivery/transport.py

"""
EmailTransport — the provider-agnostic interface OutreachManager sends
through.  Two implementations exist:

  - GmailOAuthTransport (delivery/gmail_oauth_client.py) — default. Sends
    via the Gmail API using an OAuth2 refresh token. No password is ever
    stored.
  - SmtpTransport (delivery/smtp_client.py) — legacy fallback for
    environments without Google Cloud OAuth credentials configured.

This mirrors the BaseEmailClient / GmailClient / MockEmailClient pattern:
OutreachManager is injected with a transport at construction time and
never knows which provider it's talking to (Dependency Inversion).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.message import Message
from typing import Literal, Optional


class EmailTransport(ABC):
    """Interface every send-side email provider must implement."""

    @abstractmethod
    def send(self, from_addr: str, to_addr: str, message: Message) -> str:
        """Sends a fully-built email.Message and returns a provider message id.

        Must raise on failure (TransportError or a provider-specific
        exception) rather than returning a falsy value, so OutreachManager's
        existing retry/backoff logic keeps working unchanged.
        """
        ...

    def close_thread(self) -> None:
        """Optional: release any per-thread resources (e.g. an SMTP socket).
        No-op by default since stateless HTTP-based providers (Gmail API)
        don't hold a persistent connection per thread."""
        return None


class TransportError(Exception):
    """Raised by any EmailTransport implementation on a send failure that
    should be treated as retryable by OutreachManager."""


class TransportPermanentError(Exception):
    """Raised for failures that should NOT be retried (e.g. an address the
    provider hard-rejects) — mirrors smtplib.SMTPRecipientsRefused."""


# Event status values emitted during a send run, consumed by the FastAPI
# SSE endpoint and, in future, any other progress observer.
EventStatus = Literal["started", "sent", "failed", "skipped", "run_complete"]


@dataclass
class ProgressEvent:
    """One observable moment in an outreach run. Emitted via the optional
    on_event callback threaded through OutreachManager -> the API layer."""

    status: EventStatus
    email: str = ""
    name: str = ""
    company: str = ""
    reason: Optional[str] = None
    index: Optional[int] = None
    total: Optional[int] = None
    extra: Optional[dict] = None
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "email": self.email,
            "name": self.name,
            "company": self.company,
            "reason": self.reason,
            "index": self.index,
            "total": self.total,
            "extra": self.extra,
            "timestamp": self.timestamp,
        }
