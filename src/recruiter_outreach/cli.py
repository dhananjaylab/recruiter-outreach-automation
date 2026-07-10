# FILE: src/recruiter_outreach/cli.py

"""
Entry point for Recruiter Outreach Automation.

Supported input flags
---------------------
  --csv   path/to/file.csv
  --tsv   path/to/file.tsv
  --xlsx  path/to/file.xlsx     (also accepts .xls, .xlsm, .ods)
  --pdf   path/to/file.pdf
  --json  path/to/file.json

All formats are handled by InputLoader, which auto-detects encoding,
delimiter, sheet selection, PDF extraction tier, and column aliases.

Every send goes through persistent dedup/suppression checks, optional
MX/SMTP verification, and warm-up-aware rate limiting — see README.md.
"""

from __future__ import annotations

import argparse
import sys

from recruiter_outreach.config import load_settings
from recruiter_outreach.db import Database
from recruiter_outreach.delivery.sender import OutreachManager
from recruiter_outreach.ingestion.loader import InputLoader
from recruiter_outreach.logging_setup import get_logger, setup_logging
from recruiter_outreach.reporting.report import RunReport


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Recruiter Outreach Automation — sends personalised emails from a list.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  recruiter-outreach --csv  recruiters.csv
  recruiter-outreach --xlsx HR_contacts.xlsx
  recruiter-outreach --pdf  HR_contacts.pdf
  recruiter-outreach --json export.json --dry-run
        """,
    )

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--csv", dest="file_path", metavar="FILE", help="CSV file (any delimiter, any encoding)")
    group.add_argument("--tsv", dest="file_path", metavar="FILE", help="TSV (tab-separated) file")
    group.add_argument("--xlsx", dest="file_path", metavar="FILE", help="Excel file (.xlsx/.xls/.xlsm/.ods)")
    group.add_argument("--pdf", dest="file_path", metavar="FILE", help="PDF with recruiter table or contact list")
    group.add_argument("--json", dest="file_path", metavar="FILE", help="JSON array or object with a contacts list")

    parser.add_argument("--no-llm", action="store_true", help="Disable LLM fallback for unstructured PDFs")
    parser.add_argument("--dry-run", action="store_true", help="Load and validate data only; send nothing")
    parser.add_argument("--save-csv", metavar="OUTPUT", help="Save the normalised recruiter list to a CSV file")
    parser.add_argument("--env-file", default=".env", metavar="PATH", help="Path to .env file (default: .env)")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    setup_logging()
    logger = get_logger(__name__)

    try:
        settings = load_settings(args.env_file)
    except ValueError as exc:
        logger.error(str(exc))
        return 1

    loader = InputLoader(llm_fallback=not args.no_llm, anthropic_api_key=settings.anthropic_api_key)
    try:
        df = loader.load(args.file_path)
    except (FileNotFoundError, ValueError) as exc:
        logger.error(str(exc))
        return 1
    except Exception as exc:
        logger.error(f"Unexpected error loading '{args.file_path}': {exc}")
        return 1

    if df.empty:
        logger.error("No valid recruiter records found. Exiting.")
        return 1

    logger.info(f"Loaded {len(df)} recruiter records from '{args.file_path}'.")

    if args.save_csv:
        df.to_csv(args.save_csv, index=False)
        logger.info(f"Normalised list saved to '{args.save_csv}'.")

    if args.dry_run:
        print("\n--- DRY RUN: no emails will be sent ---")
        cols = [c for c in ["Name", "Email", "Company", "Role"] if c in df.columns]
        print(df[cols].to_string(index=False))
        print(f"\nTotal: {len(df)} records.")
        return 0

    db = Database(settings.db_path)
    try:
        manager = OutreachManager(settings=settings, db=db, sequence_step=0)
    except ValueError as exc:
        logger.error(f"Configuration error: {exc}")
        return 1

    recruiters = df.to_dict("records")
    report: RunReport = manager.send_emails_concurrently(recruiters)

    report_path = RunReport.default_path(settings.reports_dir)
    report.to_csv(report_path)
    logger.info(f"Run report saved to '{report_path}'.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
