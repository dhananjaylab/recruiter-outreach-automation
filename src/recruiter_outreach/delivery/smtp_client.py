# FILE: src/recruiter_outreach/delivery/smtp_client.py

"""Thread-local SMTP connection pool — one persistent, authenticated
connection per worker thread instead of one handshake per email."""

from __future__ import annotations

import logging
import smtplib
import threading

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
