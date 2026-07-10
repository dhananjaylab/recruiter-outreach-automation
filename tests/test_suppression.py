from recruiter_outreach.compliance.suppression import (
    is_blocked,
    process_unsubscribe_keyword,
    unsubscribe_footer,
)


def test_unsubscribe_footer_contains_contact():
    footer = unsubscribe_footer("replying here")
    assert "replying here" in footer


def test_unsubscribe_footer_default_contact():
    footer = unsubscribe_footer(None)
    assert "replying to this email" in footer


def test_process_unsubscribe_keyword_detects_variants():
    assert process_unsubscribe_keyword("Please unsubscribe me")
    assert process_unsubscribe_keyword("STOP EMAILING ME")
    assert process_unsubscribe_keyword("could you remove me from this list")
    assert not process_unsubscribe_keyword("Thanks, let's chat next week!")


def test_is_blocked_reflects_db_state(db):
    assert not is_blocked(db, "a@corp.com")
    db.suppress("a@corp.com", reason="bounced")
    assert is_blocked(db, "a@corp.com")
