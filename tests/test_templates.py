import pytest

from recruiter_outreach.personalization.templates import TemplateStore


def test_default_template_used_when_no_role(template_dir):
    ts = TemplateStore(template_dir)
    name, text = ts.select(role=None, sequence_step=0)
    assert name == "default.md"


def test_role_specific_template_selected(template_dir):
    ts = TemplateStore(template_dir)
    name, _ = ts.select(role="SDE", sequence_step=0)
    assert name == "sde.md"


def test_unknown_role_falls_back_to_default(template_dir):
    ts = TemplateStore(template_dir)
    name, _ = ts.select(role="Nonexistent Role", sequence_step=0)
    assert name == "default.md"


def test_followup_template_selected_for_sequence_step(template_dir):
    ts = TemplateStore(template_dir)
    name, _ = ts.select(role=None, sequence_step=1)
    assert name == "followup_1.md"


def test_render_fills_placeholders(template_dir):
    ts = TemplateStore(template_dir)
    _, text = ts.select(role=None, sequence_step=0)
    rendered = ts.render(
        text, recruiter_name="Jane", company_name="Acme",
        opening_line="", resume_line="link", sender_name="Me",
    )
    assert "Jane" in rendered and "Acme" in rendered


def test_render_missing_placeholder_raises(template_dir):
    ts = TemplateStore(template_dir)
    _, text = ts.select(role=None, sequence_step=0)
    with pytest.raises(ValueError):
        ts.render(text, recruiter_name="Jane")  # missing company_name etc.


def test_missing_default_template_raises(tmp_path):
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    with pytest.raises(ValueError):
        TemplateStore(str(empty_dir))
