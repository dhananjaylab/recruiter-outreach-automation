"""Tests for InputLoader (ingestion/loader.py).

Uses real temp files (CSV, TSV, JSON, Excel) so the full read→normalise→
validate pipeline is exercised without touching the network.
"""

from __future__ import annotations

import json

import pandas as pd
import pytest

from recruiter_outreach.ingestion.loader import InputLoader


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

LOADER = InputLoader(llm_fallback=False)

VALID_ROWS = [
    {"Name": "Alice Smith", "Email": "alice@corp.com",  "Company": "CorpA", "Role": "SDE"},
    {"Name": "Bob Jones",   "Email": "bob@startup.io", "Company": "StartB", "Role": "MLE"},
]


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------

class TestCsvLoader:
    def test_loads_standard_csv(self, tmp_path):
        p = tmp_path / "contacts.csv"
        p.write_text("Name,Email,Company,Role\nAlice,alice@x.com,Acme,SDE\n")
        df = LOADER.load(str(p))
        assert len(df) == 1
        assert df.iloc[0]["Email"] == "alice@x.com"

    def test_maps_column_aliases(self, tmp_path):
        p = tmp_path / "contacts.csv"
        p.write_text(
            "Full Name,Work Email,Organisation,Job Title\n"
            "Alice Smith,alice@corp.com,CorpA,SDE\n"
        )
        df = LOADER.load(str(p))
        assert list(df.columns[:4]) == ["Name", "Email", "Company", "Role"]

    def test_drops_rows_with_invalid_email(self, tmp_path):
        p = tmp_path / "contacts.csv"
        p.write_text(
            "Name,Email,Company\n"
            "Good,good@corp.com,X\n"
            "Bad,not-an-email,Y\n"
        )
        df = LOADER.load(str(p))
        assert len(df) == 1

    def test_deduplicates_case_insensitively(self, tmp_path):
        p = tmp_path / "contacts.csv"
        p.write_text(
            "Name,Email,Company\n"
            "Alice,alice@corp.com,X\n"
            "Alice2,ALICE@CORP.COM,X\n"
        )
        df = LOADER.load(str(p))
        assert len(df) == 1

    def test_semicolon_delimiter_detected(self, tmp_path):
        p = tmp_path / "contacts.csv"
        p.write_text("Name;Email;Company\nAlice;alice@x.com;Acme\n")
        df = LOADER.load(str(p))
        assert df.iloc[0]["Email"] == "alice@x.com"

    def test_raises_on_missing_file(self):
        with pytest.raises(FileNotFoundError):
            LOADER.load("/does/not/exist.csv")

    def test_raises_on_unsupported_extension(self, tmp_path):
        p = tmp_path / "file.xyz"
        p.write_text("data")
        with pytest.raises(ValueError, match="Unsupported file format"):
            LOADER.load(str(p))


# ---------------------------------------------------------------------------
# TSV
# ---------------------------------------------------------------------------

class TestTsvLoader:
    def test_loads_tsv(self, tmp_path):
        p = tmp_path / "contacts.tsv"
        p.write_text("Name\tEmail\tCompany\nAlice\talice@corp.com\tAcme\n")
        df = LOADER.load(str(p))
        assert df.iloc[0]["Email"] == "alice@corp.com"


# ---------------------------------------------------------------------------
# JSON
# ---------------------------------------------------------------------------

class TestJsonLoader:
    def test_loads_list_of_records(self, tmp_path):
        p = tmp_path / "contacts.json"
        p.write_text(json.dumps(VALID_ROWS))
        df = LOADER.load(str(p))
        assert len(df) == 2

    def test_loads_nested_contacts_key(self, tmp_path):
        p = tmp_path / "contacts.json"
        p.write_text(json.dumps({"contacts": VALID_ROWS}))
        df = LOADER.load(str(p))
        assert len(df) == 2

    def test_loads_nested_results_key(self, tmp_path):
        p = tmp_path / "export.json"
        p.write_text(json.dumps({"results": VALID_ROWS, "page": 1}))
        df = LOADER.load(str(p))
        assert len(df) == 2

    def test_raises_on_unrecognised_json_shape(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text(json.dumps({"unknown_key": "value"}))
        with pytest.raises(ValueError):
            LOADER.load(str(p))

    def test_first_name_inferred_from_email_when_missing(self, tmp_path):
        rows = [{"Email": "alice@corp.com", "Company": "Corp"}]
        p = tmp_path / "contacts.json"
        p.write_text(json.dumps(rows))
        df = LOADER.load(str(p))
        assert df.iloc[0]["Name"] == "HR"        # default per validate_and_clean


# ---------------------------------------------------------------------------
# Excel
# ---------------------------------------------------------------------------

class TestExcelLoader:
    def test_loads_xlsx(self, tmp_path):
        p = tmp_path / "contacts.xlsx"
        pd.DataFrame(VALID_ROWS).to_excel(str(p), index=False)
        df = LOADER.load(str(p))
        assert len(df) == 2
        assert df.iloc[0]["Email"] == "alice@corp.com"

    def test_picks_largest_sheet(self, tmp_path):
        p = tmp_path / "multi.xlsx"
        with pd.ExcelWriter(str(p), engine="openpyxl") as w:
            pd.DataFrame(VALID_ROWS).to_excel(w, sheet_name="Big",   index=False)
            pd.DataFrame(VALID_ROWS[:1]).to_excel(w, sheet_name="Small", index=False)
        df = LOADER.load(str(p))
        assert len(df) == 2    # picked the bigger sheet
