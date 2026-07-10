# FILE: src/recruiter_outreach/followup/scheduler.py

"""
Follow-up sequence: finds recruiters who haven't replied or bounced after
FOLLOWUP_DELAY_DAYS and sends the next step in the sequence.

Rows due for a follow-up can be sitting at different sequence_steps (e.g.
some are due for step 1, others already had step 1 and are now due for
step 2), so they're grouped by their next step and sent in separate
batches — each batch picks up the matching followup_<N>.md template.
"""

from __future__ import annotations

from recruiter_outreach.config import Settings
from recruiter_outreach.db import Database
from recruiter_outreach.delivery.sender import OutreachManager
from recruiter_outreach.logging_setup import get_logger
from recruiter_outreach.reporting.report import RunReport

logger = get_logger(__name__)


def run_followups(settings: Settings, db: Database) -> list[RunReport]:
    if not settings.followup_enabled:
        logger.info("Follow-ups disabled in config (FOLLOWUP_ENABLED=false).")
        return []

    due_rows = db.due_for_followup(
        delay_days=settings.followup_delay_days,
        max_step=settings.max_followups,
    )
    if not due_rows:
        logger.info("No follow-ups due.")
        return []

    groups: dict[int, list] = {}
    for row in due_rows:
        groups.setdefault(row["sequence_step"] + 1, []).append(row)

    reports: list[RunReport] = []
    for step, rows in sorted(groups.items()):
        recruiters = [
            {"Email": r["email"], "Name": r["name"], "Company": r["company"], "Role": r["role"]}
            for r in rows
        ]
        logger.info(f"Sending {len(recruiters)} follow-up(s) at sequence_step={step}.")
        manager = OutreachManager(settings=settings, db=db, sequence_step=step)
        reports.append(manager.send_emails_concurrently(recruiters))

    return reports
