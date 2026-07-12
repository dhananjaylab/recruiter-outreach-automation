# FILE: recruiter_outreach/api/routers/auth.py

"""
Google OAuth2 web flow endpoints. Single-user, no-auth scope (see
project decisions) — the pending `state` value is held in a module-level
variable rather than a session store, which is fine for one person
running this locally but would need revisiting for multi-user deployment.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse

from recruiter_outreach.api.dependencies import get_settings
from recruiter_outreach.api.schemas import GoogleAuthStatusResponse
from recruiter_outreach.auth.google_oauth import (
    GoogleOAuthError,
    build_authorization_url,
    exchange_code_for_credentials,
    get_auth_state,
    revoke_credentials,
)
from recruiter_outreach.config import Settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth/google", tags=["auth"])

_pending_state: str | None = None


@router.get("/login")
def login(settings: Settings = Depends(get_settings)):
    global _pending_state
    try:
        authorization_url, state = build_authorization_url(settings)
    except GoogleOAuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _pending_state = state
    return RedirectResponse(authorization_url)


@router.get("/callback", response_class=HTMLResponse)
def callback(
    code: str = Query(...),
    state: str = Query(...),
    settings: Settings = Depends(get_settings),
):
    global _pending_state
    if _pending_state is None or state != _pending_state:
        raise HTTPException(
            status_code=400,
            detail="OAuth state mismatch — start the connection again from /auth/google/login.",
        )
    _pending_state = None

    try:
        exchange_code_for_credentials(settings, code=code, state=state)
    except GoogleOAuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return (
        "<html><body style='font-family: sans-serif; text-align:center; padding-top: 4rem;'>"
        "<h2>Gmail connected ✅</h2>"
        "<p>You can close this tab and return to the app.</p>"
        "</body></html>"
    )


@router.get("/status", response_model=GoogleAuthStatusResponse)
def status(settings: Settings = Depends(get_settings)):
    state = get_auth_state(settings)
    return GoogleAuthStatusResponse(
        connected=state.connected, email=state.email, scopes=state.scopes, expiry=state.expiry,
    )


@router.post("/logout")
def logout(settings: Settings = Depends(get_settings)):
    revoked = revoke_credentials(settings)
    return {"revoked": revoked}
