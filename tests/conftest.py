"""Shared pytest fixtures.

Import resolution is handled by tests/context.py (the Hitchhiker's Guide
pattern), which is imported here once so all test modules benefit
automatically without each needing its own sys.path manipulation.
"""

import pytest

import tests.context  # noqa: F401  — ensures project root is on sys.path

from recruiter_outreach.db import Database


@pytest.fixture
def db(tmp_path) -> Database:
    return Database(str(tmp_path / "test.db"))


@pytest.fixture
def template_dir(tmp_path) -> str:
    d = tmp_path / "templates"
    d.mkdir()
    (d / "default.md").write_text(
        "Hi {recruiter_name} at {company_name}. {opening_line} {resume_line} -{sender_name}"
    )
    (d / "sde.md").write_text(
        "SDE template for {recruiter_name} at {company_name}."
    )
    (d / "followup_1.md").write_text(
        "Following up with {recruiter_name} at {company_name}."
    )
    return str(d)
