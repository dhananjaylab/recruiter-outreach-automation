FROM python:3.12-slim

WORKDIR /app

# Install dependencies first (layer-cached until requirements change)
COPY pyproject.toml requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy the package at the project root (no src/ indirection)
COPY recruiter_outreach/ ./recruiter_outreach/
COPY email_templates/    ./email_templates/
COPY setup.py            ./
RUN pip install --no-cache-dir -e .

# .env, data/, and reports/ are expected to be mounted at runtime:
#   docker run --rm \
#     -v $(pwd)/.env:/app/.env \
#     -v $(pwd)/data:/app/data \
#     -v $(pwd)/reports:/app/reports \
#     -v $(pwd)/recruiters.csv:/app/recruiters.csv \
#     recruiter-outreach --csv recruiters.csv
ENTRYPOINT ["recruiter-outreach"]
