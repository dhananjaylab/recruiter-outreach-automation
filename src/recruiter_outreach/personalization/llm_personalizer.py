# FILE: src/recruiter_outreach/personalization/llm_personalizer.py

"""
Optional LLM-assisted per-recruiter opening-line generation.

Off by default (LLM_PERSONALIZATION_ENABLED=false) since it costs an API
call per recruiter. This never raises — personalization is a nice-to-have
and must never block a send.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def generate_opening_line(
    *, recruiter_name: str, company: str, role: str | None, api_key: str | None,
) -> str:
    """Returns one short custom opening sentence, or '' if unavailable/failed."""
    if not api_key:
        return ""
    try:
        import anthropic
    except ImportError:
        logger.warning("anthropic package not installed; skipping LLM personalization.")
        return ""

    prompt = (
        f"Write exactly one short, natural sentence (max 25 words) that could open a "
        f"cold outreach email to a recruiter named {recruiter_name} at {company}"
        + (f" for a {role} role" if role else "")
        + ". No greeting, no name, just the sentence itself. Plain text only, no quotes."
    )
    try:
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=100,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(block.text for block in response.content if hasattr(block, "text"))
        return text.strip()
    except Exception as exc:
        logger.warning(f"LLM personalization failed, continuing without it: {exc}")
        return ""
