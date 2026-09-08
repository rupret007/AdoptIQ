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
- Credential precedence was deliberately changed: `SNOWFLAKE_PASSWORD` is validated
  by release preflight but excluded from frozen credential generation. Installed
  runtime configuration overrides legacy bundled values.

## Included source commits

- `6c1c569`, `6e2a9f2`, `d59997c`: PR #15 / inherited PR #14 behavior.
- `fe2f0e8`, `e89fc46`, `6cf6b8e`: PR #16 and fixture isolation.
- `2b7853a`, `ec901ba`, `b373814`: PR #17 and composed severity evidence.
- `8754d8d`, `0f3d53c`: PR #18 and compatibility composition.
- `8bab5b8`: filtered chart, CSOne workbook, and deterministic acceptance fixes.
- `f295526`: runtime-only Snowflake credential handling and fail-closed preflight.

## Preserved or excluded work

- Original draft/parked PRs #2 and #3 remain untouched and are not release inputs.
- Documentation-only conflict copies were not accepted blindly; current runbooks are
  consolidated against the composed implementation and Build 117 identity.
- No candidate bytes, release manifest, installation, OneDrive publication, tag,
  release, or merge is created by source integration.
- Windows packaging is deferred to an approved native Windows host using the same
  frozen commit. macOS verification does not imply Windows approval.

## Validation state

- Focused peer-guidance integration: 65 passed.
- Focused Round 169 report regressions: 52 passed.
- Runtime credential and macOS preflight regressions: 49 passed.
- Full `make verify`, production simulation, live reconciliation, frozen smoke,
  native candidate identity, and manual review are recorded only after they complete.
