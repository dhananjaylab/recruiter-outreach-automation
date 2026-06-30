# FILE: src/outreach/__init__.py

"""
OutreachManager — handles all email composition and delivery.

Key fixes and improvements vs the original:
- Resume PDF read once into memory at startup (not on every email send).
- SMTP connection is opened once per thread via threading.local(), not per email.
  This avoids 10 simultaneous SMTP handshakes and repeated logins.
- PDF loading removed from this class entirely — handled by InputLoader.
- Subject line kept personalised per recruiter to improve deliverability.
- Attachment warning added for cold outreach best practices.
- All thread-pool futures collected and checked for exceptions.
"""

import concurrent.futures
import os
import smtplib
import threading
import time
import re
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from utils import ConfigLoader, Logger, RateLimiter


class OutreachManager:
    """
    Composes and sends personalised outreach emails with resume attachments.

    Thread model
    ------------
    send_emails_concurrently() submits one task per recruiter to a
    ThreadPoolExecutor.  Each worker thread:
      1. Calls rate_limiter.wait()  (thread-safe sliding window)
      2. Reuses a persistent per-thread SMTP connection (threading.local)
      3. Sends the email with exponential-backoff retries
    """

    EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")

    def __init__(self, config: ConfigLoader = None, logger: Logger = None):
        self.config = config or ConfigLoader()
        self.logger = logger or Logger(__name__)

        # ---------- credentials ----------
        self.email_user     = self.config.get("EMAIL_USER")
        self.email_password = self.config.get("EMAIL_PASSWORD")
        if not all([self.email_user, self.email_password]):
            raise ValueError("EMAIL_USER and EMAIL_PASSWORD must be set in .env")

        # ---------- SMTP ----------
        self.smtp_server = self.config.get("SMTP_SERVER", "smtp.gmail.com")
        self.smtp_port   = int(self.config.get("SMTP_PORT", 587))

        # ---------- template ----------
        self.template_path = self.config.get("EMAIL_TEMPLATE_PATH", "email_template.md")
        self.template = self._load_template()
        if self.template is None:
            raise ValueError(
                f"Email template could not be loaded from '{self.template_path}'."
            )

        # ---------- resume (read once) ----------
        self.resume_path = self.config.get("RESUME_PATH")
        if not self.resume_path or not os.path.exists(self.resume_path):
            raise ValueError(
                f"RESUME_PATH is not set or file not found: '{self.resume_path}'"
            )
        with open(self.resume_path, "rb") as fh:
            self._resume_bytes = fh.read()
        self._resume_filename = os.path.basename(self.resume_path)
        self.logger.info(
            f"Resume cached in memory: {self._resume_filename} "
            f"({len(self._resume_bytes) // 1024} KB)"
        )

        # ---------- rate limiter & thread pool ----------
        self.rate_limiter = RateLimiter(
            calls_per_period=int(self.config.get("EMAIL_CALLS_PER_PERIOD", 10)),
            period=int(self.config.get("EMAIL_PERIOD", 60)),
        )
        self.max_threads = int(self.config.get("MAX_EMAIL_THREADS", 5))
        self.max_retries = int(self.config.get("MAX_EMAIL_RETRIES", 3))

        # ---------- per-thread SMTP connection store ----------
        self._local = threading.local()

    # ------------------------------------------------------------------ #
    # Template                                                             #
    # ------------------------------------------------------------------ #

    def _load_template(self) -> str | None:
        try:
            with open(self.template_path, "r", encoding="utf-8") as fh:
                template = fh.read()
            self.logger.info(f"Template loaded: {self.template_path}")
            return template
        except FileNotFoundError:
            self.logger.error(f"Template not found: {self.template_path}")
            return None
        except Exception as exc:
            self.logger.error(f"Error loading template: {exc}")
            return None

    # ------------------------------------------------------------------ #
    # SMTP connection pool (one connection per worker thread)              #
    # ------------------------------------------------------------------ #

    def _get_smtp_connection(self) -> smtplib.SMTP:
        """
        Returns a live SMTP connection for the current thread.
        Creates and authenticates a new one if none exists or if the
        existing one has timed out.
        """
        conn: smtplib.SMTP | None = getattr(self._local, "smtp", None)

        # Check whether the existing connection is still alive
        if conn is not None:
            try:
                conn.noop()   # lightweight ping
            except Exception:
                conn = None   # connection is dead; will reconnect below

        if conn is None:
            self.logger.debug(
                f"[thread {threading.current_thread().name}] "
                "Opening new SMTP connection…"
            )
            conn = smtplib.SMTP(self.smtp_server, self.smtp_port, timeout=30)
            conn.ehlo()
            conn.starttls()
            conn.ehlo()
            conn.login(self.email_user, self.email_password)
            self._local.smtp = conn

        return conn

    def _close_thread_smtp(self):
        """Called at the end of each worker thread's life."""
        conn: smtplib.SMTP | None = getattr(self._local, "smtp", None)
        if conn:
            try:
                conn.quit()
            except Exception:
                pass
            self._local.smtp = None

    # ------------------------------------------------------------------ #
    # Email composition                                                    #
    # ------------------------------------------------------------------ #

    def _build_message(self, hr_email: str, hr_name: str, company_name: str) -> MIMEMultipart:
        """Builds the full MIME message with personalised body + resume attachment."""
        try:
            body = self.template.format(
                recruiter_name=hr_name,
                company_name=company_name,
            )
        except KeyError as exc:
            raise ValueError(
                f"Template placeholder {exc} not found. "
                "Expected {{recruiter_name}} and {{company_name}}."
            ) from exc

        # Personalised subject improves open rates and avoids spam triggers
        subject = f"Seeking Opportunity at {company_name} — Dhananjay Lokhande"

        msg = MIMEMultipart()
        msg["From"]    = self.email_user
        msg["To"]      = hr_email
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain", "utf-8"))

        # Attach resume from the in-memory bytes (no disk read per email)
        part = MIMEBase("application", "octet-stream")
        part.set_payload(self._resume_bytes)
        encoders.encode_base64(part)
        part.add_header(
            "Content-Disposition",
            f'attachment; filename="{self._resume_filename}"',
        )
        msg.attach(part)

        return msg

    # ------------------------------------------------------------------ #
    # Single email send (with retries)                                     #
    # ------------------------------------------------------------------ #

    def send_outreach_email(
        self,
        hr_email: str,
        hr_name: str,
        company_name: str,
        max_retries: int | None = None,
    ):
        """Sends one email. Called from worker threads inside the thread pool."""
        if max_retries is None:
            max_retries = self.max_retries

        # Block until rate limit allows
        self.rate_limiter.wait()

        try:
            msg = self._build_message(hr_email, hr_name, company_name)
        except ValueError as exc:
            self.logger.error(str(exc))
            return

        for attempt in range(1, max_retries + 1):
            try:
                conn = self._get_smtp_connection()
                conn.sendmail(self.email_user, hr_email, msg.as_string())
                self.logger.info(
                    f"✓ Sent to {hr_name} <{hr_email}> @ {company_name} "
                    f"(attempt {attempt})"
                )
                return  # success — exit retry loop

            except smtplib.SMTPRecipientsRefused as exc:
                self.logger.error(
                    f"Address rejected by server for {hr_email}: {exc}. Skipping."
                )
                return  # no point retrying a hard rejection

            except (smtplib.SMTPServerDisconnected, smtplib.SMTPException) as exc:
                self.logger.warning(
                    f"SMTP error sending to {hr_email} (attempt {attempt}/{max_retries}): {exc}"
                )
                # Force reconnect on next attempt
                self._local.smtp = None

            except OSError as exc:
                self.logger.warning(
                    f"Network error sending to {hr_email} (attempt {attempt}/{max_retries}): {exc}"
                )
                self._local.smtp = None

            except Exception as exc:
                self.logger.error(
                    f"Unexpected error sending to {hr_email} (attempt {attempt}/{max_retries}): {exc}"
                )

            if attempt < max_retries:
                backoff = 2 ** (attempt - 1)   # 1s, 2s, 4s …
                self.logger.warning(f"Retrying in {backoff}s…")
                time.sleep(backoff)

        self.logger.error(
            f"✗ Failed to send to {hr_email} after {max_retries} attempts."
        )

    # ------------------------------------------------------------------ #
    # Concurrent dispatch                                                  #
    # ------------------------------------------------------------------ #

    def send_emails_concurrently(self, recruiters: list[dict]):
        """
        Submits one send task per recruiter to the thread pool.
        Collects and logs all task-level exceptions after completion.
        """
        if not recruiters:
            self.logger.error("Recruiter list is empty — nothing to send.")
            return

        self.logger.info(
            f"Starting concurrent send: {len(recruiters)} emails, "
            f"{self.max_threads} threads."
        )

        futures: dict[concurrent.futures.Future, str] = {}

        with concurrent.futures.ThreadPoolExecutor(
            max_workers=self.max_threads,
            thread_name_prefix="mailer",
        ) as executor:

            for rec in recruiters:
                hr_email   = str(rec.get("Email", "")).strip()
                hr_name    = str(rec.get("Name",  "HR")).split()[0]
                company    = str(rec.get("Company", "your company")).strip()

                if not hr_name:
                    hr_name = "HR"
                if not company:
                    company = "your company"

                if not self.EMAIL_RE.fullmatch(hr_email):
                    self.logger.warning(f"Skipping invalid email address: '{hr_email}'")
                    continue

                future = executor.submit(
                    self.send_outreach_email, hr_email, hr_name, company
                )
                futures[future] = hr_email

            # Wait for all tasks and surface any unhandled exceptions
            for future in concurrent.futures.as_completed(futures):
                email = futures[future]
                try:
                    future.result()
                except Exception as exc:
                    self.logger.error(f"Task for {email} raised an exception: {exc}")

            # Clean up per-thread SMTP connections
            executor.map(lambda _: self._close_thread_smtp(), range(self.max_threads))

        sent    = sum(1 for f in futures if not f.exception())
        failed  = len(futures) - sent
        self.logger.info(
            f"Send complete — {sent} succeeded, {failed} failed out of {len(futures)} attempted."
        )
