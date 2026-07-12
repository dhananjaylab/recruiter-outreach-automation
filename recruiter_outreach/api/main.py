# FILE: recruiter_outreach/api/main.py

"""
FastAPI application entry point.

Run with:
    uvicorn recruiter_outreach.api.main:app --reload --port 8000

Single-user, no-auth, locally-run tool (see README) — CORS is wide open
so the Streamlit frontend (a different port) can call it freely.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from recruiter_outreach.api.routers import (
    auth,
    followups,
    health,
    ingestion,
    outreach,
    reports,
    suppressions,
    tracking,
)
from recruiter_outreach.logging_setup import setup_logging

setup_logging()

app = FastAPI(
    title="Recruiter Outreach Automation API",
    description=(
        "REST + SSE layer over the recruiter_outreach package: upload a "
        "recruiter list, preview it, approve and send with live progress, "
        "manage follow-ups, track bounces/replies, and review reports."
    ),
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(auth.router)
app.include_router(ingestion.router)
app.include_router(outreach.router)
app.include_router(followups.router)
app.include_router(tracking.router)
app.include_router(suppressions.router)
app.include_router(reports.router)
