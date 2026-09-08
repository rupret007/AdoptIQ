# AdoptIQ work rebuild / pending Build 117 work-machine prompt

Read `CLAUDE.md`, `NEXT_MACHINE_PROMPT.md`, `HANDOFF_PROMPT.md`, the latest
`QUALITY_AUDIT.md`, and the invalidated Build 115 README. Work only from the exact
approved integration commit on `cursor/work-rebuild-integration-20260908`, preserving
the Cisco repo/remotes and existing Keeper, Snowflake, CircuIT, CSConsole, CSOne, and
OneDrive configuration. Never reset/clean/force-push, expose secrets or customer rows,
weaken tests, or replace connection code wholesale.

Build 115 is invalidated/NO-GO. Build 116 is superseded source-only work and must not
be packaged. Build 117 is source-only and has no candidate identity, artifact
hash/size, live validation, release approval, or production-accuracy claim
exists yet. Require a clean tree, verify ancestry, and record the exact source SHA.
Run Python 3.12 with `constraints-build113.txt`, then `make verify` and:

`make production-simulation PY=.venv/bin/python CSONE_CORPUS_DIR='<approved external CSOne export folder>' OUTPUT_DIR='.adoptiq-acceptance/build117-prebuild'`

Require exact inventories; all report families/selections, technologies, two named
managers plus All Managers, Leader Team/Member/Customer, customer/subscription scopes,
degraded states, manager isolation, workspace, Ask AI sync/stream, replay, R114 pair
audits, all-sheet/field cross-family parity, one digest-bound prepared replay shared by
both runtimes, and all eight metamorphic truth checks. Treat timeouts, skipped or extra
results, missing links/charts, false zeroes, stale/partial mislabels, identity drift,
unauthorized rows, or mismatched `Defect_Correlations` as defects. Simulated evidence remains
`live_validation_performed=false`, `production_accuracy_claimed=false`, and
`release_ready=false`.

The Snowflake password/token is runtime-only. It must be present in owner-protected,
ignored `secrets.env` for preflight and copied to the installed Application Support
`.env`; it must never enter `_bundled_secrets.py`, Git, logs, the DMG, or the EXE.
With VPN and existing authorized access, run only approved live checks and never widen
policy.

If source changes are needed, add focused regression tests and rerun all gates. Package
only from the approved frozen commit with `ADOPTIQ_RELEASE_GATE=1` and publication
disabled. Create `release_candidates/macos-build117/` only from the exact
`AdoptIQ-v1.0.4-build117.dmg` and generated sidecars using repository creators; never
invent identity fields. Verify the candidate contract and frozen runtime, then let
`run_round146_acceptance.py` mount and launch that exact DMG. Keep live evidence
outside Git and bind manual review to its summary digest.

Windows remains pending on an approved native Windows machine. Build the same frozen
commit, provision the runtime-only Snowflake value outside the EXE, run native smoke
and acceptance, and preserve any existing release manifest slots.

Finish with exact source/candidate identities, commands/counts, offline versus live
evidence, mismatches, visual findings, and GO/NO-GO. Stop before merge, tag,
installation replacement, promotion, OneDrive/latest.json publication, outbound send,
or deployment unless that specific action is separately approved.
