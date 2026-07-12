# FILE: recruiter_outreach/api/dependencies.py

"""FastAPI dependency providers.

Settings are re-read from the .env file on every request rather than
cached — matches the CLI's behaviour (a fresh `load_settings()` per
invocation) and means editing .env takes effect without restarting the
API server, which is convenient for a locally-run personal tool.
Database connections ARE cached per db_path (thread-local connections
are already handled inside Database itself).
"""

from __future__ import annotations

import os
from functools import lru_cache

from fastapi import HTTPException

from recruiter_outreach.config import Settings, load_settings
from recruiter_outreach.db import Database


def get_settings() -> Settings:
    env_file = os.environ.get("RECRUITER_OUTREACH_ENV_FILE", ".env")
    try:
        return load_settings(env_file)
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@lru_cache(maxsize=8)
def _cached_database(db_path: str) -> Database:
    return Database(db_path)


def get_db() -> Database:
    settings = get_settings()
    return _cached_database(settings.db_path)
