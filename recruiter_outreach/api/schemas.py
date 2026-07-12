# FILE: recruiter_outreach/api/schemas.py

"""Pydantic request/response models for the FastAPI layer."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str
    version: str
    email_provider: str
    mail_reader_provider: str


class RecruiterRecord(BaseModel):
    Name: str
    Email: str
    Company: str
    Role: str = ""
    Scenario: str = "cold"


class UploadPreviewResponse(BaseModel):
    preview_id: str
    total_records: int
    dropped_rows: int
    records: list[RecruiterRecord]
    expires_in_seconds: int
    send_window_optimal: bool
    send_window_reason: str
    send_window_description: str


class SendRequest(BaseModel):
    preview_id: str
    selected_emails: Optional[list[str]] = Field(
        default=None,
        description="If provided, only these emails from the preview are sent "
        "(the approve/reject step). Omit to send the full preview.",
    )


class FollowupDueGroup(BaseModel):
    sequence_step: int
    count: int


class FollowupDueResponse(BaseModel):
    total_due: int
    groups: list[FollowupDueGroup]


class TrackingCheckResponse(BaseModel):
    bounces: int
    replies: int
    unsubscribes: int
    scanned: int


class SuppressionEntry(BaseModel):
    email: str
    reason: str
    added_at: str


class SuppressionCreateRequest(BaseModel):
    email: str
    reason: str = "manual"


class ReportSummary(BaseModel):
    filename: str
    sent: int
    failed: int
    skipped: int
    created_at: str
    size_bytes: int


class DeliverabilityStats(BaseModel):
    total_sent: int
    total_bounced: int
    total_replied: int
    bounce_rate: float
    reply_rate: float


class GoogleAuthStatusResponse(BaseModel):
    connected: bool
    email: Optional[str] = None
    scopes: Optional[list[str]] = None
    expiry: Optional[str] = None
