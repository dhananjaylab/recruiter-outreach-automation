"""Tests for InboxTracker (tracking/imap_tracker.py).

A FakeMailReader (implementing MailReader) is injected directly via the
constructor's `reader=` parameter — this exercises the bounce/reply/
unsubscribe detection logic without depending on either provider's wire
format (IMAP RFC822 bytes vs Gmail API JSON), which is now tested
separately for each MailReader implementation.
"""

from __future__ import annotations

from recruiter_outreach.config import Settings
from recruiter_outreach.db import Database
from recruiter_outreach.tracking.imap_tracker import InboxTracker
from recruiter_outreach.tracking.mail_reader import MailReader, RawEmailMessage


# ---------------------------------------------------------------------------
# Fake reader
# ---------------------------------------------------------------------------

class FakeMailReader(MailReader):
    def __init__(self, messages: list[RawEmailMessage]):
        self.messages = messages

    def search_since(self, since_days: int, limit: int = 200) -> list[RawEmailMessage]:
        return self.messages


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _settings() -> Settings:
    return Settings(
        EMAIL_USER="me@example.com",
        RESUME_LINK="https://example.com/cv",
        DB_PATH=":memory:",
        REPORTS_DIR="reports",
    )


def _tracker(db: Database, messages: list[RawEmailMessage]) -> InboxTracker:
    return InboxTracker(_settings(), db, reader=FakeMailReader(messages))


def _msg(subject: str, from_: str, body: str = "") -> RawEmailMessage:
    return RawEmailMessage(message_id="<m1>", from_addr=from_, subject=subject, body=body or subject)


# ---------------------------------------------------------------------------
# No recorded sends
# ---------------------------------------------------------------------------

class TestNoRecordedSends:
    def test_returns_zeros_when_no_prior_sends(self, db):
        tracker = _tracker(db, [])
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
        msg = _msg(
            subject="Delivery Status Notification",
            from_="mailer-daemon@corp.com",
            body="jane@corp.com could not be delivered",
        )
        stats = _tracker(db, [msg]).check(since_days=7)
        assert stats["bounces"] == 1
        assert db.is_suppressed("jane@corp.com")

    def test_bounce_subject_triggers_bounce(self, db):
        self._seed(db)
        msg = _msg(
            subject="Undelivered Mail Returned to Sender",
            from_="postmaster@example.com",
            body="jane@corp.com was not delivered",
        )
        stats = _tracker(db, [msg]).check(since_days=7)
        assert stats["bounces"] == 1

    def test_bounce_with_unknown_address_not_counted(self, db):
        self._seed(db)
        msg = _msg(
            subject="Delivery failure",
            from_="mailer-daemon@other.com",
            body="unknown@nowhere.com was rejected",
        )
        stats = _tracker(db, [msg]).check(since_days=7)
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
        msg = _msg(
            subject="Re: Seeking Opportunity",
            from_="Jane Smith <jane@corp.com>",
            body="Thanks for reaching out!",
        )
        stats = _tracker(db, [msg]).check(since_days=7)
        assert stats["replies"] == 1

    def test_reply_from_unknown_sender_ignored(self, db):
        self._seed(db)
        msg = _msg(subject="Hello", from_="stranger@other.com", body="Not a recruiter")
        stats = _tracker(db, [msg]).check(since_days=7)
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
        msg = _msg(
            subject="Re: your email",
            from_="jane@corp.com",
            body="Please unsubscribe me from your list.",
        )
        stats = _tracker(db, [msg]).check(since_days=7)
        assert stats["unsubscribes"] == 1
        assert db.is_suppressed("jane@corp.com")

    def test_opt_out_keyword_suppresses_sender(self, db):
        self._seed(db)
        msg = _msg(subject="Re:", from_="jane@corp.com", body="Please stop emailing me.")
        stats = _tracker(db, [msg]).check(since_days=7)
        assert stats["unsubscribes"] == 1


# ---------------------------------------------------------------------------
# Multiple messages in one scan
# ---------------------------------------------------------------------------

class TestMultipleMessages:
    def test_mixed_batch_counts_each_category(self, db):
        db.record_send(
            email="a@corp.com", name="A", company="Corp", role="",
            template_used="default.md", sequence_step=0, status="sent",
        )
        db.record_send(
            email="b@corp.com", name="B", company="Corp", role="",
            template_used="default.md", sequence_step=0, status="sent",
        )
        messages = [
            _msg("Undelivered Mail", "mailer-daemon@x.com", "a@corp.com bounced"),
            _msg("Re: hi", "b@corp.com", "Sounds great, let's talk!"),
            _msg("newsletter", "noreply@other.com", "unrelated"),
        ]
        stats = _tracker(db, messages).check(since_days=7)
        assert stats["bounces"] == 1
        assert stats["replies"] == 1
        assert stats["scanned"] == 3
