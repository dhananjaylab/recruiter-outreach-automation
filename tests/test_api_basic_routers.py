"""Tests for the simpler read-mostly API routers: health, suppressions,
reports, and the /followups/due endpoint."""

from __future__ import annotations

from tests.api_helpers import make_client


class TestHealth:
    def test_health_ok_without_any_settings(self, tmp_path, db):
        # No dependency override at all — must not crash even with no .env.
        from fastapi.testclient import TestClient
        from recruiter_outreach.api.main import app

        app.dependency_overrides = {}
        client = TestClient(app)
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_health_reports_configured_provider(self, tmp_path, db):
        client = make_client(tmp_path, db, EMAIL_PROVIDER="gmail_oauth")
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["email_provider"] == "gmail_oauth"


class TestSuppressions:
    def test_list_empty_initially(self, tmp_path, db):
        client = make_client(tmp_path, db)
        resp = client.get("/suppressions")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_add_and_list_suppression(self, tmp_path, db):
        client = make_client(tmp_path, db)
        resp = client.post("/suppressions", json={"email": "a@corp.com", "reason": "manual"})
        assert resp.status_code == 201

        resp = client.get("/suppressions")
        emails = [e["email"] for e in resp.json()]
        assert "a@corp.com" in emails

    def test_remove_suppression(self, tmp_path, db):
        client = make_client(tmp_path, db)
        client.post("/suppressions", json={"email": "a@corp.com", "reason": "manual"})
        resp = client.delete("/suppressions/a@corp.com")
        assert resp.status_code == 204
        assert client.get("/suppressions").json() == []

    def test_remove_nonexistent_returns_404(self, tmp_path, db):
        client = make_client(tmp_path, db)
        resp = client.delete("/suppressions/ghost@corp.com")
        assert resp.status_code == 404


class TestReports:
    def test_list_empty_when_no_reports_dir(self, tmp_path, db):
        client = make_client(tmp_path, db, REPORTS_DIR=str(tmp_path / "nope"))
        resp = client.get("/reports")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_and_fetch_report(self, tmp_path, db):
        reports_dir = tmp_path / "reports"
        reports_dir.mkdir()
        (reports_dir / "run_20260101T000000Z.csv").write_text(
            "email,status,reason\na@x.com,sent,\nb@x.com,failed,bounced\n"
        )
        client = make_client(tmp_path, db, REPORTS_DIR=str(reports_dir))

        resp = client.get("/reports")
        assert resp.status_code == 200
        assert resp.json()[0]["sent"] == 1
        assert resp.json()[0]["failed"] == 1

        resp = client.get("/reports/run_20260101T000000Z.csv")
        assert resp.status_code == 200
        assert "a@x.com" in resp.text

    def test_fetch_report_blocks_path_traversal(self, tmp_path, db):
        reports_dir = tmp_path / "reports"
        reports_dir.mkdir()
        client = make_client(tmp_path, db, REPORTS_DIR=str(reports_dir))
        resp = client.get("/reports/..%2F..%2Fetc%2Fpasswd")
        assert resp.status_code in (400, 404)

    def test_deliverability_stats(self, tmp_path, db):
        db.record_send(
            email="a@corp.com", name="A", company="C", role="",
            template_used="default.md", sequence_step=0, status="sent",
        )
        db.mark_bounced("a@corp.com")
        client = make_client(tmp_path, db)
        resp = client.get("/reports/deliverability")
        assert resp.status_code == 200
        assert resp.json()["total_sent"] == 1
        assert resp.json()["bounce_rate"] == 1.0


class TestFollowupsDue:
    def test_due_empty_initially(self, tmp_path, db):
        client = make_client(tmp_path, db)
        resp = client.get("/followups/due")
        assert resp.status_code == 200
        assert resp.json()["total_due"] == 0

    def test_due_groups_by_next_step(self, tmp_path, db):
        with db._cursor() as cur:
            cur.execute(
                "INSERT INTO sends (email, name, company, role, template_used, "
                "sequence_step, sent_at, status) VALUES (?, ?, ?, ?, ?, ?, datetime('now','-10 days'), 'sent')",
                ("a@corp.com", "A", "Corp", "", "default.md", 0),
            )
        client = make_client(tmp_path, db, FOLLOWUP_DELAY_DAYS=4, MAX_FOLLOWUPS=3)
        resp = client.get("/followups/due")
        assert resp.status_code == 200
        assert resp.json()["total_due"] == 1
        assert resp.json()["groups"][0]["sequence_step"] == 1
