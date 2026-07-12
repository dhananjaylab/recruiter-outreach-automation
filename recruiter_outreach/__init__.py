"""Recruiter Outreach Automation.

A personalized cold-outreach tool with a FastAPI + Streamlit
human-in-the-loop layer, Gmail OAuth2 delivery (SMTP kept as a legacy
fallback), deliverability hardening (warm-up, true daily-volume
governor, send-window advisory, verification), persistent send history,
bounce/reply tracking, scenario-based follow-up sequences, and
compliance (suppression/unsubscribe) built in.
"""

__version__ = "3.0.0"
