FROM python:3.12-slim

WORKDIR /app

# Install dependencies first (layer-cached until requirements change)
COPY pyproject.toml requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy the package at the project root (no src/ indirection).
# recruiter_outreach/ now includes the api/ and auth/ subpackages added
# for the FastAPI + Gmail OAuth2 layer — no separate COPY needed since
# it's all under the same directory.
COPY recruiter_outreach/ ./recruiter_outreach/
COPY email_templates/    ./email_templates/
COPY frontend/           ./frontend/
COPY setup.py            ./
RUN pip install --no-cache-dir -e .

# .env, credentials/ (Google OAuth client secret + issued token), data/,
# and reports/ are expected to be mounted at runtime.
#
# CLI (default — one-off send, e.g. from a cron container):
#   docker run --rm \
#     -v $(pwd)/.env:/app/.env \
#     -v $(pwd)/credentials:/app/credentials \
#     -v $(pwd)/data:/app/data \
#     -v $(pwd)/reports:/app/reports \
#     -v $(pwd)/recruiters.csv:/app/recruiters.csv \
#     recruiter-outreach --csv recruiters.csv
#
# API (override the entrypoint):
#   docker run --rm -p 8000:8000 \
#     -v $(pwd)/.env:/app/.env -v $(pwd)/credentials:/app/credentials \
#     -v $(pwd)/data:/app/data -v $(pwd)/reports:/app/reports \
#     --entrypoint uvicorn recruiter-outreach \
#     recruiter_outreach.api.main:app --host 0.0.0.0 --port 8000
ENTRYPOINT ["recruiter-outreach"]
