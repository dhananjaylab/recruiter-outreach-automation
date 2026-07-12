"""Validates the actual shipped email_templates/ directory (not a test
fixture) — every scenario, role, and follow-up-step template must load,
parse its embedded Subject line, and render cleanly with the standard
placeholder set. Catches typos/placeholder mismatches in real content
that per-fixture unit tests can't see."""

from __future__ import annotations

from pathlib import Path

import pytest

from recruiter_outreach.personalization.templates import KNOWN_SCENARIOS, TemplateStore

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "email_templates"

STANDARD_KWARGS = dict(
    recruiter_name="Jane",
    company_name="Acme Corp",
    opening_line="",
    resume_line="You can find my resume here: https://example.com/resume",
    sender_name="Alex Doe",
)


@pytest.fixture(scope="module")
def store() -> TemplateStore:
    return TemplateStore(str(TEMPLATE_DIR))


class TestShippedTemplateDirectory:
    def test_default_template_exists(self):
        assert (TEMPLATE_DIR / "default.md").exists()

    def test_every_known_scenario_has_a_template_file(self):
        missing = [s for s in KNOWN_SCENARIOS if not (TEMPLATE_DIR / f"{s}.md").exists()]
        assert missing == [], f"Missing scenario templates: {missing}"

    def test_three_step_followup_sequence_present(self):
        for step in (1, 2, 3):
            assert (TEMPLATE_DIR / f"followup_{step}.md").exists()

    def test_role_templates_present(self):
        for role_file in ("sde.md", "mle.md", "data_scientist.md"):
            assert (TEMPLATE_DIR / role_file).exists()


class TestEveryTemplateRenders:
    @pytest.mark.parametrize(
        "filename",
        sorted(p.name for p in TEMPLATE_DIR.glob("*.md")),
    )
    def test_renders_without_error_and_has_embedded_subject(self, store, filename):
        text = (TEMPLATE_DIR / filename).read_text(encoding="utf-8")
        subject, body = store.render_with_subject(text, **STANDARD_KWARGS)

        assert subject is not None, f"{filename} has no embedded Subject: line"
        assert "{" not in subject and "}" not in subject, f"{filename} subject has unfilled placeholder"
        assert "Jane" in body or "Jane" in subject

    @pytest.mark.parametrize(
        "filename",
        sorted(p.name for p in TEMPLATE_DIR.glob("*.md")),
    )
    def test_body_is_reasonably_short(self, filename):
        """2026 cold-outreach guidance: keep it under ~150 words so it
        reads fully on mobile without scrolling."""
        text = (TEMPLATE_DIR / filename).read_text(encoding="utf-8")
        word_count = len(text.split())
        assert word_count <= 160, f"{filename} is {word_count} words — trim it down"


class TestScenarioSelectionAgainstRealFiles:
    def test_referral_scenario_selects_referral_template(self, store):
        name, _ = store.select(role=None, sequence_step=0, scenario="referral")
        assert name == "referral.md"

    def test_cold_scenario_falls_back_to_role_or_default(self, store):
        name, _ = store.select(role="SDE", sequence_step=0, scenario="cold")
        assert name == "sde.md"

    def test_followup_step_takes_priority_over_scenario(self, store):
        name, _ = store.select(role=None, sequence_step=2, scenario="referral")
        assert name == "followup_2.md"
