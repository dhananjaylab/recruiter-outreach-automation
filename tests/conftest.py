"""Shared pytest fixtures."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from recruiter_outreach.db import Database  # noqa: E402


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
    (d / "sde.md").write_text("SDE template for {recruiter_name} at {company_name}.")
    (d / "followup_1.md").write_text("Following up with {recruiter_name} at {company_name}.")
    return str(d)
