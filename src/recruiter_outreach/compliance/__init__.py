# FILE: src/recruiter_outreach/compliance/__init__.py

from recruiter_outreach.compliance.suppression import (
    is_blocked,
    process_unsubscribe_keyword,
    unsubscribe_footer,
)

__all__ = ["is_blocked", "unsubscribe_footer", "process_unsubscribe_keyword"]
