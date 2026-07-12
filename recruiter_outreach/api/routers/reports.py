# FILE: recruiter_outreach/api/routers/reports.py

from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse

from recruiter_outreach.api.dependencies import get_db, get_settings
from recruiter_outreach.api.schemas import DeliverabilityStats, ReportSummary
from recruiter_outreach.config import Settings
from recruiter_outreach.db import Database

router = APIRouter(prefix="/reports", tags=["reports"])


def _summarize_report_file(path: Path) -> ReportSummary:
    counts: Counter[str] = Counter()
    with path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            counts[row.get("status", "")] += 1

    stat = path.stat()
    return ReportSummary(
        filename=path.name,
        sent=counts.get("sent", 0),
        failed=counts.get("failed", 0),
        skipped=counts.get("skipped", 0),
        created_at=path.name.removeprefix("run_").removesuffix(".csv"),
        size_bytes=stat.st_size,
    )


@router.get("/deliverability", response_model=DeliverabilityStats)
def deliverability(db: Database = Depends(get_db)):
    """Aggregate bounce/reply rates across all recorded sends — the
    numbers that actually indicate whether sending reputation is
    healthy, as opposed to a single run's pass/fail counts."""
    return DeliverabilityStats(**db.deliverability_stats())


@router.get("", response_model=list[ReportSummary])
def list_reports(settings: Settings = Depends(get_settings)):
    reports_dir = Path(settings.reports_dir)
    if not reports_dir.exists():
        return []
    files = sorted(reports_dir.glob("run_*.csv"), reverse=True)
    return [_summarize_report_file(f) for f in files]


@router.get("/{filename}", response_class=PlainTextResponse)
def get_report(filename: str, settings: Settings = Depends(get_settings)):
    reports_dir = Path(settings.reports_dir).resolve()
    candidate = (reports_dir / filename).resolve()

    # Path-traversal guard: the resolved path must stay inside reports_dir.
    if reports_dir not in candidate.parents and candidate != reports_dir:
        raise HTTPException(status_code=400, detail="Invalid report filename.")
    if not candidate.exists() or candidate.suffix != ".csv":
        raise HTTPException(status_code=404, detail="Report not found.")

    return candidate.read_text(encoding="utf-8")
