# Human Review Pilot Guide — DecisionOps V3 Slice

## Purpose

Run a safe, opt-in pilot of DecisionOps review + action capture using de-identified Decision Brief data and synthetic or intentionally scrubbed customer scenarios. The goal is to validate the review-and-outcome feedback path before adding more automation or broad rollout.

## Feature flag

- Keep DecisionOps in local/off by default in existing production settings.
- Explicitly enable only in pilot runs by user choice in this environment.

## Shadow-mode behavior

In pilot mode, DecisionOps must remain transparent and non-authoritative:

- recommended actions can be reviewed,
- decisions can be edited/deferred/rejected,
- outcomes can be recorded,
- no automatic source-system updates or external workflow actions are triggered,
- no external message/notification side effects are sent.

## Authorized data requirements

- Use de-identified Decision Brief snapshots.
- Do not load any customer-identifying raw source rows into pilot analysis reports.
- Never pass direct credentials or production source dumps into the pilot dataset.

## Reviewer roles (pilot)

- `reviewer`: can inspect and set review decisions
- `owner`: optional secondary reviewer for approval
- `auditor`: read-only validation and export role

## Review instructions

- Start from queue order and reason codes.
- For each recommendation:
  - confirm scope and reason for surfacing,
  - choose `accept`, `accept with edit`, `reject`, `defer`, or related reason state,
  - use a structured reason code for non-acceptance,
  - avoid adding free-text unless necessary.
- Edits must be explicit and scoped to overlay fields only.

## Reason-code guidance

Use structured codes (default list):

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

If none applies, choose the closest code and add notes.

## Outcome verification guidance

- Record outcome when a related canonical follow-up is available.
- Keep outcome interpretation explicit:
  - observed,
  - not yet observable,
  - ambiguous,
  - contradicted by evidence.
- Do not infer causality from temporal proximity alone.

## Privacy requirements

- No raw source text export without explicit approval and redaction.
- Do not include private URLs, credentials, or direct identifiers in pilot artifacts.

## Prohibited data

- raw customer names, account IDs, subscription IDs, and direct emails in exports.
- credentials and internal tokens in logs or event payloads.

## De-identification requirements

- Hash/alias scope IDs for any exported pilot dataset.
- Keep a separate local key mapping only in a restricted workspace.

## Pilot success measures

- % of queue items reviewed,
- % decisions that are accepted with/without edit,
- reason-code distribution quality,
- stale-review blockers encountered,
- outcome reporting completion rate after follow-up periods.

## Minimum sample cautions

- Avoid interpreting small sample percentages as calibrated accuracy.
- Use minimum-bucket rules before publishing aggregate signals.

## Stop conditions

Pause the pilot if:

- any review path silently overwrites canonical analysis,
- cross-customer contamination is observed,
- event/state data is no longer reproducible,
- free-text leakage appears in an export or report view.

## Rollback procedure

- Stop/disable pilot mode.
- Preserve local DB for forensics (`decision_operations.db`).
- Rebase from V2 baseline if needed and reinitialize DecisionOps store.

## Export procedure

- Export only with explicit action.
- Include manifest with scope, schema version, export purpose, and excluded fields.
- Exports should omit source/raw-text and direct identifiers by default.

## Known limitations (current slice)

- No production-grade dashboard/Word/Excel merge yet.
- No full calibration export flow yet.
- Limited concurrency and lifecycle control in this slice.

## Avoiding causal over-claim

Reviewers and downstream readers must use phrasing such as:

- "Observed after action,"
- "correlation noted,"
- "causality not established." 

Never state: "action caused the improvement."

## How pilot findings feed policy changes

- Pilot findings are evidence only, not auto-applied policy.
- Any policy tuning must be proposed as a separate change request with explicit owner approval.
