# Work Cursor rebuild handoff — AdoptIQ

Updated 2026-09-08. Marker: BOB_WORK_CURSOR_REBUILD_HANDOFF_20260908.
Scope: Jeff's work machines (discover macOS/Windows and architecture).
Coordination: https://github.com/rupret007/Bob-the-Bot/issues/4

## Mission and authorization

Jeff wants to take advantage of all the recent work: reconcile useful draft
improvements into one tested source candidate, fix reproduced issues, replace
the rotated Snowflake access token securely on the work machine, complete a
native build cycle, and update the operating documentation.

Source reconciliation, regression fixes, local tests, documentation and a
staged candidate are the work-machine task. No GitHub merge, force-push, tag,
signing, release, deployment, consumer/OneDrive publication, outbound send,
spend, or change to Snowflake access policy is authorized. Ask only for a
concrete remaining held action after independent preparation is complete.
Keep the installed app usable until a tested replacement and rollback are ready.

This document supersedes the older handoff's stale main pin and unspecified
mission. NEXT_MACHINE_PROMPT.md remains the detailed acceptance/release-gate
reference; its old Round 169/main-only selection must not discard later work.
Historical build commands are not authority to publish or embed new secrets.

## Verified source inventory

Audit snapshot: September 8, 2026. Repository: rupret007/AdoptIQ (private).

| Source | Exact SHA | Disposition |
| --- | --- | --- |
| Landed main, including existing handoff | a2a19b05e525d4821e88bd9b34bac05b0f623702 | Required base; product through Round 177 |
| PR #14, Round 178 | 2b3a6926a3c601199063fc012d0492cb91f12ef8 | Peer-path decisions; review delta |
| PR #15, Round 179 | 03814d0e39275784dfcc437ec26eda6b31f89b7c | This-account lived paths; includes #14 ancestry |
| PR #16, Round 180 | 2aa15932bdfc44fec9522b1f6708c2a183c1773d | Barrier-severity/schema approach; reconcile with #17 |
| PR #17, Round 181 | d2ae6ff31e0b7baf0f98322ef237c3a4ede3be3a | Comparable TAC-case severity; distinct from #16 |
| PR #18, Round 182 | 30ffa9c69fcd38a3e4681874b956a2921d68b443 | Question-specific lived-path guidance; not cumulative |
| PR #2/#3 | Parked | Do not resume, close, rebase or incorporate |

All five active draft lines share product merge-base
5abeea4d4e174da192e29622752691db03531476. #14 is an ancestor of #15.
#16, #17 and #18 are separate approaches/changes; #18 does NOT contain the
whole set. Main contains the later documentation commit absent from those tips.

The home canonical checkout is older, at a90fb46 (Round 174); its change is
already merged via #10 as 1c1d2d3. The other inspected home worktree heads are
either merged (#9/#13) or pushed (#14/#15). All four are clean. No missing local
product patch was found in those worktrees. Do not use their branch names as
the latest source authority.

Hosted evidence is missing or blocked before execution for this draft queue.
An empty rollup or zero-step failure is not product validation. Read current
annotations; do not infer today's cause from an old billing note, spend,
dispatch or repeatedly rerun. Local certification and hosted status stay separate.

## 1. Preserve, fetch and account for every change

1. Discover OS/architecture, checkout, Python, current installed version and
   launcher, approved runtime/config/corpus locations, disk space and VPN.
   Inspect Git state and sanitized remote identities without displaying tokens.
   Preserve existing Cisco/internal remotes; origin may NOT be the personal GitHub
   remote. Use an existing correctly mapped remote, or add a distinct GitHub
   read remote without replacing another. Do not reset, clean, stash blindly,
   prune worktrees or copy ignored files into Git.
2. Claim a four-hour lease on coord #4 before source edits; respect another live
   lease. Record America/Chicago time, base, source refs and bounded task.
   Keep one app active at a time on a shared machine.
3. Fetch main and each refs/pull/14/head through refs/pull/18/head into dedicated
   local review refs. Verify every SHA above. If a ref advanced, inspect its
   newer delta and current review/lease before including it; never silently
   accept a moved pin. The work tree used to build must be clean and identified.
4. Start a new non-main work integration branch from verified current main.
   Audit the full relevant history and each three-dot draft diff. Create
   docs/WORK_REBUILD_CHANGE_INVENTORY.md listing each meaningful change as
   already landed, included, superseded with equivalent behavior/tests, or
   deferred with a specific reason. A newer timestamp is not proof of inclusion.
5. Reconcile source changes into that branch without merging GitHub PRs or
   modifying the original draft branches. Use #15's containment of #14 to avoid
   applying #14 twice. Reconcile #16/#17's different severity semantics and
   corpus schema/migration behavior deliberately: preserve barrier versus case
   provenance, unknown/conflict states and historical data compatibility.
   Include #18's question-theme filter without losing earlier peer constraints.
   Resolve code AND test/doc conflicts; no blind ours/theirs selection.
6. Fix demonstrable defects with regression coverage. Stop speculative redesign.
   Push only the clean, secret-reviewed non-main candidate branch and an OPEN
   DRAFT PRE_KAREN when ready. Preserve the original drafts and parked holds.
   A combined candidate is unverified until its own complete checks run.

## 2. Secure Snowflake token replacement — pause for Jeff

The credential is a rotated secret access token, not an assumed RSA key.
Before asking for it, trace the actual work-machine auth source/precedence
through config.py, adoptiq_backend.py and existing Keeper/runtime setup.
Report only the storage mechanism and required field names, never old values.

Ask Jeff: "Please enter the new Snowflake token in the local secure entry
window or existing approved secret manager. Tell me when it is saved; do not
paste it into this chat." Prefer the already approved secret manager. A local
masked terminal prompt must run in Jeff's own interactive terminal outside
agent capture, with echo/tracing/history disabled for the secret; never pass
the token as a command argument, inline environment assignment, URL or log.
Use a protected local runtime file only where the app already supports it,
outside the checkout/build context, with owner-only permissions/Windows ACL.
Provide a way to cancel without changing the current configuration.

Verify the actual token type and connector version. Snowflake documents PAT
authentication through the Python connector's password field; if this is a PAT,
the existing SNOWFLAKE_PASSWORD path may be appropriate. OAuth uses a different
auth path. Do not guess from the token string, log/decode it, or change account,
user, role, warehouse, database, schema, network policy or grants to bypass a
failure. Confirm whether both apps should use the same credential or separate
approved credentials. Re-read secret precedence so an old env/Keeper/bundle
value cannot shadow the replacement. Do not regenerate unrelated app secrets
or corpus encryption keys.

Packaging risk already observed: embed_credentials.py generates recoverable
_bundled_secrets.py; build_pc.bat invokes it; the normal Mac spec can include it.
Obfuscation is NOT secure storage. The new token must not enter source,
_bundled_secrets.py, installers, candidate sidecars, reports, screenshots or
GitHub. Use an approved runtime credential mechanism. If the frozen runtime
cannot read it, make and test the smallest runtime/packaging fix first; do not
remove a security gate merely to produce an artifact. Check the built archive
and frozen process for absence of the token without printing it.

After Jeff explicitly authorizes the bounded work-machine read, use the normal
connector for a minimal read-only connection/metadata check; then approved
report scopes. Record success, account/role match and expiry status in redacted
form. Do not print customer rows or raw authentication errors. Do not revoke or
rotate a server-side credential yourself. Code rollback must not restore an
expired/revoked token; retain the newly approved runtime credential separately.

Reference: https://docs.snowflake.com/en/user-guide/programmatic-access-tokens

## 3. Verification and source/corpus reconciliation

Read README.md, current CLAUDE.md invariants, QUALITY_AUDIT.md, Makefile,
NEXT_MACHINE_PROMPT.md and the relevant draft tests. Use Python 3.12 and
constraints-build113.txt; the constraints filename is dependency lineage,
not the selected app build number. Create an isolated environment. No incidental
major upgrades or fabricated test counts.

Run narrow tests after each integration/fix, then the actual complete gates:

- make verify (lint, Bandit HIGH/MED, dependency audit, full non-eval pytest,
  deterministic Ask AI eval). On Windows without make, run its exact targets
  using that environment's Python; do not omit eval/security/audit.
- Round 175/176/177 peer invariants plus every included Round 178–182 test.
  Inspect actual test filenames at fetched tips rather than inventing them.
- make production-simulation with an approved external CSOne export directory
  and private output directory. Require the exact expected gate inventory,
  both passes, shared digest-bound replay, eight metamorphic checks and all
  DOCX/XLSX pair audits. Missing evidence is not a zero or a pass.
- Browser/main/admin checks on owned unused loopback ports: Ask AI sync/SSE,
  Customer 360, Historical Context, report jobs/history/downloads and degraded
  state; no unrelated stronger theme, false likely-next or live-readiness flag.
- Approved work-only live preflight/reconciliation after credential entry:
  source dates/rows/links/scope, two managers plus All Managers, all report
  families/technologies and customer/subscription scopes. Reconcile canonical
  facts, 17-sheet workbook inventory, Word chart/KPI/lineage parity, distinct
  TAC/BEMS IDs, Action Plan status/aging, pagination and thin/partial evidence.
  Inspect rendered Word/Excel pages for clipping, density and usable source links.

Use privately stored authorized exports only; never transfer them to the home
agent, personal app integrations or public artifacts. Offline replay is not live
validation. Report blocked/skipped checks explicitly and fix real mismatches.

## 4. Build, smoke, rollback and documentation

Inventory the currently installed app and make a verified, protected backup of
its state, reports, settings, customer/alias mappings and corpus BEFORE any
migration. Use SQLite's supported snapshot/backup procedure or stop only the
owned process for a consistent copy; copying an active database alone is not
enough. Preserve corpus encryption material separately. Demonstrate restore
against an isolated copy. Older binaries must not open newly migrated state.

Determine the next unused build identity from current source, installed version
and candidate registry. Main says v1.0.4/source Build 116 with no accepted
candidate; Build 115 is invalidated/NO-GO. Never overwrite/recreate an immutable
old artifact. Stamp versions/docs before final tests and freeze a clean commit.
Use the same exact source commit for native Mac and Windows builds; a source
fix invalidates prior artifacts and requires fresh identity/tests/builds.

Inspect the exact build script BEFORE execution. build_pc.bat can embed secrets,
bump source and publish to OneDrive/consumer latest.json; it is not an unattended
stage-only command. The Mac release script stages but invokes ad-hoc signing.
Keep all publication disabled. Use a documented credential-free candidate path
where suitable, clearly labelled validation-only; it does not replace production
corpus/live acceptance. If a staged production path is missing, implement/test
that boundary before using it. Current signing holds require Jeff's explicit
approval for the exact signing command, including ad-hoc signing; prepare other
gates first. Do not install over the working app or alter auto-update channels.

Verify generated build_info, source SHA, lockfile hash, platform/architecture,
artifact SHA-256/size/time and packaged version from actual bytes. Run frozen
startup/API/report/Ask AI/native-library/model-resource smoke in isolated state.
Use safe runtime secret provisioning; clean-environment smoke must not accidentally
depend on an old app/corpus/cache. Then prepare the explicit install decision,
tested backup/restore steps and separate publication decision for Jeff.

Update README current pointer, WORK_CURSOR_HANDOFF, NEXT_MACHINE_PROMPT,
WORK_MACHINE_BUILD116_PROMPT (or its justified successor), active platform
instructions, QUALITY_AUDIT, changelog/version/build metadata and candidate
receipts. Mark historical guidance as historical instead of rewriting evidence.
Record commands/counts, included/superseded/deferred draft deltas, before/after
versions, clean source SHA, artifact hashes, visual review, offline/live/hosted
distinctions, secret-handling mechanism (no value), remaining gates and GO/NO-GO.
Push sanitized source/docs only, OPEN DRAFT PRE_KAREN; AFTER + lease FREE.

## MyAgent suite continuity

Preserve the planned work-app integration. MyAgent reports Circuit invoking a
registered suite MCP connector; no inbound MyAgent API is established. Future
work needs actual registry/auth/hosting/data-policy docs. Keep source attribution,
scope, freshness, deduplication and app-owned decisions. No live connector,
registration, cross-account data transfer, scheduler or send is part of this
rebuild. Do not turn the existing Circuit model client into an invented MyAgent API.
