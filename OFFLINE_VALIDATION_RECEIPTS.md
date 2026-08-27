# AdoptIQ offline validation receipts

## What this closes

AdoptIQ can now persist and reverify a redacted receipt proving that one exact
sanitized fixture report passed the guarded offline gate inventory. The receipt
survives application restarts because it is stored in a dedicated SQLite ledger,
not the trimmed in-memory/status JSON map.

This is **not** a live-validation, production-accuracy, release, or customer-share
receipt. Its exact honesty assertions always include:

```text
fixture_validation_performed=true
fixture_validation_passed=true
live_validation_attempted=false
live_validation_performed=false
live_validation_passed=false
production_accuracy_claimed=false
manual_source_reconciliation_complete=false
release_ready=false
customer_shareable=false
owner_customer_share_approved=false
ready_for_live_cisco=false
```

## Trust and persistence model

- `offline_validation_receipt.py` defines one strict
  `adoptiq-offline-fixture-validation-receipt/v1` schema. Unknown, missing,
  duplicate, string-ish, oversized, expired, or noncanonical fields fail closed.
- A trusted acceptance runner signs the canonical payload with Ed25519. The private
  key is never stored in the app, database, status JSON, workbook, browser, or this
  repository. Verification requires an explicit public-key allowlist; the runtime
  default is empty.
- The signature binds the receipt to the exact report-history row, privacy-safe
  analysis-id and scope-identity digests, report/scope types, fact fingerprint, Word
  and Excel SHA-256, canonical data-as-of time, source commit, validator-build digest,
  fixture manifest, gate specification, and complete final public web-projection
  digest. Free-text
  build labels are rejected; only their SHA-256 may be stored.
- Acceptance and reload require all public trust bindings explicitly:
  `OFFLINE_VALIDATION_RECEIPT_SOURCE_COMMIT_SHA`,
  `OFFLINE_VALIDATION_RECEIPT_VALIDATOR_BUILD_SHA256`, and
  `OFFLINE_VALIDATION_RECEIPT_FIXTURE_MANIFEST_SHA256`, in addition to the public-key
  allowlist. There are no built-in trust defaults. Missing or malformed configuration
  is `unconfigured`, never verified.
- `enhanced_admin_dashboard_v2.py` stores canonical receipt JSON in the dedicated
  append-only `offline_validation_receipts` table. It stores no customer names,
  scope values, source rows, paths, URLs, or error text. A conflicting second
  receipt is rejected instead of replacing the first.
- Every reload reparses the bounded JSON, rejects noncanonical Base64, reverifies the
  signature and expiry, and rebinds it to the current durable report row, exact
  selector identity, freshly hashed Word and Excel bytes, current code/fixture/build
  identities, and recomputed web projection. Missing trust keys, mutation, replay,
  expiry, artifact loss, or drift returns a closed state.
- The version-2 projection covers the complete path-free public snapshot, including
  visible scope, all metrics, insights, action plans, accounts, charts, evidence,
  source limitations, timestamps, artifact availability, download links, and Ask AI
  binding/URL. It does not truncate long collections. Private display values are
  transient hash input; only the final digest is persisted.
- The Decision Workspace exposes only a small state/boolean summary. Raw payloads,
  receipt IDs, signer IDs, signatures, hashes, and internal errors never cross the
  API boundary.

The SQLite append-only guards protect against accidental application edits; the
Ed25519 signature is the trust anchor. The database itself is not described as a
forensic or tamper-proof store.

The official guarded offline harness intentionally labels every synthetic source
`partial` so it cannot be mistaken for live Cisco coverage. Fixture receipt gates
therefore accept only projection-bound `available`, `zero`, or `partial` fixture
states and the one exact no-live-validation disclosure. Failed, unavailable, stale,
or unknown sources, any additional warning, formula, artifact/hash mismatch, or
evidence-digest failure still invalidates the receipt. This fixture-specific gate is
separate from customer-share readiness, which remains false.

## Supported fixture-runner workflow

The official `scripts/generate_offline_acceptance_artifacts.py` runner provides the
non-test adapter for the receipt ledger:

1. Generate the pinned sanitized customer or subscription fixture artifacts.
2. Call `prepare_generated_offline_validation_receipt(...)`. It reloads and verifies
   the canonical workbook/evidence, recomputes both artifact hashes and the complete
   final public projection, rehashes the exact sanitized fixture against its trusted
   manifest digest, validates every public trust binding, registers or reuses the
   exact local report-history row, and returns only redacted signer fields plus runtime
   hashes.
3. An external authorized signer calls `issue_offline_validation_receipt(...)` with
   those `receipt_fields`, its private Ed25519 key, and a short issue/expiry window.
   The runner and AdoptIQ application never receive or persist that private key.
4. Call `persist_generated_offline_validation_receipt(...)` with the signed envelope
   and explicit public-key allowlist. It repeats preparation from the current bytes
   and projection before the append-only insert. Any artifact, scope, code, fixture,
   build, or projection change fails closed.

The adapter returns no customer/scope value, filesystem path, source row, warning
text, private key, or signature material in its preparation result. It performs no
live connector call, upload, publication, customer send, or deployment.

## Reused code

- Existing `report_history` row identity and persisted artifact hashes remain the
  authoritative run binding.
- Existing `manager_decision_workspace.py` projections remain the only web fact and
  insight source. The receipt hashes the final complete public projection; it does
  not recalculate any metric, report, or narrative.
- Existing current-workbook hash and evidence checks remain required, and current Word
  bytes are now checked as well. This round adds
  no LLM, report generator, source connector, upload, publish, send, deploy, or export
  path.

## Still required before customer use

1. Resolve the owner-held AdoptIQ drafts and choose an authorized work-machine trust
   key/configuration process. Do not commit a private signing key.
2. Run authorized live source reconciliation and manual customer-output review.
3. Define a separate signed **live** receipt with reviewer/owner authorization. The
   offline schema cannot be upgraded or mode-switched into that receipt.
4. Approve access control, privacy, print behavior, and route-level hash rechecks for
   any customer-scoped hosted presentation.
5. Obtain separate exact owner approval before hosting, publishing, sending, or
   production deployment.
