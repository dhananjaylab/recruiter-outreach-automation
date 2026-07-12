# FILE: recruiter_outreach/config.py

"""
Application configuration, validated with pydantic.

Types (int, bool) are validated and coerced at startup instead of being
read as raw strings and cast ad hoc throughout the codebase.  A single,
clear ValueError is raised on any bad/missing value, with all problems
listed at once rather than failing on the first .get() call that happens
to need an int.  Values keep the same .env variable names so existing
.env files continue to work unchanged.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal, Optional

from pydantic import Field, ValidationError, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore", populate_by_name=True)

    # ---------- identity ----------
    email_user: str = Field(..., alias="EMAIL_USER")
    sender_name: Optional[str] = Field(None, alias="SENDER_NAME")

    # ---------- email provider ----------
    # "gmail_oauth" (default, recommended) sends via the Gmail API using an
    # OAuth2 refresh token — no app password stored anywhere. "smtp" is kept
    # as a legacy fallback for environments without Google Cloud access.
    email_provider: Literal["gmail_oauth", "smtp"] = Field("gmail_oauth", alias="EMAIL_PROVIDER")
    mail_reader_provider: Literal["gmail_oauth", "imap"] = Field("gmail_oauth", alias="MAIL_READER_PROVIDER")

    # ---------- legacy SMTP / IMAP (email_provider="smtp" / mail_reader_provider="imap") ----------
    email_password: Optional[str] = Field(None, alias="EMAIL_PASSWORD")
    smtp_server: str = Field("smtp.gmail.com", alias="SMTP_SERVER")
    smtp_port: int = Field(587, alias="SMTP_PORT")
    imap_server: str = Field("imap.gmail.com", alias="IMAP_SERVER")
    imap_port: int = Field(993, alias="IMAP_PORT")

    # ---------- Google OAuth2 (Gmail API) ----------
    google_client_secrets_path: str = Field(
        "credentials/client_secret.json", alias="GOOGLE_CLIENT_SECRETS_PATH"
    )
    google_token_path: str = Field("credentials/token.json", alias="GOOGLE_TOKEN_PATH")
    google_oauth_redirect_uri: str = Field(
        "http://localhost:8000/auth/google/callback", alias="GOOGLE_OAUTH_REDIRECT_URI"
    )

    # ---------- resume ----------
    resume_link: Optional[str] = Field(None, alias="RESUME_LINK")
    resume_path: Optional[str] = Field(None, alias="RESUME_PATH")

    # ---------- templates ----------
    email_template_dir: str = Field("email_templates", alias="EMAIL_TEMPLATE_DIR")
    default_scenario: str = Field("cold", alias="DEFAULT_SCENARIO")

    # ---------- sending & rate limits ----------
    max_email_threads: int = Field(5,  alias="MAX_EMAIL_THREADS")
    max_email_retries: int = Field(3,  alias="MAX_EMAIL_RETRIES")
    email_calls_per_period: int = Field(10, alias="EMAIL_CALLS_PER_PERIOD")
    email_period: int = Field(60, alias="EMAIL_PERIOD")

    # ---------- warm-up ----------
    # NOTE: warmup_ceiling is the eventual steady-state DAILY send volume
    # once the ramp completes — deliberately separate from
    # email_calls_per_period, which only smooths bursts *within* a run via
    # the sliding-window RateLimiter. Conflating the two (feeding the
    # warm-up cap into the rate limiter) was a latent bug: a long-running
    # process could exceed the intended daily volume since the rate
    # limiter's window resets every `email_period` seconds indefinitely.
    # DailySendGovernor is what actually enforces warmup_ceiling per
    # calendar day, backed by a DB query.
    warmup_enabled: bool = Field(True,  alias="WARMUP_ENABLED")
    warmup_start_cap: int = Field(20,   alias="WARMUP_START_CAP")
    warmup_daily_increment: int = Field(15, alias="WARMUP_DAILY_INCREMENT")
    warmup_days: int = Field(14, alias="WARMUP_DAYS")
    warmup_ceiling: int = Field(150, alias="WARMUP_CEILING")

    # ---------- true daily volume governor ----------
    # Separate from the sliding-window rate limiter (EMAIL_CALLS_PER_PERIOD /
    # EMAIL_PERIOD, which only smooths bursts within a run). This caps total
    # sends per *calendar day* across all runs, which is what actually
    # protects sender reputation during warm-up. See DailySendGovernor.
    daily_send_cap_enabled: bool = Field(True, alias="DAILY_SEND_CAP_ENABLED")

    # ---------- optimal send-window advisory ----------
    # 2026 deliverability research: Tue-Thu, mid-morning recipient-local time
    # gets meaningfully better open/reply rates than Mon/Fri or off-hours.
    # Advisory by default (surfaced in the UI); can be made a hard gate.
    send_window_enabled: bool = Field(True, alias="SEND_WINDOW_ENABLED")
    send_window_enforce: bool = Field(False, alias="SEND_WINDOW_ENFORCE")
    send_window_start_hour: int = Field(8, alias="SEND_WINDOW_START_HOUR")
    send_window_end_hour: int = Field(11, alias="SEND_WINDOW_END_HOUR")
    send_window_days: str = Field("Tue,Wed,Thu", alias="SEND_WINDOW_DAYS")

    # ---------- follow-ups ----------
    # 2026 cold-outreach research: 3-4 day spacing, 3-touch sequences ending
    # in a low-pressure "breakup" email consistently outperform a single
    # nudge sent a week later.
    followup_enabled: bool = Field(True, alias="FOLLOWUP_ENABLED")
    followup_delay_days: int = Field(4,  alias="FOLLOWUP_DELAY_DAYS")
    max_followups: int = Field(3, alias="MAX_FOLLOWUPS")

    # ---------- compliance ----------
    unsubscribe_contact: Optional[str] = Field(None, alias="UNSUBSCRIBE_CONTACT")

    # ---------- LLM ----------
    anthropic_api_key: Optional[str] = Field(None, alias="ANTHROPIC_API_KEY")
    llm_personalization_enabled: bool = Field(False, alias="LLM_PERSONALIZATION_ENABLED")

    # ---------- verification ----------
    verify_mx: bool = Field(True,  alias="VERIFY_MX")
    verify_smtp_rcpt: bool = Field(False, alias="VERIFY_SMTP_RCPT")

    # ---------- storage ----------
    db_path: str = Field("data/outreach.db", alias="DB_PATH")
    reports_dir: str = Field("reports", alias="REPORTS_DIR")

    # ---------- API / uploads ----------
    max_upload_size_mb: int = Field(10, alias="MAX_UPLOAD_SIZE_MB")
    preview_ttl_seconds: int = Field(1800, alias="PREVIEW_TTL_SECONDS")

    @model_validator(mode="after")
    def _validate_provider_requirements(self) -> "Settings":
        """SMTP/IMAP need a password only when actually selected as the
        provider — Gmail OAuth mode never touches EMAIL_PASSWORD."""
        if self.email_provider == "smtp" and not self.email_password:
            raise ValueError(
                "EMAIL_PASSWORD is required when EMAIL_PROVIDER=smtp "
                "(use an app password, not your regular Gmail password)."
            )
        return self


def load_settings(env_file: str = ".env") -> Settings:
    """
    Loads and validates settings from the given .env file.

    Raises ValueError (not pydantic's ValidationError) with a single,
    readable message so callers only need to catch one exception type.
    """
    path = Path(env_file)
    if not path.exists():
        raise ValueError(
            f".env file not found at '{env_file}'. "
            "Copy .env.example to .env and fill in your details."
        )
    try:
        return Settings(_env_file=str(path), _env_file_encoding="utf-8")  # type: ignore[call-arg]
    except ValidationError as exc:
        problems = "; ".join(
            f"{e['loc'][0]}: {e['msg']}" if e["loc"] else e["msg"] for e in exc.errors()
        )
        raise ValueError(f"Invalid configuration in '{env_file}': {problems}") from exc
