# FILE: recruiter_outreach/api/routers/health.py

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends

import recruiter_outreach
from recruiter_outreach.api.dependencies import get_settings
from recruiter_outreach.api.schemas import HealthResponse
from recruiter_outreach.config import Settings

router = APIRouter(tags=["health"])


def get_settings_optional() -> Optional[Settings]:
    """Never raises — /health must stay up even before .env is configured,
    but still needs to go through Depends() so tests can override it the
    same way as every other settings-dependent route."""
    try:
        return get_settings()
    except Exception:
        return None


@router.get("/health", response_model=HealthResponse)
def health_check(settings: Optional[Settings] = Depends(get_settings_optional)):
    return HealthResponse(
        status="ok",
        version=recruiter_outreach.__version__,
        email_provider=settings.email_provider if settings else "unconfigured",
        mail_reader_provider=settings.mail_reader_provider if settings else "unconfigured",
    )
