# FILE: recruiter_outreach/tracking/imap_tracker.py

"""
Bounce and reply detection — provider-agnostic.

Run periodically (e.g. via `recruiter-outreach-check-inbox` on a cron) to:
  - detect bounce notifications and suppress the bounced address
  - detect replies from recruiters previously emailed
  - detect 'unsubscribe' replies and suppress those addresses

InboxTracker itself contains only the bounce/reply/unsubscribe heuristics;
the actual mailbox scanning is delegated to a MailReader (see
tracking/mail_reader.py) — GmailOAuthMailReader by default, ImapMailReader
as a legacy fallback. The class name and constructor signature
(InboxTracker(settings, db)) are kept stable for backward compatibility;
`reader` is an optional third parameter used mainly by tests.
"""

from __future__ import annotations

import re

from recruiter_outreach.compliance.suppression import process_unsubscribe_keyword
from recruiter_outreach.config import Settings
from recruiter_outreach.db import Database
from recruiter_outreach.logging_setup import get_logger
from recruiter_outreach.tracking.mail_reader import MailReader, build_mail_reader

logger = get_logger(__name__)

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
BOUNCE_SENDER_HINTS  = ("mailer-daemon", "postmaster", "mail delivery")
BOUNCE_SUBJECT_HINTS = (
    "undelivered", "delivery status", "returned mail",
    "failure notice", "delivery failed",
)


class InboxTracker:
    def __init__(self, settings: Settings, db: Database, reader: MailReader | None = None):
        self.settings = settings
        self.db = db
        self.reader = reader or build_mail_reader(settings)

    def check(self, since_days: int = 14, limit: int = 200) -> dict:
        """Scans the inbox for recent messages and updates bounce/reply state."""
        stats = {"bounces": 0, "replies": 0, "unsubscribes": 0, "scanned": 0}
        known_emails = self.db.known_recipient_emails()
        if not known_emails:
            logger.info("No recorded sends yet — nothing to check replies/bounces against.")
            return stats

        for raw in self.reader.search_since(since_days, limit):
            stats["scanned"] += 1
            from_addr = raw.from_addr
            subject = raw.subject

            if self._looks_like_bounce(from_addr, subject):
                bounced_email = self._extract_bounced_address(raw.body, known_emails)
                if bounced_email:
                    self.db.mark_bounced(bounced_email)
                    stats["bounces"] += 1
                    logger.info(f"Bounce detected for {bounced_email}")
                continue

            sender_email = self._extract_sender_email(from_addr)
            if sender_email and sender_email.lower() in known_emails:
                if process_unsubscribe_keyword(raw.body):
                    self.db.suppress(sender_email, reason="unsubscribed")
                    stats["unsubscribes"] += 1
                    logger.info(f"Unsubscribe detected from {sender_email}")
                else:
                    self.db.mark_replied(sender_email)
                    stats["replies"] += 1
                    logger.info(f"Reply detected from {sender_email}")

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
    def _extract_bounced_address(body: str, known_emails: set[str]) -> str | None:
        """Scans the whole body for any address we recognise as a prior recipient."""
        found = {m.lower() for m in EMAIL_RE.findall(body)}
        overlap = found & known_emails
        return next(iter(overlap), None)
