Recruiter Outreach Automation
=============================

.. toctree::
   :maxdepth: 2
   :caption: Contents:

   usage
   configuration
   api

Overview
--------

Recruiter Outreach Automation is a personalized cold-outreach tool with:

- Deliverability hardening (warm-up ramp, MX/SMTP verification)
- Persistent send history backed by SQLite
- Bounce and reply detection via IMAP
- Follow-up sequences with configurable delays
- Compliance helpers (unsubscribe footer, opt-out suppression)
- CSV/TSV/Excel/JSON/PDF input with ~30 column-alias mappings

Quick start
-----------

.. code-block:: bash

   pip install -e .
   cp .env.example .env   # fill in your credentials
   recruiter-outreach --csv recruiters.csv --dry-run

For full setup and configuration options, see README.md or the
configuration reference in ``docs/configuration.rst``.
