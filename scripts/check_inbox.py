#!/usr/bin/env python3
# FILE: scripts/check_inbox.py
"""Scans the mailbox for bounces, replies, and unsubscribe requests, and
updates the local database accordingly. Run periodically (e.g. every few
hours via cron) to keep follow-ups and the suppression list accurate.

Example cron entry (every 4 hours):
  0 */4 * * * cd /path/to/project && .venv/bin/python scripts/check_inbox.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from recruiter_outreach.tracking.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
