# ADOPTIQ DECISIONOPS V3 REPORT

- Report date: 2026-07-13
- Working tree: `/Users/jeffstory/Downloads/AdoptIQ_logic_improvement_working_20260713T043647Z`
- Branch: `codex/adoptiq-decisionops-v3`
- Commit: `3dc57ab`
- Baseline commit verified: `1627ab1e61cf4a775e06e5ceea1c2013d783f0e8` (`codex/adoptiq-decision-intelligence-v2`)
- Local mode: synthetic fixtures, no customer data, no production systems/connectors

## Executive Summary

DecisionOps V3 is now implemented as a minimal, closed-loop slice: canonical recommendations can be reviewed, annotated, converted into tracked action records, and tied to outcome observations without mutating the immutable analysis snapshot.

Key user lift:
- queue/review workflow now records decision outcomes and reasoned edits
- one canonical action can be approved, updated, and followed through event history
- an outcome path exists for human-confirmed result logging
- privacy-safe calibration feedback export is available and explicit opt-in only

What remains unvalidated in this round:
- full multi-period recurrence and de-duplication policy beyond ID-based identity reuse
- complete Review Workbench and Action Register UX (API primitives exist, dedicated screens are partial)
- portfolio-level operating brief changes and full Word/Excel integration for all DecisionOps fields
- full cross-output parity (dashboard, DOCX, XLSX, Ask AI) for all DecisionOps states
- full-suite hardening beyond currently runnable slices

## Verified Starting Point

- Baseline commit exists and is reachable from the current branch:
  - `1627ab1e61cf4a775e06e5ceea1c2013d783f0e8`
- Current local branch contains V2 baseline plus V3 work:
  - `codex/adoptiq-decisionops-v3`
  - local commit pointer `3dc57ab`
  - clean branch status before edits
- Required engineering handoffs reviewed:
  - `ADOPTIQ_LOGIC_IMPROVEMENT_REPORT.md`
  - `ADOPTIQ_DECISION_INTELLIGENCE_V2_REPORT.md`

## Architecture

### Canonical Analysis Layer

- Immutable canonical recommendations and evidence remain in the V2 `AnalysisBundle` and report snapshot files.
- DecisionOps reads snapshots and does **not** alter canonical recommendation text or evidence IDs in place.

### Human Decision Layer

- Review decisions and overlays are stored in `decision_ops_*` tables via `DecisionOpsStore`.
- Human actions are explicit:
  - reason code
  - edited overlay payload
  - reviewer identity/time
  - source analysis fingerprint
  - audit events

### Presentation Layer

- `app_simple.py` merges canonical action content with decision/action/outcome overlays for queue/detail endpoints.
- Canonical fields remain recoverable and separable from user-entered review fields.

### Domain model and tables

- `decision_ops_actions`: durable tracked action rows keyed by `(action_id, scope_fingerprint)`
- `decision_ops_reviews`: immutable review history with normalized decision/reason and overlay payloads
- `decision_ops_outcomes`: observable result records per action
- `decision_ops_events`: append-only event ledger for review/outcome sync events

## Review Workflow

### Queue logic

- `POST /api/decisionops/export` and `GET /api/decisionops/queue/<analysis_id>` resolve snapshot path from analysis history and sync latest recommendations.
- Syncing is idempotent: recommendations absent from the latest analysis are marked inactive, not deleted.

### Review behavior

- API accepts and normalizes decisions:
  - accept/approve -> accepted
  - deny/reject -> rejected
  - edit -> accepted_with_edit
  - defer/needs_more_evidence/duplicate/out_of_scope/etc -> specific states
- Edit/reject paths persist overlay values and reason codes (no canonical mutation).
- Events are emitted for each state change.

### Stale protection / concurrency basics

- Refreshing recommendations updates the active set and stale-checks writes.
- Review writes reject inactive actions (`analysis_stale`).
- Optional `analysis_fingerprint` mismatches are rejected when supplied to review.
- Concurrent write collision handling is not fully transactional for every UI race case yet; API returns deterministic errors where guarded.

### Revalidation

- Revalidation state can be represented by review states and explicit overlays.
- Full automatic revalidation heuristics (scope/evidence drift scoring) remain a next-step improvement.

## Action Register

- Action identity is primarily `action_id + scope_fingerprint`; repeated runs update existing active actions.
- Event ledger tracks action lifecycle transitions through `review_*` and sync/outcome events.
- Proposed owner/priority/scope corrections are represented as review overlays, not changes to canonical action text.
- Completion vs verification are distinct by design:
  - completion is recorded via `decision_ops_outcomes`
  - verification requires separate human review and may disagree with automatic interpretations

## Longitudinal History

- Action rows include analysis fingerprint and snapshot path for period-to-period correlation.
- Events/reviews/outcomes expose historical order (most-recent-first).
- No dedicated separate policy-managed recurrence table was added in this slice; repeated actionable conditions should continue to map to action identity and can be extended with explicit recurrence metadata in the next slice.

## Outcome Ledger

- Outcomes are recorded with:
  - outcome enum (`succeeded`, `not_succeeded`, `in_progress`, `unknown`)
  - observed signal/value
  - reporter/reasoning notes
- A human can record outcome outcome decisions independent of automatic states.
- The system does not claim causality; all outcome language is explicitly treated as aligned/observed, not causal.

## Feedback and Calibration Readiness

- Added privacy-safe export in `DecisionOpsStore.export_feedback()`.
- Default export behavior:
  - IDs are pseudonymized (salted hash tokens)
  - free text and direct rationale/owner/context fields are suppressed
  - explicit export schema manifest is included
- Export requires explicit confirmation string:
  - request must include `confirm_export: "I_UNDERSTAND"`
- Available via `POST /api/decisionops/export`.
- API returns raw IDs + free text when caller passes corresponding flags.

## User Experience Surface Status

- Decision review/action/outcome endpoints are present and test-covered.
- User-facing workbench, portfolio, and action-ledger pages are implemented in this slice (`/decisionops`, `/decisionops/portfolio/<analysis_id>`, `/decisionops/action/<analysis_id>/<action_id>`).
- Word/Excel integration points currently consume canonical recommendations; DecisionOps overlays are not yet fully embedded in every report surface.
- Ask AI has not yet been wired to explicitly cite full DecisionOps overlays in every response path.

## Changes Implemented (by file)

- `decision_operations.py`
  - stable schema-safe table backfill helpers for action/review/outcome/events tables
  - `_extract_recommended_actions` normalization and dedupe handling
  - review/outcome persistence with reason/overlay fields
  - event recording and deterministic retrieval
  - privacy-safe `export_feedback()` with manifest + schema version + field inclusion model
  - record builders for reviews/outcomes/events and pseudonymization helpers
- `app_simple.py`
  - `POST /api/decisionops/review`
  - `POST /api/decisionops/outcome`
  - `GET /api/decisionops/queue/<analysis_id>`
  - `GET /api/decisionops/action/<analysis_id>/<action_id>`
  - `POST /api/decisionops/export`
- `tests/test_decision_operations.py`
  - migration/backfill regression
  - stale review rejection
  - overlay and reason-code tests
  - endpoint passthrough tests
  - calibration export endpoint and pseudonymization tests

## Validation

Executed locally in this environment:

- `PYTHONPATH=/tmp/snowflake_stub:/opt/homebrew/lib/python3.12/site-packages /opt/homebrew/bin/python3.12 -m pytest tests/test_decision_operations.py -q`
  - result: `16 passed`
- `PYTHONPATH=/tmp/snowflake_stub:/opt/homebrew/lib/python3.12/site-packages /opt/homebrew/bin/python3.12 -m pytest -q tests/test_decision_intelligence*.py`
  - result: `135 passed`
- `PYTHONPATH=/tmp/snowflake_stub:/opt/homebrew/lib/python3.12/site-packages /opt/homebrew/bin/python3.12 -m pytest -q tests/test_decision_operations.py tests/test_decision_intelligence*.py`
  - result: `151 passed`
- `/opt/homebrew/bin/python3.12 -m py_compile decision_operations.py app_simple.py tests/test_decision_operations.py`
  - result: success

Not re-run in this cycle:
- full-repo suite, package/build checks, and DOCX/XLSX save-and-reopen loops (already validated in earlier V2 passes).

## Remaining Limitations

- No destructive or destructive migration operations outside targeted `ALTER TABLE ADD COLUMN`.
- No explicit permission model introduced for export/review roles beyond current UI/API controls.
- No recurrence/dedup beyond action-id/scoped identity.
- No complete concurrent review conflict model with transaction-level locking beyond error paths already exercised.
- Portfolio and customer isolation must continue to be validated under larger integration runs.
- No production connector validation and no deployment.

## Next Highest-Value Step

Implement the dedicated Decision Review Workbench + Action Register UI surfaces and harden revalidation logic for changed recommendations (scope/evidence drift + identity matching), then wire those states into Ask AI and Word/Excel summaries in a single cohesive slice.
