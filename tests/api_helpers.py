"""Shared helpers for API router tests. Not collected by pytest (no
test_ prefix) — imported by the actual test modules."""

from __future__ import annotations

from fastapi.testclient import TestClient

from recruiter_outreach.api.dependencies import get_db, get_settings
from recruiter_outreach.api.main import app
from recruiter_outreach.api.routers.health import get_settings_optional
from recruiter_outreach.config import Settings
from recruiter_outreach.db import Database


def make_settings(tmp_path, **overrides) -> Settings:
    base = dict(
        EMAIL_USER="me@example.com",
        RESUME_LINK="https://example.com/cv",
        EMAIL_TEMPLATE_DIR="email_templates",
        DB_PATH=str(tmp_path / "test.db"),
        REPORTS_DIR=str(tmp_path / "reports"),
        GOOGLE_CLIENT_SECRETS_PATH=str(tmp_path / "client_secret.json"),
        GOOGLE_TOKEN_PATH=str(tmp_path / "token.json"),
    )
    base.update(overrides)
    return Settings(**base)


def make_client(tmp_path, db: Database, **setting_overrides) -> TestClient:
    """Resets dependency overrides each call so tests never see stale
    settings/db from a previous test module (app is a module-level
    singleton shared across the whole test session).

    get_settings_optional (used only by /health) is overridden separately
    since it deliberately swallows get_settings' exceptions internally
    rather than chaining through Depends(get_settings) — FastAPI can't
    intercept a plain in-body function call via dependency_overrides."""
    settings = make_settings(tmp_path, **setting_overrides)
    app.dependency_overrides = {
        get_settings: lambda: settings,
        get_db: lambda: db,
        get_settings_optional: lambda: settings,
    }
    return TestClient(app)
