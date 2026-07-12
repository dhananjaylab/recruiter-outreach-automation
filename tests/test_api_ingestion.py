"""Tests for routers/ingestion.py — POST /upload."""

from __future__ import annotations

import io

from tests.api_helpers import make_client


class TestUpload:
    def test_uploads_and_previews_csv(self, tmp_path, db):
        client = make_client(tmp_path, db)
        csv_bytes = b"Name,Email,Company,Role\nAlice,alice@corp.com,Acme,SDE\n"
        resp = client.post(
            "/upload",
            files={"file": ("contacts.csv", io.BytesIO(csv_bytes), "text/csv")},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total_records"] == 1
        assert body["records"][0]["Email"] == "alice@corp.com"
        assert "preview_id" in body
        assert body["expires_in_seconds"] > 0

    def test_rejects_unsupported_extension(self, tmp_path, db):
        client = make_client(tmp_path, db)
        resp = client.post(
            "/upload",
            files={"file": ("contacts.xyz", io.BytesIO(b"data"), "application/octet-stream")},
        )
        assert resp.status_code == 400

    def test_rejects_file_over_size_limit(self, tmp_path, db):
        client = make_client(tmp_path, db, MAX_UPLOAD_SIZE_MB=1)
        big = b"a" * (2 * 1024 * 1024)
        resp = client.post(
            "/upload",
            files={"file": ("big.csv", io.BytesIO(big), "text/csv")},
        )
        assert resp.status_code == 413

    def test_rejects_file_with_no_valid_records(self, tmp_path, db):
        client = make_client(tmp_path, db)
        csv_bytes = b"Name,Email,Company\nBad,not-an-email,X\n"
        resp = client.post(
            "/upload",
            files={"file": ("contacts.csv", io.BytesIO(csv_bytes), "text/csv")},
        )
        assert resp.status_code == 400

    def test_response_includes_send_window_advisory(self, tmp_path, db):
        client = make_client(tmp_path, db, SEND_WINDOW_ENABLED=True)
        csv_bytes = b"Name,Email,Company\nAlice,alice@corp.com,Acme\n"
        resp = client.post(
            "/upload",
            files={"file": ("contacts.csv", io.BytesIO(csv_bytes), "text/csv")},
        )
        assert resp.status_code == 200
        assert "send_window_optimal" in resp.json()
        assert "send_window_description" in resp.json()

    def test_preview_defaults_scenario_to_cold(self, tmp_path, db):
        client = make_client(tmp_path, db)
        csv_bytes = b"Name,Email,Company\nAlice,alice@corp.com,Acme\n"
        resp = client.post(
            "/upload",
            files={"file": ("contacts.csv", io.BytesIO(csv_bytes), "text/csv")},
        )
        assert resp.json()["records"][0]["Scenario"] == "cold"

    def test_preview_respects_explicit_scenario_column(self, tmp_path, db):
        client = make_client(tmp_path, db)
        csv_bytes = b"Name,Email,Company,Scenario\nAlice,alice@corp.com,Acme,referral\n"
        resp = client.post(
            "/upload",
            files={"file": ("contacts.csv", io.BytesIO(csv_bytes), "text/csv")},
        )
        assert resp.json()["records"][0]["Scenario"] == "referral"
