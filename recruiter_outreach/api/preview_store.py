# FILE: recruiter_outreach/api/preview_store.py

"""
PreviewStore — bridges POST /upload (dry-run preview) and POST /send
(approved live send) so the person can review a normalised recruiter
list before anything goes out, per the human-in-the-loop flow.

An in-process dict with TTL eviction is a deliberate, scoped choice: this
is a single-user local tool (no auth, per project scope) — there is no
multi-worker deployment to coordinate across, so Redis/a database table
would be unjustified complexity. If this ever needs to run with multiple
uvicorn workers, swap this for a shared store keyed the same way.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field

import pandas as pd


@dataclass
class PreviewEntry:
    df: pd.DataFrame
    created_at: float = field(default_factory=time.time)
    ttl_seconds: int = 1800

    @property
    def expired(self) -> bool:
        return (time.time() - self.created_at) > self.ttl_seconds

    @property
    def expires_in_seconds(self) -> int:
        return max(0, int(self.ttl_seconds - (time.time() - self.created_at)))


class PreviewStore:
    def __init__(self):
        self._entries: dict[str, PreviewEntry] = {}
        self._lock = threading.Lock()

    def put(self, df: pd.DataFrame, ttl_seconds: int) -> str:
        preview_id = str(uuid.uuid4())
        with self._lock:
            self._sweep_locked()
            self._entries[preview_id] = PreviewEntry(df=df, ttl_seconds=ttl_seconds)
        return preview_id

    def get(self, preview_id: str) -> PreviewEntry | None:
        with self._lock:
            entry = self._entries.get(preview_id)
            if entry is None:
                return None
            if entry.expired:
                del self._entries[preview_id]
                return None
            return entry

    def delete(self, preview_id: str) -> None:
        with self._lock:
            self._entries.pop(preview_id, None)

    def _sweep_locked(self) -> None:
        expired = [k for k, v in self._entries.items() if v.expired]
        for k in expired:
            del self._entries[k]


_store = PreviewStore()


def get_preview_store() -> PreviewStore:
    return _store
