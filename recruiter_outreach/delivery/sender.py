# FILE: recruiter_outreach/delivery/sender.py

"""
OutreachManager — composes and sends personalised outreach emails.

Every send goes through:
  1. Suppression check (bounced / unsubscribed / manually blocked)
  2. Duplicate check against persistent send history
  3. Daily volume governor (DailySendGovernor — a true per-calendar-day
     cap, separate from the rate limiter's burst smoothing)
  4. MX-record check (and optional SMTP RCPT probe)
  5. Rate limiting (sliding window, smooths bursts within this run)
  6. Scenario/role template selection + optional LLM opening line
  7. An unsubscribe footer appended to every email
  8. Delivery through the configured EmailTransport (Gmail OAuth by
     default; SMTP as a legacy fallback) with retry/backoff
  9. A record written to the database (for follow-ups + reporting)

Progress is optionally observable via an `on_event` callback receiving
ProgressEvent instances — this is what the FastAPI SSE endpoint uses to
stream live progress to the Streamlit UI without any additional
job-queue infrastructure.

Resume handling: RESUME_LINK (a hosted copy) is preferred over attaching
the PDF directly, since attachments are a known deliverability risk.
RESUME_PATH is kept only as a fallback.
"""

from __future__ import annotations

import concurrent.futures
import itertools
import os
import time
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import make_msgid
from typing import Callable, Optional

from recruiter_outreach.compliance.suppression import is_blocked, unsubscribe_footer
from recruiter_outreach.config import Settings
from recruiter_outreach.db import Database
from recruiter_outreach.delivery.daily_governor import DailySendGovernor
from recruiter_outreach.delivery.factory import build_transport
from recruiter_outreach.delivery.rate_limiter import RateLimiter
from recruiter_outreach.delivery.send_scheduler import SendWindowAdvisor
from recruiter_outreach.delivery.transport import (
    EmailTransport,
    ProgressEvent,
    TransportError,
    TransportPermanentError,
)
from recruiter_outreach.delivery.warmup import WarmupScheduler
from recruiter_outreach.logging_setup import get_logger
from recruiter_outreach.personalization.llm_personalizer import generate_opening_line
from recruiter_outreach.personalization.templates import TemplateStore
from recruiter_outreach.reporting.report import RunReport
from recruiter_outreach.verification.email_verifier import has_mx_record, has_valid_format, smtp_rcpt_check

logger = get_logger(__name__)

OnEventCallback = Callable[[ProgressEvent], None]


class OutreachManager:
    """
    Thread model
    ------------
    send_emails_concurrently() submits one task per recruiter to a
    ThreadPoolExecutor. Each worker thread:
      1. Runs pre-send checks (suppression, dedup, daily cap, verification)
      2. Calls rate_limiter.wait() (thread-safe sliding window)
      3. Sends through the shared EmailTransport (thread-safe by contract)
      4. Sends with exponential-backoff retries, recording the outcome
      5. Emits a ProgressEvent for each state transition, if on_event is set
    """

    def __init__(
        self,
        settings: Settings,
        db: Database,
        sequence_step: int = 0,
        transport: Optional[EmailTransport] = None,
        on_event: Optional[OnEventCallback] = None,
    ):
        self.settings = settings
        self.db = db
        self.sequence_step = sequence_step
        self.on_event = on_event

        if not settings.email_user:
            raise ValueError("EMAIL_USER must be set in .env")

        self.templates = TemplateStore(settings.email_template_dir)

        self._resume_bytes: bytes | None = None
        self._resume_filename: str | None = None
        if settings.resume_link:
            logger.info("Using RESUME_LINK — no attachment will be sent (recommended).")
        elif settings.resume_path and os.path.exists(settings.resume_path):
            with open(settings.resume_path, "rb") as fh:
                self._resume_bytes = fh.read()
            self._resume_filename = os.path.basename(settings.resume_path)
            logger.warning(
                "Using RESUME_PATH attachment. PDF attachments hurt deliverability — "
                "consider setting RESUME_LINK to a hosted copy instead."
            )
        else:
            raise ValueError("Set either RESUME_LINK or a valid RESUME_PATH in .env")

        self.warmup = (
            WarmupScheduler(
                db=db,
                start_cap=settings.warmup_start_cap,
                daily_increment=settings.warmup_daily_increment,
                ceiling=settings.warmup_ceiling,
                warmup_days=settings.warmup_days,
            )
            if settings.warmup_enabled
            else None
        )
        if self.warmup:
            logger.info(f"Warm-up active — today's daily volume cap: {self.warmup.today_cap()}")

        # Rate limiter smooths bursts WITHIN this run; it is intentionally
        # NOT warm-up-adjusted (that conflation was a latent bug — see
        # daily_governor.py). DailySendGovernor owns the true daily cap.
        self.rate_limiter = RateLimiter(
            calls_per_period=settings.email_calls_per_period, period=settings.email_period,
        )
        self.daily_governor = DailySendGovernor(
            db=db, warmup=self.warmup, static_ceiling=settings.warmup_ceiling,
        )
        self.send_window = SendWindowAdvisor(settings)

        self.transport = transport or build_transport(settings)
        self.max_threads = settings.max_email_threads
        self.max_retries = settings.max_email_retries
        self.report = RunReport()
        self._event_seq = itertools.count(1)

    # ------------------------------------------------------------------
    # Progress events
    # ------------------------------------------------------------------

    def _emit(self, **kwargs) -> None:
        if self.on_event is None:
            return
        try:
            self.on_event(ProgressEvent(**kwargs))
        except Exception:  # noqa: BLE001 - a broken observer must never break sending
            logger.exception("on_event callback raised; continuing send run.")

    # ------------------------------------------------------------------
    # Composition
    # ------------------------------------------------------------------

    def _sender_display_name(self) -> str:
        return self.settings.sender_name or self.settings.email_user.split("@")[0]

    def _resume_line(self) -> str:
        if self.settings.resume_link:
            return f"You can find my resume here: {self.settings.resume_link}"
        return "Please find my resume attached for your reference."

    def _build_message(
        self, *, hr_email: str, hr_name: str, company: str, role: str | None, scenario: str | None,
    ):
        template_name, template_text = self.templates.select(role, self.sequence_step, scenario)

        opening_line = ""
        if self.settings.llm_personalization_enabled:
            opening_line = generate_opening_line(
                recruiter_name=hr_name,
                company=company,
                role=role,
                api_key=self.settings.anthropic_api_key,
            )

        render_kwargs = dict(
            recruiter_name=hr_name,
            company_name=company,
            opening_line=opening_line,
            resume_line=self._resume_line(),
            sender_name=self._sender_display_name(),
        )
        try:
            template_subject, body = self.templates.render_with_subject(template_text, **render_kwargs)
        except ValueError:
            raise

        body += unsubscribe_footer(self.settings.unsubscribe_contact)

        if template_subject:
            subject = template_subject
        else:
            subject = f"Seeking Opportunity at {company} — {self._sender_display_name()}"
            if self.sequence_step > 0:
                subject = "Following up: " + subject

        msg = MIMEMultipart()
        msg["From"] = self.settings.email_user
        msg["To"] = hr_email
        msg["Subject"] = subject
        msg_id = make_msgid()
        msg["Message-ID"] = msg_id
        msg.attach(MIMEText(body, "plain", "utf-8"))

        if self._resume_bytes:
            part = MIMEBase("application", "octet-stream")
            part.set_payload(self._resume_bytes)
            encoders.encode_base64(part)
            part.add_header("Content-Disposition", f'attachment; filename="{self._resume_filename}"')
            msg.attach(part)

        return msg, template_name, msg_id

    # ------------------------------------------------------------------
    # Pre-send checks
    # ------------------------------------------------------------------

    def _pre_send_checks(self, email: str) -> str | None:
        """Returns a skip reason, or None if the send should proceed."""
        if not has_valid_format(email):
            return "invalid_email_format"
        if is_blocked(self.db, email):
            return "suppressed"
        if self.db.already_sent(email, self.sequence_step):
            return "duplicate"
        if self.settings.daily_send_cap_enabled and not self.daily_governor.can_send():
            return "daily_cap_reached"
        if self.settings.verify_mx and not has_mx_record(email):
            return "no_mx_record"
        if self.settings.verify_smtp_rcpt:
            result = smtp_rcpt_check(email)
            if result is False:
                return "rcpt_rejected"
        return None

    # ------------------------------------------------------------------
    # Single send (with retries)
    # ------------------------------------------------------------------

    def send_outreach_email(
        self,
        hr_email: str,
        hr_name: str,
        company: str,
        role: str | None = None,
        scenario: str | None = None,
        *,
        index: int | None = None,
        total: int | None = None,
    ):
        self._emit(
            status="started", email=hr_email, name=hr_name, company=company,
            index=index, total=total,
        )

        skip_reason = self._pre_send_checks(hr_email)
        if skip_reason:
            logger.warning(f"Skipping {hr_email}: {skip_reason}")
            self.report.record_skip(hr_email, skip_reason)
            self._emit(
                status="skipped", email=hr_email, name=hr_name, company=company,
                reason=skip_reason, index=index, total=total,
            )
            return

        self.rate_limiter.wait()

        try:
            msg, template_name, msg_id = self._build_message(
                hr_email=hr_email, hr_name=hr_name, company=company, role=role, scenario=scenario,
            )
        except ValueError as exc:
            logger.error(str(exc))
            self.report.record_failure(hr_email, str(exc))
            self._emit(
                status="failed", email=hr_email, name=hr_name, company=company,
                reason=str(exc), index=index, total=total,
            )
            return

        for attempt in range(1, self.max_retries + 1):
            try:
                self.transport.send(self.settings.email_user, hr_email, msg)
                logger.info(f"✓ Sent to {hr_name} <{hr_email}> @ {company} (attempt {attempt})")
                self.db.record_send(
                    email=hr_email, name=hr_name, company=company, role=role or "",
                    template_used=template_name, sequence_step=self.sequence_step,
                    status="sent", message_id=msg_id,
                )
                self.report.record_success(hr_email)
                self._emit(
                    status="sent", email=hr_email, name=hr_name, company=company,
                    index=index, total=total,
                )
                return

            except TransportPermanentError as exc:
                logger.error(f"Address rejected by provider for {hr_email}: {exc}")
                self.db.record_send(
                    email=hr_email, name=hr_name, company=company, role=role or "",
                    template_used=template_name, sequence_step=self.sequence_step,
                    status="failed", message_id=msg_id,
                )
                self.report.record_failure(hr_email, "recipient_refused")
                self._emit(
                    status="failed", email=hr_email, name=hr_name, company=company,
                    reason="recipient_refused", index=index, total=total,
                )
                return

            except TransportError as exc:
                logger.warning(
                    f"Transport error sending to {hr_email} (attempt {attempt}/{self.max_retries}): {exc}"
                )

            except Exception as exc:  # noqa: BLE001
                logger.error(f"Unexpected error sending to {hr_email}: {exc}")

            if attempt < self.max_retries:
                backoff = 2 ** (attempt - 1)
                logger.warning(f"Retrying in {backoff}s…")
                time.sleep(backoff)

        logger.error(f"✗ Failed to send to {hr_email} after {self.max_retries} attempts.")
        self.report.record_failure(hr_email, "max_retries_exceeded")
        self._emit(
            status="failed", email=hr_email, name=hr_name, company=company,
            reason="max_retries_exceeded", index=index, total=total,
        )

    # ------------------------------------------------------------------
    # Concurrent dispatch
    # ------------------------------------------------------------------

    def send_emails_concurrently(self, recruiters: list[dict]) -> RunReport:
        if not recruiters:
            logger.error("Recruiter list is empty — nothing to send.")
            self._emit(status="run_complete", extra=self.report.summary())
            return self.report

        window_status = self.send_window.check()
        if self.settings.send_window_enabled and self.settings.send_window_enforce and not window_status.is_optimal:
            logger.warning(
                f"Refusing to send: outside the enforced send window "
                f"({window_status.window_description}). {window_status.reason}"
            )
            self._emit(
                status="run_complete",
                extra={
                    "blocked_by_send_window": True,
                    "reason": window_status.reason,
                    "window": window_status.window_description,
                    **self.report.summary(),
                },
            )
            return self.report

        logger.info(
            f"Starting concurrent send: {len(recruiters)} recruiters, "
            f"{self.max_threads} threads, sequence_step={self.sequence_step}."
        )

        total = len(recruiters)

        with concurrent.futures.ThreadPoolExecutor(
            max_workers=self.max_threads, thread_name_prefix="mailer",
        ) as executor:
            futures: dict[concurrent.futures.Future, str] = {}
            for i, rec in enumerate(recruiters, start=1):
                hr_email = str(rec.get("Email", "")).strip()
                raw_name = str(rec.get("Name", "") or "").strip()
                hr_name  = raw_name.split()[0] if raw_name else "HR"
                company  = str(rec.get("Company", "") or "").strip() or "your company"
                role     = str(rec.get("Role", "") or "").strip() or None
                scenario = str(rec.get("Scenario", "") or "").strip() or None

                future = executor.submit(
                    self.send_outreach_email,
                    hr_email, hr_name, company, role, scenario,
                    index=i, total=total,
                )
                futures[future] = hr_email

            for future in concurrent.futures.as_completed(futures):
                email = futures[future]
                try:
                    future.result()
                except Exception as exc:
                    logger.error(f"Task for {email} raised an exception: {exc}")
                    self.report.record_failure(email, str(exc))

            for _ in range(self.max_threads):
                executor.submit(self.transport.close_thread).result()

        self.report.log_summary(logger)
        self._emit(status="run_complete", total=total, extra=self.report.summary())
        return self.report
