# FILE: recruiter_outreach/verification/email_verifier.py

"""
Lightweight pre-send email verification — no paid API required.

Two tiers:
  1. MX record check (fast, reliable, safe to run on every send — default ON)
  2. SMTP RCPT probe (best-effort — most consumer/office networks block
     outbound port 25, and many mail servers accept-then-bounce rather
     than reject at RCPT time. Default OFF.)
"""

from __future__ import annotations

import logging
import re
import smtplib
import socket

import dns.resolver

logger = logging.getLogger(__name__)

EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+\-]+@([a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})$")

_mx_cache: dict[str, list[str]] = {}


def has_valid_format(email: str) -> bool:
    return bool(EMAIL_RE.fullmatch(email))


def get_mx_hosts(domain: str) -> list[str]:
    if domain in _mx_cache:
        return _mx_cache[domain]
    try:
        answers = dns.resolver.resolve(domain, "MX", lifetime=5)
        hosts = sorted(str(r.exchange).rstrip(".") for r in answers)
    except Exception as exc:
        logger.debug(f"MX lookup failed for {domain}: {exc}")
        hosts = []
    _mx_cache[domain] = hosts
    return hosts


def has_mx_record(email: str) -> bool:
    if not has_valid_format(email):
        return False
    domain = email.split("@")[1]
    return len(get_mx_hosts(domain)) > 0


def smtp_rcpt_check(
    email: str, helo_domain: str = "example.com", timeout: int = 8
) -> bool | None:
    """
    Opens an SMTP connection to the recipient domain's MX and issues
    RCPT TO without sending DATA.

    Returns True/False on a clear signal, or None when inconclusive
    (timeout, greylisting, blocked port 25, catch-all domains, etc.) —
    callers should treat None as "unknown, proceed."
    """
    if not has_valid_format(email):
        return False
    domain = email.split("@")[1]
    hosts = get_mx_hosts(domain)
    if not hosts:
        return False

    for host in hosts[:2]:
        try:
            with smtplib.SMTP(host, 25, timeout=timeout) as smtp:
                smtp.helo(helo_domain)
                smtp.mail("verify@" + helo_domain)
                code, _ = smtp.rcpt(email)
                if code in (250, 251):
                    return True
                if code in (550, 551, 553):
                    return False
                return None
        except (socket.timeout, smtplib.SMTPException, OSError) as exc:
            logger.debug(f"SMTP RCPT check failed against {host}: {exc}")
            continue
    return None
