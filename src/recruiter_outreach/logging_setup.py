# FILE: src/recruiter_outreach/logging_setup.py

"""Centralised logging configuration."""

from __future__ import annotations

import logging


def setup_logging(level: int = logging.INFO) -> None:
    """Configures the root logger once. Safe to call multiple times."""
    root = logging.getLogger()
    if root.handlers:
        return
    root.setLevel(level)
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    )
    root.addHandler(handler)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
