"""Extended email verifier tests covering MX lookup and SMTP RCPT probe.

dns.resolver and smtplib.SMTP are patched — no network required.
"""

from __future__ import annotations

import smtplib
from unittest.mock import MagicMock, patch

import pytest

from recruiter_outreach.verification.email_verifier import (
    get_mx_hosts,
    has_mx_record,
    smtp_rcpt_check,
)


# ---------------------------------------------------------------------------
# MX record lookup
# ---------------------------------------------------------------------------

class TestGetMxHosts:
    def test_returns_sorted_hosts_on_success(self):
        mock_answer = [MagicMock(exchange="mx2.corp.com."), MagicMock(exchange="mx1.corp.com.")]
        with patch("recruiter_outreach.verification.email_verifier.dns.resolver.resolve",
                   return_value=mock_answer), \
             patch.dict("recruiter_outreach.verification.email_verifier._mx_cache", {}, clear=True):
            hosts = get_mx_hosts("corp.com")
        assert hosts == ["mx1.corp.com", "mx2.corp.com"]

    def test_returns_empty_list_on_dns_failure(self):
        with patch("recruiter_outreach.verification.email_verifier.dns.resolver.resolve",
                   side_effect=Exception("NXDOMAIN")), \
             patch.dict("recruiter_outreach.verification.email_verifier._mx_cache", {}, clear=True):
            hosts = get_mx_hosts("no-such-domain.invalid")
        assert hosts == []

    def test_caches_result(self):
        mock_answer = [MagicMock(exchange="mx.corp.com.")]
        with patch("recruiter_outreach.verification.email_verifier.dns.resolver.resolve",
                   return_value=mock_answer) as mock_resolve, \
             patch.dict("recruiter_outreach.verification.email_verifier._mx_cache", {}, clear=True):
            get_mx_hosts("cached.com")
            get_mx_hosts("cached.com")   # second call hits cache
        assert mock_resolve.call_count == 1


class TestHasMxRecord:
    def test_returns_true_when_mx_exists(self):
        with patch("recruiter_outreach.verification.email_verifier.get_mx_hosts",
                   return_value=["mx.corp.com"]):
            assert has_mx_record("jane@corp.com") is True

    def test_returns_false_when_no_mx(self):
        with patch("recruiter_outreach.verification.email_verifier.get_mx_hosts",
                   return_value=[]):
            assert has_mx_record("jane@no-mx.com") is False

    def test_returns_false_for_invalid_format(self):
        assert has_mx_record("not-an-email") is False


# ---------------------------------------------------------------------------
# SMTP RCPT probe
# ---------------------------------------------------------------------------

class TestSmtpRcptCheck:
    def _mock_smtp(self, rcpt_response: tuple):
        ctx = MagicMock()
        smtp_instance = MagicMock()
        smtp_instance.__enter__ = MagicMock(return_value=smtp_instance)
        smtp_instance.__exit__ = MagicMock(return_value=False)
        smtp_instance.rcpt.return_value = rcpt_response
        ctx.return_value = smtp_instance
        return ctx

    def test_returns_true_on_250(self):
        with patch("recruiter_outreach.verification.email_verifier.get_mx_hosts",
                   return_value=["mx.corp.com"]), \
             patch("recruiter_outreach.verification.email_verifier.smtplib.SMTP",
                   self._mock_smtp((250, b"OK"))):
            assert smtp_rcpt_check("jane@corp.com") is True

    def test_returns_false_on_550(self):
        with patch("recruiter_outreach.verification.email_verifier.get_mx_hosts",
                   return_value=["mx.corp.com"]), \
             patch("recruiter_outreach.verification.email_verifier.smtplib.SMTP",
                   self._mock_smtp((550, b"No such user"))):
            assert smtp_rcpt_check("ghost@corp.com") is False

    def test_returns_none_on_inconclusive_code(self):
        with patch("recruiter_outreach.verification.email_verifier.get_mx_hosts",
                   return_value=["mx.corp.com"]), \
             patch("recruiter_outreach.verification.email_verifier.smtplib.SMTP",
                   self._mock_smtp((451, b"Try later"))):
            result = smtp_rcpt_check("jane@corp.com")
        assert result is None

    def test_returns_none_on_smtp_exception(self):
        with patch("recruiter_outreach.verification.email_verifier.get_mx_hosts",
                   return_value=["mx.corp.com"]), \
             patch("recruiter_outreach.verification.email_verifier.smtplib.SMTP",
                   side_effect=smtplib.SMTPException("connection refused")):
            result = smtp_rcpt_check("jane@corp.com")
        assert result is None

    def test_returns_false_for_invalid_format(self):
        assert smtp_rcpt_check("not-an-email") is False

    def test_returns_false_when_no_mx(self):
        with patch("recruiter_outreach.verification.email_verifier.get_mx_hosts",
                   return_value=[]):
            assert smtp_rcpt_check("jane@no-mx.com") is False
