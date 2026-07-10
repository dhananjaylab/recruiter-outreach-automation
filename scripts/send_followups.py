#!/usr/bin/env python3
# FILE: scripts/send_followups.py
"""Sends the next follow-up in the sequence to recruiters who haven't
replied or bounced. Run once daily via cron, ideally shortly after
check_inbox.py so replies/bounces are picked up first.

Example cron entry (daily at 10am, after an inbox check at 9am):
  0 9  * * * cd /path/to/project && .venv/bin/python scripts/check_inbox.py
  0 10 * * * cd /path/to/project && .venv/bin/python scripts/send_followups.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from recruiter_outreach.followup.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
