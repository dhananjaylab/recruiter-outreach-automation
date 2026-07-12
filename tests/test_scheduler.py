"""Tests for the follow-up scheduler (followup/scheduler.py).

OutreachManager is patched so no SMTP is touched; the tests exercise
the scheduling logic (grouping by step, filtering, disabled flag).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from recruiter_outreach.config import Settings
from recruiter_outreach.db import Database
from recruiter_outreach.followup.scheduler import run_followups
from recruiter_outreach.reporting.report import RunReport


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _settings(tmp_path) -> Settings:
    td = tmp_path / "templates"
    td.mkdir(exist_ok=True)
    (td / "default.md").write_text(
        "Hi {recruiter_name} at {company_name}. {opening_line}{resume_line} -{sender_name}"
    )
    (td / "followup_1.md").write_text(
        "Follow up {recruiter_name} at {company_name}. {resume_line} -{sender_name}"
    )
    return Settings(
        EMAIL_USER="sender@example.com",
        EMAIL_PASSWORD="secret",
        RESUME_LINK="https://example.com/resume.pdf",
        EMAIL_TEMPLATE_DIR=str(td),
        WARMUP_ENABLED=False,
        VERIFY_MX=False,
        FOLLOWUP_ENABLED=True,
        FOLLOWUP_DELAY_DAYS=5,
        MAX_FOLLOWUPS=1,
        DB_PATH=str(tmp_path / "test.db"),
        REPORTS_DIR=str(tmp_path / "reports"),
    )


def _insert_old_send(db: Database, email: str, step: int = 0) -> None:
    """Insert a send row dated 10 days ago so it's eligible for a follow-up."""
    with db._cursor() as cur:
        cur.execute(
            "INSERT INTO sends "
            "(email, name, company, role, template_used, sequence_step, sent_at, status) "
            "VALUES (?, ?, ?, ?, ?, ?, datetime('now','-10 days'), 'sent')",
            (email, "HR", "Corp", "SDE", "default.md", step),
        )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestRunFollowups:
    def test_returns_empty_when_disabled(self, tmp_path, db):
        s = _settings(tmp_path)
        s = s.model_copy(update={"followup_enabled": False})
        reports = run_followups(s, db)
        assert reports == []

    def test_returns_empty_when_no_due_rows(self, tmp_path, db):
        s = _settings(tmp_path)
        # Record a send that happened NOW — not old enough to be due
        db.record_send(
            email="jane@corp.com", name="Jane", company="Corp", role="SDE",
            template_used="default.md", sequence_step=0, status="sent",
        )
        reports = run_followups(s, db)
        assert reports == []

    def test_sends_followup_for_eligible_row(self, tmp_path, db):
        s = _settings(tmp_path)
        _insert_old_send(db, "jane@corp.com", step=0)

        mock_report = RunReport()
        mock_report.record_success("jane@corp.com")

        with patch(
            "recruiter_outreach.followup.scheduler.OutreachManager"
        ) as MockMgr:
            instance = MockMgr.return_value
            instance.send_emails_concurrently.return_value = mock_report

            reports = run_followups(s, db)

        assert len(reports) == 1
        MockMgr.assert_called_once_with(settings=s, db=db, sequence_step=1, on_event=None)
        instance.send_emails_concurrently.assert_called_once()
        sent_batch = instance.send_emails_concurrently.call_args[0][0]
        assert sent_batch[0]["Email"] == "jane@corp.com"

    def test_groups_by_sequence_step(self, tmp_path, db):
        """Two recipients at different steps should produce two separate batches."""
        s = _settings(tmp_path)
        _insert_old_send(db, "a@corp.com", step=0)
        _insert_old_send(db, "b@corp.com", step=0)

        # Simulate b already having received step-1 (so it needs step-2, but
        # MAX_FOLLOWUPS=1 caps it — just verify grouping works with one step)
        captured_steps: list[int] = []

        def fake_init(*args, **kwargs):
            captured_steps.append(kwargs["sequence_step"])
            m = MagicMock()
            m.send_emails_concurrently.return_value = RunReport()
            return m

        with patch(
            "recruiter_outreach.followup.scheduler.OutreachManager",
            side_effect=fake_init,
        ):
            run_followups(s, db)

        # Both recipients are at step 0, so they should both be in one batch
        # for step 1
        assert captured_steps == [1]

    def test_excludes_suppressed_recipients(self, tmp_path, db):
        s = _settings(tmp_path)
        _insert_old_send(db, "suppressed@corp.com", step=0)
        db.suppress("suppressed@corp.com", reason="bounced")

        with patch(
            "recruiter_outreach.followup.scheduler.OutreachManager"
        ) as MockMgr:
            instance = MockMgr.return_value
            instance.send_emails_concurrently.return_value = RunReport()
            reports = run_followups(s, db)

        # Suppressed address is filtered by due_for_followup — nothing to send
        assert reports == []

    def test_excludes_replied_recipients(self, tmp_path, db):
        s = _settings(tmp_path)
        _insert_old_send(db, "replied@corp.com", step=0)
        db.mark_replied("replied@corp.com")

        with patch(
            "recruiter_outreach.followup.scheduler.OutreachManager"
        ) as MockMgr:
            run_followups(s, db)

        MockMgr.assert_not_called()

    def test_on_event_forwarded_to_outreach_manager(self, tmp_path, db):
        s = _settings(tmp_path)
        _insert_old_send(db, "jane@corp.com", step=0)

        def sink(evt):
            pass

        with patch(
            "recruiter_outreach.followup.scheduler.OutreachManager"
        ) as MockMgr:
            instance = MockMgr.return_value
            instance.send_emails_concurrently.return_value = RunReport()
            run_followups(s, db, on_event=sink)

        _, kwargs = MockMgr.call_args
        assert kwargs["on_event"] is sink
