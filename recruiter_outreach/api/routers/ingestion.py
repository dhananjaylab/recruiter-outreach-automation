# FILE: recruiter_outreach/api/routers/ingestion.py

"""
Upload -> normalise -> preview. This is step one of the human-in-the-loop
flow: nothing is sent here. The normalised DataFrame is stashed in
PreviewStore keyed by a preview_id, which the person then reviews (and
can deselect rows for) before calling POST /send with that preview_id.
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile

from recruiter_outreach.api.dependencies import get_settings
from recruiter_outreach.api.preview_store import PreviewStore, get_preview_store
from recruiter_outreach.api.schemas import RecruiterRecord, UploadPreviewResponse
from recruiter_outreach.config import Settings
from recruiter_outreach.delivery.send_scheduler import SendWindowAdvisor
from recruiter_outreach.ingestion.loader import InputLoader

logger = logging.getLogger(__name__)
router = APIRouter(tags=["ingestion"])

_SUPPORTED_SUFFIXES = {".csv", ".tsv", ".xlsx", ".xls", ".xlsm", ".ods", ".pdf", ".json"}


@router.post("/upload", response_model=UploadPreviewResponse)
async def upload_and_preview(
    file: UploadFile,
    no_llm: bool = False,
    settings: Settings = Depends(get_settings),
    store: PreviewStore = Depends(get_preview_store),
):
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in _SUPPORTED_SUFFIXES:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '{suffix}'. Supported: {', '.join(sorted(_SUPPORTED_SUFFIXES))}",
        )

    contents = await file.read()
    max_bytes = settings.max_upload_size_mb * 1024 * 1024
    if len(contents) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds MAX_UPLOAD_SIZE_MB ({settings.max_upload_size_mb} MB).",
        )

    tmp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(contents)
            tmp_path = tmp.name

        loader = InputLoader(llm_fallback=not no_llm, anthropic_api_key=settings.anthropic_api_key)
        try:
            df = loader.load(tmp_path)
        except (FileNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        if tmp_path:
            Path(tmp_path).unlink(missing_ok=True)

    if df.empty:
        raise HTTPException(status_code=400, detail="No valid recruiter records found in the file.")

    preview_id = store.put(df, ttl_seconds=settings.preview_ttl_seconds)
    entry = store.get(preview_id)

    window_status = SendWindowAdvisor(settings).check()

    records = [
        RecruiterRecord(
            Name=row.get("Name", ""), Email=row.get("Email", ""),
            Company=row.get("Company", ""), Role=row.get("Role", ""),
            Scenario=row.get("Scenario", "cold"),
        )
        for row in df.to_dict("records")
    ]

    return UploadPreviewResponse(
        preview_id=preview_id,
        total_records=len(df),
        dropped_rows=getattr(loader, "_dropped", 0),
        records=records,
        expires_in_seconds=entry.expires_in_seconds if entry else 0,
        send_window_optimal=window_status.is_optimal,
        send_window_reason=window_status.reason,
        send_window_description=window_status.window_description,
    )
