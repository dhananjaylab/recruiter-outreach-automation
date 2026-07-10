from recruiter_outreach.verification.email_verifier import has_valid_format


def test_valid_formats():
    assert has_valid_format("jane.doe@corp.com")
    assert has_valid_format("a+b@sub.corp.co.uk")


def test_invalid_formats():
    assert not has_valid_format("not-an-email")
    assert not has_valid_format("missing@domain")
    assert not has_valid_format("@corp.com")
    assert not has_valid_format("jane@@corp.com")
