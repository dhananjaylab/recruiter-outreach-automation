from recruiter_outreach.reporting.report import RunReport


def test_summary_counts():
    r = RunReport()
    r.record_success("a@x.com")
    r.record_success("b@x.com")
    r.record_failure("c@y.com", "recipient_refused")
    r.record_skip("d@y.com", "duplicate")

    s = r.summary()
    assert s["sent"] == 2
    assert s["failed"] == 1
    assert s["skipped"] == 1
    assert s["total"] == 4
    assert s["by_domain_success"] == {"x.com": 2}


def test_to_csv_writes_all_rows(tmp_path):
    r = RunReport()
    r.record_success("a@x.com")
    r.record_failure("b@y.com", "bad")
    r.record_skip("c@z.com", "suppressed")
    path = tmp_path / "report.csv"
    r.to_csv(str(path))
    content = path.read_text()
    assert "a@x.com,sent," in content
    assert "b@y.com,failed,bad" in content
    assert "c@z.com,skipped,suppressed" in content
