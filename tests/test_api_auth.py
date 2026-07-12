"""Tests for routers/auth.py — Google OAuth2 login/callback/status/logout.

The underlying auth/google_oauth.py functions are mocked at their import
site in the router module — no real OAuth flow or network call happens.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from recruiter_outreach.auth.google_oauth import GoogleAuthState, GoogleOAuthError
from tests.api_helpers import make_client


class TestLogin:
    def test_redirects_to_google_authorization_url(self, tmp_path, db):
        client = make_client(tmp_path, db)
        with patch(
            "recruiter_outreach.api.routers.auth.build_authorization_url",
            return_value=("https://accounts.google.com/o/oauth2/auth?x=1", "state123"),
        ):
            resp = client.get("/auth/google/login", follow_redirects=False)
        assert resp.status_code in (302, 307)
        assert resp.headers["location"].startswith("https://accounts.google.com")

    def test_returns_400_when_secrets_missing(self, tmp_path, db):
        client = make_client(tmp_path, db)
        with patch(
            "recruiter_outreach.api.routers.auth.build_authorization_url",
            side_effect=GoogleOAuthError("client secrets not found"),
        ):
            resp = client.get("/auth/google/login", follow_redirects=False)
        assert resp.status_code == 400


class TestCallback:
    def test_exchanges_code_and_returns_success_page(self, tmp_path, db):
        client = make_client(tmp_path, db)
        with patch(
            "recruiter_outreach.api.routers.auth.build_authorization_url",
            return_value=("https://accounts.google.com/auth", "state123"),
        ):
            client.get("/auth/google/login", follow_redirects=False)

        with patch(
            "recruiter_outreach.api.routers.auth.exchange_code_for_credentials",
            return_value=MagicMock(),
        ) as mock_exchange:
            resp = client.get("/auth/google/callback?code=abc&state=state123")

        assert resp.status_code == 200
        assert "connected" in resp.text.lower()
        mock_exchange.assert_called_once()

    def test_rejects_mismatched_state(self, tmp_path, db):
        client = make_client(tmp_path, db)
        with patch(
            "recruiter_outreach.api.routers.auth.build_authorization_url",
            return_value=("https://accounts.google.com/auth", "state123"),
        ):
            client.get("/auth/google/login", follow_redirects=False)

        resp = client.get("/auth/google/callback?code=abc&state=WRONG")
        assert resp.status_code == 400


class TestStatus:
    def test_reports_not_connected(self, tmp_path, db):
        client = make_client(tmp_path, db)
        with patch(
            "recruiter_outreach.api.routers.auth.get_auth_state",
            return_value=GoogleAuthState(connected=False),
        ):
            resp = client.get("/auth/google/status")
        assert resp.status_code == 200
        assert resp.json()["connected"] is False

    def test_reports_connected_with_email(self, tmp_path, db):
        client = make_client(tmp_path, db)
        with patch(
            "recruiter_outreach.api.routers.auth.get_auth_state",
            return_value=GoogleAuthState(connected=True, email="me@gmail.com", scopes=["gmail.send"]),
        ):
            resp = client.get("/auth/google/status")
        assert resp.json()["connected"] is True
        assert resp.json()["email"] == "me@gmail.com"


class TestLogout:
    def test_revokes_and_reports_result(self, tmp_path, db):
        client = make_client(tmp_path, db)
        with patch("recruiter_outreach.api.routers.auth.revoke_credentials", return_value=True):
            resp = client.post("/auth/google/logout")
        assert resp.status_code == 200
        assert resp.json()["revoked"] is True
