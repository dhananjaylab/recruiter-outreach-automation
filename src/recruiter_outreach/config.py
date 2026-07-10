# FILE: src/recruiter_outreach/config.py

"""
Application configuration, validated with pydantic.

Fixes applied vs the original ConfigLoader:
- Types (int, bool) are validated and coerced at startup instead of being
  read as raw strings and cast ad hoc throughout the codebase.
- A single, clear ValueError is raised on any bad/missing value, with all
  problems listed at once rather than failing on the first .get() call
  that happens to need an int.
- Values keep the same .env variable names as before, so existing .env
  files continue to work unchanged.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from pydantic import Field, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore", populate_by_name=True)

    # ---------- credentials ----------
    email_user: str = Field(..., alias="EMAIL_USER")
    email_password: str = Field(..., alias="EMAIL_PASSWORD")
    sender_name: Optional[str] = Field(None, alias="SENDER_NAME")

    # ---------- SMTP / IMAP ----------
    smtp_server: str = Field("smtp.gmail.com", alias="SMTP_SERVER")
    smtp_port: int = Field(587, alias="SMTP_PORT")
    imap_server: str = Field("imap.gmail.com", alias="IMAP_SERVER")
    imap_port: int = Field(993, alias="IMAP_PORT")

    # ---------- resume ----------
    resume_link: Optional[str] = Field(None, alias="RESUME_LINK")
    resume_path: Optional[str] = Field(None, alias="RESUME_PATH")

    # ---------- templates ----------
    email_template_dir: str = Field("email_templates", alias="EMAIL_TEMPLATE_DIR")

    # ---------- sending & rate limits ----------
    max_email_threads: int = Field(5, alias="MAX_EMAIL_THREADS")
    max_email_retries: int = Field(3, alias="MAX_EMAIL_RETRIES")
    email_calls_per_period: int = Field(10, alias="EMAIL_CALLS_PER_PERIOD")
    email_period: int = Field(60, alias="EMAIL_PERIOD")

    # ---------- warm-up ----------
    warmup_enabled: bool = Field(True, alias="WARMUP_ENABLED")
    warmup_start_cap: int = Field(20, alias="WARMUP_START_CAP")
    warmup_daily_increment: int = Field(15, alias="WARMUP_DAILY_INCREMENT")
    warmup_days: int = Field(14, alias="WARMUP_DAYS")

    # ---------- follow-ups ----------
    followup_enabled: bool = Field(True, alias="FOLLOWUP_ENABLED")
    followup_delay_days: int = Field(5, alias="FOLLOWUP_DELAY_DAYS")
    max_followups: int = Field(1, alias="MAX_FOLLOWUPS")

    # ---------- compliance ----------
    unsubscribe_contact: Optional[str] = Field(None, alias="UNSUBSCRIBE_CONTACT")

    # ---------- LLM ----------
    anthropic_api_key: Optional[str] = Field(None, alias="ANTHROPIC_API_KEY")
    llm_personalization_enabled: bool = Field(False, alias="LLM_PERSONALIZATION_ENABLED")

    # ---------- verification ----------
    verify_mx: bool = Field(True, alias="VERIFY_MX")
    verify_smtp_rcpt: bool = Field(False, alias="VERIFY_SMTP_RCPT")

    # ---------- storage ----------
    db_path: str = Field("data/outreach.db", alias="DB_PATH")
    reports_dir: str = Field("reports", alias="REPORTS_DIR")


def load_settings(env_file: str = ".env") -> Settings:
    """
    Loads and validates settings from the given .env file.

    Raises ValueError (not pydantic's ValidationError) with a single,
    readable message so callers only need to catch one exception type —
    matching the original ConfigLoader's contract with main.py.
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
        problems = "; ".join(f"{e['loc'][0]}: {e['msg']}" for e in exc.errors())
        raise ValueError(f"Invalid configuration in '{env_file}': {problems}") from exc
