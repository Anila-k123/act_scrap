# acts-importer

A standalone, headless batch job that imports Central + Tamil Nadu bare acts
(and their sections) from [India Code](https://indiacode.gov.in)'s public
DSpace REST API into Postgres.

This is **not a web service** — no `runserver`, no views, no REST endpoints.
It's a Django project used only for its ORM/migrations, run manually or from
a scheduled task (cron / Windows Task Scheduler).

## Why this is its own project

- `scrap` (sibling project) does **live** court-status scraping — a request
  comes in, it scrapes right then, and returns. Acts data isn't like that:
  it's imported in bulk, occasionally, ahead of time.
- `ams` (the main app server) reads Acts data but must never scrape or import
  it — that's what caused this to be split out in the first place. `ams`
  will eventually have its own read-only, unmanaged models pointing at these
  same tables (mirroring how it already treats tables it doesn't own).
- This project writes into the **same Postgres database** (`advocate_db`)
  `ams` uses, via its own `.env` — the two codebases share a database, not a
  process or a repo.

## Setup

```
python -m venv venv
./venv/Scripts/pip install -r requirements.txt
copy .env.example .env   # fill in DB credentials (same as ams's .env)
./venv/Scripts/python manage.py migrate
```

## Commands

```
# Feasibility spike - fetch one act, print what comes back, no DB writes
./venv/Scripts/python manage.py spike_fetch_act --query "Right to Information Act 2005"

# Real import - Central + Tamil Nadu by default
./venv/Scripts/python manage.py import_acts
./venv/Scripts/python manage.py import_acts --jurisdictions CENTRAL
./venv/Scripts/python manage.py import_acts --limit 20     # smoke test
./venv/Scripts/python manage.py import_acts --refresh       # re-sync stored acts
```

## Known data gaps (confirmed live against India Code, not bugs here)

- No server-side filter for jurisdiction exists on India Code's API, so
  `import_acts` walks the full ~11,463-item ACT collection and filters
  client-side. As of the last discovery scan: 846 Central acts, 404 Tamil
  Nadu acts.
- Some older/less-referenced acts have section records with no digitized
  body text at all (title/number only) — the source hasn't backfilled OCR
  text for everything. `content`/`footnote` are simply blank in that case.
- `enforcement_date` is free text, not a clean date — some acts store a full
  sentence there instead of a date.
