# FILE: recruiter_outreach/ingestion/normalize.py

"""Column alias normalisation, validation, and deduplication for ingested
recruiter records (used by every input format)."""

from __future__ import annotations

import logging
import re

import pandas as pd

logger = logging.getLogger(__name__)

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
    "Role": [
        "role", "job title", "title", "position", "target role", "job",
    ],
    "Scenario": [
        "scenario", "outreach type", "outreach scenario", "context",
        "situation", "email type", "template type",
    ],
    # Intermediate columns, merged into Name before returning
    "_First": ["first name", "firstname", "first", "given name", "fname"],
    "_Last":  ["last name",  "lastname",  "last",  "surname", "family name", "lname"],
}

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")


def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [" ".join(str(c) for c in col).strip() for col in df.columns]

    alias_lookup: dict[str, str] = {}
    for canonical, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            alias_lookup[alias.lower()] = canonical

    rename_map: dict[str, str] = {}
    for col in df.columns:
        normalised = str(col).strip().lower()
        if normalised in alias_lookup:
            canonical = alias_lookup[normalised]
            if canonical not in rename_map.values():
                rename_map[col] = canonical

    df = df.rename(columns=rename_map)

    if "Name" not in df.columns:
        if "_First" in df.columns and "_Last" in df.columns:
            df["Name"] = (
                df["_First"].fillna("").str.strip() + " " + df["_Last"].fillna("").str.strip()
            ).str.strip()
        elif "_First" in df.columns:
            df["Name"] = df["_First"].str.strip()
        elif "_Last" in df.columns:
            df["Name"] = df["_Last"].str.strip()

    df = df.drop(columns=[c for c in ["_First", "_Last"] if c in df.columns])

    # Regex-PDF fallback: only an email + raw surrounding text was found
    if "_raw_context" in df.columns and "Name" not in df.columns:
        df["Name"]    = df["_raw_context"].apply(_guess_name)
        df["Company"] = df["_raw_context"].apply(_guess_company)
        df = df.drop(columns=["_raw_context"])

    return df


def _guess_name(context: str) -> str:
    words = context.split()
    name_tokens: list[str] = []
    for w in words:
        stripped = re.sub(r"[^a-zA-Z\-']", "", w)
        if stripped and stripped[0].isupper() and len(stripped) > 1:
            name_tokens.append(stripped)
            if len(name_tokens) == 2:
                break
    return " ".join(name_tokens) if name_tokens else ""


def _guess_company(context: str) -> str:
    match = EMAIL_RE.search(context)
    if match:
        domain = match.group().split("@")[1]
        return domain.split(".")[0].capitalize()
    return ""


def validate_and_clean(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Returns (clean_df, dropped_row_count)."""
    for col, default in [
        ("Name", "HR"), ("Company", "your company"), ("Email", ""),
        ("Role", ""), ("Scenario", "cold"),
    ]:
        if col not in df.columns:
            df[col] = default

    for col in ["Name", "Email", "Company", "Role", "Scenario"]:
        df[col] = df[col].fillna("").astype(str).str.strip()

    before = len(df)
    df = df[df["Email"].apply(lambda e: bool(EMAIL_RE.fullmatch(e)))]
    dropped = before - len(df)
    if dropped:
        logger.warning(f"Dropped {dropped} rows with missing or invalid email addresses.")

    df["_email_lower"] = df["Email"].str.lower()
    df = df.drop_duplicates(subset="_email_lower", keep="first")
    df = df.drop(columns=["_email_lower"])

    df["Name"]     = df["Name"].replace("", "HR")
    df["Company"]  = df["Company"].replace("", "your company")
    df["Scenario"] = df["Scenario"].replace("", "cold")

    return df.reset_index(drop=True), dropped
