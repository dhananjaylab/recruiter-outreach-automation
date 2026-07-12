"""Tests for routers/outreach.py — POST /send (SSE-streamed).

OutreachManager is mocked at its import site in the router: the mock's
send_emails_concurrently() calls the on_event callback it was given with
a scripted sequence of ProgressEvents, letting us assert on the resulting
SSE stream content without touching Gmail/SMTP at all.
"""

from __future__ import annotations

import io
from unittest.mock import MagicMock, patch

from recruiter_outreach.delivery.transport import ProgressEvent
from tests.api_helpers import make_client


def _upload_csv(client, csv_bytes: bytes = b"Name,Email,Company\nAlice,alice@corp.com,Acme\n") -> str:
    resp = client.post("/upload", files={"file": ("c.csv", io.BytesIO(csv_bytes), "text/csv")})
    assert resp.status_code == 200
    return resp.json()["preview_id"]


def _scripted_manager_factory(events: list[ProgressEvent]):
    def factory(*args, **kwargs):
        on_event = kwargs["on_event"]
        mock_mgr = MagicMock()

        def fake_send(recruiters):
            for evt in events:
                on_event(evt)

        mock_mgr.send_emails_concurrently.side_effect = fake_send
        return mock_mgr

    return factory


class TestSend:
    def test_returns_404_for_unknown_preview(self, tmp_path, db):
        client = make_client(tmp_path, db)
        resp = client.post("/send", json={"preview_id": "does-not-exist"})
        assert resp.status_code == 404

    def test_streams_progress_events_as_sse(self, tmp_path, db):
        client = make_client(tmp_path, db)
        preview_id = _upload_csv(client)

        events = [
            ProgressEvent(status="started", email="alice@corp.com", index=1, total=1),
            ProgressEvent(status="sent", email="alice@corp.com", index=1, total=1),
            ProgressEvent(status="run_complete", extra={"sent": 1, "failed": 0, "skipped": 0}),
        ]

        with patch(
            "recruiter_outreach.api.routers.outreach.OutreachManager",
            side_effect=_scripted_manager_factory(events),
        ):
            resp = client.post("/send", json={"preview_id": preview_id})

        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        assert "event: started" in resp.text
        assert "event: sent" in resp.text
        assert "event: run_complete" in resp.text
        assert "alice@corp.com" in resp.text

    def test_selected_emails_filters_the_batch(self, tmp_path, db):
        client = make_client(tmp_path, db)
        csv_bytes = (
            b"Name,Email,Company\n"
            b"Alice,alice@corp.com,Acme\n"
            b"Bob,bob@corp.com,Acme\n"
        )
        preview_id = _upload_csv(client, csv_bytes)

        captured_recruiters = []

        def factory(*args, **kwargs):
            mock_mgr = MagicMock()

            def fake_send(recruiters):
                captured_recruiters.extend(recruiters)

            mock_mgr.send_emails_concurrently.side_effect = fake_send
            return mock_mgr

        with patch("recruiter_outreach.api.routers.outreach.OutreachManager", side_effect=factory):
            resp = client.post(
                "/send", json={"preview_id": preview_id, "selected_emails": ["bob@corp.com"]},
            )

        assert resp.status_code == 200
        assert len(captured_recruiters) == 1
        assert captured_recruiters[0]["Email"] == "bob@corp.com"

    def test_selecting_no_matching_emails_returns_400(self, tmp_path, db):
        client = make_client(tmp_path, db)
        preview_id = _upload_csv(client)
        resp = client.post(
            "/send", json={"preview_id": preview_id, "selected_emails": ["nobody@corp.com"]},
        )
        assert resp.status_code == 400

    def test_preview_consumed_after_send(self, tmp_path, db):
        client = make_client(tmp_path, db)
        preview_id = _upload_csv(client)

        with patch(
            "recruiter_outreach.api.routers.outreach.OutreachManager",
            side_effect=_scripted_manager_factory([ProgressEvent(status="run_complete", extra={})]),
        ):
            client.post("/send", json={"preview_id": preview_id})

        # Second send against the same (now-consumed) preview_id should 404.
        resp = client.post("/send", json={"preview_id": preview_id})
        assert resp.status_code == 404

    def test_error_in_run_fn_emits_error_event_not_500(self, tmp_path, db):
        client = make_client(tmp_path, db)
        preview_id = _upload_csv(client)

        def factory(*args, **kwargs):
            raise RuntimeError("gmail credentials invalid")

        with patch("recruiter_outreach.api.routers.outreach.OutreachManager", side_effect=factory):
            resp = client.post("/send", json={"preview_id": preview_id})

        assert resp.status_code == 200  # the HTTP response itself already started streaming
        assert "event: error" in resp.text
        assert "gmail credentials invalid" in resp.text
