from recruiter_outreach.db import Database


def test_suppression_roundtrip(db: Database):
    assert not db.is_suppressed("a@corp.com")
    db.suppress("a@corp.com", reason="manual")
    assert db.is_suppressed("a@corp.com")


def test_suppression_case_insensitive(db: Database):
    db.suppress("A@Corp.com", reason="manual")
    assert db.is_suppressed("a@corp.com")


def test_record_send_and_already_sent(db: Database):
    assert not db.already_sent("a@corp.com", 0)
    db.record_send(
        email="a@corp.com", name="A", company="Corp", role="SDE",
        template_used="default.md", sequence_step=0, status="sent",
    )
    assert db.already_sent("a@corp.com", 0)
    assert not db.already_sent("a@corp.com", 1)  # different sequence step


def test_mark_bounced_suppresses(db: Database):
    db.record_send(
        email="a@corp.com", name="A", company="Corp", role="",
        template_used="default.md", sequence_step=0, status="sent",
    )
    db.mark_bounced("a@corp.com")
    assert db.is_suppressed("a@corp.com")


def test_due_for_followup_respects_delay(db: Database):
    db.record_send(
        email="a@corp.com", name="A", company="Corp", role="",
        template_used="default.md", sequence_step=0, status="sent",
    )
    # sent "now" -> not due for a 5-day-delay follow-up yet
    due = db.due_for_followup(delay_days=5, max_step=1)
    assert due == []


def test_due_for_followup_excludes_replied(db: Database):
    with db._cursor() as cur:  # internal helper reused only for test setup
        cur.execute(
            "INSERT INTO sends (email, name, company, role, template_used, "
            "sequence_step, sent_at, status) VALUES (?, ?, ?, ?, ?, ?, datetime('now','-10 days'), 'sent')",
            ("a@corp.com", "A", "Corp", "", "default.md", 0),
        )
    db.mark_replied("a@corp.com")
    due = db.due_for_followup(delay_days=5, max_step=1)
    assert due == []


def test_due_for_followup_finds_eligible_row(db: Database):
    with db._cursor() as cur:
        cur.execute(
            "INSERT INTO sends (email, name, company, role, template_used, "
            "sequence_step, sent_at, status) VALUES (?, ?, ?, ?, ?, ?, datetime('now','-10 days'), 'sent')",
            ("a@corp.com", "A", "Corp", "", "default.md", 0),
        )
    due = db.due_for_followup(delay_days=5, max_step=1)
    assert len(due) == 1
    assert due[0]["email"] == "a@corp.com"


def test_meta_roundtrip(db: Database):
    assert db.get_meta("k") is None
    db.set_meta("k", "v1")
    assert db.get_meta("k") == "v1"
    db.set_meta("k", "v2")
    assert db.get_meta("k") == "v2"
