Finish Round 167.4 on the Cisco Mac with Fable 5 Extra/Very High: Plan then
Build. Read `CLAUDE.md`, `NEXT_MACHINE_PROMPT.md`, `QUALITY_AUDIT.md`, and
`release_candidates/macos-build115/README.md`.

Validate `rupret007/main` against immutable v1.0.4 Build 115 and prove reports/AI
against authorized Cisco sources. Candidate identity is only
`release_candidates/macos-build115/candidate.json`. Build 114 is invalid. Any product
fix invalidates Build 115; bump, rebuild, and repeat all gates.

1. Preserve work/remotes. Fetch `rupret007/main`; require `main` = upstream and
candidate SHA an ancestor. Never reset/clean/
force-push, expose credentials/customer rows, or replace connection code wholesale.
Verify the DMG with `scripts/release_candidate_contract.py`; stop on any
name/hash/size/sidecar/status mismatch.
Obtain the exact DMG by approved encrypted transfer, never Git. If unavailable,
bump/rebuild; never recreate Build 115.

2. With Python 3.12 + `constraints-build113.txt`, run `make verify`, then
`make production-simulation CSONE_CORPUS_DIR='<approved export folder>'`. Require
exact inventory, R114 pair audits, and meaningful cross-report source
consistency. A skip, source/freshness mismatch, unexplained unknown, or missing link
is a defect. Run the metadata-only Snowflake profiler in `NEXT_MACHINE_PROMPT.md`;
never query rows, widen allow-lists, or call denied access zero.

3. Create `$EVIDENCE` outside Git; run:
`.venv/bin/python scripts/smoke_frozen_candidate.py --candidate "$DMG" \
 --expected-version 1.0.4 --expected-build 115 --require-release-corpus \
 --summary "$EVIDENCE/frozen-smoke.json"`
Require exact bytes, frozen identity, the manifest-recorded corpus inventory,
hybrid retrieval, ready models, and zero dense backlog.

4. Do not prestart the app. Let the runner verify, mount, and launch the exact DMG:
`.venv/bin/python scripts/run_round146_acceptance.py \
 --output-dir "$EVIDENCE/live-summary" \
 --retain-sensitive-dir "$EVIDENCE/live-artifacts" work-machine \
 --candidate-dmg "$DMG" \
 --candidate-manifest release_candidates/macos-build115/candidate.json \
 --manager '<manager>' --member-email '<authorized member>' \
 --customer-name '<unambiguous customer>' \
 --customer-member-email '<authorized owner>' \
 --ambiguous-customer-name '<known ambiguous customer>' \
 --subscription-id '<authorized subscription>' \
 --technology 'All Contact Center' --days 90 \
 --as-of '<current precise UTC timestamp>' \
 --csone-file '<approved current CSOne xlsx>'`
No skips. A–G must cover configured managers/report families; four decision scopes
run twice; AI/workspace/replay pass; equivalent reports agree on
IDs, attribution, source state, and bounded freshness. Invalid scopes fail before
source access.

5. Require all 17 sheets including `Defect_Correlations`. Reconcile KPIs/charts,
claims, and evidence to `Chart_Data`, `Metric_Lineage`, `Evidence_Links`, and live
records. Review Leader Team/Member/Customer; Comprehensive/Compact/Renewal
portfolio/customer; Subscription; two managers; All Managers. Open one Action Plan,
Barrier, Pulse, and Success Priority link; confirm object, record, account/claim.
Check AP lifecycle/dates, TAC/BEMS IDs, renewal exposure vs AdoptIQ risk, attribution,
pagination/charts, `undefined`/`null`, unexplained `Unknown`, false zeroes, and partial
disclosure. Copy `manual-review.template.json` outside Git,
bind it to the live-summary SHA-256, and attest only from evidence.

6. Report commands, counts, hashes, failures, samples, and GO/NO-GO. Do
not commit live artifacts, weaken tests, or let LLM prose author facts. Before push/
merge/tag/OneDrive/manifest/install/deploy, stop for approval. Only after approval
run `scripts/promote_mac_release.py` with exact manifest, fresh smoke, live summary,
and manual review. It must preserve the PC slot or its absence, reject downgrade or
same-build/different-bytes, copy bytes first, and expose `latest.json` last. Never
bypass it with `write_release_manifest.py`.
