FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/
COPY email_templates/ ./email_templates/
COPY scripts/ ./scripts/
RUN pip install --no-cache-dir -e .

# .env, data/, and reports/ are expected to be mounted at runtime, e.g.:
#   docker run --rm -v $(pwd)/.env:/app/.env -v $(pwd)/data:/app/data \
#     -v $(pwd)/reports:/app/reports -v $(pwd)/recruiters.csv:/app/recruiters.csv \
#     recruiter-outreach --csv recruiters.csv
ENTRYPOINT ["recruiter-outreach"]
