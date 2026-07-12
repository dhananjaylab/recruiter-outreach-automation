# FILE: recruiter_outreach/api/routers/suppressions.py

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response

from recruiter_outreach.api.dependencies import get_db
from recruiter_outreach.api.schemas import SuppressionCreateRequest, SuppressionEntry
from recruiter_outreach.db import Database

router = APIRouter(prefix="/suppressions", tags=["suppressions"])


@router.get("", response_model=list[SuppressionEntry])
def list_suppressions(db: Database = Depends(get_db)):
    rows = db.list_suppressions()
    return [SuppressionEntry(email=r["email"], reason=r["reason"], added_at=r["added_at"]) for r in rows]


@router.post("", response_model=SuppressionEntry, status_code=201)
def add_suppression(body: SuppressionCreateRequest, db: Database = Depends(get_db)):
    db.suppress(body.email, reason=body.reason)
    return SuppressionEntry(email=body.email, reason=body.reason, added_at="")


@router.delete("/{email}", status_code=204)
def remove_suppression(email: str, db: Database = Depends(get_db)):
    removed = db.remove_suppression(email)
    if not removed:
        raise HTTPException(status_code=404, detail=f"'{email}' is not on the suppression list.")
    return Response(status_code=204)
