# FILE: recruiter_outreach/tracking/imap_tracker.py

"""
IMAP-based bounce and reply detection.

Run periodically (e.g. via `recruiter-outreach-check-inbox` on a cron) to:
  - detect bounce notifications and suppress the bounced address
  - detect replies from recruiters previously emailed
  - detect 'unsubscribe' replies and suppress those addresses
"""

from __future__ import annotations

import email as email_lib
import imaplib
import re
from datetime import datetime, timedelta
from email.header import decode_header

from recruiter_outreach.compliance.suppression import process_unsubscribe_keyword
from recruiter_outreach.config import Settings
from recruiter_outreach.db import Database
from recruiter_outreach.logging_setup import get_logger

logger = get_logger(__name__)

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
BOUNCE_SENDER_HINTS  = ("mailer-daemon", "postmaster", "mail delivery")
BOUNCE_SUBJECT_HINTS = (
    "undelivered", "delivery status", "returned mail",
    "failure notice", "delivery failed",
)


def _decode(value: str | None) -> str:
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


def _imap_date(days_ago: int) -> str:
    dt = datetime.now() - timedelta(days=days_ago)
    return dt.strftime("%d-%b-%Y")


class InboxTracker:
    def __init__(self, settings: Settings, db: Database):
        self.settings = settings
        self.db = db

    def _connect(self) -> imaplib.IMAP4_SSL:
        conn = imaplib.IMAP4_SSL(self.settings.imap_server, self.settings.imap_port)
        conn.login(self.settings.email_user, self.settings.email_password)
        return conn

    def check(self, since_days: int = 14, limit: int = 200) -> dict:
        """Scans the inbox for recent messages and updates bounce/reply state."""
        stats = {"bounces": 0, "replies": 0, "unsubscribes": 0, "scanned": 0}
        known_emails = self.db.known_recipient_emails()
        if not known_emails:
            logger.info("No recorded sends yet — nothing to check replies/bounces against.")
            return stats

        conn = self._connect()
        try:
            conn.select("INBOX")
            status, data = conn.search(None, f'(SINCE "{_imap_date(since_days)}")')
            if status != "OK":
                logger.warning("IMAP search failed.")
                return stats

            msg_ids = data[0].split()[-limit:]
            for msg_id in msg_ids:
                status, msg_data = conn.fetch(msg_id, "(RFC822)")
                if status != "OK" or not msg_data or not msg_data[0]:
                    continue
                raw = msg_data[0][1]
                msg = email_lib.message_from_bytes(raw)
                stats["scanned"] += 1

                from_addr = _decode(msg.get("From", ""))
                subject   = _decode(msg.get("Subject", ""))

                if self._looks_like_bounce(from_addr, subject):
                    bounced_email = self._extract_bounced_address(msg, known_emails)
                    if bounced_email:
                        self.db.mark_bounced(bounced_email)
                        stats["bounces"] += 1
                        logger.info(f"Bounce detected for {bounced_email}")
                    continue

                sender_email = self._extract_sender_email(from_addr)
                if sender_email and sender_email.lower() in known_emails:
                    body = _get_body_text(msg)
                    if process_unsubscribe_keyword(body):
                        self.db.suppress(sender_email, reason="unsubscribed")
                        stats["unsubscribes"] += 1
                        logger.info(f"Unsubscribe detected from {sender_email}")
                    else:
                        self.db.mark_replied(sender_email)
                        stats["replies"] += 1
                        logger.info(f"Reply detected from {sender_email}")
        finally:
            try:
                conn.logout()
            except Exception:
                pass

        return stats

    @staticmethod
    def _looks_like_bounce(from_addr: str, subject: str) -> bool:
        f, s = from_addr.lower(), subject.lower()
        return (
            any(h in f for h in BOUNCE_SENDER_HINTS)
            or any(h in s for h in BOUNCE_SUBJECT_HINTS)
        )

    @staticmethod
    def _extract_sender_email(from_addr: str) -> str | None:
        match = EMAIL_RE.search(from_addr)
        return match.group() if match else None

    @staticmethod
    def _extract_bounced_address(msg, known_emails: set[str]) -> str | None:
        """Scans the whole body for any address we recognise as a prior recipient."""
        body  = _get_body_text(msg)
        found = {m.lower() for m in EMAIL_RE.findall(body)}
        overlap = found & known_emails
        return next(iter(overlap), None)
