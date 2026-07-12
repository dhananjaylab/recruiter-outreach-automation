# Recruiter Outreach Automation

A personalized cold-outreach tool for recruiters, with a FastAPI + Streamlit
human-in-the-loop layer, Gmail OAuth2 delivery, deliverability hardening,
scenario-based follow-up sequences, and bounce/reply tracking.

## What's new in v3.0

| Area | v2.0 | v3.0 |
|---|---|---|
| Interface | CLI only | FastAPI REST + SSE API, Streamlit UI, CLI still works |
| Sending | SMTP + app password | **Gmail API via OAuth2** (SMTP kept as a legacy fallback) |
| Tracking | IMAP + app password | **Gmail API via OAuth2** (IMAP kept as a legacy fallback) |
| Approval | Send immediately | Upload → preview → approve/reject individual rows → send |
| Progress | Log lines only | Live-streamed progress (SSE) in the UI as each email sends |
| Daily volume | Rate-limiter only (didn't actually cap daily volume) | True per-calendar-day cap (`DailySendGovernor`), backed by the DB |
| Send timing | None | Optimal send-window advisory (Tue-Thu, mid-morning) |
| Templates | Role-based (SDE/MLE/DS) + one generic follow-up | **Scenario-based** (cold, referral, post-application, informational interview, event follow-up, alumni) + 3-step follow-up sequence ending in a "breakup" email |
| Subject lines | Hardcoded in Python | Embedded per-template, personalized |

## Architecture

```
Streamlit UI (frontend/)  <---- REST + SSE ---->  FastAPI app (api/)
                                                          |
                       +----------------------------------+----------------------------+
                       v                                  v                            v
             recruiter_outreach/                recruiter_outreach/           recruiter_outreach/
             delivery/ (send)                    tracking/ (bounces)          ingestion/ (parse files)
                       |                                  |
          +------------+------------+          +----------+-----------+
          v                         v          v                        v
  GmailOAuthTransport      SmtpTransport   GmailOAuthMailReader    ImapMailReader
     (default)               (legacy)          (default)             (legacy)
```

`OutreachManager` and `InboxTracker` never talk to Gmail/SMTP/IMAP
directly — they depend on the `EmailTransport` and `MailReader`
interfaces (`delivery/transport.py`, `tracking/mail_reader.py`), the same
Dependency Inversion pattern used throughout this codebase. Swapping
providers is a config change (`EMAIL_PROVIDER=gmail_oauth|smtp`), not a
code change.

The CLI (`recruiter-outreach`, `recruiter-outreach-followups`,
`recruiter-outreach-check-inbox`) still works exactly as before, on top
of the same underlying package — the API is a new layer, not a
replacement.

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate

pip install -e ".[dev]"
cp .env.example .env            # fill in EMAIL_USER at minimum
```

### 1. Connect Gmail (OAuth2 — no password stored)

1. In [Google Cloud Console](https://console.cloud.google.com/), create a
   project and enable the **Gmail API**.
2. Create an **OAuth 2.0 Client ID** of type *Web application*.
3. Add `http://localhost:8000/auth/google/callback` as an authorized
   redirect URI (matches `GOOGLE_OAUTH_REDIRECT_URI` in `.env`).
4. Download the client secret JSON, save it at
   `credentials/client_secret.json`.
5. Start the API (below), then visit `http://localhost:8000/auth/google/login`
   once in a browser — or click **Connect Gmail** in the Streamlit
   sidebar. `credentials/token.json` is created automatically.

Don't want to set up Google Cloud OAuth? Set `EMAIL_PROVIDER=smtp` and
`MAIL_READER_PROVIDER=imap` in `.env`, plus an
[app password](https://myaccount.google.com/apppasswords) for
`EMAIL_PASSWORD` — the legacy path still works identically to v2.0.

### 2. Run the API + UI

```bash
# Terminal 1
make run-api          # uvicorn recruiter_outreach.api.main:app --reload --port 8000

# Terminal 2
make run-ui            # streamlit run frontend/streamlit_app.py
```

Open the Streamlit URL it prints (typically `http://localhost:8501`).
API docs (Swagger UI) are at `http://localhost:8000/docs`.

### 3. Or keep using the CLI

```bash
recruiter-outreach --csv recruiters.csv --dry-run
recruiter-outreach --csv recruiters.csv
recruiter-outreach-check-inbox --since-days 14
recruiter-outreach-followups
```

## The human-in-the-loop flow

1. **Upload** a CSV/TSV/Excel/JSON/PDF file in the Streamlit "Upload &
   Send" tab (or `POST /upload`). It's parsed, normalised, and validated
   — nothing is sent yet.
2. **Preview & approve** — an editable table shows every record with a
   `Send?` checkbox. Uncheck rows you don't want to send to, or catch a
   bad row before it goes out. The send-window advisory (🟢/🟡) tells you
   whether now is a good time to send.
3. **Send** — click "Approve & Send". Progress streams live: each
   email's outcome (sent/failed/skipped, with reason) appears as it
   happens, via Server-Sent Events (`POST /send`) — no polling, no job
   queue infrastructure.
4. **Track** — periodically run Inbox Tracking to detect bounces,
   replies, and unsubscribe requests, which suppress/stop follow-ups
   automatically.
5. **Follow up** — the Follow-ups tab shows who's due for the next touch
   and sends the 3-step sequence with the same live-progress view.

## Outreach scenarios

Beyond "cold outreach," a `Scenario` column (with ~6 recognised aliases:
`scenario`, `outreach type`, `context`, etc.) lets each row pick the
template that actually fits the situation:

| Scenario | When to use it |
|---|---|
| `cold` (default) | No prior contact — falls back to a role-based template (`sde`/`mle`/`data_scientist`) or `default.md` |
| `referral` | A mutual contact suggested reaching out |
| `post_application` | Following up after formally applying |
| `informational_interview` | Asking for a short call, not a job, before applying |
| `event_followup` | Met the recruiter at a conference/career fair |
| `alumni` | Shared university/program as a connection point |

Add a matching `email_templates/<scenario>.md` file to customize any of
these, or add new scenarios entirely — `TemplateStore.select()` looks
for `<scenario>.md`, falling back to role, then `default.md`.

### Subject lines are embedded in templates

Any template can start with a `Subject: ...` line followed by a blank
line before the body — it's parsed out and rendered separately from the
body, so subject lines are personalized and easy to A/B without touching
Python:

```
Subject: Quick question about opportunities at {company_name}

Hi {recruiter_name},
...
```

Templates without an embedded subject fall back to the previous
hardcoded default.

## Follow-up sequence

`followup_1.md` -> `followup_2.md` -> `followup_3.md`, spaced
`FOLLOWUP_DELAY_DAYS` apart (default 4), stopping automatically on reply
or bounce:

1. **Bump** — gentle "in case this got buried."
2. **Add value** — a new angle or detail, not just a repeat.
3. **Breakup** — "I'll take the silence as a sign, wishing you well" —
   deliberately low-pressure. This tends to be the highest-reply-rate
   touch in the sequence; closing the loop removes any awkwardness the
   recipient might feel about not having responded.

## Deliverability engineering

- **True daily volume cap** (`DailySendGovernor`, `delivery/daily_governor.py`)
  — separate from the sliding-window rate limiter
  (`EMAIL_CALLS_PER_PERIOD`/`EMAIL_PERIOD`, which only smooths bursts
  *within* a run). This is backed by an actual DB query for today's send
  count, so a long-running or repeatedly-invoked process can't exceed
  the intended daily volume — the previous design fed the warm-up cap
  into the rate limiter, which reset every period indefinitely.
- **Warm-up ramp** (`WARMUP_START_CAP` -> `WARMUP_CEILING` over
  `WARMUP_DAYS`) for a new or low-volume sender.
- **Send-window advisory** (`delivery/send_scheduler.py`) — surfaces
  whether "now" is Tue-Thu mid-morning (the window 2026 cold-outreach
  data consistently associates with better open/reply rates) in the UI
  before you click send. Advisory by default; `SEND_WINDOW_ENFORCE=true`
  makes it a hard gate.
- **MX-record verification** (`VERIFY_MX`, default on) catches typo'd
  domains before sending.
- **Suppression list** — bounced/unsubscribed addresses are never
  emailed again, checked on every send.
- **Unsubscribe footer** on every email, with opt-out keyword detection
  on replies.
- **Resume link over attachment** — `RESUME_LINK` avoids the spam-filter
  risk of PDF attachments; `RESUME_PATH` is a fallback.

## API reference

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Health check — never fails, even before `.env` is configured |
| `GET` | `/auth/google/login` | Redirects to Google's OAuth consent screen |
| `GET` | `/auth/google/callback` | OAuth callback (handled automatically) |
| `GET` | `/auth/google/status` | Connected account email + granted scopes |
| `POST` | `/auth/google/logout` | Revokes and deletes the local token |
| `POST` | `/upload` | Upload a file -> normalise -> preview (nothing sent) |
| `POST` | `/send` | Approve & send a preview (SSE-streamed progress) |
| `GET` | `/followups/due` | Who's due for the next follow-up step, grouped by step |
| `POST` | `/followups/run` | Send all due follow-ups (SSE-streamed progress) |
| `POST` | `/tracking/check` | Scan the inbox for bounces/replies/unsubscribes |
| `GET`/`POST`/`DELETE` | `/suppressions` | View / add / remove the suppression list |
| `GET` | `/reports` | List past run reports |
| `GET` | `/reports/{filename}` | Download a specific report CSV |
| `GET` | `/reports/deliverability` | Aggregate bounce/reply rate across all sends |

Full interactive docs at `/docs` once the API is running.

## Project structure

```
recruiter-outreach-automation/
├── recruiter_outreach/
│   ├── api/                     # FastAPI layer
│   │   ├── main.py                # app assembly
│   │   ├── dependencies.py        # Settings/Database DI
│   │   ├── preview_store.py       # in-process TTL store (upload -> send handoff)
│   │   ├── events.py              # SSE thread/queue bridge
│   │   ├── schemas.py             # Pydantic request/response models
│   │   └── routers/               # health, auth, ingestion, outreach, followups,
│   │                               # tracking, suppressions, reports
│   ├── auth/
│   │   └── google_oauth.py      # OAuth2 web flow (login/callback/refresh/revoke)
│   ├── delivery/
│   │   ├── transport.py           # EmailTransport ABC + ProgressEvent
│   │   ├── gmail_oauth_client.py  # GmailOAuthTransport (default)
│   │   ├── smtp_client.py         # SmtpConnectionPool + SmtpTransport (legacy)
│   │   ├── factory.py             # picks the configured transport
│   │   ├── daily_governor.py      # true per-calendar-day send cap
│   │   ├── send_scheduler.py      # optimal send-window advisor
│   │   ├── sender.py              # OutreachManager — orchestrates a send run
│   │   ├── rate_limiter.py        # sliding-window burst control
│   │   └── warmup.py              # linear daily-volume ramp
│   ├── tracking/
│   │   ├── mail_reader.py         # MailReader ABC, GmailOAuthMailReader (default), ImapMailReader (legacy)
│   │   └── imap_tracker.py        # InboxTracker — bounce/reply/unsubscribe detection
│   ├── personalization/
│   │   └── templates.py           # scenario/role/follow-up selection + subject parsing
│   ├── ingestion/                 # CSV/TSV/Excel/JSON/PDF -> DataFrame
│   ├── compliance/                # unsubscribe footer + suppression
│   ├── reporting/                 # per-run CSV + deliverability stats
│   ├── followup/                  # scheduler + CLI
│   ├── verification/              # email format + MX + optional SMTP RCPT
│   ├── db.py                      # SQLite: sends, suppressions, meta
│   ├── config.py                  # pydantic Settings
│   └── cli.py                     # `recruiter-outreach` entry point
├── frontend/
│   └── streamlit_app.py         # human-in-the-loop UI
├── email_templates/              # scenario/role/follow-up .md templates
├── tests/                        # 225 tests
├── credentials/                  # Google OAuth client secret + token (gitignored)
├── data/                         # outreach.db (gitignored)
└── reports/                      # per-run CSV reports (gitignored)
```

## Tests

```bash
make test
# or:
pytest tests/ -v --cov=recruiter_outreach --cov-report=term-missing
```

225 tests, covering: the FastAPI routers (including the SSE thread/queue
bridge, using `TestClient`), the Streamlit app's actual execution (via
Streamlit's official `AppTest` harness — both offline and with a mocked
healthy backend), the Gmail OAuth2 flow and both transport/reader
implementations, the daily governor and send-window advisor, scenario
template selection against the real shipped `email_templates/` content,
plus everything from v2.0 (ingestion, normalization, rate limiting,
warm-up, suppression, reporting).

## Docker

```bash
make docker-build

# CLI (default entrypoint)
docker run --rm \
  -v $(pwd)/.env:/app/.env \
  -v $(pwd)/credentials:/app/credentials \
  -v $(pwd)/data:/app/data \
  -v $(pwd)/reports:/app/reports \
  -v $(pwd)/recruiters.csv:/app/recruiters.csv \
  recruiter-outreach --csv recruiters.csv

# API (override the entrypoint)
docker run --rm -p 8000:8000 \
  -v $(pwd)/.env:/app/.env -v $(pwd)/credentials:/app/credentials \
  -v $(pwd)/data:/app/data -v $(pwd)/reports:/app/reports \
  --entrypoint uvicorn recruiter-outreach \
  recruiter_outreach.api.main:app --host 0.0.0.0 --port 8000
```

## Suggested cron (CLI path, unchanged from v2.0)

```cron
0 */4 * * * cd /path/to/project && .venv/bin/recruiter-outreach-check-inbox
0 10  * * * cd /path/to/project && .venv/bin/recruiter-outreach-followups
```

## Notes

- **Single-user, no auth** — this is scoped as a personal local tool
  (CORS is wide open on the API). If you'd deploy this for multiple
  people, add an auth layer and swap `PreviewStore`'s in-process dict for
  something shared across workers.
- **SSE, not a job queue** — `POST /send` and `POST /followups/run` run
  the actual send synchronously in a background thread; progress is
  bridged to the HTTP response via a `queue.Queue`. No Celery/Redis
  needed at this scale.
