# AdoptIQ DecisionOps V3 Report

- **Report date:** 2026-07-13
- **Working branch:** `codex/adoptiq-decisionops-v3`
- **Commit:** `ea6bc61c5f5b85d65b6e93e3d420fe9152bf3547`
- **Working tree location:** `/Users/jeffstory/Downloads/AdoptIQ_logic_improvement_working_20260713T043647Z`
- **Starting commit verified:** `1627ab1e61cf4a775e06e5ceea1c2013d783f0e8` (`codex/adoptiq-decision-intelligence-v2`)
- **Constraint mode:** local only, synthetic fixtures only, no production systems, no customer data

## Executive Summary

This slice converts the canonical recommendation output into a versioned review + action pathway without touching raw canonical evidence. The main user-visible lift is the ability to: persist a recommended action, record a human review decision (including edit, reason code, and overlay), append review/outcome events, and expose that state through API surfaces.

Decision Operations was selected because it is the minimal safe path to operationalize recommendations while preserving the existing canonical-analysis contract from V2.

What remains unvalidated in this slice:
- full multi-period recurrence + timeline UX surfaces,
- complete portfolio operating brief and policy-evaluation controls,
- robust calibration export implementation,
- full dashboard/Word/Excel integration of all DecisionOps states,
- complete concurrent/stale edit race-hardening in UI.

## Verified Starting Point

- Branch/commit verified before v3 edits:
  - `codex/adoptiq-decision-intelligence-v2`
  - `1627ab1e61cf4a775e06e5ceea1c2013d783f0e8`
- Current branch points to: `codex/adoptiq-decisionops-v3`
- Git status at validation start: clean, local branch clean after patching
- Baseline reference (from the included V2 report and handoff):
  - 6,583 tests collected / 6,521 passed / 11 skipped / 6 deselected / 22 failed / 23 errors,
  - 135/135 Decision Intelligence tests passing,
  - 200/200 adversarial assertions passing,
  - 29/29 workflow and package checks passing,
  - DOCX/XLSX save-and-reopen validations passing.
- Known baseline caveat:
  - remaining non-green nodes are linked to missing Round 56/57 artifacts and baseline environment/tooling constraints.

## Architecture

### Canonical analysis layer

- Canonical source of truth remains `AnalysisBundle` in `decision_intelligence.py` and persisted snapshot files.
- `DecisionOpsStore` reads snapshot files only; it does not rewrite canonical recommendations.

### Human decision layer

- New state is captured in `decision_ops_actions`, `decision_ops_reviews`, `decision_ops_outcomes`, and `decision_ops_events` tables in `decision_operations.py`.
- Edits are overlay-style fields (`review_edited_value_json`, `review_reason_code`) and do not mutate `analysis_fingerprint` or recommendation content.

### Presentation layer

- API endpoints in `app_simple.py` combine canonical state (action/action events/outcomes/reviews) for UI/API consumption.

### Review model

- Decision normalization: accepts a richer decision vocabulary and maps to states via `accepted`, `accepted_with_edit`, `rejected`, `deferred`, etc.
- Decision reason codes normalized to canonical list (`as_original`, `owner_corrected`, `priority_corrected`, `evidence_quality`, etc.) with default fallback.

### Action model

- `decision_ops_actions` stores proposal details, owner, urgency, evidence references, success signals, timing, and review state.
- Duplicate/cleanup behavior: actions are keyed by `(action_id, scope_fingerprint)` and synced as active/inactive across analysis updates.

### Event model

- Every review and outcome write records an event in `decision_ops_events` (`review_*`, `action_synced`, `outcome_recorded`).

### Outcome model

- `decision_ops_outcomes` stores observed signal/value and actor metadata.
- Human-visible outcome states are currently limited to normalized enums: `succeeded`, `not_succeeded`, `in_progress`, `unknown`.

### Feedback model

- Feedback-relevant signals are preserved as review reason codes and outcome records.

### Audit model

- Event table is append-only; decisions/outcomes write audit payloads with analysis fingerprint and timestamps.

### History model

- DecisionOps is coupled to existing snapshot IDs (`analysis_fingerprint`, `analysis_snapshot_path`) and scope (`scope_fingerprint`) for cross-run correlation.

### Persistence and migration design

- SQLite tables auto-create on first use and include schema upgrade helpers:
  - `decision_ops_actions` backfill columns,
  - `decision_ops_reviews` backfill columns.
- Added explicit stale checks for inactive/changed recommendation state.

## Review Workflow

### Queue logic

- `GET /api/decisionops/queue/<analysis_id>` resolves analysis snapshot and syncs recommendations.
- Returns active recommendations plus recent reviews/events/outcomes and computed state.

### Review states

- Persisted decision states include: `proposed`, `accepted`, `accepted_with_edit`, `rejected`, `deferred`, `needs_more_evidence`, `duplicate`, `already_completed`, `out_of_scope`, `needs_revalidation`, `unknown`.

### Edit and rejection behavior

- `POST /api/decisionops/review` stores:
  - normalized decision,
  - normalized reason code,
  - optional edited value overlay,
  - review notes,
  - event record.

### Stale review protection

- If reviewed action is no longer active, the API/store raises `analysis_stale`.
- If `analysis_fingerprint` is provided and mismatched, review is rejected.

### Revalidation

- Initial implementation provides explicit stale detection, but full multi-factor revalidation heuristics are staged for next slice.

### Concurrency behavior

- API-level check remains single-write-attempt semantics with explicit conflict/error signaling; duplicate/conflict-heavy cases still need broader endpoint-level concurrency tests.

## Action Register

### Action identity

- Uses `action_id` + `scope_fingerprint` as durable key.

### State machine

- Current implemented state transitions are persisted via `review_state` + event log (`action_synced`, `review_*`).
- Full explicit multi-state transition table (proposed/assigned/completion/verification) is planned next.

### Ownership, timing, dependencies

- Proposed owner retained from V2 action payload; explicit owner correction is represented via review reason overlay.
- Timing and dependency fields preserved from recommendation fields where available.

### Completion and verification

- Completion is captured by `outcome` entries and remains separate from canonical action record.
- Completion ≠ verification is not enforced as a strict separate state yet, but event separation is implemented.

### Recurrence and deduplication

- Duplicate analysis entries are deactivated when absent and reactivated as needed by scope refresh.
- `sync_from_snapshot` updates existing keys in place to avoid action explosion; repeated run with stable recommendation IDs naturally reuses active records.

## Longitudinal History

### Snapshot series

- V3 uses existing snapshot path/id from V2 as analysis correlation point.
- No dedicated multi-period DecisionOps history table is added in this slice.

### Multi-period comparison

- Comparison available at action level through `analysis_fingerprint` and scope fields.

### Timeline

- Event and reviews arrays provide a chronological trail in `action_detail` API response.

### Migration / compatibility

- Existing V2 history untouched and preserved.

## Outcome Ledger

### Success-signal evaluation

- Outcomes are recorded after action-level submissions, with normalized outcome enums.
- Canonical metric re-evaluation across later snapshots is not yet closed into an automatic ledger verdict in this slice.

### Observation windows

- Explicit window policy is not yet implemented in DB schema; recorded as part of next slice.

### Causal caution

- Outcome and action linkage is correlation-only; no causal claim is introduced by code.

## Feedback and Calibration Readiness

### Reason codes

- Structured codes supported and persisted at review time.

### Descriptive metrics

- Not yet implemented as aggregate exports/dashboards; records are available for future aggregation.

### Minimum sample behavior

- Not yet implemented in this slice.

### Privacy-safe export

- Not yet implemented. Plan: add scoped, schema-versioned export with pseudonymization and field allowlists.

### No-self-modification safeguards

- No model policy/rule changes are driven by collected feedback in this slice.

## User Experience

### Decision Review

- API-backed queue and action detail endpoints are implemented in `app_simple.py`.

### Action Register / Timeline / Operating brief

- Endpoint-level primitives now exist; dedicated UI/dashboard views remain to implement.

### Word / Excel / Ask AI

- Canonical analysis + recommendations remain reused from V2 contracts.
- DecisionOps fields are not yet injected into all Word/Excel/Ask AI surface templates.

## Changes Implemented

- `decision_operations.py`
  - Added normalized decision/reason-code handling.
  - Added review/edition overlays and stale/invalid analysis rejection.
  - Added decision, review, outcome events and persistence schema with migration helpers.
  - Added idempotent action sync behavior and event/audit rows.
- `app_simple.py`
  - Added/updated API routes:
    - `/api/decisionops/queue/<analysis_id>`
    - `/api/decisionops/review`
    - `/api/decisionops/outcome`
    - `/api/decisionops/action/<analysis_id>/<action_id>`
  - Added support for review reason code/edit payload forwarding.
- `tests/test_decision_operations.py`
  - Added focused tests for:
    - stale review rejection,
    - reason-code/edit overlay persistence,
    - queue/outcome/review endpoint pass-through,
    - migration/backfill validation.

## Validation

Executed in this environment:

- `python3 -m py_compile decision_operations.py`
- `python3 -m py_compile app_simple.py tests/test_decision_operations.py`
- Manual focused store-path script (fresh DB) for one recommendation, one review edit, stale check, and one outcome record
- Focused pytest attempts were blocked by missing environment dependencies despite installing lightweight test/runtime packages.
  - `python3 -m pytest tests/test_decision_operations.py -q`
  - Fails at collection due missing optional packages (`openpyxl`, `hvac`, `cryptography`, `snowflake.connector`, etc. at intermediate steps)

Baseline and full-suite comparison not rerun in this environment; existing V2/full-suite figures are preserved from prior verified reports.

## Remaining limitations

- No official DecisionOps V3 migration path from legacy DecisionOps records yet.
- No full concurrency race-control test matrix.
- No dedicated calibration export or portfolio summary integrations.
- No dedicated UI pages for action register/timeline/portfolio brief.
- No policy lab.
- This environment lacked optional dependencies required for complete pytest collection.

## Next highest-value step

Implement the visible Action Register + Customer Timeline surfaces on top of current DecisionOps primitives and add a privacy-safe calibration export with pseudonymized identifiers.
