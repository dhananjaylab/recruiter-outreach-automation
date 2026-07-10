#!/usr/bin/env python3
# FILE: scripts/send_outreach.py
"""Convenience wrapper — run this directly from a git clone without
installing the package. See recruiter_outreach.cli for the implementation."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from recruiter_outreach.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
