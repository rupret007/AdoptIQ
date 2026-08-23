# AdoptIQ Round 169 / pending Build 116 work-machine prompt

Use Claude Cowork Fable 5 with Extra/Very High reasoning: Plan first, then Build only
after reviewing the plan. Read `CLAUDE.md`, `NEXT_MACHINE_PROMPT.md`,
`HANDOFF_PROMPT.md`, the latest `QUALITY_AUDIT.md`, and the invalidated Build 115
README. Work from the exact approved Round 169 source on `rupret007/main` while
preserving the Cisco repo/remotes and existing Keeper, Snowflake, CircuIT, CSConsole,
CSOne, and OneDrive configuration. Never reset/clean/force-push, expose secrets or
customer rows, weaken tests, or replace connection code wholesale.

Build 115 is invalidated/NO-GO. Build 116 is source-only: no candidate identity,
artifact hash/size, live validation, release approval, or production-accuracy claim
exists yet. First require a clean tree, fetch both authorized remotes, verify ancestry,
and record the exact source SHA. Run Python 3.12 with `constraints-build113.txt`, then
`make verify` and:

`make production-simulation PY=.venv/bin/python CSONE_CORPUS_DIR='<approved external CSOne export folder>' OUTPUT_DIR='.adoptiq-acceptance/build116-prebuild'`

Require exact inventories; all report families/selections, technologies, two named
managers plus All Managers, Leader Team/Member/Customer, customer/subscription scopes,
degraded states, manager isolation, workspace, Ask AI sync/stream, replay, R114 pair
audits, all-sheet/field cross-family parity, one digest-bound prepared replay shared by
both runtimes, and all eight metamorphic truth checks. A timeout, skip, malformed or
extra result, missing link/chart, unexplained Unknown, literal undefined/null, false
zero, stale/partial mislabel, identity/attribution/freshness drift, or unauthorized row
is a defect. Local/simulated evidence remains `live_validation_performed=false`,
`production_accuracy_claimed=false`, and `release_ready=false`.

With VPN and existing authorized access, run the metadata-only Snowflake capability
profiler from `NEXT_MACHINE_PROMPT.md`; query no customer rows and never widen policy.
Then run normal-app live preflight and two independent passes across every report
family and required scope. Reconcile Word KPIs/charts/claims to the exact 17-sheet
paired workbook (`Chart_Data`, `Metric_Lineage`, `Evidence_Links`, source rows, and
`Defect_Correlations`). Review Leader detail without overload, Comprehensive depth,
Compact brevity, Renewal source outlook versus AdoptIQ risk, Subscription isolation,
Action Plan lifecycle/dates, TAC/BEMS distinct IDs, account/member attribution,
freshness, pagination, and every populated CSConsole link. Open at least one authorized
Action Plan, Barrier, Pulse, and Success Priority link and confirm object, record, and
account. Exercise Ask AI/report-bound AI for evidence, citations, stale/partial state,
unanswerable questions, scope isolation, sync/SSE parity, and sanitized provider errors.

If source changes are needed, add focused regression tests, rerun all gates, and do
not package until source is clean and approved. After an explicit packaging approval,
run the release-gated Mac build from that exact commit. Create
`release_candidates/macos-build116/` only from the exact DMG and generated build_info
using the repository creator; never invent or hand-copy hash/size/time. Verify the
candidate contract and frozen runtime, then let `run_round146_acceptance.py` mount and
launch that exact DMG for live acceptance. Bind a sanitized manual review outside Git
to the live-summary SHA-256.

Finish with exact source/candidate identity, commands/counts, offline versus live
evidence, mismatches, visual findings, and GO/NO-GO. Keep reports/logs/evidence outside
Git. Stop before commit/push/merge/tag/build/install/promotion/OneDrive/latest.json or
deployment unless that specific action is explicitly approved. Only
`scripts/promote_mac_release.py` may promote exact eligible bytes after separate
approval; preserve any PC slot and expose the manifest last.
