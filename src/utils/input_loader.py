# FILE: src/utils/input_loader.py

"""
InputLoader — universal recruiter data ingestion.

Supported formats
-----------------
| Format        | Extension(s)             | Notes                          |
|---------------|--------------------------|--------------------------------|
| CSV           | .csv                     | Any delimiter, any encoding    |
| TSV           | .tsv                     | Tab-delimited                  |
| Excel         | .xlsx .xls .xlsm .ods   | Multi-sheet: picks largest     |
| PDF           | .pdf                     | Table → text regex → LLM       |
| JSON          | .json                    | List or {"data":[...]} wrapper |

Column aliases
--------------
Any of the recognised header spellings are mapped to the three canonical
names expected downstream: Name, Email, Company.
Split first/last-name columns are merged automatically.

LLM fallback
------------
For PDFs that contain no machine-readable tables (scanned documents,
visually formatted cards, etc.) the loader extracts raw text and asks
Claude to return a JSON array of {Name, Email, Company} objects.
Requires ANTHROPIC_API_KEY in .env.
"""

import csv
import json
import logging
import re
from pathlib import Path
from typing import Optional

import chardet
import pandas as pd
import pdfplumber

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Column alias map
# ---------------------------------------------------------------------------
COLUMN_ALIASES: dict[str, list[str]] = {
    "Name": [
        "name", "full name", "fullname", "contact name", "recruiter",
        "recruiter name", "hr name", "person", "contact", "display name",
    ],
    "Email": [
        "email", "email address", "e-mail", "e mail", "mail",
        "recruiter email", "contact email", "work email", "business email",
    ],
    "Company": [
        "company", "company name", "organization", "organisation",
        "employer", "firm", "recruiter company", "account", "business",
        "workplace", "current company",
    ],
    # Intermediate — merged into Name before returning
    "_First": [
        "first name", "firstname", "first", "given name", "fname",
    ],
    "_Last": [
        "last name", "lastname", "last", "surname", "family name",
        "lname",
    ],
}

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

class InputLoader:
    """
    Detects format from file extension, loads the data, and returns a
    normalised pandas DataFrame with at minimum columns [Name, Email, Company].

    Usage
    -----
    loader = InputLoader(llm_fallback=True)   # llm_fallback needs ANTHROPIC_API_KEY
    df = loader.load("recruiters.xlsx")
    """

    def __init__(self, llm_fallback: bool = True):
        """
        Parameters
        ----------
        llm_fallback : bool
            When True, unstructured / scanned PDFs are parsed via the
            Anthropic API.  Set False to skip that dependency.
        """
        self.llm_fallback = llm_fallback

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    def load(self, file_path: str) -> pd.DataFrame:
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"Input file not found: {file_path}")

        suffix = path.suffix.lower()
        loaders = {
            ".csv":  self._load_csv,
            ".tsv":  self._load_tsv,
            ".xlsx": self._load_excel,
            ".xls":  self._load_excel,
            ".xlsm": self._load_excel,
            ".ods":  self._load_excel,
            ".pdf":  self._load_pdf,
            ".json": self._load_json,
        }

        loader_fn = loaders.get(suffix)
        if loader_fn is None:
            raise ValueError(
                f"Unsupported file format '{suffix}'. "
                f"Supported: {', '.join(loaders)}"
            )

        logger.info(f"Loading '{path.name}' as {suffix.upper()[1:]}…")
        df = loader_fn(str(path))

        if df is None or df.empty:
            raise ValueError(f"No data could be extracted from '{file_path}'.")

        df = self._normalize_columns(df)
        df = self._validate_and_clean(df)

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
            # Handle BOM in UTF-8 files exported from Windows Excel
            encoding_errors="replace",
        )

    def _load_tsv(self, file_path: str) -> pd.DataFrame:
        encoding = self._detect_encoding(file_path)
        return pd.read_csv(
            file_path,
            sep="\t",
            encoding=encoding,
            on_bad_lines="skip",
        )

    def _load_excel(self, file_path: str) -> pd.DataFrame:
        """
        Reads all sheets and returns the one with the most rows.
        Falls back to xlrd engine for legacy .xls files.
        """
        suffix = Path(file_path).suffix.lower()
        engine = "odf" if suffix == ".ods" else "openpyxl"
        try:
            sheets: dict[str, pd.DataFrame] = pd.read_excel(
                file_path,
                sheet_name=None,
                engine=engine,
                dtype=str,          # keep everything as strings initially
            )
        except Exception:
            # xlrd fallback for very old .xls binary format
            sheets = pd.read_excel(file_path, sheet_name=None, engine="xlrd", dtype=str)

        if not sheets:
            raise ValueError("Excel file contains no sheets.")

        # Log all sheets found
        for name, sdf in sheets.items():
            logger.debug(f"  Sheet '{name}': {len(sdf)} rows × {len(sdf.columns)} cols")

        # Pick the sheet most likely to be the recruiter list
        best = max(sheets.values(), key=lambda sdf: len(sdf))
        return best

    def _load_json(self, file_path: str) -> pd.DataFrame:
        with open(file_path, encoding="utf-8", errors="replace") as fh:
            data = json.load(fh)

        # Support list-of-records or {"data":[...]} wrappers
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
        """
        Three-tier PDF extraction strategy:
          1. pdfplumber structured table extraction (fastest, most accurate)
          2. Regex scan of raw text (catches simple unstructured layouts)
          3. LLM-assisted extraction via Anthropic API (handles anything)
        """
        all_rows: list[list] = []
        raw_texts: list[str] = []
        header: Optional[list] = None

        with pdfplumber.open(file_path) as pdf:
            for page_num, page in enumerate(pdf.pages):
                # --- Tier 1: structured table ---
                table = page.extract_table()
                if table and len(table) > 1:
                    if header is None and self._looks_like_header(table[0]):
                        header = table[0]
                        all_rows.extend(table[1:])
                    else:
                        # Skip repeated header rows on subsequent pages
                        start = 1 if self._looks_like_header(table[0]) else 0
                        all_rows.extend(table[start:])
                    logger.debug(
                        f"Page {page_num+1}: extracted {len(table)-1} rows via table parser."
                    )
                else:
                    # --- Tier 2 prep: collect raw text for fallback ---
                    text = page.extract_text() or ""
                    if text.strip():
                        raw_texts.append(text)
                    logger.debug(
                        f"Page {page_num+1}: no structured table found, "
                        "queued for text/LLM extraction."
                    )

        # Build DataFrame from structured rows if we got any
        if all_rows:
            if header:
                df = pd.DataFrame(all_rows, columns=header)
            else:
                df = pd.DataFrame(all_rows)
            logger.info(f"PDF structured extraction: {len(df)} raw rows found.")
            return df

        # --- Tier 2: regex scan of raw text ---
        if raw_texts:
            combined_text = "\n".join(raw_texts)
            regex_records = self._regex_extract(combined_text)
            if regex_records:
                logger.info(
                    f"PDF regex extraction: {len(regex_records)} records found."
                )
                return pd.DataFrame(regex_records)

            # --- Tier 3: LLM fallback ---
            if self.llm_fallback:
                logger.info(
                    "PDF has no parseable structure. Attempting LLM extraction…"
                )
                llm_records = self._llm_extract(combined_text)
                if llm_records:
                    logger.info(f"LLM extraction: {len(llm_records)} records found.")
                    return pd.DataFrame(llm_records)

        raise ValueError(
            "Could not extract any recruiter records from the PDF. "
            "Ensure the document contains names, emails, and company names, "
            "or enable llm_fallback=True for scanned documents."
        )

    # ------------------------------------------------------------------
    # PDF helpers
    # ------------------------------------------------------------------

    def _looks_like_header(self, row: list) -> bool:
        if not row:
            return False
        header_keywords = {
            "name", "email", "company", "contact", "recruiter",
            "organisation", "organization", "mail", "firm",
        }
        matched = sum(
            1 for cell in row
            if cell and str(cell).strip().lower() in header_keywords
        )
        return matched >= 1

    def _regex_extract(self, text: str) -> list[dict]:
        """
        Scans raw text line-by-line. For each line containing an email,
        attempts to extract a name and company from surrounding context.
        """
        records = []
        lines = text.split("\n")
        for i, line in enumerate(lines):
            match = EMAIL_RE.search(line)
            if not match:
                continue
            email = match.group()
            # Heuristic: look at current line and ±2 context lines for name/company
            context = " ".join(lines[max(0, i-2): i+3])
            # Remove the email itself from context before guessing name
            context_clean = context.replace(email, "").strip()
            records.append({
                "Email": email,
                "_raw_context": context_clean,
            })
        return records

    def _llm_extract(self, text: str) -> list[dict]:
        """
        Sends raw PDF text to Claude and asks it to return structured JSON.
        Handles scanned documents, business-card layouts, and free-form text.
        """
        try:
            import anthropic
        except ImportError:
            logger.error(
                "anthropic package not installed. "
                "Run: pip install anthropic --break-system-packages"
            )
            return []

        # Truncate to ~30 k chars to stay within token limits
        text_chunk = text[:30_000]

        try:
            client = anthropic.Anthropic()
            response = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=4096,
                messages=[{
                    "role": "user",
                    "content": (
                        "Extract all recruiter / HR contact records from the text below.\n"
                        "Return ONLY a valid JSON array — no explanation, no markdown fences.\n"
                        "Each element must have exactly these keys: "
                        '"Name", "Email", "Company".\n'
                        'Use null for any field that cannot be found.\n\n'
                        f"TEXT:\n{text_chunk}"
                    ),
                }],
            )
            raw = response.content[0].text.strip()
            # Strip accidental markdown fences
            raw = re.sub(r"^```[a-z]*\n?", "", raw)
            raw = re.sub(r"\n?```$", "", raw)
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            logger.error(f"LLM returned non-JSON response: {exc}")
            return []
        except Exception as exc:
            logger.error(f"LLM extraction failed: {exc}")
            return []

    # ------------------------------------------------------------------
    # Column normalisation
    # ------------------------------------------------------------------

    def _normalize_columns(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        1. Strip whitespace from column names.
        2. Match each column to a canonical name via COLUMN_ALIASES.
        3. Merge _First + _Last → Name if Name column is absent.
        """
        # Flatten multi-level column headers (common in Excel pivot tables)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [" ".join(str(c) for c in col).strip() for col in df.columns]

        # Build lookup: lowercased_header → canonical_name
        alias_lookup: dict[str, str] = {}
        for canonical, aliases in COLUMN_ALIASES.items():
            for alias in aliases:
                alias_lookup[alias.lower()] = canonical

        rename_map: dict[str, str] = {}
        for col in df.columns:
            normalised = str(col).strip().lower()
            if normalised in alias_lookup:
                canonical = alias_lookup[normalised]
                # Avoid overwriting if canonical already mapped
                if canonical not in rename_map.values():
                    rename_map[col] = canonical

        df = df.rename(columns=rename_map)

        # Merge split name columns
        if "Name" not in df.columns:
            if "_First" in df.columns and "_Last" in df.columns:
                df["Name"] = (
                    df["_First"].fillna("").str.strip()
                    + " "
                    + df["_Last"].fillna("").str.strip()
                ).str.strip()
            elif "_First" in df.columns:
                df["Name"] = df["_First"].str.strip()
            elif "_Last" in df.columns:
                df["Name"] = df["_Last"].str.strip()

        # Drop internal columns
        df = df.drop(columns=[c for c in ["_First", "_Last"] if c in df.columns])

        # If we only have _raw_context (regex PDF fallback), try to parse Name
        if "_raw_context" in df.columns and "Name" not in df.columns:
            df["Name"] = df["_raw_context"].apply(self._guess_name)
            df["Company"] = df["_raw_context"].apply(self._guess_company)
            df = df.drop(columns=["_raw_context"])

        return df

    def _guess_name(self, context: str) -> str:
        """Heuristic: first token sequence that looks like a proper name."""
        # Title-case words, skip obvious non-names
        words = context.split()
        name_tokens = []
        for w in words:
            stripped = re.sub(r"[^a-zA-Z\-']", "", w)
            if stripped and stripped[0].isupper() and len(stripped) > 1:
                name_tokens.append(stripped)
                if len(name_tokens) == 2:
                    break
        return " ".join(name_tokens) if name_tokens else ""

    def _guess_company(self, context: str) -> str:
        """Heuristic: look for @ domain and guess company from it."""
        match = EMAIL_RE.search(context)
        if match:
            domain = match.group().split("@")[1]
            # Strip TLD and capitalise
            company = domain.split(".")[0].capitalize()
            return company
        return ""

    # ------------------------------------------------------------------
    # Validation & cleaning
    # ------------------------------------------------------------------

    def _validate_and_clean(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        - Ensure Name, Email, Company columns exist (fill with defaults if absent)
        - Drop rows without a valid email address
        - Strip whitespace from all string columns
        - Deduplicate by email (case-insensitive)
        """
        self._dropped = 0

        # Guarantee all three columns exist
        for col, default in [("Name", "HR"), ("Company", "your company"), ("Email", "")]:
            if col not in df.columns:
                df[col] = default

        # Coerce to string and strip
        for col in ["Name", "Email", "Company"]:
            df[col] = df[col].fillna("").astype(str).str.strip()

        # Drop rows with no valid email
        before = len(df)
        df = df[df["Email"].apply(lambda e: bool(EMAIL_RE.fullmatch(e)))]
        self._dropped = before - len(df)

        if self._dropped:
            logger.warning(
                f"Dropped {self._dropped} rows with missing or invalid email addresses."
            )

        # Deduplicate (keep first occurrence, case-insensitive on email)
        df["_email_lower"] = df["Email"].str.lower()
        df = df.drop_duplicates(subset="_email_lower", keep="first")
        df = df.drop(columns=["_email_lower"])

        # Apply default names
        df["Name"] = df["Name"].replace("", "HR")
        df["Company"] = df["Company"].replace("", "your company")

        return df.reset_index(drop=True)

    # ------------------------------------------------------------------
    # Utility helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _detect_encoding(file_path: str) -> str:
        with open(file_path, "rb") as fh:
            raw = fh.read(100_000)
        result = chardet.detect(raw)
        encoding = result.get("encoding") or "utf-8"
        # Treat utf-8-sig (BOM) as utf-8 to avoid the \ufeff header artefact
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
            return ","  # safe default
