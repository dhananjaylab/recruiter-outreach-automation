# FILE: recruiter_outreach/api/routers/outreach.py

"""
POST /send — the human-in-the-loop "Approve & Send" step. Takes a
preview_id from a prior POST /upload, optionally narrowed to
selected_emails (the rows the person actually approved), and streams
live progress back as Server-Sent Events while OutreachManager sends.

No background job queue: send_emails_concurrently() runs synchronously
in a worker thread, and its on_event callback is bridged to the HTTP
response stream via api/events.py's queue-based generator.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from recruiter_outreach.api.dependencies import get_db, get_settings
from recruiter_outreach.api.events import stream_progress
from recruiter_outreach.api.preview_store import PreviewStore, get_preview_store
from recruiter_outreach.api.schemas import SendRequest
from recruiter_outreach.config import Settings
from recruiter_outreach.db import Database
from recruiter_outreach.delivery.sender import OutreachManager

logger = logging.getLogger(__name__)
router = APIRouter(tags=["outreach"])


@router.post("/send")
def send(
    request: SendRequest,
    settings: Settings = Depends(get_settings),
    db: Database = Depends(get_db),
    store: PreviewStore = Depends(get_preview_store),
):
    entry = store.get(request.preview_id)
    if entry is None:
        raise HTTPException(
            status_code=404,
            detail="Preview not found or expired — upload the file again.",
        )

    df = entry.df
    if request.selected_emails is not None:
        selected = set(request.selected_emails)
        df = df[df["Email"].isin(selected)]
        if df.empty:
            raise HTTPException(status_code=400, detail="None of the selected emails matched the preview.")

    recruiters = df.to_dict("records")

    def run_fn(on_event):
        manager = OutreachManager(settings=settings, db=db, sequence_step=0, on_event=on_event)
        manager.send_emails_concurrently(recruiters)
        store.delete(request.preview_id)

    return StreamingResponse(stream_progress(run_fn), media_type="text/event-stream")
