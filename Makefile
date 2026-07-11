# Makefile — generic management tasks.
#
# The Hitchhiker's Guide to Python recommends a Makefile at the project
# root for common tasks like init, test, lint, and clean.
# Reference: https://docs.python-guide.org/writing/structure/#makefile

.PHONY: init install test lint clean docker-build docker-run help

# ── Setup ─────────────────────────────────────────────────────────────────────

init:
	pip install -r requirements.txt

install:
	pip install -e ".[dev]"

install-llm:
	pip install -e ".[dev,llm]"

# ── Testing ───────────────────────────────────────────────────────────────────

test:
	pytest tests/ -v --cov=recruiter_outreach --cov-report=term-missing

test-fast:
	pytest tests/ -x -q

# ── Code quality ──────────────────────────────────────────────────────────────

lint:
	python -m py_compile recruiter_outreach/**/*.py
	@echo "Syntax check passed."

# ── Outreach commands ─────────────────────────────────────────────────────────

dry-run:
	@echo "Usage: make dry-run FILE=recruiters.csv"
	recruiter-outreach --csv $(FILE) --dry-run

check-inbox:
	recruiter-outreach-check-inbox --since-days 14

followups:
	recruiter-outreach-followups

# ── Docker ────────────────────────────────────────────────────────────────────

docker-build:
	docker build -t recruiter-outreach .

docker-run:
	docker run --rm \
		-v $(PWD)/.env:/app/.env \
		-v $(PWD)/data:/app/data \
		-v $(PWD)/reports:/app/reports \
		-v $(PWD)/recruiters.csv:/app/recruiters.csv \
		recruiter-outreach --csv recruiters.csv

# ── Housekeeping ──────────────────────────────────────────────────────────────

clean:
	find . -type f -name "*.pyc" -delete
	find . -type d -name "__pycache__" -delete
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	rm -rf .pytest_cache htmlcov .coverage

help:
	@echo ""
	@echo "Available targets:"
	@echo "  init          Install runtime dependencies from requirements.txt"
	@echo "  install       Install package in editable mode with dev extras"
	@echo "  install-llm   Install with LLM extras (anthropic SDK)"
	@echo "  test          Run full test suite with coverage"
	@echo "  test-fast     Run tests, stop on first failure"
	@echo "  lint          Syntax-check all Python source files"
	@echo "  dry-run       Preview outreach (FILE=recruiters.csv)"
	@echo "  check-inbox   Scan inbox for bounces/replies"
	@echo "  followups     Send due follow-up emails"
	@echo "  docker-build  Build the Docker image"
	@echo "  docker-run    Run outreach in Docker (needs .env + recruiters.csv)"
	@echo "  clean         Remove bytecode, caches, and build artifacts"
	@echo ""
