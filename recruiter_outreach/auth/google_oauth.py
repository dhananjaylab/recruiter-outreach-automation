# FILE: recruiter_outreach/auth/google_oauth.py

"""
Google OAuth2 (web application flow) for Gmail API access.

Replaces SMTP app-password auth entirely for the default provider: no
password is stored anywhere. A one-time browser consent grants a refresh
token, which is persisted locally (GOOGLE_TOKEN_PATH) and silently
refreshed thereafter — the same pattern the Gmail API quickstart uses,
adapted for a server (FastAPI) redirect flow instead of a desktop
`run_local_server()` loopback, since InstalledAppFlow assumes a local
browser on the same machine as the process, which doesn't hold for a
web app.

Scopes requested:
  - gmail.send      -> deliver outreach emails and follow-ups
  - gmail.readonly  -> scan the inbox for bounces/replies (tracking)
  - gmail.modify    -> mark tracked messages as read (label changes only;
                        never delete/gmail.https://mail.google.com/ full scope)
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from recruiter_outreach.config import Settings

logger = logging.getLogger(__name__)

GOOGLE_OAUTH_SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
]


@dataclass
class GoogleAuthState:
    connected: bool
    email: Optional[str] = None
    scopes: Optional[list[str]] = None
    expiry: Optional[str] = None


class GoogleOAuthError(Exception):
    """Raised for any unrecoverable OAuth/credentials problem."""


def _build_flow(settings: Settings, state: Optional[str] = None) -> Flow:
    secrets_path = Path(settings.google_client_secrets_path)
    if not secrets_path.exists():
        raise GoogleOAuthError(
            f"Google OAuth client secrets not found at '{secrets_path}'. "
            "Download an OAuth 2.0 Client ID (type: Web application) from "
            "Google Cloud Console -> APIs & Services -> Credentials, add "
            f"'{settings.google_oauth_redirect_uri}' as an authorized "
            "redirect URI, and save the JSON there."
        )
    return Flow.from_client_secrets_file(
        str(secrets_path),
        scopes=GOOGLE_OAUTH_SCOPES,
        redirect_uri=settings.google_oauth_redirect_uri,
        state=state,
    )


def build_authorization_url(settings: Settings) -> tuple[str, str]:
    """Returns (authorization_url, state). The caller (FastAPI router)
    should send the person to authorization_url and keep `state` around
    to validate the callback (CSRF protection, per the OAuth2 spec)."""
    flow = _build_flow(settings)
    authorization_url, state = flow.authorization_url(
        access_type="offline",       # required to receive a refresh_token
        include_granted_scopes="true",
        prompt="consent",            # forces refresh_token even on re-auth
    )
    return authorization_url, state


def exchange_code_for_credentials(settings: Settings, code: str, state: str) -> Credentials:
    """Exchanges the authorization code from the OAuth callback for
    credentials, persists them, and returns them."""
    flow = _build_flow(settings, state=state)
    flow.fetch_token(code=code)
    creds = flow.credentials
    _save_credentials(settings.google_token_path, creds)
    logger.info("Google OAuth: credentials saved to %s", settings.google_token_path)
    return creds


def load_credentials(settings: Settings) -> Optional[Credentials]:
    """Loads persisted credentials, refreshing the access token if expired.
    Returns None if never connected or the refresh token was revoked."""
    token_path = Path(settings.google_token_path)
    if not token_path.exists():
        return None

    try:
        creds = Credentials.from_authorized_user_file(str(token_path), GOOGLE_OAUTH_SCOPES)
    except (ValueError, json.JSONDecodeError) as exc:
        logger.warning("Stored Google credentials unreadable, ignoring: %s", exc)
        return None

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            _save_credentials(settings.google_token_path, creds)
            return creds
        except Exception as exc:
            logger.warning("Google token refresh failed — re-auth required: %s", exc)
            return None

    return None


def revoke_credentials(settings: Settings) -> bool:
    """Deletes the local token file. Best-effort revoke against Google's
    endpoint too, so the grant disappears from the user's Google Account
    permissions page. Returns True if a token was present and removed."""
    token_path = Path(settings.google_token_path)
    if not token_path.exists():
        return False

    try:
        creds = Credentials.from_authorized_user_file(str(token_path), GOOGLE_OAUTH_SCOPES)
        import requests as _requests

        _requests.post(
            "https://oauth2.googleapis.com/revoke",
            params={"token": creds.token},
            headers={"content-type": "application/x-www-form-urlencoded"},
            timeout=10,
        )
    except Exception as exc:
        logger.debug("Best-effort Google token revoke failed (continuing): %s", exc)

    token_path.unlink()
    return True


def get_auth_state(settings: Settings) -> GoogleAuthState:
    """Human-facing connection status: which Gmail account is connected,
    what scopes were granted. Used by /auth/google/status and the
    Streamlit sidebar so the person always knows what they've authorized."""
    creds = load_credentials(settings)
    if not creds:
        return GoogleAuthState(connected=False)

    email = None
    try:
        service = build("gmail", "v1", credentials=creds, cache_discovery=False)
        profile = service.users().getProfile(userId="me").execute()
        email = profile.get("emailAddress")
    except HttpError as exc:
        logger.warning("Could not fetch Gmail profile for status check: %s", exc)

    return GoogleAuthState(
        connected=True,
        email=email,
        scopes=list(creds.scopes) if creds.scopes else None,
        expiry=creds.expiry.isoformat() if creds.expiry else None,
    )


def _save_credentials(token_path: str, creds: Credentials) -> None:
    path = Path(token_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(creds.to_json(), encoding="utf-8")
