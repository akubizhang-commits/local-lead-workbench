# Failure log review — 2026-09-24

Audit snapshot: 2026-09-24T03:50:02.399151+00:00. All timestamps below are UTC.

## Scope and counting rules

Read SQLite using Python sqlite3 with URI `file:data/workbench.sqlite3?mode=ro` and an explicit read transaction. No Store was constructed against production, no database mutation, job replay, platform login, or outbound message was performed. Scanned every row of runs/work_items/social_runs/social_work; market_runs unresolved entries were also checked. Counts are retained records, not unique incidents or all events ever emitted. Run errors often duplicate item messages; never add the two totals together.

For work items, category matching recursively includes message/error/warnings/reason/stop_reason/budget_warning and nested checkpoint logs, with a maximum of one count per category per row. A row may have multiple categories. Latest means the last retained row by run creation date for (stage,target), or social work creation date for (company_id,stage,target); it is not proof of present source health and does not imply identical settings across attempts. No raw company names, emails, URLs, query strings, session data, or browser launch command lines are reproduced. Examples below are manually sanitized, at most one per finding and under 180 characters.

Runtime text log allowlist: data/searxng.log, three top-level data/social-browser/*-login.log files, and data/runs/*/*/maps-*/{stdout,stderr}.log. Also reviewed all 18 retained docs/**/*.log validation artifacts separately. Backups, credentials, browser profiles and caches are not evidence sources. During initial inventory, an overly broad data/runs glob inadvertently read six nested 000003.log storage files before the allowlist was tightened; their contents were not retained in this report or used for conclusions. No further profile/cache files were read.

## Inventory

| Table | Rows | States |
|---|---:|---|
| runs | 14 | blocked=2, completed=4, partial=8 |
| work_items | 179 | blocked=53, failed=11, partial=74, succeeded=41 |
| social_runs | 16 | completed=2, partial=14 |
| social_work | 913 | blocked=498, failed=21, skipped=4, succeeded=390 |

runs creation range: 2026-09-22T03:16:28.228420+00:00 through 2026-09-24T03:18:48.937977+00:00.

social_runs creation range: 2026-09-22T09:28:19.625827+00:00 through 2026-09-23T11:08:25.212043+00:00.

Latest retained work items: 168 (blocked=50, failed=11, partial=70, succeeded=37). Latest retained social work items: 874 (blocked=475, failed=16, skipped=4, succeeded=379).

Market unresolved metadata: 5 market_runs rows, 0 unresolved entries; 0 malformed payloads.

## Website / Maps item categories

| Category | All item rows | Latest item rows |
|---|---:|---:|
| Body byte limit | 19 | 17 |
| Historical page limit | 29 | 28 |
| Depth limit | 26 | 26 |
| Queue limit | 6 | 6 |
| Website elapsed/batch time limit | 21 | 20 |
| Robots unavailable | 32 | 31 |
| Robots explicit disallow | 9 | 8 |
| Malformed robots response | 2 | 2 |
| Robots pacing budget | 1 | 1 |
| Cross-site identity boundary | 8 | 7 |
| HTTP 403 (including robots) | 22 | 22 |
| HTTP 404 | 10 | 8 |
| Other HTTP failure | 3 | 3 |
| TLS certificate/handshake | 7 | 7 |
| Named connection failure | 9 | 9 |
| TimeoutError (includes planning) | 9 | 8 |
| Empty robots reason | 4 | 3 |
| Image content treated as HTML | 3 | 3 |
| Atom content treated as HTML | 2 | 2 |
| Historical PDF as unsupported HTML | 4 | 2 |
| Static JavaScript limitation | 6 | 6 |
| DOM bound | 1 | 1 |
| Maps no validated listings | 4 | 2 |
| Geocoding fallback | 4 | 2 |
| Company limit | 1 | 1 |

Runs with nonempty error: 10. Runs with a nonterminal status: 0. These are summary-level records, not additional failure incidents.

Category coverage: 142 of 179 work items match at least one issue/limit category. Remaining rows by state: succeeded=37.

Important retained-history distinction: three succeeded checkpoint items contain 11 old failed log events (2/6/3), while each reports failed=0, remaining=0, empty failures, and no warnings. Those old events contribute to the category table because this is an all-log audit; they are not currently unresolved failures. The checkpoint logs also contain 18 parsed and 311 excluded events. Parent failure presentation must prefer current summary/failures/warnings over historical checkpoint.log status. A fourth succeeded item with a category is an informational limit, not an unresolved source failure.

Run-level issue categories (deduplicated per run, includes snapshot/progress warnings):

| Category | Count |
|---|---:|
| Body byte limit | 4 |
| Geocoding fallback | 4 |
| Historical page limit | 3 |
| Website elapsed/batch time limit | 3 |
| Robots unavailable | 3 |
| TimeoutError (includes planning) | 3 |
| Empty robots reason | 3 |
| Robots explicit disallow | 2 |
| Cross-site identity boundary | 2 |
| Maps no validated listings | 2 |
| Depth limit | 1 |
| Queue limit | 1 |
| Malformed robots response | 1 |
| Robots pacing budget | 1 |
| HTTP 403 (including robots) | 1 |
| HTTP 404 | 1 |
| Other HTTP failure | 1 |
| TLS certificate/handshake | 1 |
| Named connection failure | 1 |
| Image content treated as HTML | 1 |
| Atom content treated as HTML | 1 |
| Historical PDF as unsupported HTML | 1 |
| Static JavaScript limitation | 1 |
| DOM bound | 1 |

## Social results

| Structured reason | Count |
|---|---:|
| (no structured reason) | 544 |
| provider_unavailable | 210 |
| source_paused | 65 |
| unsupported | 61 |
| network_error | 20 |
| search_unrecognized | 5 |
| missing_website | 4 |
| robots_denied | 3 |
| http_error | 1 |

| Result status | Count |
|---|---:|
| preview_read | 371 |
| blocked | 344 |
| unreadable | 153 |
| failed | 21 |
| succeeded | 19 |
| skipped | 4 |
| auth_required | 1 |

Work state and result status differ intentionally: preview_read is not a complete account read. In particular, succeeded must not be displayed as “all posts collected.”

| Stage | State | All rows | Latest rows |
|---|---|---:|---:|
| platform_search | blocked | 6 | 5 |
| platform_search | succeeded | 10 | 7 |
| profile | blocked | 157 | 151 |
| profile | succeeded | 171 | 165 |
| read | blocked | 122 | 106 |
| read | failed | 17 | 12 |
| read | succeeded | 200 | 198 |
| search | blocked | 210 | 210 |
| website | blocked | 3 | 3 |
| website | failed | 4 | 4 |
| website | skipped | 4 | 4 |
| website | succeeded | 9 | 9 |

Social preview indicators: profile_only=true on 267 rows; partial=true on 155 rows; cache_reused=true on 22 rows. Social run sample arrays contain selected company metadata, not failure logs; all 16 were decoded without reproducing metadata.

## Runtime text logs

Maps: 14 stderr files, 14 stdout files; 758 stderr lines read. All stdout files were empty. Structured error fields were checked even on info-level events. A numOfJobsFailed=0 field is not a failure and not proof of validated listings.

| Category | Log events | Affected subprocess logs |
|---|---:|---:|
| Inactivity timeout | 2 | 2 |
| Browser target/context closed | 6 | 2 |

SearxNG: 7 lines, two ERROR entries for unavailable Git version/branch metadata and one WARNING for missing limiter configuration. These startup diagnostics do not establish why historical search requests timed out. The social result with engine_failures records four timeout entries (two each for DuckDuckGo and Google); 209 further provider-unavailable messages say search was paused. Avoid treating each paused item as an independent engine outage.

All three top-level login launcher logs are empty; this provides no evidence that any platform session is authenticated.

## Historical versus still unresolved

2 historical non-success work-item rows have a later succeeded row for the same (stage,target). The latest main run (2026-09-24T03:18:48Z) is completed, but this does not close failures for other targets. Latest retained non-success rows remain unresolved/unverified until an applicable later result is available; no live replay was performed here.

Previous website fixes are present in the inspected source: style/garment catalogue exclusions, PDF files_unparsed, and autonomous checkpoints with a 20-attempt batch budget. Historical Page limit reached and application/pdf errors remain historical records. Targeted existing filter/batch tests passed in this audit, but old rows must not be rewritten as fixed. PDF parsing is still unavailable.

The 18 historical validation logs include one audit run with six failing tests (57 passed), covering five areas: identity reassignment review; rejected identity counted as procurement; manual status history; region search ignoring address; run-only export metadata (CSV and XLSX). Later validation artifacts record passing suites up to 484 tests, but this audit did not replay those six temporary audit cases. Parent should map those cases to retained regression tests before declaring each closed. Validation failures are separate from source-runtime failures.

## Evidence-backed changes in this subtask

1. Image/feed URLs consumed the HTML crawl budget and became failures. Evidence includes image/jpeg, image/webp, image/gif and application/atom+xml across multiple website item rows. Sanitized example: `<site>/uploads/photo.webp: FetchError: Unsupported content type: image/webp`. Added explicit image and .atom/.rss suffix classification as asset, skipping at link discovery, old checkpoint dequeue and redirect validation. An asset used as the website entry is blocked with an explanation rather than reported as an HTML success. Uncertain extensionless/query-based downloads remain eligible; PDFs keep their existing separate treatment.

2. Exceptions with empty str(exc), especially TimeoutError, lost their type in robots warnings and checkpoint log reasons. Sanitized example: `<site>: robots unavailable: `. The fallback now preserves the exception class, and page timeout warnings no longer end with an empty colon. This improves diagnosis; it does not fix external network availability.

Files changed: leadworkbench/sources/website.py; leadworkbench/website_link_policy.py; new tests/test_website_failure_logs.py; this report. No db.py, api.py, frontend, or existing technical document was edited by this subtask.

## Precise handling recommended to parent

- Website runtime: distinguish policy denial, source HTTP/TLS/network failure, crawl limits, unparsed file and intentionally skipped asset. Keep body/DOM bounds and TLS verification; do not raise limits or bypass robots merely to make a status green. Extensionless non-HTML MIME responses remain an explicit limitation. Record structured exception type, source stage, affected target and retry eligibility; show a bounded sanitized example and counts instead of concatenating full URLs/errors.
- Social search: provider_unavailable (210) is a shared source failure; source_paused (65) is deferred work, not 65 independent account failures. Persist pause scope/provider and causal item, and distinguish manual resume/health check from retrying every account. Preserve engine failure summaries without raw search queries. Current SearxNG startup metadata errors are not sufficient evidence of provider request health.
- Social reading: unsupported (61) should be capability/unavailable, without retry; network_error (20) should retain exception type and bounded retry handling; unreadable (153) needs layout/not-loaded handling without declaring account absence. auth_required (1) should route to an explicit user session action only. Do not infer login necessity from TimeoutError. profile_only / partial must remain visible even when work state is succeeded; cached results need freshness markers.
- Maps: browser target closed appears in six error-bearing events across two subprocess logs, including info-level events. Inactivity timeout appears once in each of two other subprocess logs. Capture process exit code plus bounded stderr error classification; a zero exit code or zero failed-job counter is not enough to call discovery complete. Require validated output, retain retryability for worker/process failures, and never translate empty output to “no businesses.” Geocoding fallback must retain exception type and degraded geographic confidence; do not silently treat text-only search as resolved city bounds.
- Historical presentation: show run timestamp, latest applicable attempt and resolution evidence separately. Group related causes while retaining drill-down to individual items. Do not mutate retained errors to manufacture success after a code change. Parent owns UI/backend integration and historical QA case reconciliation.

## Verification

New offline regression file was first run against unchanged production code: 12 failed, 4 passed (after correcting mock routes so failures exercised the intended assertions). After fixes, command `.venv/bin/python -m pytest tests/test_website_failure_logs.py tests/test_website.py tests/test_website_filter.py tests/test_website_batches.py -q` returned **104 passed, 44 subtests passed**. No network requests or production database writes were used by these tests. Only the targeted website suites were run; this is not a claim that parent UI/backend changes or all historical failures are resolved.

## Bounded Maps / market-plan follow-up — 2026-09-24

This follow-up inspected only application source and synthetic tests, using the retained audit findings above. It did not reopen production logs, browser profiles, caches, or the database, and made no live network requests.

Confirmed defects and fixes:

- `sources/maps.py` previously accepted exit code 0 plus any valid below-cap output as completed/exhausted without examining stderr. Offline fixtures reproduced false completion when an info-level JSON event contained `playwright: target closed` or `inactivity timeout`. The adapter now recognizes these two audited error signatures in the structured `error` field, irrespective of log level. A natural zero-exit job with such an error is partial when valid records exist and blocked otherwise, with `exhausted=false`; records remain available. Stop reasons are `browser_closed` and `inactivity_timeout`. Warnings contain fixed descriptions, not raw browser commands or stderr contents.
- Classification is limited to these audited signatures. It streams the adapter's own stderr using bounded line reads; malformed/oversized lines and unrelated informational messages are ignored. Zero failed-job counters, empty error fields, and incidental timeout wording in an informational message do not prevent genuine below-cap completion. Explicit cancellation, adapter timeout, quantity-cap termination, nonzero exit, and invalid-output handling retain their existing precedence. No resume or retry behavior was introduced.
- Maps launch, cleanup, final-output-read and adapter exceptions could lose diagnostics when `str(exc)` was empty. Empty diagnostics now fall back to the exception class. Adapter exceptions still propagate after failure metadata is written; that metadata now also records the primary adapter error.
- `market_plan._geocode` already correctly preserves empty TimeoutError/OSError/ValueError types, stores them in the failure cache, and avoids immediate duplicate requests; its existing tests pass unchanged. Legacy cache messages correctly avoid guessing a lost exception type. No change was needed to those paths. The outer `plan_market` cache-failure handler still emitted an empty reason, so it now falls back to the exception class while preserving partial status, absent geometry, and root-only text searches.

Follow-up changed files: `leadworkbench/sources/maps.py`, `leadworkbench/market_plan.py`, new `tests/test_maps_failure_logs.py`, and this appended audit section. No db/API/frontend files were edited.

Verification: the new regression file first reported **9 failed, 4 passed** against the previous source, demonstrating false exhaustion and empty diagnostics. After the fixes, `.venv/bin/python -m pytest tests/test_maps_failure_logs.py tests/test_maps.py tests/test_market_plan.py -q` returned **78 passed in 16.68s**. Subprocess fixtures and cache/metadata artifacts were confined to pytest temporary directories. These tests verify local behavior, not recovery of any historical or currently deployed source job.
