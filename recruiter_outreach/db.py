# FILE: recruiter_outreach/db.py

"""
Persistent state for the outreach tool, backed by a single SQLite file.

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

CREATE INDEX IF NOT EXISTS idx_sends_email  ON sends(email);
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

    def list_suppressions(self) -> list[sqlite3.Row]:
        with self._cursor() as cur:
            cur.execute("SELECT email, reason, added_at FROM suppressions ORDER BY added_at DESC")
            return cur.fetchall()

    def remove_suppression(self, email: str) -> bool:
        with self._cursor() as cur:
            cur.execute("DELETE FROM suppressions WHERE email = ?", (email,))
            return cur.rowcount > 0

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
                    status     = excluded.status,
                    sent_at    = excluded.sent_at,
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

    def sends_today_count(self) -> int:
        """Count of successful sends recorded today (UTC calendar day),
        across ALL sequence steps and process runs. Backs DailySendGovernor
        — the sliding-window rate limiter alone doesn't cap total daily
        volume for a long-running process, this does."""
        with self._cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) AS n FROM sends "
                "WHERE status = 'sent' AND date(sent_at) = date('now')"
            )
            row = cur.fetchone()
            return int(row["n"]) if row else 0

    def deliverability_stats(self) -> dict:
        """Aggregate health metrics across all recorded sends — backs the
        API's /reports/deliverability dashboard endpoint. bounce_rate and
        reply_rate are the numbers that actually indicate whether your
        sending reputation is healthy, unlike a single run's summary.

        A row counts toward total_sent if it was ever successfully
        delivered — status='sent' OR status='bounced' (mark_bounced()
        rewrites status from 'sent' to 'bounced' in place, since a bounce
        is a post-delivery event, not a delivery failure at send time).
        status='failed' rows (the transport rejected them outright) never
        left and are excluded."""
        with self._cursor() as cur:
            cur.execute(
                """
                SELECT
                    COUNT(*) FILTER (WHERE status IN ('sent', 'bounced')) AS total_sent,
                    COUNT(*) FILTER (WHERE status = 'bounced' OR bounced_at IS NOT NULL) AS total_bounced,
                    COUNT(*) FILTER (WHERE replied_at IS NOT NULL) AS total_replied
                FROM sends
                """
            )
            row = cur.fetchone()

        total_sent = int(row["total_sent"] or 0)
        total_bounced = int(row["total_bounced"] or 0)
        total_replied = int(row["total_replied"] or 0)

        return {
            "total_sent": total_sent,
            "total_bounced": total_bounced,
            "total_replied": total_replied,
            "bounce_rate": round(total_bounced / total_sent, 4) if total_sent else 0.0,
            "reply_rate": round(total_replied / total_sent, 4) if total_sent else 0.0,
        }

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
