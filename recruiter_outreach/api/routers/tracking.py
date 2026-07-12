# FILE: recruiter_outreach/api/routers/tracking.py

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException

from recruiter_outreach.api.dependencies import get_db, get_settings
from recruiter_outreach.api.schemas import TrackingCheckResponse
from recruiter_outreach.auth.google_oauth import GoogleOAuthError
from recruiter_outreach.config import Settings
from recruiter_outreach.db import Database
from recruiter_outreach.tracking.imap_tracker import InboxTracker

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/tracking", tags=["tracking"])


@router.post("/check", response_model=TrackingCheckResponse)
def check(
    since_days: int = 14,
    settings: Settings = Depends(get_settings),
    db: Database = Depends(get_db),
):
    try:
        tracker = InboxTracker(settings, db)
        stats = tracker.check(since_days=since_days)
    except GoogleOAuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return TrackingCheckResponse(**stats)
