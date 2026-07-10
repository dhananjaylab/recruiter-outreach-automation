import pandas as pd

from recruiter_outreach.ingestion.normalize import normalize_columns, validate_and_clean


def test_column_aliases_are_mapped():
    df = pd.DataFrame({
        "Full Name": ["Jane Doe"],
        "Work Email": ["jane@corp.com"],
        "Organisation": ["Corp"],
        "Job Title": ["Recruiter"],
    })
    out = normalize_columns(df)
    assert set(out.columns) >= {"Name", "Email", "Company", "Role"}
    assert out.iloc[0]["Email"] == "jane@corp.com"


def test_first_last_name_merge():
    df = pd.DataFrame({
        "First Name": ["Jane"], "Last Name": ["Doe"], "Email": ["jane@corp.com"],
    })
    out = normalize_columns(df)
    assert out.iloc[0]["Name"] == "Jane Doe"


def test_invalid_emails_dropped():
    df = pd.DataFrame({
        "Name": ["A", "B", "C"],
        "Email": ["a@corp.com", "not-an-email", "c@corp.com"],
        "Company": ["X", "Y", "Z"],
    })
    clean, dropped = validate_and_clean(df)
    assert dropped == 1
    assert len(clean) == 2


def test_dedup_case_insensitive():
    df = pd.DataFrame({
        "Name": ["A", "A2"],
        "Email": ["dup@corp.com", "DUP@corp.com"],
        "Company": ["X", "X"],
    })
    clean, _ = validate_and_clean(df)
    assert len(clean) == 1


def test_missing_columns_get_defaults():
    df = pd.DataFrame({"Email": ["a@corp.com"]})
    clean, _ = validate_and_clean(df)
    assert clean.iloc[0]["Name"] == "HR"
    assert clean.iloc[0]["Company"] == "your company"
