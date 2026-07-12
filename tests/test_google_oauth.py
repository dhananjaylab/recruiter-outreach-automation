"""Tests for auth/google_oauth.py and tracking/mail_reader.py's Gmail
implementation. Google's own libraries (Flow, Credentials, googleapiclient
build) are mocked throughout — no real network/OAuth is exercised."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from recruiter_outreach.auth.google_oauth import (
    GoogleOAuthError,
    build_authorization_url,
    exchange_code_for_credentials,
    get_auth_state,
    load_credentials,
    revoke_credentials,
)
from recruiter_outreach.config import Settings


def _settings(tmp_path, **overrides) -> Settings:
    base = dict(
        EMAIL_USER="me@example.com",
        RESUME_LINK="https://example.com/cv",
        GOOGLE_CLIENT_SECRETS_PATH=str(tmp_path / "client_secret.json"),
        GOOGLE_TOKEN_PATH=str(tmp_path / "token.json"),
        DB_PATH=str(tmp_path / "test.db"),
        REPORTS_DIR="reports",
    )
    base.update(overrides)
    return Settings(**base)


def _write_fake_secrets(tmp_path) -> None:
    (tmp_path / "client_secret.json").write_text(json.dumps({
        "web": {
            "client_id": "abc",
            "client_secret": "shh",
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    }))


# ---------------------------------------------------------------------------
# build_authorization_url
# ---------------------------------------------------------------------------

class TestBuildAuthorizationUrl:
    def test_raises_when_secrets_file_missing(self, tmp_path):
        s = _settings(tmp_path)
        with pytest.raises(GoogleOAuthError, match="client secrets not found"):
            build_authorization_url(s)

    def test_returns_url_and_state(self, tmp_path):
        _write_fake_secrets(tmp_path)
        s = _settings(tmp_path)
        mock_flow = MagicMock()
        mock_flow.authorization_url.return_value = ("https://accounts.google.com/auth?x=1", "state123")
        with patch(
            "recruiter_outreach.auth.google_oauth.Flow.from_client_secrets_file",
            return_value=mock_flow,
        ):
            url, state = build_authorization_url(s)
        assert url == "https://accounts.google.com/auth?x=1"
        assert state == "state123"
        mock_flow.authorization_url.assert_called_once_with(
            access_type="offline", include_granted_scopes="true", prompt="consent",
        )


# ---------------------------------------------------------------------------
# exchange_code_for_credentials
# ---------------------------------------------------------------------------

class TestExchangeCode:
    def test_saves_credentials_after_fetch_token(self, tmp_path):
        _write_fake_secrets(tmp_path)
        s = _settings(tmp_path)
        mock_creds = MagicMock()
        mock_creds.to_json.return_value = '{"token": "abc"}'
        mock_flow = MagicMock()
        mock_flow.credentials = mock_creds

        with patch(
            "recruiter_outreach.auth.google_oauth.Flow.from_client_secrets_file",
            return_value=mock_flow,
        ):
            creds = exchange_code_for_credentials(s, code="authcode", state="state123")

        assert creds is mock_creds
        mock_flow.fetch_token.assert_called_once_with(code="authcode")
        assert (tmp_path / "token.json").read_text() == '{"token": "abc"}'


# ---------------------------------------------------------------------------
# load_credentials
# ---------------------------------------------------------------------------

class TestLoadCredentials:
    def test_returns_none_when_no_token_file(self, tmp_path):
        s = _settings(tmp_path)
        assert load_credentials(s) is None

    def test_returns_credentials_when_valid(self, tmp_path):
        s = _settings(tmp_path)
        (tmp_path / "token.json").write_text('{"token": "x"}')
        mock_creds = MagicMock(valid=True)
        with patch(
            "recruiter_outreach.auth.google_oauth.Credentials.from_authorized_user_file",
            return_value=mock_creds,
        ):
            result = load_credentials(s)
        assert result is mock_creds

    def test_refreshes_expired_credentials(self, tmp_path):
        s = _settings(tmp_path)
        (tmp_path / "token.json").write_text('{"token": "x"}')
        mock_creds = MagicMock(valid=False, expired=True, refresh_token="rt")
        mock_creds.to_json.return_value = '{"token": "refreshed"}'

        with patch(
            "recruiter_outreach.auth.google_oauth.Credentials.from_authorized_user_file",
            return_value=mock_creds,
        ), patch("recruiter_outreach.auth.google_oauth.Request"):
            result = load_credentials(s)

        assert result is mock_creds
        mock_creds.refresh.assert_called_once()
        assert (tmp_path / "token.json").read_text() == '{"token": "refreshed"}'

    def test_returns_none_when_refresh_fails(self, tmp_path):
        s = _settings(tmp_path)
        (tmp_path / "token.json").write_text('{"token": "x"}')
        mock_creds = MagicMock(valid=False, expired=True, refresh_token="rt")
        mock_creds.refresh.side_effect = Exception("revoked")

        with patch(
            "recruiter_outreach.auth.google_oauth.Credentials.from_authorized_user_file",
            return_value=mock_creds,
        ):
            result = load_credentials(s)
        assert result is None

    def test_returns_none_on_unreadable_token_file(self, tmp_path):
        s = _settings(tmp_path)
        (tmp_path / "token.json").write_text("not json")
        with patch(
            "recruiter_outreach.auth.google_oauth.Credentials.from_authorized_user_file",
            side_effect=ValueError("bad token"),
        ):
            result = load_credentials(s)
        assert result is None


# ---------------------------------------------------------------------------
# revoke_credentials
# ---------------------------------------------------------------------------

class TestRevokeCredentials:
    def test_returns_false_when_nothing_to_revoke(self, tmp_path):
        s = _settings(tmp_path)
        assert revoke_credentials(s) is False

    def test_deletes_token_file(self, tmp_path):
        s = _settings(tmp_path)
        (tmp_path / "token.json").write_text('{"token": "x"}')
        mock_creds = MagicMock(token="tok123")
        with patch(
            "recruiter_outreach.auth.google_oauth.Credentials.from_authorized_user_file",
            return_value=mock_creds,
        ), patch("requests.post"):
            result = revoke_credentials(s)
        assert result is True
        assert not (tmp_path / "token.json").exists()


# ---------------------------------------------------------------------------
# get_auth_state
# ---------------------------------------------------------------------------

class TestGetAuthState:
    def test_not_connected_when_no_credentials(self, tmp_path):
        s = _settings(tmp_path)
        state = get_auth_state(s)
        assert state.connected is False

    def test_connected_includes_email(self, tmp_path):
        s = _settings(tmp_path)
        (tmp_path / "token.json").write_text('{"token": "x"}')
        mock_creds = MagicMock(valid=True, scopes=["gmail.send"], expiry=None)
        mock_service = MagicMock()
        mock_service.users().getProfile().execute.return_value = {"emailAddress": "me@gmail.com"}

        with patch(
            "recruiter_outreach.auth.google_oauth.Credentials.from_authorized_user_file",
            return_value=mock_creds,
        ), patch("recruiter_outreach.auth.google_oauth.build", return_value=mock_service):
            state = get_auth_state(s)

        assert state.connected is True
        assert state.email == "me@gmail.com"
