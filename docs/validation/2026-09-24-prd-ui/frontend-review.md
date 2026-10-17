# PRD frontend interaction review — 2026-09-24

Independent scope: read-only production-code review of company pages, forms, resource hooks and social/account details. Parent owns TaskActivityPage/App/navigation/styles changes, browser review and final integration. This subtask changed only the new regression test file and this report. No production API, database, platform session, browser profile, or actual sending action was accessed.

Requirements reviewed: `docs/product/overseas-lead-acquisition.prd.md`, `interaction-design.md`, `ui-design.md`; relevant technical contracts in `docs/technical/api-contract.md` and `outreach-planned.md`. Target features explicitly marked planned in those documents are not claimed as implemented or accepted.

## Executed verification

| Check | Result |
|---|---|
| Existing frontend suite, before adding tests: `npm test` | **55 passed, 0 failed** |
| New `node --experimental-strip-types --test tests/prdInteraction20260924.test.ts` | **3 passed, 3 failed** |
| Full frontend suite including new regressions: `npm test` | **58 passed, 3 failed**, 61 total |
| Real browser, layout/zoom/focus/scroll inspection | Not performed; parent owns |
| Backend full suite / live API behavior | Not performed by this subtask; do not infer backend coverage from frontend mocks |
| Production source fixes | Not performed; parent owns |

All six new tests use jsdom and mocked fetch. The social mocks reject non-GET requests; they never save outreach records or send messages. Three failing tests intentionally express the required behavior and remain unresolved regression gates, not skipped/TODO tests.

## Actionable P2 findings

### F1 — Changing country retains a different country's state and city

- Source: `frontend/src/forms.tsx`, `TaskForm` country/state handlers.
- Requirement: IX-02, IT01. Changed parent geography must not silently retain mismatched descendants.
- Reproduction: enter US / California / Los Angeles, then change country to CA. The state input still contains California and the city input Los Angeles. Submission reads these unchanged DOM values through FormData and combines them with CA.
- Root cause: `setState('')` resets only the selected region identifier used for suggestions; state/city inputs are uncontrolled and their submitted values remain intact. Changing state similarly does not clear city.
- Regression: **FAIL** — `PRD IT01: changing country must not retain the previous state and city`; observed `California !== ''`.
- Proposed fix: own country/state/city values explicitly; clear incompatible descendant values when the parent changes. Keep custom geography visibly unverified rather than silently treating an old suggested city as compatible.

### F2 — Switching follow-up target silently reassigns the previous target's content

- Sources: `frontend/src/social/SocialFollowup.tsx:63`, `frontend/src/social/useFollowupDraft.ts:9`.
- Requirement: PRD-08, IX-06, IT07. Content belongs to a selected target, and switching must not silently copy it.
- Reproduction: open confirmed account A, click “记录跟进 / 草稿”, type text specifically for A, then choose confirmed account B from the follow-up target dropdown. The form now targets B but still contains A's text. A subsequent save would submit B's entity_id with A's note. No actual save was performed.
- Root cause: target changes update target and confirmation state while a single shared note remains unchanged. The separate `onFollow`/followTarget path also changes the target without resolving the old note.
- Regression: **FAIL** — `PRD IT07: switching targets must not silently attach A draft to B`.
- Proposed fix: route all target changes through one dirty-draft guard. Save successfully or obtain explicit discard consent before changing target; cancel/save failure stays on A. Load B's own draft; copying content must be explicit. This does not require implementing actual sending.

### F3 — Manual reply history is presented without manual provenance

- Sources: `frontend/src/social/presentation.ts:23`, `frontend/src/social/SocialFollowup.tsx` history rendering.
- Requirement: PRD-10, IX-08, UI-06, VT08; technical API contract explicitly says social/outreach is manual recording, not a reply receipt.
- Reproduction: a saved outreach event with state=replied renders “已收到回复” and a timestamp. The history entry does not identify this as a user-entered record, unlike contacted's “人工确认已联系”.
- Regression: **FAIL** — `PRD-10: manually recorded replies must identify their manual provenance`.
- Proposed fix: label replied consistently as “人工记录·已回复” (including selector/history), and keep future provider-confirmed reply events a separate provenance/state model. A confirmation checkbox does not create a platform receipt.

### F4 — Returning from account detail discards unsaved follow-up state

- Sources: `frontend/src/pages/CompanyDetailPage.tsx` account/list branches and their SocialPanel keys; `frontend/src/social/useFollowupDraft.ts:5`.
- Requirement: current-path IT08 / UI-07, independently of the planned full sending editor.
- Concrete source trace: account detail mounts SocialPanel with key=selected; returning to company mounts another SocialPanel with key=`social:${selected}` in a different branch. The first panel is unmounted, and its note/target state exists only in useState. The back button calls onBack without a dirty guard. Opening the account again therefore starts a new empty draft.
- Status: **source-confirmed, not exercised by the new DOM tests**. The existing socialPanelDom test only verifies a same-panel refetch after a decision; it does not cover the CompanyDetailPage branch transition.
- Proposed fix: keep per-company/per-target draft ownership above the conditional panel, and guard real back/navigation actions. Persist saved drafts independently of component lifetime. Do not claim the same-panel refetch test covers navigation or reload preservation.

## Other priority checks and limits

| Requirement / risk | Status and evidence |
|---|---|
| Refusal guard | **TESTED PASS:** do_not_contact account detail has no editable follow-up form or “记录跟进 / 草稿” action; read-only history area remains. No non-GET mock calls. This checks the UI with current refusal data, not backend races. |
| Late response / wrong selected detail | **TESTED PASS:** delayed response A arriving after selected B does not overwrite B; useResource's effect cancellation guards the stale response. |
| Removed account fallback | **TESTED PASS:** missing account in a successful social response says the record is unassociated/removed and exposes a working return action. |
| HTTP 404 company detail | **INSPECTED ONLY:** CompanyDetailPage uses generic Resource failure handling, which has no HTTP-status-specific removed-record state. Its global navigation is parent-owned. A missing social entity in HTTP 200 is not an HTTP 404 test; parent should exercise the latter explicitly. |
| Filters / current-page selection | **INSPECTED ONLY:** CompaniesPage clears selection on filter/page/version changes, intersects selection with refreshed visible IDs, and disables batch execution on loading/error. No demonstrated P1/P2 scope expansion found. No new DOM batch-selection test added. |
| Contact/source chronology | Existing frontend suite covers contact presentation, grouping and social display. No new live chronology verification; passing tests do not prove every historical record is correct. |
| Failed save retains input | Source shows CompanyForm does not clear DOM input on failure; SocialFollowup clears note only after a successful await. New failure-injection test not added. |
| Unsaved draft across temporary same-panel loading | Existing socialPanelDom test passes. This is narrower than return/navigation/reload persistence; see F4. |
| External link is not actual contact | No send endpoint exists in current technical contract; new tests perform no external navigation or non-GET social action. Do not treat opening a link as verified contact. |

## Planned / not implemented, not accepted by this review

- Unified email/social target editor, per-target persistent editable drafts, full save/discard navigation contract: PRD-08 is explicitly incomplete. F2/F4 concern unsafe behavior in the already-present social follow-up form.
- Actual email/social sending, sender identity, preview/final admission checks, idempotency, unknown-result reconciliation and delivery/reply receipts: PRD-09 and technical outreach plan are not implemented. No live-send validation was attempted.
- Unified person/channel association and shared-entry identity workflow: target capability is incomplete; existing grouping tests are not acceptance of the full future identity model.
- Contact-entry export with complete social targets and outreach history: planned extension; current CSV company export must not be described as that feature.
- Responsive layout, keyboard focus return, 200% zoom and list scroll restoration: not measured in jsdom; parent browser review remains necessary.

Parent handoff: fix F1–F3 against the three named failing regressions, cover F4 through the real company/account navigation path, then rerun the full frontend suite. This report is an independent review checkpoint, not a claim of final PRD/UI acceptance.
