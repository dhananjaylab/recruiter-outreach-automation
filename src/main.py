# FILE: src/main.py

"""
Entry point for Recruiter Outreach Automation.

Supported input flags
---------------------
  --csv   path/to/file.csv
  --tsv   path/to/file.tsv
  --xlsx  path/to/file.xlsx     (also accepts .xls, .xlsm, .ods)
  --pdf   path/to/file.pdf
  --json  path/to/file.json

All formats are handled by InputLoader which auto-detects encoding,
delimiter, sheet selection, PDF extraction tier, and column aliases.
"""

import argparse
import sys

from outreach import OutreachManager
from utils import ConfigLoader, InputLoader, Logger


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Recruiter Outreach Automation — sends personalised emails from a list.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python src/main.py --csv  recruiters.csv
  python src/main.py --xlsx HR_contacts.xlsx
  python src/main.py --pdf  HR_contacts.pdf
  python src/main.py --json export.json
        """,
    )

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--csv",  dest="file_path", metavar="FILE", help="CSV file (any delimiter, any encoding)")
    group.add_argument("--tsv",  dest="file_path", metavar="FILE", help="TSV (tab-separated) file")
    group.add_argument("--xlsx", dest="file_path", metavar="FILE", help="Excel file (.xlsx / .xls / .xlsm / .ods)")
    group.add_argument("--pdf",  dest="file_path", metavar="FILE", help="PDF with recruiter table or contact list")
    group.add_argument("--json", dest="file_path", metavar="FILE", help="JSON array or object with contacts list")

    parser.add_argument(
        "--no-llm",
        action="store_true",
        help="Disable LLM fallback for unstructured PDFs (no Anthropic API call)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Load and validate data, print preview, but do NOT send any emails",
    )
    parser.add_argument(
        "--save-csv",
        metavar="OUTPUT",
        help="Save the normalised recruiter list to a CSV file before sending",
    )

    return parser


def main():
    parser  = build_parser()
    args    = parser.parse_args()
    logger  = Logger(__name__)

    # ------------------------------------------------------------------ #
    # 1. Configuration                                                     #
    # ------------------------------------------------------------------ #
    try:
        config = ConfigLoader()
    except ValueError as exc:
        logger.error(str(exc))
        sys.exit(1)

    # ------------------------------------------------------------------ #
    # 2. Load recruiter data (universal — works for any supported format) #
    # ------------------------------------------------------------------ #
    loader = InputLoader(llm_fallback=not args.no_llm)
    try:
        df = loader.load(args.file_path)
    except FileNotFoundError as exc:
        logger.error(str(exc))
        sys.exit(1)
    except ValueError as exc:
        logger.error(f"Could not parse input file: {exc}")
        sys.exit(1)
    except Exception as exc:
        logger.error(f"Unexpected error loading '{args.file_path}': {exc}")
        sys.exit(1)

    if df.empty:
        logger.error("No valid recruiter records found. Exiting.")
        sys.exit(1)

    logger.info(f"Loaded {len(df)} recruiter records from '{args.file_path}'.")

    # ------------------------------------------------------------------ #
    # 3. Optional: save normalised CSV for audit / re-use                 #
    # ------------------------------------------------------------------ #
    if args.save_csv:
        df.to_csv(args.save_csv, index=False)
        logger.info(f"Normalised list saved to '{args.save_csv}'.")

    # ------------------------------------------------------------------ #
    # 4. Dry-run preview                                                  #
    # ------------------------------------------------------------------ #
    if args.dry_run:
        print("\n─── DRY RUN — no emails will be sent ───")
        print(df[["Name", "Email", "Company"]].to_string(index=False))
        print(f"\nTotal: {len(df)} records.")
        sys.exit(0)

    # ------------------------------------------------------------------ #
    # 5. Initialise OutreachManager and send                              #
    # ------------------------------------------------------------------ #
    try:
        manager = OutreachManager(config=config, logger=logger)
    except ValueError as exc:
        logger.error(f"Configuration error: {exc}")
        sys.exit(1)

    recruiters = df.to_dict("records")
    manager.send_emails_concurrently(recruiters)


if __name__ == "__main__":
    main()
