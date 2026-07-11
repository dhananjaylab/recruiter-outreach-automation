# FILE: recruiter_outreach/reporting/report.py

"""Run-level reporting: aggregates per-recruiter outcomes into a summary
and an exportable CSV, including a per-domain breakdown."""

from __future__ import annotations

import csv
import logging
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class RunReport:
    successes: list[str]             = field(default_factory=list)
    failures:  list[tuple[str, str]] = field(default_factory=list)
    skips:     list[tuple[str, str]] = field(default_factory=list)

    def record_success(self, email: str) -> None:
        self.successes.append(email)

    def record_failure(self, email: str, reason: str) -> None:
        self.failures.append((email, reason))

    def record_skip(self, email: str, reason: str) -> None:
        self.skips.append((email, reason))

    @staticmethod
    def _domain_of(email: str) -> str:
        return email.split("@")[-1].lower() if "@" in email else "unknown"

    def summary(self) -> dict:
        total = len(self.successes) + len(self.failures) + len(self.skips)
        by_domain_success = Counter(self._domain_of(e)       for e in self.successes)
        by_domain_failed  = Counter(self._domain_of(e)       for e, _ in self.failures)
        return {
            "total":            total,
            "sent":             len(self.successes),
            "failed":           len(self.failures),
            "skipped":          len(self.skips),
            "by_domain_success": dict(by_domain_success),
            "by_domain_failed":  dict(by_domain_failed),
        }

    def log_summary(self, logger: logging.Logger) -> None:
        s = self.summary()
        logger.info(
            f"Run complete — sent={s['sent']} failed={s['failed']} "
            f"skipped={s['skipped']} (total attempted={s['total']})."
        )
        if self.failures:
            logger.warning(f"Failed sends: {self.failures}")
        if self.skips:
            skip_reasons = Counter(reason for _, reason in self.skips)
            logger.info(f"Skip reasons: {dict(skip_reasons)}")

    def to_csv(self, path: str) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(["email", "status", "reason"])
            for email in self.successes:
                writer.writerow([email, "sent", ""])
            for email, reason in self.failures:
                writer.writerow([email, "failed", reason])
            for email, reason in self.skips:
                writer.writerow([email, "skipped", reason])

    @staticmethod
    def default_path(reports_dir: str) -> str:
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        return str(Path(reports_dir) / f"run_{ts}.csv")
