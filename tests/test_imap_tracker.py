"""Tests for InboxTracker (tracking/imap_tracker.py).

imaplib.IMAP4_SSL is fully mocked — no network required.
Tests cover bounce detection, reply detection, unsubscribe detection,
and the various edge cases (unknown sender, no recorded sends).
"""

from __future__ import annotations

import email as email_lib
from email.mime.text import MIMEText
from unittest.mock import MagicMock, patch

import pytest

from recruiter_outreach.config import Settings
from recruiter_outreach.db import Database
from recruiter_outreach.tracking.imap_tracker import InboxTracker


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _settings() -> Settings:
    return Settings(
        EMAIL_USER="me@example.com",
        EMAIL_PASSWORD="secret",
        IMAP_SERVER="imap.example.com",
        IMAP_PORT=993,
        RESUME_LINK="https://example.com/cv",
        DB_PATH=":memory:",
        REPORTS_DIR="reports",
    )


def _raw(subject: str, from_: str, body: str = "") -> bytes:
    """Build a minimal RFC-822 message as raw bytes."""
    msg = MIMEText(body or subject, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"]    = from_
    msg["To"]      = "me@example.com"
    return msg.as_bytes()


def _make_imap(messages: list[bytes]) -> MagicMock:
    """Return a mock IMAP4_SSL that yields the given raw messages."""
    imap = MagicMock()
    imap.select.return_value = ("OK", [b""])
    imap.search.return_value = (
        "OK",
        [b" ".join(str(i + 1).encode() for i in range(len(messages)))],
    )
    imap.fetch.side_effect = [
        ("OK", [(None, raw)]) for raw in messages
    ]
    return imap


def _tracker(db: Database) -> InboxTracker:
    return InboxTracker(_settings(), db)


# ---------------------------------------------------------------------------
# No recorded sends
# ---------------------------------------------------------------------------

class TestNoRecordedSends:
    def test_returns_zeros_when_no_prior_sends(self, db):
        tracker = _tracker(db)
        with patch("recruiter_outreach.tracking.imap_tracker.imaplib.IMAP4_SSL"):
            stats = tracker.check(since_days=7)
        assert stats == {"bounces": 0, "replies": 0, "unsubscribes": 0, "scanned": 0}


# ---------------------------------------------------------------------------
# Bounce detection
# ---------------------------------------------------------------------------

class TestBounceDetection:
    def _seed(self, db: Database, email: str = "jane@corp.com") -> None:
        db.record_send(
            email=email, name="Jane", company="Corp", role="SDE",
            template_used="default.md", sequence_step=0, status="sent",
        )

    def test_mailer_daemon_from_triggers_bounce(self, db):
        self._seed(db)
        raw = _raw(
            subject="Delivery Status Notification",
            from_="mailer-daemon@corp.com",
            body="jane@corp.com could not be delivered",
        )
        tracker = _tracker(db)
        mock_imap = _make_imap([raw])
        with patch(
            "recruiter_outreach.tracking.imap_tracker.imaplib.IMAP4_SSL",
            return_value=mock_imap,
        ):
            stats = tracker.check(since_days=7)

        assert stats["bounces"] == 1
        assert db.is_suppressed("jane@corp.com")

    def test_bounce_subject_triggers_bounce(self, db):
        self._seed(db)
        raw = _raw(
            subject="Undelivered Mail Returned to Sender",
            from_="postmaster@example.com",
            body="jane@corp.com was not delivered",
        )
        tracker = _tracker(db)
        mock_imap = _make_imap([raw])
        with patch(
            "recruiter_outreach.tracking.imap_tracker.imaplib.IMAP4_SSL",
            return_value=mock_imap,
        ):
            stats = tracker.check(since_days=7)

        assert stats["bounces"] == 1

    def test_bounce_with_unknown_address_not_counted(self, db):
        self._seed(db)
        raw = _raw(
            subject="Delivery failure",
            from_="mailer-daemon@other.com",
            body="unknown@nowhere.com was rejected",  # not in known_emails
        )
        tracker = _tracker(db)
        mock_imap = _make_imap([raw])
        with patch(
            "recruiter_outreach.tracking.imap_tracker.imaplib.IMAP4_SSL",
            return_value=mock_imap,
        ):
            stats = tracker.check(since_days=7)

        assert stats["bounces"] == 0


# ---------------------------------------------------------------------------
# Reply detection
# ---------------------------------------------------------------------------

class TestReplyDetection:
    def _seed(self, db: Database, email: str = "jane@corp.com") -> None:
        db.record_send(
            email=email, name="Jane", company="Corp", role="SDE",
            template_used="default.md", sequence_step=0, status="sent",
        )

    def test_reply_from_known_recipient_marked(self, db):
        self._seed(db)
        raw = _raw(
            subject="Re: Seeking Opportunity",
            from_="Jane Smith <jane@corp.com>",
            body="Thanks for reaching out!",
        )
        tracker = _tracker(db)
        mock_imap = _make_imap([raw])
        with patch(
            "recruiter_outreach.tracking.imap_tracker.imaplib.IMAP4_SSL",
            return_value=mock_imap,
        ):
            stats = tracker.check(since_days=7)

        assert stats["replies"] == 1

    def test_reply_from_unknown_sender_ignored(self, db):
        self._seed(db)
        raw = _raw(
            subject="Hello",
            from_="stranger@other.com",
            body="Not a recruiter",
        )
        tracker = _tracker(db)
        mock_imap = _make_imap([raw])
        with patch(
            "recruiter_outreach.tracking.imap_tracker.imaplib.IMAP4_SSL",
            return_value=mock_imap,
        ):
            stats = tracker.check(since_days=7)

        assert stats["replies"] == 0


# ---------------------------------------------------------------------------
# Unsubscribe detection
# ---------------------------------------------------------------------------

class TestUnsubscribeDetection:
    def _seed(self, db: Database, email: str = "jane@corp.com") -> None:
        db.record_send(
            email=email, name="Jane", company="Corp", role="SDE",
            template_used="default.md", sequence_step=0, status="sent",
        )

    def test_unsubscribe_keyword_suppresses_sender(self, db):
        self._seed(db)
        raw = _raw(
            subject="Re: your email",
            from_="jane@corp.com",
            body="Please unsubscribe me from your list.",
        )
        tracker = _tracker(db)
        mock_imap = _make_imap([raw])
        with patch(
            "recruiter_outreach.tracking.imap_tracker.imaplib.IMAP4_SSL",
            return_value=mock_imap,
        ):
            stats = tracker.check(since_days=7)

        assert stats["unsubscribes"] == 1
        assert db.is_suppressed("jane@corp.com")

    def test_opt_out_keyword_suppresses_sender(self, db):
        self._seed(db)
        raw = _raw(
            subject="Re:",
            from_="jane@corp.com",
            body="Please stop emailing me.",
        )
        tracker = _tracker(db)
        mock_imap = _make_imap([raw])
        with patch(
            "recruiter_outreach.tracking.imap_tracker.imaplib.IMAP4_SSL",
            return_value=mock_imap,
        ):
            stats = tracker.check(since_days=7)

        assert stats["unsubscribes"] == 1


# ---------------------------------------------------------------------------
# IMAP search failure
# ---------------------------------------------------------------------------

class TestImapSearchFailure:
    def test_returns_zeros_on_search_failure(self, db):
        db.record_send(
            email="jane@corp.com", name="Jane", company="Corp", role="",
            template_used="default.md", sequence_step=0, status="sent",
        )
        tracker = _tracker(db)
        mock_imap = MagicMock()
        mock_imap.select.return_value = ("OK", [b""])
        mock_imap.search.return_value  = ("NO", [b""])

        with patch(
            "recruiter_outreach.tracking.imap_tracker.imaplib.IMAP4_SSL",
            return_value=mock_imap,
        ):
            stats = tracker.check(since_days=7)

        assert stats["bounces"] == 0
        assert stats["replies"] == 0
