"""Tests for the main CLI entry point (recruiter_outreach/cli.py).

Settings, InputLoader, and OutreachManager are all mocked so the tests
exercise argument parsing, error-handling, and control-flow without
requiring a real .env file, network connection, or SMTP server.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from recruiter_outreach.cli import main


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_VALID_DF = pd.DataFrame([
    {"Name": "Alice", "Email": "alice@corp.com", "Company": "Acme", "Role": "SDE"},
    {"Name": "Bob",   "Email": "bob@corp.com",   "Company": "Beta", "Role": "MLE"},
])

def _mock_settings(tmp_path):
    s = MagicMock()
    s.anthropic_api_key = None
    s.db_path            = str(tmp_path / "test.db")
    s.reports_dir        = str(tmp_path / "reports")
    return s


# ---------------------------------------------------------------------------
# Error paths: settings / loader failures
# ---------------------------------------------------------------------------

class TestCliErrorPaths:
    def test_returns_1_when_env_file_missing(self, tmp_path):
        rc = main(["--csv", "x.csv", "--env-file", str(tmp_path / "missing.env")])
        assert rc == 1

    def test_returns_1_when_load_settings_raises(self, tmp_path):
        with patch("recruiter_outreach.cli.load_settings", side_effect=ValueError("bad")):
            rc = main(["--csv", "x.csv", "--env-file", str(tmp_path / ".env")])
        assert rc == 1

    def test_returns_1_when_file_not_found(self, tmp_path, capsys):
        with patch("recruiter_outreach.cli.load_settings", return_value=_mock_settings(tmp_path)), \
             patch("recruiter_outreach.cli.InputLoader") as MockLoader:
            MockLoader.return_value.load.side_effect = FileNotFoundError("nope")
            rc = main(["--csv", "ghost.csv", "--env-file", ".env"])
        assert rc == 1

    def test_returns_1_when_loader_raises_value_error(self, tmp_path):
        with patch("recruiter_outreach.cli.load_settings", return_value=_mock_settings(tmp_path)), \
             patch("recruiter_outreach.cli.InputLoader") as MockLoader:
            MockLoader.return_value.load.side_effect = ValueError("bad format")
            rc = main(["--csv", "bad.csv", "--env-file", ".env"])
        assert rc == 1

    def test_returns_1_when_df_is_empty(self, tmp_path):
        with patch("recruiter_outreach.cli.load_settings", return_value=_mock_settings(tmp_path)), \
             patch("recruiter_outreach.cli.InputLoader") as MockLoader:
            MockLoader.return_value.load.return_value = pd.DataFrame()
            rc = main(["--csv", "empty.csv", "--env-file", ".env"])
        assert rc == 1

    def test_returns_1_when_manager_raises_value_error(self, tmp_path):
        with patch("recruiter_outreach.cli.load_settings", return_value=_mock_settings(tmp_path)), \
             patch("recruiter_outreach.cli.InputLoader") as MockLoader, \
             patch("recruiter_outreach.cli.Database"), \
             patch("recruiter_outreach.cli.OutreachManager", side_effect=ValueError("bad cfg")):
            MockLoader.return_value.load.return_value = _VALID_DF
            rc = main(["--csv", "ok.csv", "--env-file", ".env"])
        assert rc == 1


# ---------------------------------------------------------------------------
# Dry-run
# ---------------------------------------------------------------------------

class TestCliDryRun:
    def test_dry_run_prints_table_and_returns_0(self, tmp_path, capsys):
        with patch("recruiter_outreach.cli.load_settings", return_value=_mock_settings(tmp_path)), \
             patch("recruiter_outreach.cli.InputLoader") as MockLoader:
            MockLoader.return_value.load.return_value = _VALID_DF
            rc = main(["--csv", "ok.csv", "--dry-run", "--env-file", ".env"])

        assert rc == 0
        out = capsys.readouterr().out
        assert "DRY RUN" in out
        assert "alice@corp.com" in out

    def test_dry_run_does_not_instantiate_manager(self, tmp_path):
        with patch("recruiter_outreach.cli.load_settings", return_value=_mock_settings(tmp_path)), \
             patch("recruiter_outreach.cli.InputLoader") as MockLoader, \
             patch("recruiter_outreach.cli.OutreachManager") as MockMgr:
            MockLoader.return_value.load.return_value = _VALID_DF
            main(["--csv", "ok.csv", "--dry-run", "--env-file", ".env"])

        MockMgr.assert_not_called()

    def test_save_csv_written_on_dry_run(self, tmp_path):
        out_csv = tmp_path / "out.csv"
        with patch("recruiter_outreach.cli.load_settings", return_value=_mock_settings(tmp_path)), \
             patch("recruiter_outreach.cli.InputLoader") as MockLoader:
            MockLoader.return_value.load.return_value = _VALID_DF
            main([
                "--csv", "ok.csv", "--dry-run",
                "--save-csv", str(out_csv),
                "--env-file", ".env",
            ])
        assert out_csv.exists()
        assert "alice@corp.com" in out_csv.read_text()


# ---------------------------------------------------------------------------
# Happy path (live send mode)
# ---------------------------------------------------------------------------

class TestCliSendMode:
    def test_calls_manager_and_writes_report(self, tmp_path):
        from recruiter_outreach.reporting.report import RunReport

        mock_report = RunReport()
        mock_report.record_success("alice@corp.com")

        with patch("recruiter_outreach.cli.load_settings", return_value=_mock_settings(tmp_path)), \
             patch("recruiter_outreach.cli.InputLoader") as MockLoader, \
             patch("recruiter_outreach.cli.Database"), \
             patch("recruiter_outreach.cli.OutreachManager") as MockMgr, \
             patch("recruiter_outreach.cli.RunReport.to_csv") as mock_to_csv:
            MockLoader.return_value.load.return_value = _VALID_DF
            MockMgr.return_value.send_emails_concurrently.return_value = mock_report

            rc = main(["--csv", "ok.csv", "--env-file", ".env"])

        assert rc == 0
        MockMgr.return_value.send_emails_concurrently.assert_called_once()
        mock_to_csv.assert_called_once()

    def test_no_llm_flag_disables_llm_fallback(self, tmp_path):
        from recruiter_outreach.reporting.report import RunReport

        with patch("recruiter_outreach.cli.load_settings", return_value=_mock_settings(tmp_path)), \
             patch("recruiter_outreach.cli.InputLoader") as MockLoader, \
             patch("recruiter_outreach.cli.Database"), \
             patch("recruiter_outreach.cli.OutreachManager") as MockMgr, \
             patch("recruiter_outreach.cli.RunReport.to_csv"):
            MockLoader.return_value.load.return_value = _VALID_DF
            MockMgr.return_value.send_emails_concurrently.return_value = RunReport()

            main(["--csv", "ok.csv", "--no-llm", "--env-file", ".env"])

        # InputLoader should have been constructed with llm_fallback=False
        _, kwargs = MockLoader.call_args
        assert kwargs.get("llm_fallback") is False
