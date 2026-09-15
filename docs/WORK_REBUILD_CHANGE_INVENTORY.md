# AdoptIQ work rebuild change inventory

Status: Build 117 source integration in progress. This inventory is sanitized; it
contains no credentials, customer rows, generated reports, or live evidence.

## Source selection

- Base: verified `jeff-source/main`.
- Integration branch: `cursor/work-rebuild-integration-20260908`.
- PR #15 was selected as the base peer-guidance implementation and includes the
  useful behavior from PR #14: outcome-aware decisions and this-account lived paths.
- PR #16 was composed on top for comparable adoption-barrier severity. Its tests were
  isolated from bundled already-lived fixtures so each decision path is deterministic.
- PR #17 was composed rather than substituted: TAC-case severity remains independent
  from adoption-barrier severity, and Word scan-line output remains bounded.
- PR #18 was composed last so Ask AI uses the question theme while retaining the
  already-lived and dual-severity decisions from PRs #15–#17.
- Local Round 169 work was included for per-series chart withholding, robust CSOne
  header detection, and deterministic acceptance output.
- Build 117 zero-secret installers: every authentication value is runtime-only and
  excluded from frozen credential generation because `_bundled_secrets.py` is only
  XOR-obfuscated and is not a confidentiality boundary. This deployment authenticates
  to Snowflake through the Keeper AppRole path, so `KEEPER_SECRET_ID` is the live
  token and `SNOWFLAKE_PASSWORD` is unset. Installed runtime configuration overrides
  legacy bundled values, and preflight fails closed when the runtime `.env` is
  absent, stale, group-readable, or when a generated bundle leaks runtime keys.

## Included source commits

- `6c1c569`, `6e2a9f2`, `d59997c`: PR #15 / inherited PR #14 behavior.
- `fe2f0e8`, `e89fc46`, `6cf6b8e`: PR #16 and fixture isolation.
- `2b7853a`, `ec901ba`, `b373814`: PR #17 and composed severity evidence.
- `8754d8d`, `0f3d53c`: PR #18 and compatibility composition.
- `8bab5b8`: filtered chart, CSOne workbook, and deterministic acceptance fixes.
- `f295526`: runtime-only Snowflake credential handling and fail-closed preflight.
- Leader run-over-run artifact parity: the rebuilt Source Data facts now reuse the
  same prior-run snapshot as the Word document.
- Prior-run movement prose is excluded from current-value KPI extraction, so a
  repeated report no longer disagrees with its own workbook.
- Runtime-only credential set expanded to all authentication values, with
  `scripts/provision_runtime_credentials.py` as the per-machine provisioning step.

## Preserved or excluded work

- Original draft/parked PRs #2 and #3 remain untouched and are not release inputs.
- Documentation-only conflict copies were not accepted blindly; current runbooks are
  consolidated against the composed implementation and Build 117 identity.
- No candidate bytes, release manifest, installation, OneDrive publication, tag,
  release, or merge is created by source integration.
- Windows packaging is deferred to an approved native Windows host using the same
  frozen commit. macOS verification does not imply Windows approval.

## Validation state

- Frozen source commits: `2088e86` (zero-secret contract + regressions),
  `1448ecf` (candidate staging docs).
- Full `make verify`: 8968 passed, 8 skipped, 14 deselected; ruff clean; no
  HIGH/MED bandit findings; Ask AI eval 14 passed.
- Production simulation rerun blocked on this host: disk nearly full caused
  `report_matrix` `OSError: [Errno 28] No space left on device` during the local
  acceptance matrix. Prior green run remains at
  `.adoptiq-acceptance/build117-final-simulation-rerun2/` (`acceptance_complete=true`).
- Keeper AppRole login and private-key retrieval succeeded with the rotated
  `KEEPER_SECRET_ID` resolved from the runtime `.env` (verified by digest only).
- Live Snowflake reconciliation is BLOCKED off-VPN: the account enforces an IP
  allowlist and rejected this host with `390422 (08001) ... is not allowed to access
  Snowflake`. The credential chain is proven; only network egress is unauthorized.
- Frozen smoke, native candidate identity, and manual review are recorded only after
  they complete.
