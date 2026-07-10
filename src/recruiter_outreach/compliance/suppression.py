# FILE: src/recruiter_outreach/compliance/suppression.py

"""Compliance helpers: unsubscribe footer text, suppression checks, and a
small heuristic for detecting opt-out replies."""

from __future__ import annotations

from recruiter_outreach.db import Database

_UNSUBSCRIBE_KEYWORDS = ("unsubscribe", "remove me", "stop emailing", "please stop", "opt out", "opt-out")


def unsubscribe_footer(contact: str | None) -> str:
    contact = contact or "replying to this email"
    return (
        "\n\n---\n"
        f"If you'd prefer not to receive further messages like this, just let me "
        f"know by {contact} and I won't follow up again."
    )


def is_blocked(db: Database, email: str) -> bool:
    return db.is_suppressed(email)


def process_unsubscribe_keyword(body: str) -> bool:
    """Heuristic used by the inbox tracker to detect opt-out replies."""
    lowered = body.lower()
    return any(kw in lowered for kw in _UNSUBSCRIBE_KEYWORDS)
