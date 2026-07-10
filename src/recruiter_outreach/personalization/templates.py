# FILE: src/recruiter_outreach/personalization/templates.py

"""
Template selection and rendering.

Beyond the original single-file/single-field templating, this supports:
  - Per-role variants: a 'Role' column value of "SDE" looks for sde.md,
    falling back to default.md if no matching file exists.
  - Follow-up variants: sequence_step > 0 looks for followup_<N>.md.
  - Extra personalization fields (opening_line, resume_line, sender_name)
    that templates may or may not reference — unused placeholders are
    simply ignored by str.format().
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


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

    def select(self, role: str | None, sequence_step: int = 0) -> tuple[str, str]:
        """Returns (template_name, template_text)."""
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

        if role:
            candidate = role.strip().lower().replace(" ", "_").replace("/", "_") + ".md"
            if (self.template_dir / candidate).exists():
                return candidate, self._load(candidate)

        return "default.md", self._load("default.md")

    def render(self, template_text: str, **kwargs) -> str:
        try:
            return template_text.format(**kwargs)
        except KeyError as exc:
            raise ValueError(
                f"Template placeholder {exc} not found. Provided fields: {sorted(kwargs)}"
            ) from exc
