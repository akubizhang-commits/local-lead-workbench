# Local Lead Workbench Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development. Work only in assigned files; integrate through the contracts below.

**Goal:** Deliver a runnable local internal-testing application with actual source adapters, durable tasks, company/contact review, exports and scheduled refresh.

**Architecture:** FastAPI owns a SQLite business store; an independent worker executes durable jobs and schedules. React/Vite provides the local UI. Source adapters return observations and never write the business database.

**Tech Stack:** Python, FastAPI, SQLite (stdlib migrations initially), React, TypeScript, Vite, gosom executable, HTTP/HTML extraction.

**Spec:** `docs/requirements.md`, `docs/technical-design.md`, `docs/crawler-research.md`.

## Global Constraints

- No paid services, online models, proxy pools, messaging or invented business records.
- No automatic sample insertion. Test fixtures remain in temporary test databases.
- Discovery needs query, source URL, observation timestamp. Missing Maps executable returns blocked.
- Network fetches reject internal/reserved addresses and validate redirects.
- Live crawl failures never erase existing data or overwrite manual corrections.
- Ruling: use versioned SQLite SQL migrations rather than SQLAlchemy/Alembic initially; keep transaction and migration coverage. This reduces startup dependencies without changing data contracts.
- Ruling: task-local browser component gets separate source capability status; independent website refresh remains usable if Maps is blocked.

## Task 1 — Database and business contracts (parent)

Files: `leadworkbench/db.py`, `leadworkbench/service.py`, `tests/test_store.py`.

- [x] Write tests for idempotent source ingestion, updates preserving overrides, refusal exclusion, recovery leases and calendar scheduling.
- [x] Run failing tests before implementation.
- [x] Implement versioned schema and transactional store.
- [x] Re-run tests against real temporary SQLite files.

```python
store.ingest(record, run_id, query)
store.update_company(company_id, revision, fields, notes, status)
# Re-ingesting the same source must return the same company.
# Observing a changed phone must add history without replacing a manual phone.
```

## Task 2 — Website adapter (delegated)

Files: `leadworkbench/sources/website.py`, `tests/test_website.py`.

- [x] Test private-address/redirect rejection and contact block attribution.
- [x] Implement safe fetch, bounded same-site traversal, identity observations and public channel extraction.
- [x] Verify offline fixture tests. No live test contacts enter the application database.

Contract: `async enrich_website(url: str, *, max_pages: int = 20) -> dict`.
Result keys: `observations` list of `{field,value,source_url,evidence,observed_at}`, `contacts` list of `{name,role,email,url,kind,role_status,association_status,source_url,evidence,observed_at}`, `social_links` list, `pages_checked`, `warnings`, `status`.
Statuses: `completed`, `partial`, `blocked`, `failed`. No personal association without same-block evidence.

## Task 3 — React application (delegated)

Files: `frontend/**` only.

- [x] Build six views: tasks, companies/detail, review, changes, settings.
- [x] Use real API calls with loading/error/empty states; no mock business data.
- [x] Create task, run controls, company correction, contacts/review, exports, schedules and backups.
- [x] Verify TypeScript/build, then parent verifies browser flows.

API contract: `docs/api-contract.md`.

## Task 4 — Maps adapter and validation (delegated)

Files: `leadworkbench/sources/maps.py`, `tests/test_maps.py`, `scripts/install_maps.sh`, `docs/maps-validation.md`.

- [x] Test parsing real documented JSONL shape with synthetic fixtures, missing binary, invalid outputs and stopped subprocess.
- [x] Wrap local executable with bounded query execution and explicit status.
- [x] Pin upstream checkout, build locally if dependencies/network permit; disable telemetry.
- [x] Attempt small real query and report exact success/blocked evidence; do not fabricate output.

Contract: `async discover_maps(query: str, output_dir: Path, *, limit: int = 20, timeout_seconds: int = 300, should_stop: Callable[[], bool] | None = None) -> dict`.
Result: `{status,records,warnings,stop_reason}`. Records: `{source_key,source_url,name,address,phone,website,latitude,longitude,category,observed_at}`.

## Task 5 — API, worker, scheduling, delivery (parent)

Files: `leadworkbench/api.py`, `leadworkbench/worker.py`, `leadworkbench/export.py`, `scripts/start.sh`, `README.md`, integration tests.

- [x] Test auth/origin protection, endpoint validation, durable run transitions and export/backup consistency.
- [x] Implement API contract; single worker lock, cancellation/leases and scheduling.
- [x] Launch real local application; build and check UI in browser.
- [x] Review source security, partial results, schedule idempotency and no fabricated data.
- [x] Document actual supported scope and any live Maps limitations.

## Progress

Started 2026-09-22. No code existed at baseline. Dedicated project initialized on `feat/local-workbench`.


## Delivery verification — 2026-09-22

Runnable internal increment delivered. Python: 94 passed plus 41 subtests, with two upstream deprecation warnings. Frontend: TypeScript/Vite build and 3 tests passed; npm audit found zero vulnerabilities. Actual UI query saved five Maps candidates, recovery and backup creation verified. Website Buyer attribution regression fixed from actual page structure. Full product extensions still pending are explicitly listed in README; this plan completion is not acceptance of all long-term PRD criteria.
