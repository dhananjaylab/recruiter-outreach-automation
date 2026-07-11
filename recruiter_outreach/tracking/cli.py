# FILE: recruiter_outreach/tracking/cli.py

"""CLI entry point: scans the inbox for bounces/replies/unsubscribes."""

from __future__ import annotations

import argparse
import sys

from recruiter_outreach.config import load_settings
from recruiter_outreach.db import Database
from recruiter_outreach.logging_setup import get_logger, setup_logging
from recruiter_outreach.tracking.imap_tracker import InboxTracker


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check inbox for bounces/replies/unsubscribes."
    )
    parser.add_argument(
        "--since-days", type=int, default=14, help="How far back to scan (default: 14)"
    )
    parser.add_argument("--env-file", default=".env")
    args = parser.parse_args(argv)

    setup_logging()
    logger = get_logger(__name__)

    try:
        settings = load_settings(args.env_file)
    except ValueError as exc:
        logger.error(str(exc))
        return 1

    db = Database(settings.db_path)
    tracker = InboxTracker(settings, db)
    stats = tracker.check(since_days=args.since_days)
    logger.info(f"Inbox check complete: {stats}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
