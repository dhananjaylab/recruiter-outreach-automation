# Recruiter Outreach Automation (v2)

Sends personalized outreach emails to recruiters, loaded from CSV/TSV/Excel/JSON/PDF,
with deliverability hardening, persistent send history, bounce/reply tracking,
and follow-up sequences.

## What's new in v2

The project has been restructured into an installable `src/` package and
gained the pieces that were previously missing entirely:

| Area | v1 | v2 |
|---|---|---|
| State between runs | none — every run stateless | SQLite (`data/outreach.db`): send history, suppression list |
| Duplicate sends | possible | blocked automatically via send history |
| Bounces | invisible | detected via IMAP, address auto-suppressed |
| Replies | invisible | detected via IMAP, stops follow-ups |
| Follow-ups | none | configurable sequence, skips repliers/bounces |
| Sending volume | fixed rate limit | warm-up ramp (low → ceiling over N days) |
| Resume delivery | PDF attachment only | hosted link preferred; attachment is a fallback |
| Personalization | 2 fields (name, company) | + role-based templates, optional LLM opening line |
| Pre-send checks | email regex only | regex + MX record (+ optional SMTP RCPT probe) |
| Compliance | none | unsubscribe footer + opt-out detection + suppression list |
| Reporting | log lines only | per-run CSV report + per-domain success/failure breakdown |
| Config | raw `.env` string parsing | validated with pydantic, fails fast with clear errors |
| Layout | flat `src/` scripts | proper installable `src/recruiter_outreach/` package |

### Deliberately not implemented

- **LinkedIn profile lookup** — scraping or unofficial API access to enrich
  contacts touches LinkedIn's ToS and personal-data-collection concerns
  directly; not something to build into an automated tool. If you want this,
  a manual `Role`/`Company` column (already supported) or LinkedIn's own
  official Talent/Sales Nav APIs (with proper authorization) are the
  legitimate paths.
- **Web dashboard** — a real UI (FastAPI/Flask + frontend) is a genuinely
  separate project. `--dry-run` plus the CSV reports in `reports/` cover
  review/audit for now; happy to help build a dashboard as a follow-up if
  you want it.

## Project structure

```
recruiter-outreach-automation/
├── README.md
├── pyproject.toml              # installable package, console scripts
├── requirements.txt
├── .env.example                # copy to .env
├── Dockerfile
├── .github/workflows/ci.yml    # runs pytest on push/PR
├── email_templates/
│   ├── default.md              # fallback template
│   ├── sde.md / mle.md / data_scientist.md   # role-based variants
│   └── followup_1.md           # follow-up sequence step 1
├── src/recruiter_outreach/
│   ├── cli.py                  # main send command
│   ├── config.py                # pydantic Settings + load_settings()
│   ├── db.py                    # SQLite: sends, suppressions, meta
│   ├── logging_setup.py
│   ├── ingestion/                # CSV/TSV/Excel/JSON/PDF -> DataFrame
│   │   ├── loader.py
│   │   ├── pdf_extract.py        # 3-tier: table -> regex -> LLM
│   │   └── normalize.py          # column aliases, validation, dedup
│   ├── verification/
│   │   └── email_verifier.py    # format + MX + optional SMTP RCPT check
│   ├── personalization/
│   │   ├── templates.py         # role/follow-up template selection
│   │   └── llm_personalizer.py  # optional per-recruiter opening line
│   ├── delivery/
│   │   ├── rate_limiter.py      # thread-safe sliding window
│   │   ├── warmup.py            # linear send-cap ramp
│   │   ├── smtp_client.py       # per-thread SMTP connection pool
│   │   └── sender.py            # OutreachManager — orchestrates a send
│   ├── tracking/
│   │   └── imap_tracker.py      # bounce/reply/unsubscribe detection
│   ├── followup/
│   │   └── scheduler.py         # finds + sends due follow-ups
│   ├── compliance/
│   │   └── suppression.py       # unsubscribe footer + opt-out detection
│   └── reporting/
│       └── report.py            # per-run CSV + domain breakdown
├── scripts/                     # thin wrappers for running without install
│   ├── send_outreach.py
│   ├── check_inbox.py
│   └── send_followups.py
├── tests/                       # 35 tests across db/rate-limiter/templates/etc.
├── data/                        # outreach.db lives here (gitignored)
└── reports/                     # per-run CSV reports (gitignored)
```

## Setup

```bash
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -e .                  # installs the package + console scripts
# or: pip install -r requirements.txt
```

Copy `.env.example` to `.env` and fill in your details — every field is
documented inline, including the new warm-up, follow-up, and verification
options.

> **Gmail users:** use an [App Password](https://myaccount.google.com/apppasswords),
> not your regular password, for both `EMAIL_PASSWORD` (SMTP) and IMAP.

## Running

Once installed (`pip install -e .`), three commands are available:

```bash
# Send outreach from any supported format
recruiter-outreach --csv  recruiters.csv
recruiter-outreach --xlsx HR_contacts.xlsx
recruiter-outreach --pdf  HR_contacts.pdf
recruiter-outreach --json export.json --dry-run    # preview, sends nothing

# Check the inbox for bounces/replies/unsubscribes (run periodically)
recruiter-outreach-check-inbox --since-days 14

# Send any due follow-ups (run daily, after check-inbox)
recruiter-outreach-followups
```

Without installing, the same commands work via the wrapper scripts:
`python scripts/send_outreach.py --csv recruiters.csv`, etc.

### CLI flags

| Flag | Purpose |
|---|---|
| `--csv / --tsv / --xlsx / --pdf / --json` | input file (pick one) |
| `--dry-run` | load, validate, and preview — sends nothing |
| `--save-csv OUTPUT` | save the normalised list before sending |
| `--no-llm` | disable the LLM fallback for unstructured PDFs |
| `--env-file PATH` | use a `.env` file other than the default |

### Suggested cron setup

```cron
# Check for bounces/replies every 4 hours
0 */4 * * * cd /path/to/project && .venv/bin/recruiter-outreach-check-inbox

# Send due follow-ups once a day, after the morning inbox check
0 9  * * * cd /path/to/project && .venv/bin/recruiter-outreach-check-inbox
0 10 * * * cd /path/to/project && .venv/bin/recruiter-outreach-followups
```

## How the pieces fit together

1. **Ingestion** (`ingestion/`) reads your file, auto-detects encoding/delimiter/sheet,
   maps ~30 column-name aliases (`"Work Email"`, `"HR Name"`, `"Job Title"`, …) onto
   `Name`/`Email`/`Company`/`Role`, validates emails, and deduplicates.
2. **Pre-send checks** (`delivery/sender.py` + `verification/`, `compliance/`, `db.py`):
   for every recruiter, skip if suppressed, already sent to, or has no valid MX record.
3. **Warm-up** (`delivery/warmup.py`) computes today's send cap from a linear ramp
   stored in the database, so a fresh sender doesn't blast at full volume immediately.
4. **Personalization** (`personalization/`) picks a role-specific template (or falls
   back to `default.md`), optionally adds an LLM-generated opening line, and appends
   an unsubscribe footer.
5. **Delivery** (`delivery/smtp_client.py`, `sender.py`) sends via a per-thread SMTP
   connection pool with retries, and records every outcome to the database.
6. **Reporting** (`reporting/report.py`) writes a per-run CSV to `reports/` with
   sent/failed/skipped counts and a per-domain breakdown.
7. **Tracking** (`tracking/imap_tracker.py`), run periodically, scans the inbox for
   bounce notifications and replies, updating the database so bounced addresses are
   suppressed and repliers stop receiving follow-ups.
8. **Follow-ups** (`followup/scheduler.py`) queries the database for recruiters whose
   last send is old enough, hasn't bounced or replied, and sends the next step.

## Notes on the deliverability changes

- **Resume link over attachment.** Set `RESUME_LINK` to a hosted copy (Drive, Notion,
  a personal site) — attachments are one of the most common spam-filter triggers.
  `RESUME_PATH` still works as a fallback if you're not ready to host a link.
- **Warm-up is meaningful but not a substitute for domain authentication.** If you're
  sending from a bare Gmail address at real volume, SPF/DKIM/DMARC live at the DNS/domain
  level and can't be configured from this codebase — they need a domain you control
  (e.g. via Google Workspace, SES, Postmark). Worth doing before scaling volume further.
- **SMTP RCPT verification is best-effort.** Most home/office networks block outbound
  port 25, and many mail servers accept every RCPT and bounce later, so
  `VERIFY_SMTP_RCPT` defaults to off. MX-record checking (`VERIFY_MX`, on by default)
  is fast, safe, and catches typo'd domains.

## Tests

```bash
pip install -e ".[dev]"
pytest tests/ -v --cov=recruiter_outreach
```

35 tests cover the rate limiter (including thread-safety), column
normalization/dedup, the database layer (suppression, dedup, follow-up
eligibility), the warm-up ramp, template selection/rendering, the
unsubscribe/opt-out heuristics, and report generation.

## Docker

```bash
docker build -t recruiter-outreach .
docker run --rm \
  -v $(pwd)/.env:/app/.env \
  -v $(pwd)/data:/app/data \
  -v $(pwd)/reports:/app/reports \
  -v $(pwd)/recruiters.csv:/app/recruiters.csv \
  recruiter-outreach --csv recruiters.csv
```
