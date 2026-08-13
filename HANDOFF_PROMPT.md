# AdoptIQ — Round 167.4 handoff

The authoritative detailed runbook is `NEXT_MACHINE_PROMPT.md`. The copy-ready
prompt below 4,000 characters is `WORK_MACHINE_BUILD115_PROMPT.md`. Read the latest
Round 167.4 entry in `QUALITY_AUDIT.md` for exact gate evidence.

## Release state

- Active product source: v1.0.4 Build 115 on `rupret007/main`.
- Build 114 candidate: explicitly **invalidated**; it predates final runtime
  cross-report source-window and fail-closed CSOne provenance hardening.
- Build 115 candidate: must be identified only by
  `release_candidates/macos-build115/candidate.json` after packaging. Do not copy a
  hash from chat or infer identity from a filename.
- Live Cisco validation: still required on the authorized work Mac with its existing
  Keeper/Snowflake/CircuIT/CSConsole/CSOne/OneDrive configuration.
- OneDrive promotion: not performed here. Last observed Mac slot was Build 113;
  re-read it at publication time and preserve any PC slot or its absence.
- Production accuracy/release readiness: false until automated live acceptance,
  manual source reconciliation, visual review, and separate promotion approval pass.

## What materially improved

The project now has a realistic offline source lab and extensive prebuild run across
report selections, technologies, managers, scopes, degraded states, evidence links,
and grounded AI. Round 167.4 additionally closes false-green seams found by deep
audit:

- live A–G scenarios use explicit authorized manager/customer values rather than
  embedded defaults;
- report-family KPI gates apply to actual endpoints, not mismatched scenario names;
- expected and completed scenario inventories must match exactly;
- every successful report pair needs a parseable, zero-exit R114 audit;
- equivalent report scopes compare privacy-safe canonical source IDs, attribution,
  source states, and freshness across Compact/Comprehensive/Renewal/Leader;
- Compact explicitly uses the selected window and Leader shares the strict CSOne
  scope/provenance boundary;
- release candidate manifests bind status, source commit, artifact name/hash/size,
  build time, and sidecars; invalidated candidates cannot verify;
- live acceptance mounts and launches the exact candidate DMG instead of trusting an
  unrelated installed process;
- promotion requires every detailed report/AI gate, exact candidate identity, fresh
  smoke, and a manual review hashed to the live summary;
- promotion accepts a valid PC slot or no PC slot, rejects downgrade and same-build
  byte changes, enforces managed OneDrive paths, copies bytes first, and updates the
  consumer manifest last;
- the previously documented direct `write_release_manifest.py` fallback was removed.

## Accuracy findings

Retained pre-fix artifacts exposed real historical route drift: equivalent reports
used different TAC windows and source freshness. A fresh current-code four-family
HTTP simulation against the bounded real-shaped CSOne replay reconciled Compact,
Comprehensive, Renewal, and Leader at two canonical subscriptions and 347 TAC cases,
with matching source states/clocks and 6/6 source comparisons. The final extensive
production simulation and full verification floor are recorded in the Round 167.4
quality journal; they are still offline evidence, not live-source proof.

Extra physical subscription rows used for family-specific context are marked
`Legacy_Record_Type=Family-specific reported fact` and excluded from canonical
subscription identities/KPIs. This preserves format-specific usefulness without
inflating the cross-report canonical count.

## Non-negotiable product rules

1. Canonical deterministic data owns every KPI, chart, risk component, source state,
   freshness claim, evidence row, and report-bound Ask AI fact. LLM text is downstream
   and may never invent or override values.
2. Word is concise and decision-focused; the paired exact 17-sheet Source Data
   workbook owns record detail, lineage, chart data, source links, and raw quality
   context, including `Evidence_Links` and `Defect_Correlations`.
3. Leader must be detailed but not overwhelming: Team, Member, and Customer scopes
   are fail-closed and preserve action tracking, priority metrics, four valid charts,
   account/team synthesis, risks, and evidence links.
4. Comprehensive remains deeper than Compact but does not dump raw rows into Word.
   Compact is a short call sheet. Renewal separates source outlook/exposure from
   AdoptIQ risk. Subscription cannot leak another subscription.
5. Stable record IDs are required for actionable CSConsole links. Missing IDs are an
   explicit data-quality limitation—never `undefined`, `null`, or a fabricated URL.
6. Unavailable/partial/stale sources must be disclosed, never represented as zero.
7. Manager/member/customer/subscription/technology filters may narrow scope only.
   Empty or ambiguous authorization fails before connection/filter work.
8. Snowflake SQL stays parameterized and table-policy allow-listed. Live schema
   exploration is metadata-only unless a separately approved task authorizes rows.
9. Generated reports, live evidence, credentials, source exports, logs, and runtime
   databases never enter Git.
10. Any runtime fix after packaging invalidates that candidate and requires a new
    build number, rebuild, smoke, live run, and manual review.

## Next action

On the Cisco Mac, paste `WORK_MACHINE_BUILD115_PROMPT.md` into Claude Cowork Fable 5
with Extra/Very High reasoning. Use Plan mode first, inspect the plan for preservation
of internal connections/remotes and exact candidate binding, then Build with the same
model. Do not use Auto for this release/accuracy pass.

The live process must be launched by `scripts/run_round146_acceptance.py` from the
exact eligible DMG. It must generate and validate the full report matrix, decision
scopes, AI paths, workspace, replay, evidence, negative scopes, and source
consistency. Then manually reconcile all required report families, two managers and
All Managers, source links, unknowns, charts, pagination, Action Plan lifecycle,
TAC/BEMS IDs, renewal facts, and shared attribution.

If any result needs code, return to source, add a focused regression, invalidate the
candidate, bump the build, and repeat. If all results pass, present a GO/NO-GO and
stop for explicit promotion approval. Never merge/release/publish merely because the
automated command returned zero.

## Security note

A GitHub personal-access token was previously pasted into conversation context. It
must be treated as exposed and revoked/rotated. Do not store it in shell history,
remote URLs, files, prompts, reports, or handoff evidence. Use the machine’s existing
credential helper or an approved newly issued credential.
