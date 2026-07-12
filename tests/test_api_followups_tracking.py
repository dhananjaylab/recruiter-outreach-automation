"""Tests for routers/followups.py's POST /run (SSE) and
routers/tracking.py's POST /check."""

from __future__ import annotations

from unittest.mock import patch

from recruiter_outreach.auth.google_oauth import GoogleOAuthError
from recruiter_outreach.delivery.transport import ProgressEvent
from tests.api_helpers import make_client


class TestFollowupsRun:
    def test_streams_run_complete_event(self, tmp_path, db):
        client = make_client(tmp_path, db)

        def fake_run_followups(settings, db, on_event=None):
            if on_event:
                on_event(ProgressEvent(status="run_complete", extra={"sent": 0}))
            return []

        with patch(
            "recruiter_outreach.api.routers.followups.run_followups",
            side_effect=fake_run_followups,
        ):
            resp = client.post("/followups/run")

        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        assert "event: run_complete" in resp.text

    def test_error_surfaces_as_sse_error_event(self, tmp_path, db):
        client = make_client(tmp_path, db)

        with patch(
            "recruiter_outreach.api.routers.followups.run_followups",
            side_effect=RuntimeError("boom"),
        ):
            resp = client.post("/followups/run")

        assert resp.status_code == 200
        assert "event: error" in resp.text
        assert "boom" in resp.text


class TestTrackingCheck:
    def test_returns_stats_on_success(self, tmp_path, db):
        client = make_client(tmp_path, db)
        mock_stats = {"bounces": 1, "replies": 2, "unsubscribes": 0, "scanned": 10}

        with patch("recruiter_outreach.api.routers.tracking.InboxTracker") as MockTracker:
            MockTracker.return_value.check.return_value = mock_stats
            resp = client.post("/tracking/check?since_days=7")

        assert resp.status_code == 200
        assert resp.json() == mock_stats

    def test_returns_400_when_not_connected(self, tmp_path, db):
        client = make_client(tmp_path, db)

        with patch(
            "recruiter_outreach.api.routers.tracking.InboxTracker",
            side_effect=GoogleOAuthError("not connected"),
        ):
            resp = client.post("/tracking/check")

        assert resp.status_code == 400

    def test_default_since_days_is_14(self, tmp_path, db):
        client = make_client(tmp_path, db)
        with patch("recruiter_outreach.api.routers.tracking.InboxTracker") as MockTracker:
            MockTracker.return_value.check.return_value = {
                "bounces": 0, "replies": 0, "unsubscribes": 0, "scanned": 0,
            }
            client.post("/tracking/check")
        MockTracker.return_value.check.assert_called_once_with(since_days=14)
