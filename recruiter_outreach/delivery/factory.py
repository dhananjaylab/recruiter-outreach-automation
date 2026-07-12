# FILE: recruiter_outreach/delivery/factory.py

"""Builds the configured EmailTransport. Single place that knows about
both concrete providers, so sender.py only ever depends on the
EmailTransport interface (Dependency Inversion)."""

from __future__ import annotations

from recruiter_outreach.config import Settings
from recruiter_outreach.delivery.transport import EmailTransport


def build_transport(settings: Settings) -> EmailTransport:
    if settings.email_provider == "gmail_oauth":
        from recruiter_outreach.delivery.gmail_oauth_client import GmailOAuthTransport

        return GmailOAuthTransport(settings)

    if settings.email_provider == "smtp":
        from recruiter_outreach.delivery.smtp_client import SmtpConnectionPool, SmtpTransport

        if not settings.email_password:
            raise ValueError("EMAIL_PASSWORD must be set when EMAIL_PROVIDER=smtp")
        pool = SmtpConnectionPool(
            server=settings.smtp_server,
            port=settings.smtp_port,
            user=settings.email_user,
            password=settings.email_password,
        )
        return SmtpTransport(pool)

    raise ValueError(f"Unknown EMAIL_PROVIDER: {settings.email_provider!r}")
