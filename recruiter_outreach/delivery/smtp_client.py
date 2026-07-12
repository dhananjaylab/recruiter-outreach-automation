# FILE: recruiter_outreach/delivery/smtp_client.py

"""Thread-local SMTP connection pool — one persistent, authenticated
connection per worker thread instead of one handshake per email.

Kept as the EMAIL_PROVIDER="smtp" legacy path now that GmailOAuthTransport
(gmail_oauth_client.py) is the default — see delivery/transport.py for the
shared EmailTransport interface both implement."""

from __future__ import annotations

import logging
import smtplib
import threading
from email.message import Message

from recruiter_outreach.delivery.transport import (
    EmailTransport,
    TransportError,
    TransportPermanentError,
)

logger = logging.getLogger(__name__)


class SmtpConnectionPool:
    def __init__(self, server: str, port: int, user: str, password: str):
        self.server = server
        self.port = port
        self.user = user
        self.password = password
        self._local = threading.local()

    def get(self) -> smtplib.SMTP:
        conn: smtplib.SMTP | None = getattr(self._local, "smtp", None)

        if conn is not None:
            try:
                conn.noop()
            except Exception:
                conn = None

        if conn is None:
            logger.debug(f"[{threading.current_thread().name}] opening new SMTP connection…")
            conn = smtplib.SMTP(self.server, self.port, timeout=30)
            conn.ehlo()
            conn.starttls()
            conn.ehlo()
            conn.login(self.user, self.password)
            self._local.smtp = conn

        return conn

    def invalidate(self) -> None:
        """Forces a reconnect on the next get() call from this thread."""
        self._local.smtp = None

    def close_current_thread(self) -> None:
        conn: smtplib.SMTP | None = getattr(self._local, "smtp", None)
        if conn:
            try:
                conn.quit()
            except Exception:
                pass
            self._local.smtp = None


class SmtpTransport(EmailTransport):
    """Adapts SmtpConnectionPool to the shared EmailTransport interface so
    OutreachManager can use it interchangeably with GmailOAuthTransport."""

    def __init__(self, pool: SmtpConnectionPool):
        self._pool = pool

    def send(self, from_addr: str, to_addr: str, message: Message) -> str:
        if "From" not in message:
            message["From"] = from_addr
        if "To" not in message:
            message["To"] = to_addr
        try:
            conn = self._pool.get()
            conn.sendmail(from_addr, to_addr, message.as_string())
            return message.get("Message-ID", "")
        except smtplib.SMTPRecipientsRefused as exc:
            raise TransportPermanentError(str(exc)) from exc
        except (smtplib.SMTPServerDisconnected, smtplib.SMTPException, OSError) as exc:
            self._pool.invalidate()
            raise TransportError(str(exc)) from exc

    def close_thread(self) -> None:
        self._pool.close_current_thread()
