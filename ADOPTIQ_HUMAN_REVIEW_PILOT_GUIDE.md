# Human Review Pilot Guide — DecisionOps V3

## Purpose

Validate the end-to-end human review loop on synthetic or de-identified Decision Brief scenarios:

- review canonical recommendations
- record accept/edit/reject/defer decisions with reason codes
- convert accepted decisions into tracked action records
- record outcome observations
- generate privacy-safe calibration feedback

## Scope and constraints

- Local-only testing with synthetic fixtures or explicitly scrubbed snapshots
- No production data, connectors, live credentials, or autonomous model retraining
- No policy changes are made automatically from pilot data
- Default behavior is conservative and privacy-preserving

## Feature controls

- Export is **disabled by default** and requires explicit user confirmation:
  - endpoint payload must include `confirm_export: "I_UNDERSTAND"`
- Review endpoints accept only request payload fields defined in API contracts
- No external ticketing or workflow mutation occurs from this pilot path

## Shadow-mode behavior

- Canonical recommendations remain immutable.
- Human edits are overlay records only.
- Recommendations are visible as "canonical + human overlay".
- Exported feedback can include raw IDs only when explicitly requested.

## Data and role requirements

- Reviewer roles are operational, not security-critical (no new enterprise RBAC introduced in this slice):
  - Reviewer: can record review decisions and edits
  - Owner: optional second-opinion reviewer for outcome confirmation
  - Auditor: can read/export records
- Keep a separate local mapping if de-identification mappings are introduced for study logs.

## Reviewer playbook

For each queue item:

1. Verify action and customer/portfolio scope.
2. Confirm whether recommendation is still materially applicable.
3. Choose decision:
   - accept
   - accept with edit
   - reject
   - defer
4. For non-accept paths, set a structured `reason_code`.
5. Fill optional notes only when needed.
6. Save and confirm resulting action state.

### Reason-code guidance

- `as_original`
- `owner_corrected`
- `priority_corrected`
- `scope_corrected`
- `evidence_quality`
- `already_completed`
- `wrong_scope`
- `stale_evidence`
- `duplicate`
- `out_of_scope`
- `needs_more_evidence`

### Outcome review playbook

- Record outcome only when follow-up signal is available.
- Use conservative interpretation terms:
  - expected improvement observed
  - no measurable change
  - insufficient evidence
  - contradictory evidence observed
  - human rejected/confirmed assessment
- Never record causality language.

## Privacy and safe export

- Default export fields:
  - no direct IDs
  - no raw rationale/free text
  - pseudonymized actor/action/scope identifiers
- Optional override with explicit flags:
  - `include_raw_ids`
  - `include_free_text`
- Never export raw URLs, credentials, private emails, account names, or customer IDs.

## Pilot success measures

- Decision throughput by queue bucket
- Decision mix (`accepted`, `accepted_with_edit`, `rejected`, `deferred`)
- Reason code quality/completeness
- Revalidation conflict rate on reruns
- Outcome reporting + human confirmation rates
- Any cross-customer leakage or privacy leakage incidents

## Minimum sample and caveats

- Do not infer model-calibration or policy quality from single samples.
- No causality claims are allowed in pilot reporting.
- Report observed correlations only.

## Stop conditions and rollback

Stop immediately if:

- review writes mutate canonical recommendation text or evidence
- one customer can affect another’s queue/action/outcome
- export unexpectedly contains free text/identifiers without explicit override
- event/state history becomes non-deterministic across reloads

Rollback steps:

- Stop review activity
- Preserve `decision_operations.db` for analysis
- Reinitialize local pilot DB if corruption is suspected
- Continue using V2 canonical-only workflow while issues are corrected

## Export procedure

1. Resolve analysis ID in UI/API.
2. Call `POST /api/decisionops/export` with:
   - `analysis_id`
   - `confirm_export: "I_UNDERSTAND"`
   - optional `include_raw_ids`, `include_free_text`, `export_salt`
3. Validate manifest fields before using output.
4. Store export artifacts in a local pilot directory with access restrictions.

## Known limitations (current slice)

- No full DecisionOps workbench UI pages are yet complete (API-first pilot acceptable).
- No full concurrent UI race-hardening.
- No portfolio-level operating brief and no full Word/Excel injection yet.
- No policy/evaluation lab pass has been completed on pilot output.

## Post-pilot review process

- Aggregate pilot outcomes only for research/hypothesis validation.
- Any policy/rank changes must go through an explicit change-request path.
- Continue to enforce synthetic test suites before any production rollout.
