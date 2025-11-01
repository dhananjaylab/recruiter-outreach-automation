# FILE: src/outreach/__init__.py

import concurrent.futures
import os
import smtplib
import time
import re  # Import regex
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import pandas as pd
import pdfplumber

# Use the new refactored utils
from utils import ConfigLoader, Logger, RateLimiter

class OutreachManager:
    """
    OutreachManager handles recruiter outreach automation, including loading recruiter details,
    sending personalized outreach emails with resume attachments, and managing email rate limiting.
    """
    def __init__(self, config=None, logger=None):
        self.config = config or ConfigLoader()
        self.logger = logger or Logger(__name__)
        
        self.email_user = self.config.get("EMAIL_USER")
        self.email_password = self.config.get("EMAIL_PASSWORD")
        self.resume_path = self.config.get("RESUME_PATH")
        self.template_path = self.config.get("EMAIL_TEMPLATE_PATH", "email_template.md")

        # --- Validate critical configurations on init ---
        if not all([self.email_user, self.email_password]):
            raise ValueError("EMAIL_USER and EMAIL_PASSWORD must be set in .env file.")
        
        if not self.resume_path or not os.path.exists(self.resume_path):
            raise ValueError(f"RESUME_PATH is not set or file not found at '{self.resume_path}'")
        
        # --- Load template ONCE ---
        self.template = self.load_template()
        if self.template is None:
            raise ValueError(f"Email template could not be loaded from '{self.template_path}'.")

        self.smtp_server = self.config.get("SMTP_SERVER", "smtp.gmail.com")
        self.smtp_port = int(self.config.get("SMTP_PORT", 587))
        
        self.email_rate_limiter = RateLimiter(
            calls_per_period=int(self.config.get("EMAIL_CALLS_PER_PERIOD", 10)),
            period=int(self.config.get("EMAIL_PERIOD", 60))
        )
        
        self.max_threads = int(self.config.get("MAX_EMAIL_THREADS", 10))
        self.max_retries = int(self.config.get("MAX_EMAIL_RETRIES", 3))
        
        # Regex for finding email
        self.email_regex = re.compile(r"([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})")


    def load_template(self):
        """Loads the email template from the specified file."""
        try:
            with open(self.template_path, "r", encoding="utf-8") as f:
                template = f.read()
            self.logger.info(f"Email template loaded from {self.template_path}")
            return template
        except FileNotFoundError:
            self.logger.error(f"Email template file not found at {self.template_path}")
            return None
        except Exception as e:
            self.logger.error(f"Error loading email template: {e}")
            return None

    def _clean_text(self, text):
        """Helper to clean newline characters and extra spaces from extracted text."""
        if text is None:
            return ""
        return " ".join(text.split()).strip()

    def _find_email(self, text):
        """Helper to find the first valid email in a block of text."""
        if text is None:
            return None
        match = self.email_regex.search(text)
        return match.group(1) if match else None

    def load_recruiters_from_pdf(self, file_path):
        """
        Extracts recruiter details from a PDF file, handles messy data, and returns a DataFrame.
        """
        processed_data = []
        try:
            with pdfplumber.open(file_path) as pdf:
                for i, page in enumerate(pdf.pages):
                    try:
                        table = page.extract_table()
                        if not table:
                            self.logger.warning(f"No table found on page {i+1}.")
                            continue
                        
                        # Skip header row on subsequent pages
                        if i > 0:
                            table = table[1:]

                        for row in table:
                            if len(row) != 5:
                                self.logger.warning(f"Skipping row with incorrect column count on page {i+1}: {row}")
                                continue

                            # Extract data robustly
                            name = self._clean_text(row[1])
                            email_block = str(row[2]) # Cell can contain email + title + newlines
                            company = self._clean_text(row[4])

                            # Find the email in the block of text
                            email = self._find_email(email_block)

                            if email:
                                processed_data.append({
                                    "Name": name,
                                    "Email": email,
                                    "Company": company
                                })
                            else:
                                self.logger.warning(f"No valid email found in row on page {i+1}. Skipping. Data: {row}")

                    except Exception as e:
                        self.logger.error(f"Error processing page {i+1}: {e}")

            if not processed_data:
                self.logger.error("No valid recruiter data extracted from PDF.")
                return None

            df = pd.DataFrame(processed_data)
            
            # Save a CSV as an artifact for user review
            output_csv = "recruiters_list.csv"
            df.to_csv(output_csv, index=False)
            self.logger.info(f"Recruiter details extracted and saved to {output_csv}")
            
            return df # Return the DataFrame for in-memory use

        except FileNotFoundError as e:
            self.logger.error(f"PDF file not found: {e}")
            return None
        except Exception as e:
            self.logger.error(f"Unexpected error loading recruiters from PDF: {e}")
            return None

    def send_outreach_email(self, hr_email, hr_name, company_name, max_retries=None):
        """
        Sends an outreach email to a recruiter.
        """
        if max_retries is None:
            max_retries = self.max_retries

        self.email_rate_limiter.wait()
        subject = "Seeking Assistance for Suitable Job Opportunity & Referral"
        
        # --- Use the pre-loaded template ---
        if self.template is None:
            self.logger.error("Email template is not loaded. Cannot send email.")
            return

        template_vars = {"recruiter_name": hr_name, "company_name": company_name}
        try:
            body = self.template.format(**template_vars)
        except KeyError as e:
            self.logger.error(f"Email template is missing a placeholder: {e}. Check email_template.md")
            return

        msg = MIMEMultipart()
        msg["From"] = self.email_user
        msg["To"] = hr_email
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain"))

        try:
            with open(self.resume_path, "rb") as attachment:
                part = MIMEBase("application", "octet-stream")
                part.set_payload(attachment.read())
            encoders.encode_base64(part)
            part.add_header(
                "Content-Disposition",
                f"attachment; filename={os.path.basename(self.resume_path)}",
            )
            msg.attach(part)
        except FileNotFoundError:
            # This check is technically redundant now, but good for safety
            self.logger.error(f"Resume file not found at {self.resume_path}. Cannot attach.")
            return
        except Exception as e:
            self.logger.error(f"Error attaching resume: {e}")
            return

        for attempt in range(max_retries):
            try:
                with smtplib.SMTP(self.smtp_server, self.smtp_port) as server:
                    server.starttls()
                    server.login(self.email_user, self.email_password)
                    server.sendmail(self.email_user, hr_email, msg.as_string())

                self.logger.info(
                    f"Email sent successfully to {hr_name} ({hr_email}) after {attempt+1} attempts"
                )
                time.sleep(3)  # Keep a small delay
                return  # Email sent successfully, exit retry loop

            except smtplib.SMTPException as e:
                self.logger.error(
                    f"SMTP error while sending email to {hr_email} (attempt {attempt+1}): {e}"
                )
            except OSError as e:
                self.logger.error(
                    f"OS error while sending email to {hr_email} (attempt {attempt+1}): {e}"
                )
            except Exception as e:
                self.logger.error(
                    f"Unexpected error sending email to {hr_email} (attempt {attempt+1}): {e}"
                )
            
            if attempt < max_retries - 1:
                sleep_time = 2**attempt
                self.logger.warning(f"Retrying in {sleep_time} seconds...")
                time.sleep(sleep_time)  # Exponential backoff

        self.logger.error(f"Failed to send email to {hr_email} after {max_retries} attempts")

    def send_emails_concurrently(self, recruiters):
        """
        Sends emails concurrently using a thread pool.
        """
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=self.max_threads
        ) as executor:
            futures = []
            for recruiter in recruiters:
                try:
                    hr_email = str(recruiter["Email"]).strip()
                    # Get first name, or use 'HR' as default
                    hr_name = str(recruiter.get("Name", "HR")).split()[0]
                    company_name = str(recruiter.get("Company", "your company")).strip()

                    if not hr_name:
                        hr_name = "HR"
                    if not company_name:
                        company_name = "your company" # Default placeholder
                    
                    if not self._find_email(hr_email):
                        self.logger.warning(f"Skipping invalid email: {hr_email}")
                        continue

                    self.logger.info(f"Submitting email task for {hr_email}")
                    future = executor.submit(
                        self.send_outreach_email, hr_email, hr_name, company_name
                    )
                    futures.append(future)
                
                except KeyError as e:
                    self.logger.error(f"Skipping record due to missing key: {e}. Record: {recruiter}")
                except Exception as e:
                    self.logger.error(f"Error submitting task for {recruiter.get('Email')}: {e}")


            for future in concurrent.futures.as_completed(futures):
                try:
                    future.result()  # Raise exceptions if any occurred in the thread
                except Exception as e:
                    self.logger.error(f"An email sending task failed: {e}")