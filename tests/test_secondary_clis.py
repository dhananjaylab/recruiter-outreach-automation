"""Tests for the secondary CLI entry points:
  - recruiter_outreach.tracking.cli  (recruiter-outreach-check-inbox)
  - recruiter_outreach.followup.cli  (recruiter-outreach-followups)
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from recruiter_outreach.followup.cli import main as followup_main
from recruiter_outreach.tracking.cli import main as tracking_main


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_settings():
    s = MagicMock()
    s.db_path      = ":memory:"
    s.reports_dir  = "reports"
    s.imap_server  = "imap.example.com"
    s.imap_port    = 993
    s.email_user   = "me@example.com"
    s.email_password = "secret"
    return s


# ---------------------------------------------------------------------------
# tracking/cli.py
# ---------------------------------------------------------------------------

class TestTrackingCli:
    def test_returns_0_on_success(self):
        mock_stats = {"bounces": 0, "replies": 1, "unsubscribes": 0, "scanned": 5}
        with patch("recruiter_outreach.tracking.cli.load_settings", return_value=_mock_settings()), \
             patch("recruiter_outreach.tracking.cli.Database"), \
             patch("recruiter_outreach.tracking.cli.InboxTracker") as MockTracker:
            MockTracker.return_value.check.return_value = mock_stats
            rc = tracking_main(["--since-days", "7", "--env-file", ".env"])
        assert rc == 0
        MockTracker.return_value.check.assert_called_once_with(since_days=7)

    def test_returns_1_when_settings_fail(self):
        with patch(
            "recruiter_outreach.tracking.cli.load_settings",
            side_effect=ValueError("missing key"),
        ):
            rc = tracking_main(["--env-file", "missing.env"])
        assert rc == 1

    def test_default_since_days_is_14(self):
        with patch("recruiter_outreach.tracking.cli.load_settings", return_value=_mock_settings()), \
             patch("recruiter_outreach.tracking.cli.Database"), \
             patch("recruiter_outreach.tracking.cli.InboxTracker") as MockTracker:
            MockTracker.return_value.check.return_value = {}
            tracking_main(["--env-file", ".env"])
        _, kwargs = MockTracker.return_value.check.call_args
        assert kwargs.get("since_days", None) == 14 or \
               MockTracker.return_value.check.call_args[0][0] == 14


# ---------------------------------------------------------------------------
# followup/cli.py
# ---------------------------------------------------------------------------

class TestFollowupCli:
    def test_returns_0_on_success(self):
        with patch("recruiter_outreach.followup.cli.load_settings", return_value=_mock_settings()), \
             patch("recruiter_outreach.followup.cli.Database"), \
             patch("recruiter_outreach.followup.cli.run_followups", return_value=[]) as mock_run:
            rc = followup_main(["--env-file", ".env"])
        assert rc == 0
        mock_run.assert_called_once()

    def test_returns_1_when_settings_fail(self):
        with patch(
            "recruiter_outreach.followup.cli.load_settings",
            side_effect=ValueError("bad config"),
        ):
            rc = followup_main(["--env-file", "missing.env"])
        assert rc == 1

    def test_passes_settings_and_db_to_run_followups(self):
        mock_s = _mock_settings()
        mock_db = MagicMock()

        with patch("recruiter_outreach.followup.cli.load_settings", return_value=mock_s), \
             patch("recruiter_outreach.followup.cli.Database", return_value=mock_db), \
             patch("recruiter_outreach.followup.cli.run_followups", return_value=[]) as mock_run:
            followup_main(["--env-file", ".env"])

        mock_run.assert_called_once_with(mock_s, mock_db)
