# FILE: recruiter_outreach/api/routers/followups.py

from __future__ import annotations

import logging
from collections import Counter

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from recruiter_outreach.api.dependencies import get_db, get_settings
from recruiter_outreach.api.events import stream_progress
from recruiter_outreach.api.schemas import FollowupDueGroup, FollowupDueResponse
from recruiter_outreach.config import Settings
from recruiter_outreach.db import Database
from recruiter_outreach.followup.scheduler import run_followups

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/followups", tags=["followups"])


@router.get("/due", response_model=FollowupDueResponse)
def due(settings: Settings = Depends(get_settings), db: Database = Depends(get_db)):
    rows = db.due_for_followup(delay_days=settings.followup_delay_days, max_step=settings.max_followups)
    counts = Counter(row["sequence_step"] + 1 for row in rows)
    return FollowupDueResponse(
        total_due=len(rows),
        groups=[FollowupDueGroup(sequence_step=step, count=n) for step, n in sorted(counts.items())],
    )


@router.post("/run")
def run(settings: Settings = Depends(get_settings), db: Database = Depends(get_db)):
    def run_fn(on_event):
        run_followups(settings, db, on_event=on_event)

    return StreamingResponse(stream_progress(run_fn), media_type="text/event-stream")
