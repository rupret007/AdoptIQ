# AdoptIQ — Round 168 handoff (pending Build 116)

The authoritative runbook is `NEXT_MACHINE_PROMPT.md`; the copy-ready work-machine
prompt is `WORK_MACHINE_BUILD116_PROMPT.md` and is kept below 4,000 bytes. Exact gate
results belong in the latest Round 168 section of `QUALITY_AUDIT.md`.

## Release truth

- Product source identity is v1.0.4 Build 116.
- Build 115 retains its original source SHA, artifact name/hash/size, build time, and
  `build_info.txt`, but its manifest/manual template now say `invalidated`. Round 168
  runtime changes make installation, acceptance, promotion, and deployment NO-GO.
- No Build 116 candidate exists yet. Do not invent a candidate SHA, artifact digest,
  byte size, build time, smoke result, live result, or approval.
- A clean approved source commit must precede packaging. Candidate evidence is created
  only from the exact new DMG. Packaging, live acceptance, manual review, and promotion
  are separate fail-closed stages with separate authorization.
- Offline replay never proves production accuracy. Authorized live Cisco validation
  remains mandatory.

## Round 168 product improvements under final validation

- Report publication now enforces supported CSConsole object IDs and canonical links
  across Word, Source Data, evidence rows, and manager drill-through; missing IDs are
  explicit data-quality limitations rather than fabricated links.
- Unresolved defect correlation cannot collapse distinct anonymous records into one
  identity. Ambiguous records are quarantined from decision claims.
- Report-bound Ask AI evaluates report age and source trust: stale, invalid, or future
  timestamps downgrade trust, withhold current-state claims, and remain consistent on
  sync and SSE paths. Aggregate identity limitations are disclosed safely.
- Dense report tables use compact coverage-aware values (`At least N`, `N (stale)`,
  explicit unavailable) while source coverage and workbook detail retain the complete
  evidence. Blank Leader technology renders as `All` rather than ambiguous copy.
- Success Priorities that are policy-blocked are unavailable, not zero. The local
  Snowflake simulator first proves those production paths fail closed, then uses a
  narrow exact-table fixture-only seam to test query shape while every other policy
  decision remains enforced.
- Production-like acceptance fails closed on missing/extra runs, non-literal booleans,
  incomplete two-pass inventories, or publication in the missing-ID negative control.
- Admin history labels artifact checks as integrity rather than claiming data accuracy;
  legacy rows cannot be silently reclassified as live truth.
- Corpus replay is representative and privacy-preserving: metadata-led selection spans
  age/volume/schema edges, bounds rows, and emits aggregate/pseudonymous evidence only.
- Hosted GitHub workflows are developer-candidate-only and credential/corpus-free.
  Production packaging is an authorized local work-machine action, never a hosted tag
  or release lane.

## Non-negotiable accuracy contract

1. Deterministic canonical data owns metrics, risk, charts, source state, freshness,
   evidence, and AI facts. LLM output is downstream narrative only.
2. Word is concise and actionable; the paired exact 17-sheet workbook owns row detail,
   lineage, chart series, source links, quality states, and `Defect_Correlations`.
3. Leader is detailed without overwhelming: Team, Member, and Customer scopes preserve
   prioritized metrics, risks, tracked Action Plans, synthesis, charts, and evidence.
4. Comprehensive stays deeper than Compact; Renewal separates source outlook/exposure
   from AdoptIQ risk; Subscription cannot leak another subscription.
5. Unavailable/partial/stale is never zero. Lower bounds remain lower bounds. Stable
   IDs are required for actionable links. Unknown is allowed only with an explicit
   source-specific reason.
6. Scope authorization fails closed before filtering or source access. Snowflake SQL
   stays parameterized and table-policy allow-listed. Capability exploration is
   metadata-only unless separately approved.
7. Reports, live evidence, exports, credentials, logs, runtime databases, and candidate
   binaries never enter Git.

## Required next action

Complete source gates on this Mac and record exact outcomes. After explicit source
commit/push and packaging authorization, create Build 116 from that exact clean commit;
then create `release_candidates/macos-build116/` using the repository creator. On the
authorized Cisco Mac, run `WORK_MACHINE_BUILD116_PROMPT.md`, reconcile all report and
AI surfaces against live sources, and bind manual review to exact candidate/live hashes.

Any defect returns to source, adds a focused regression, invalidates the packaged
candidate, and restarts packaging/smoke/live/manual gates. Even a green command does
not authorize commit, push, merge, tag, install, promotion, publication, or deployment.

## Security

A GitHub token previously pasted into conversation context must be treated as exposed
and revoked/rotated. Never store it in URLs, shell history, source, prompts, logs, or
evidence. Use an approved credential helper or newly issued credential.
