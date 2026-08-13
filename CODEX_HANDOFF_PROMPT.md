# AdoptIQ — Codex continuation prompt (Round 167.4)

Continue AdoptIQ from current `rupret007/main`. Read `CLAUDE.md`,
`NEXT_MACHINE_PROMPT.md`, `HANDOFF_PROMPT.md`, the latest Round 167.4 entry in
`QUALITY_AUDIT.md`, and the applicable `release_candidates/` README before acting.

Build 114 is invalidated and must never be promoted. Active source is v1.0.4 Build
115. Treat `release_candidates/macos-build115/candidate.json` as the only candidate
identity authority after it exists; it pins eligibility, source SHA, artifact
name/hash/size, build time, and sidecars. Local simulation is never a live Cisco or
production-accuracy claim.

Mission: preserve the product’s current manager-decision UX while proving accuracy
across every report and AI path. Word is concise; the paired exact 17-sheet Source
Data workbook contains row detail, chart data, lineage, `Evidence_Links`,
`Defect_Correlations`, and quality states.
Canonical metrics/risk/source state/freshness are deterministic and never authored by
an LLM. Stable IDs are required for links. Unavailable/partial/stale is never zero.
Authorization narrows scope and fails closed on empty/ambiguous identifiers.

First inspect Git cleanliness/upstream and existing changes. Never reset, clean,
force-push, change remotes, expose secrets/customer rows, or overwrite Cisco
connection code. Preserve the user-owned untracked
`CURSOR_HANDOFF.md.pre-fix-backup`.

Run focused tests for any change, then `make verify` and the exact extensive
`make production-simulation` command in `NEXT_MACHINE_PROMPT.md` with the external
CSOne corpus. Require exact scenario inventory, R114 per artifact pair, meaningful
cross-report source comparison, and zero identity/attribution/state/freshness drift.
Do not weaken tests to erase red.

If on the authorized Cisco Mac, use `WORK_MACHINE_BUILD115_PROMPT.md`: the runner
must verify/mount/launch the exact eligible DMG itself, then execute all decision,
A–G, AI, workspace, replay, evidence, and negative-scope gates. Manually reconcile
all report families, two managers plus All Managers, Chart_Data, Metric_Lineage,
Evidence_Links, CSConsole links, Action Plan lifecycle, TAC/BEMS IDs, renewal facts,
unknowns, pagination, and charts. Keep live artifacts outside Git.

Any runtime change after packaging invalidates the candidate, requires a build bump,
and restarts smoke/live/manual evidence. Publication is separate explicit approval.
Only `scripts/promote_mac_release.py` may update OneDrive/latest.json; never call the
manifest writer directly. Preserve any PC slot or its absence and expose the
manifest last.

Finish with exact diff, commands/results, evidence classification, remaining risks,
GO/NO-GO, a sanitized `QUALITY_AUDIT.md` entry, and updated handoff. Push to
`rupret007/main` only after all source gates are green and no sensitive/generated
files are staged.
