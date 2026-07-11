# Recruiter Outreach Automation

Sends personalized outreach emails to recruiters, loaded from CSV/TSV/Excel/JSON/PDF,
with deliverability hardening, persistent send history, bounce/reply tracking,
and follow-up sequences.

## Project structure

Following the [Hitchhiker's Guide to Python](https://docs.python-guide.org/writing/structure/):
the installable package lives at the **project root** (not inside `src/`), the
`scripts/` wrapper layer is eliminated in favour of `console_scripts` entry
points, and `tests/context.py` handles import resolution.

```
recruiter-outreach-automation/
├── README.md
├── LICENSE                         # MIT
├── Makefile                        # init / test / lint / clean / docker-*
├── setup.py                        # thin shim — real config is in pyproject.toml
├── pyproject.toml                  # build, deps, entry points, pytest config
├── requirements.txt                # runtime deps (mirrors pyproject.toml)
├── .env.example                    # copy to .env and fill in credentials
├── Dockerfile
├── .github/workflows/ci.yml        # pytest on 3.10 / 3.11 / 3.12
│
├── recruiter_outreach/             # ← installable package at project root
│   ├── __init__.py
│   ├── py.typed                    # PEP 561 — inline type information shipped
│   ├── cli.py                      # `recruiter-outreach` entry point
│   ├── config.py                   # pydantic Settings + load_settings()
│   ├── db.py                       # SQLite: sends / suppressions / meta
│   ├── logging_setup.py
│   ├── compliance/
│   │   └── suppression.py          # unsubscribe footer + opt-out detection
│   ├── delivery/
│   │   ├── rate_limiter.py         # thread-safe sliding-window
│   │   ├── warmup.py               # linear send-cap ramp
│   │   ├── smtp_client.py          # per-thread SMTP connection pool
│   │   └── sender.py               # OutreachManager — orchestrates a send run
│   ├── followup/
│   │   ├── cli.py                  # `recruiter-outreach-followups` entry point
│   │   └── scheduler.py            # finds + sends due follow-ups
│   ├── ingestion/
│   │   ├── loader.py               # CSV / TSV / Excel / JSON / PDF → DataFrame
│   │   ├── normalize.py            # ~30 column-alias mappings + dedup
│   │   └── pdf_extract.py          # 3-tier: table → regex → LLM
│   ├── personalization/
│   │   ├── templates.py            # role-based + follow-up template selection
│   │   └── llm_personalizer.py     # optional per-recruiter opening line
│   ├── reporting/
│   │   └── report.py               # per-run CSV + domain breakdown
│   ├── tracking/
│   │   ├── cli.py                  # `recruiter-outreach-check-inbox` entry point
│   │   └── imap_tracker.py         # bounce / reply / unsubscribe detection
│   └── verification/
│       └── email_verifier.py       # format + MX + optional SMTP RCPT check
│
├── tests/
│   ├── context.py                  # sys.path insertion (Hitchhiker's Guide pattern)
│   ├── conftest.py                 # shared fixtures (db, template_dir)
│   ├── test_db.py
│   ├── test_email_verifier.py
│   ├── test_normalize.py
│   ├── test_rate_limiter.py
│   ├── test_report.py
│   ├── test_suppression.py
│   ├── test_templates.py
│   └── test_warmup.py
│
├── email_templates/
│   ├── default.md
│   ├── sde.md / mle.md / data_scientist.md
│   └── followup_1.md
│
├── data/                           # outreach.db lives here (gitignored)
├── reports/                        # per-run CSV reports (gitignored)
└── docs/                           # Sphinx-ready
    ├── conf.py
    └── index.rst
```

### What changed from the previous layout

| Before | After | Why |
|---|---|---|
| `src/recruiter_outreach/` | `recruiter_outreach/` at root | Guide: module shouldn't live in an ambiguous `src/` subdir |
| `scripts/*.py` with `sys.path` hacks | Removed — `console_scripts` handles it | Redundant once the package is at root and installed |
| `sys.path.insert` in `conftest.py` | `tests/context.py` imported once | Guide's canonical test-suite import pattern |
| No `setup.py` | `setup.py` thin shim at root | Conventional root file; older toolchains need it |
| No `Makefile` | `Makefile` with `init/test/lint/clean/docker-*` | Guide explicitly recommends this |
| No `LICENSE` | `LICENSE` (MIT) | Guide calls it "arguably the most important file" |
| No `docs/` | `docs/conf.py` + `docs/index.rst` | Guide recommends Sphinx-ready `docs/` at root |
| No `py.typed` | `recruiter_outreach/py.typed` | PEP 561: signals that inline types are shipped |
| `pyproject.toml` `where = ["src"]` | `where = ["."]`, no `pythonpath` in pytest | Matches flat layout |

## Setup

```bash
python -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate

make install                    # pip install -e ".[dev]"
# or without make:
pip install -e ".[dev]"
```

Copy `.env.example` → `.env` and fill in your details.

> **Gmail users:** use an [App Password](https://myaccount.google.com/apppasswords)
> for both `EMAIL_PASSWORD` (SMTP) and IMAP — not your regular password.

## Running

After `pip install -e .`, three commands are available globally:

```bash
# Send outreach
recruiter-outreach --csv  recruiters.csv
recruiter-outreach --xlsx HR_contacts.xlsx
recruiter-outreach --pdf  HR_contacts.pdf
recruiter-outreach --json export.json --dry-run   # preview, sends nothing

# Check for bounces / replies / unsubscribes (run periodically)
recruiter-outreach-check-inbox --since-days 14

# Send due follow-ups (run daily, after check-inbox)
recruiter-outreach-followups
```

Or via `make`:

```bash
make dry-run FILE=recruiters.csv
make check-inbox
make followups
```

### CLI flags

| Flag | Purpose |
|---|---|
| `--csv / --tsv / --xlsx / --pdf / --json` | input file |
| `--dry-run` | load, validate, preview — sends nothing |
| `--save-csv OUTPUT` | save normalised list before sending |
| `--no-llm` | disable LLM fallback for unstructured PDFs |
| `--env-file PATH` | use a `.env` other than the default |

### Suggested cron

```cron
0 */4 * * * cd /path/to/project && .venv/bin/recruiter-outreach-check-inbox
0 9   * * * cd /path/to/project && .venv/bin/recruiter-outreach-check-inbox
0 10  * * * cd /path/to/project && .venv/bin/recruiter-outreach-followups
```

## How the pieces fit together

1. **Ingestion** (`ingestion/`) reads the input file, auto-detects encoding and
   delimiter, maps ~30 column-name aliases onto `Name/Email/Company/Role`, validates
   emails, and deduplicates.
2. **Pre-send checks** (`delivery/sender.py` + `verification/`, `compliance/`, `db.py`):
   skip if suppressed, already sent to, or no valid MX record.
3. **Warm-up** (`delivery/warmup.py`) computes today's send cap from a linear ramp.
4. **Personalization** (`personalization/`) picks a role-specific template (falling
   back to `default.md`), optionally adds an LLM-generated opening line, and appends
   an unsubscribe footer.
5. **Delivery** (`delivery/smtp_client.py`, `sender.py`) sends via a per-thread SMTP
   connection pool with exponential-backoff retries, recording every outcome to SQLite.
6. **Reporting** (`reporting/report.py`) writes a per-run CSV to `reports/`.
7. **Tracking** (`tracking/imap_tracker.py`) scans the inbox for bounces and replies,
   suppressing bounced addresses and stopping follow-ups for repliers.
8. **Follow-ups** (`followup/scheduler.py`) queries the database for recruiters due
   for the next step and dispatches them.

## Tests

```bash
make test
# or:
pytest tests/ -v --cov=recruiter_outreach --cov-report=term-missing
```

35 tests cover the rate limiter (including thread-safety), column normalisation/dedup,
the database layer (suppression, dedup, follow-up eligibility), the warm-up ramp,
template selection/rendering, the unsubscribe/opt-out heuristics, and report generation.

## Docker

```bash
make docker-build

docker run --rm \
  -v $(pwd)/.env:/app/.env \
  -v $(pwd)/data:/app/data \
  -v $(pwd)/reports:/app/reports \
  -v $(pwd)/recruiters.csv:/app/recruiters.csv \
  recruiter-outreach --csv recruiters.csv
```

## Notes on deliverability

- **Resume link over attachment.** Set `RESUME_LINK` to a hosted copy (Drive, Notion,
  personal site) — PDF attachments are one of the most common spam-filter triggers.
- **Warm-up is meaningful but not a substitute for domain authentication.** SPF/DKIM/DMARC
  live at the DNS/domain level and must be configured separately if you control the domain.
- **SMTP RCPT verification is best-effort.** Most networks block outbound port 25;
  `VERIFY_SMTP_RCPT` defaults to off. MX-record checking (`VERIFY_MX=true`, the default)
  is fast, safe, and catches typo'd domains.
