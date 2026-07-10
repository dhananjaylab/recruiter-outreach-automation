# FILE: src/recruiter_outreach/ingestion/loader.py

"""InputLoader — universal recruiter data ingestion across CSV/TSV/Excel/JSON/PDF."""

from __future__ import annotations

import csv
import json
import logging
from pathlib import Path
from typing import Optional

import chardet
import pandas as pd

from recruiter_outreach.ingestion import normalize, pdf_extract

logger = logging.getLogger(__name__)


class InputLoader:
    """
    Detects format from file extension, loads the data, and returns a
    normalised pandas DataFrame with columns [Name, Email, Company, Role].

    Usage
    -----
    loader = InputLoader(llm_fallback=True, anthropic_api_key=settings.anthropic_api_key)
    df = loader.load("recruiters.xlsx")
    """

    def __init__(self, llm_fallback: bool = True, anthropic_api_key: Optional[str] = None):
        self.llm_fallback = llm_fallback
        self.anthropic_api_key = anthropic_api_key
        self._dropped = 0

    def load(self, file_path: str) -> pd.DataFrame:
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"Input file not found: {file_path}")

        suffix = path.suffix.lower()
        loaders = {
            ".csv": self._load_csv,
            ".tsv": self._load_tsv,
            ".xlsx": self._load_excel,
            ".xls": self._load_excel,
            ".xlsm": self._load_excel,
            ".ods": self._load_excel,
            ".pdf": self._load_pdf,
            ".json": self._load_json,
        }

        loader_fn = loaders.get(suffix)
        if loader_fn is None:
            raise ValueError(
                f"Unsupported file format '{suffix}'. Supported: {', '.join(loaders)}"
            )

        logger.info(f"Loading '{path.name}' as {suffix.upper()[1:]}…")
        df = loader_fn(str(path))

        if df is None or df.empty:
            raise ValueError(f"No data could be extracted from '{file_path}'.")

        df = normalize.normalize_columns(df)
        df, self._dropped = normalize.validate_and_clean(df)

        logger.info(
            f"Loaded {len(df)} valid records from '{path.name}' "
            f"(dropped {self._dropped} malformed rows)."
        )
        return df

    # ------------------------------------------------------------------
    # Format-specific loaders
    # ------------------------------------------------------------------

    def _load_csv(self, file_path: str) -> pd.DataFrame:
        encoding = self._detect_encoding(file_path)
        delimiter = self._sniff_delimiter(file_path, encoding)
        logger.debug(f"CSV encoding={encoding}, delimiter='{delimiter}'")
        return pd.read_csv(
            file_path,
            encoding=encoding,
            sep=delimiter,
            engine="python",
            on_bad_lines="skip",
            skip_blank_lines=True,
            encoding_errors="replace",
        )

    def _load_tsv(self, file_path: str) -> pd.DataFrame:
        encoding = self._detect_encoding(file_path)
        return pd.read_csv(file_path, sep="\t", encoding=encoding, on_bad_lines="skip")

    def _load_excel(self, file_path: str) -> pd.DataFrame:
        suffix = Path(file_path).suffix.lower()
        engine = "odf" if suffix == ".ods" else "openpyxl"
        try:
            sheets: dict[str, pd.DataFrame] = pd.read_excel(
                file_path, sheet_name=None, engine=engine, dtype=str,
            )
        except Exception:
            sheets = pd.read_excel(file_path, sheet_name=None, engine="xlrd", dtype=str)

        if not sheets:
            raise ValueError("Excel file contains no sheets.")

        for name, sdf in sheets.items():
            logger.debug(f"  Sheet '{name}': {len(sdf)} rows x {len(sdf.columns)} cols")

        return max(sheets.values(), key=lambda sdf: len(sdf))

    def _load_json(self, file_path: str) -> pd.DataFrame:
        with open(file_path, encoding="utf-8", errors="replace") as fh:
            data = json.load(fh)

        if isinstance(data, list):
            return pd.DataFrame(data)

        for key in ("data", "results", "records", "contacts", "items", "leads"):
            if key in data and isinstance(data[key], list):
                return pd.DataFrame(data[key])

        raise ValueError(
            "JSON file must be a list of records or an object with a "
            "'data'/'results'/'records'/'contacts' key containing a list."
        )

    def _load_pdf(self, file_path: str) -> pd.DataFrame:
        return pdf_extract.extract(
            file_path, llm_fallback=self.llm_fallback, anthropic_api_key=self.anthropic_api_key,
        )

    # ------------------------------------------------------------------
    # Utility helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _detect_encoding(file_path: str) -> str:
        with open(file_path, "rb") as fh:
            raw = fh.read(100_000)
        result = chardet.detect(raw)
        encoding = result.get("encoding") or "utf-8"
        if encoding.lower() in ("utf-8-sig", "utf-8-bom"):
            encoding = "utf-8-sig"
        logger.debug(f"Detected encoding: {encoding} (confidence {result.get('confidence', 0):.0%})")
        return encoding

    @staticmethod
    def _sniff_delimiter(file_path: str, encoding: str) -> str:
        with open(file_path, encoding=encoding, errors="replace") as fh:
            sample = fh.read(8192)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
            return dialect.delimiter
        except csv.Error:
            return ","
