# FILE: src/recruiter_outreach/ingestion/pdf_extract.py

"""Three-tier PDF extraction: structured table -> regex scan -> LLM fallback."""

from __future__ import annotations

import json
import logging
import re
from typing import Optional

import pandas as pd
import pdfplumber

logger = logging.getLogger(__name__)

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")


def extract(file_path: str, *, llm_fallback: bool, anthropic_api_key: Optional[str]) -> pd.DataFrame:
    all_rows: list[list] = []
    raw_texts: list[str] = []
    header: Optional[list] = None

    with pdfplumber.open(file_path) as pdf:
        for page_num, page in enumerate(pdf.pages):
            table = page.extract_table()
            if table and len(table) > 1:
                if header is None and _looks_like_header(table[0]):
                    header = table[0]
                    all_rows.extend(table[1:])
                else:
                    start = 1 if _looks_like_header(table[0]) else 0
                    all_rows.extend(table[start:])
                logger.debug(f"Page {page_num + 1}: extracted {len(table) - 1} rows via table parser.")
            else:
                text = page.extract_text() or ""
                if text.strip():
                    raw_texts.append(text)
                logger.debug(f"Page {page_num + 1}: no structured table, queued for text/LLM extraction.")

    if all_rows:
        df = pd.DataFrame(all_rows, columns=header) if header else pd.DataFrame(all_rows)
        logger.info(f"PDF structured extraction: {len(df)} raw rows found.")
        return df

    if raw_texts:
        combined_text = "\n".join(raw_texts)
        regex_records = _regex_extract(combined_text)
        if regex_records:
            logger.info(f"PDF regex extraction: {len(regex_records)} records found.")
            return pd.DataFrame(regex_records)

        if llm_fallback:
            logger.info("PDF has no parseable structure. Attempting LLM extraction…")
            llm_records = _llm_extract(combined_text, anthropic_api_key)
            if llm_records:
                logger.info(f"LLM extraction: {len(llm_records)} records found.")
                return pd.DataFrame(llm_records)

    raise ValueError(
        "Could not extract any recruiter records from the PDF. "
        "Ensure the document contains names, emails, and company names, "
        "or enable llm_fallback for scanned documents."
    )


def _looks_like_header(row: list) -> bool:
    if not row:
        return False
    header_keywords = {
        "name", "email", "company", "contact", "recruiter",
        "organisation", "organization", "mail", "firm",
    }
    matched = sum(1 for cell in row if cell and str(cell).strip().lower() in header_keywords)
    return matched >= 1


def _regex_extract(text: str) -> list[dict]:
    records = []
    lines = text.split("\n")
    for i, line in enumerate(lines):
        match = EMAIL_RE.search(line)
        if not match:
            continue
        email = match.group()
        context = " ".join(lines[max(0, i - 2): i + 3])
        context_clean = context.replace(email, "").strip()
        records.append({"Email": email, "_raw_context": context_clean})
    return records


def _llm_extract(text: str, api_key: Optional[str]) -> list[dict]:
    try:
        import anthropic
    except ImportError:
        logger.error("anthropic package not installed. Run: pip install anthropic --break-system-packages")
        return []

    text_chunk = text[:30_000]
    try:
        client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=4096,
            messages=[{
                "role": "user",
                "content": (
                    "Extract all recruiter / HR contact records from the text below.\n"
                    "Return ONLY a valid JSON array — no explanation, no markdown fences.\n"
                    'Each element must have exactly these keys: "Name", "Email", "Company", "Role".\n'
                    "Use null for any field that cannot be found.\n\n"
                    f"TEXT:\n{text_chunk}"
                ),
            }],
        )
        raw = "".join(block.text for block in response.content if hasattr(block, "text")).strip()
        raw = re.sub(r"^```[a-z]*\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw)
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.error(f"LLM returned non-JSON response: {exc}")
        return []
    except Exception as exc:
        logger.error(f"LLM extraction failed: {exc}")
        return []
