# FILE: src/main.py

import argparse
import pandas as pd
import sys

from outreach import OutreachManager
from utils import ConfigLoader, Logger


def main():
    """
    Main function to orchestrate the recruiter outreach automation.
    """
    # Initialize argument parser
    parser = argparse.ArgumentParser(description="Recruiter Outreach Automation Script")
    # Create a mutually exclusive group for PDF or CSV
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--pdf", dest="pdf_file_path", help="Path to the PDF file containing recruiter data")
    group.add_argument("--csv", dest="csv_file_path", help="Path to the CSV file containing recruiter data")
    
    args = parser.parse_args()

    # Initialize ConfigLoader and Logger
    logger = Logger(__name__)
    try:
        config = ConfigLoader()
        # Initialize the OutreachManager
        outreach_manager = OutreachManager(config=config, logger=logger)
    except ValueError as e:
        logger.error(f"Configuration error: {e}")
        sys.exit(1)


    recruiters_df = None

    if args.pdf_file_path:
        logger.info(f"Loading recruiters from PDF: {args.pdf_file_path}")
        recruiters_df = outreach_manager.load_recruiters_from_pdf(args.pdf_file_path)
    
    elif args.csv_file_path:
        logger.info(f"Loading recruiters from CSV: {args.csv_file_path}")
        try:
            recruiters_df = pd.read_csv(args.csv_file_path)
        except FileNotFoundError:
            logger.error(f"CSV file not found at: {args.csv_file_path}")
            sys.exit(1)
        except pd.errors.EmptyDataError:
            logger.error(f"CSV file is empty: {args.csv_file_path}")
            sys.exit(1)

    if recruiters_df is not None and not recruiters_df.empty:
        # Standardize column names to be safe
        recruiters_df.rename(columns={
            'recruiter_name': 'Name',
            'company_name': 'Company',
            'recruiter_email': 'Email'
        }, inplace=True)

        try:
            # Validate required columns
            if not {'Name', 'Company', 'Email'}.issubset(recruiters_df.columns):
                logger.error("Data is missing required columns. Must include 'Name', 'Company', and 'Email'.")
                sys.exit(1)
                
            recruiters = recruiters_df.to_dict("records")
            logger.info(f"Loaded {len(recruiters)} recruiter records.")
            outreach_manager.send_emails_concurrently(recruiters)
        
        except KeyError as e:
            logger.error(f"Data missing required column. Error: {e}")
    
    else:
        logger.error("No recruiter data loaded. Exiting.")


if __name__ == "__main__":
    main()