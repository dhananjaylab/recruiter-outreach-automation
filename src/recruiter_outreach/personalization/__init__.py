# FILE: src/recruiter_outreach/personalization/__init__.py

from recruiter_outreach.personalization.llm_personalizer import generate_opening_line
from recruiter_outreach.personalization.templates import TemplateStore

__all__ = ["TemplateStore", "generate_opening_line"]
