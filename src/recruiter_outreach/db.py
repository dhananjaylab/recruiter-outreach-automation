# FILE: src/recruiter_outreach/db.py

"""
Persistent state for the outreach tool, backed by a single SQLite file.

This is the piece that was entirely missing before: every run used to be
stateless, so nothing stopped the tool from re-emailing the same recruiter
twice, and there was no way to know who had bounced, replied, or should
get a follow-up. Everything below exists to close that gap.

Tables
------
sends         one row per (email, sequence_step) send attempt
suppressions  addresses that must never be emailed again, with a reason
meta          small key/value store (currently just the warm-up start date)
"""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS sends (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL COLLATE NOCASE,
    name TEXT,
    company TEXT,
    role TEXT,
    template_used TEXT,
    sequence_step INTEGER NOT NULL DEFAULT 0,
    sent_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'sent',
    message_id TEXT,
    replied_at TEXT,
    bounced_at TEXT,
    UNIQUE(email, sequence_step)
);

CREATE TABLE IF NOT EXISTS suppressions (
    email TEXT PRIMARY KEY COLLATE NOCASE,
    reason TEXT NOT NULL,
    added_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE INDEX IF NOT EXISTS idx_sends_email ON sends(email);
CREATE INDEX IF NOT EXISTS idx_sends_status ON sends(status);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    """
    Thread-safe wrapper around a single SQLite file (one connection per
    thread, since sqlite3 connections cannot be shared across threads).
    """

    def __init__(self, db_path: str):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        with self._cursor() as cur:
            cur.executescript(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.db_path, timeout=30)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL;")
            self._local.conn = conn
        return conn

    @contextmanager
    def _cursor(self) -> Iterator[sqlite3.Cursor]:
        conn = self._connect()
        cur = conn.cursor()
        try:
            yield cur
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()

    # ------------------------------------------------------------------
    # Suppression list
    # ------------------------------------------------------------------

    def is_suppressed(self, email: str) -> bool:
        with self._cursor() as cur:
            cur.execute("SELECT 1 FROM suppressions WHERE email = ?", (email,))
            return cur.fetchone() is not None

    def suppress(self, email: str, reason: str) -> None:
        with self._cursor() as cur:
            cur.execute(
                "INSERT INTO suppressions (email, reason, added_at) VALUES (?, ?, ?) "
                "ON CONFLICT(email) DO UPDATE SET reason = excluded.reason",
                (email, reason, _now()),
            )

    # ------------------------------------------------------------------
    # Sends
    # ------------------------------------------------------------------

    def already_sent(self, email: str, sequence_step: int = 0) -> bool:
        with self._cursor() as cur:
            cur.execute(
                "SELECT 1 FROM sends WHERE email = ? AND sequence_step = ? AND status = 'sent'",
                (email, sequence_step),
            )
            return cur.fetchone() is not None

    def record_send(
        self,
        *,
        email: str,
        name: str,
        company: str,
        role: str,
        template_used: str,
        sequence_step: int,
        status: str,
        message_id: Optional[str] = None,
    ) -> None:
        with self._cursor() as cur:
            cur.execute(
                """
                INSERT INTO sends
                    (email, name, company, role, template_used, sequence_step,
                     sent_at, status, message_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(email, sequence_step) DO UPDATE SET
                    status = excluded.status,
                    sent_at = excluded.sent_at,
                    message_id = excluded.message_id
                """,
                (email, name, company, role, template_used, sequence_step, _now(), status, message_id),
            )

    def mark_bounced(self, email: str) -> None:
        with self._cursor() as cur:
            cur.execute(
                "UPDATE sends SET status = 'bounced', bounced_at = ? WHERE email = ?",
                (_now(), email),
            )
        self.suppress(email, reason="bounced")

    def mark_replied(self, email: str) -> None:
        with self._cursor() as cur:
            cur.execute(
                "UPDATE sends SET replied_at = ? WHERE email = ? AND replied_at IS NULL",
                (_now(), email),
            )

    def known_recipient_emails(self) -> set[str]:
        with self._cursor() as cur:
            cur.execute("SELECT DISTINCT email FROM sends")
            return {row["email"].lower() for row in cur.fetchall()}

    def due_for_followup(self, *, delay_days: int, max_step: int) -> list[sqlite3.Row]:
        """
        Returns the latest send row for each recruiter who:
          - was successfully sent to
          - has not replied or bounced
          - is not suppressed
          - hasn't already received the next step in the sequence
          - is due (their last send is older than delay_days)
          - hasn't already hit max_step
        """
        with self._cursor() as cur:
            cur.execute(
                """
                SELECT s.* FROM sends s
                WHERE s.status = 'sent'
                  AND s.replied_at IS NULL
                  AND s.bounced_at IS NULL
                  AND s.sequence_step < ?
                  AND datetime(s.sent_at) <= datetime('now', ?)
                  AND s.email NOT IN (SELECT email FROM suppressions)
                  AND NOT EXISTS (
                      SELECT 1 FROM sends s2
                      WHERE s2.email = s.email AND s2.sequence_step = s.sequence_step + 1
                  )
                  AND s.sequence_step = (
                      SELECT MAX(s3.sequence_step) FROM sends s3 WHERE s3.email = s.email
                  )
                """,
                (max_step, f"-{delay_days} days"),
            )
            return cur.fetchall()

    # ------------------------------------------------------------------
    # Meta (used by the warm-up scheduler)
    # ------------------------------------------------------------------

    def get_meta(self, key: str) -> Optional[str]:
        with self._cursor() as cur:
            cur.execute("SELECT value FROM meta WHERE key = ?", (key,))
            row = cur.fetchone()
            return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self._cursor() as cur:
            cur.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn:
            conn.close()
            self._local.conn = None
