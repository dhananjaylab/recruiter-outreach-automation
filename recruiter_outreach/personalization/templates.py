# FILE: recruiter_outreach/personalization/templates.py

"""
Template selection and rendering.

Supports:
  - Scenario variants: a 'Scenario' column value like "referral",
    "post_application", "informational_interview", "event_followup", or
    "alumni" looks for <scenario>.md, letting the same tool handle the
    range of outreach situations 2026 job-search research identifies as
    highest-response (not just cold outreach) — see README for the full
    list and when to use each.
  - Per-role variants: a 'Role' column value of "SDE" looks for sde.md,
    used when no non-"cold" scenario is given.
  - Follow-up variants: sequence_step > 0 looks for followup_<N>.md,
    taking priority over both scenario and role (the follow-up sequence
    is a fixed 3-touch cadence regardless of how email #1 was framed).
  - An optional embedded subject line: a template may start with
    `Subject: ...` on its own line followed by a blank line before the
    body. render_with_subject() parses and formats it separately, so
    subject lines are personalized and A/B-editable per scenario/role
    instead of being hardcoded in sender.py.
  - Extra personalization fields (opening_line, resume_line, sender_name)
    that templates may or may not reference — unused placeholders are
    simply ignored by str.format().
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Scenarios that map to a dedicated template file when EMAIL_TEMPLATE_DIR
# contains one. Anything else (including the default "cold") falls back
# to role-based or default.md selection.
KNOWN_SCENARIOS = frozenset(
    {"referral", "post_application", "informational_interview", "event_followup", "alumni"}
)


class TemplateStore:
    def __init__(self, template_dir: str):
        self.template_dir = Path(template_dir)
        self._cache: dict[str, str] = {}
        if not self.template_dir.exists():
            raise ValueError(f"Template directory not found: {template_dir}")
        if not (self.template_dir / "default.md").exists():
            raise ValueError(
                f"'{self.template_dir}' must contain a default.md template."
            )

    def _load(self, filename: str) -> str:
        if filename not in self._cache:
            path = self.template_dir / filename
            if not path.exists():
                raise ValueError(f"Template file not found: {path}")
            self._cache[filename] = path.read_text(encoding="utf-8")
        return self._cache[filename]

    def select(
        self, role: str | None, sequence_step: int = 0, scenario: str | None = None,
    ) -> tuple[str, str]:
        """Returns (template_name, template_text).

        Selection order: follow-up step > named scenario > role > default.
        """
        if sequence_step > 0:
            name = f"followup_{sequence_step}.md"
            if (self.template_dir / name).exists():
                return name, self._load(name)
            fallback = "followup_1.md"
            if (self.template_dir / fallback).exists():
                return fallback, self._load(fallback)
            logger.warning(
                f"No follow-up template found for step {sequence_step}; using default.md."
            )

        normalized_scenario = (scenario or "").strip().lower().replace(" ", "_")
        if normalized_scenario and normalized_scenario != "cold":
            candidate = f"{normalized_scenario}.md"
            if (self.template_dir / candidate).exists():
                return candidate, self._load(candidate)
            if normalized_scenario in KNOWN_SCENARIOS:
                logger.warning(
                    f"Scenario '{normalized_scenario}' recognised but no "
                    f"{candidate} found in {self.template_dir}; falling back."
                )

        if role:
            candidate = role.strip().lower().replace(" ", "_").replace("/", "_") + ".md"
            if (self.template_dir / candidate).exists():
                return candidate, self._load(candidate)

        return "default.md", self._load("default.md")

    def render(self, template_text: str, **kwargs) -> str:
        """Renders the full template text as-is (legacy behaviour — no
        subject-line extraction). Prefer render_with_subject() for new
        code so subject lines get personalized too."""
        try:
            return template_text.format(**kwargs)
        except KeyError as exc:
            raise ValueError(
                f"Template placeholder {exc} not found. "
                f"Provided fields: {sorted(kwargs)}"
            ) from exc

    def render_with_subject(self, template_text: str, **kwargs) -> tuple[str | None, str]:
        """Splits an optional leading `Subject: ...` line (+ blank line)
        from the body, formats each independently, and returns
        (subject_or_None, body). Templates without an embedded subject
        line render exactly as render() would, with subject=None."""
        subject_line: str | None = None
        body_text = template_text

        if template_text.startswith("Subject:"):
            head, _, rest = template_text.partition("\n")
            # Require the conventional blank line separating subject from
            # body; if absent, treat the whole thing as body (safer than
            # silently eating a real first line of content).
            if rest.startswith("\n"):
                subject_line = head[len("Subject:"):].strip()
                body_text = rest[1:]

        body = self.render(body_text, **kwargs)
        subject = self.render(subject_line, **kwargs) if subject_line else None
        return subject, body
