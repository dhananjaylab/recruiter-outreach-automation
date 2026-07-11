# FILE: recruiter_outreach/followup/cli.py

"""CLI entry point: sends the next due follow-up email in the sequence."""

from __future__ import annotations

import argparse
import sys

from recruiter_outreach.config import load_settings
from recruiter_outreach.db import Database
from recruiter_outreach.followup.scheduler import run_followups
from recruiter_outreach.logging_setup import get_logger, setup_logging


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Send due follow-up emails.")
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
    run_followups(settings, db)
    return 0


if __name__ == "__main__":
    sys.exit(main())
