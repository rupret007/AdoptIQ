# AdoptIQ macOS Build 117 candidate — staged / NO-GO

**Status: NO-GO / staged only.** No `candidate.json` exists yet because the release-gated
DMG has not been produced on this host. The frozen source commit is recorded below;
native packaging, artifact hashing, frozen smoke, VPN-based live Snowflake
reconciliation, and manual review remain pending.

## Frozen source (sanitized)

| Field | Value |
| --- | --- |
| Branch | `cursor/work-rebuild-integration-20260908` |
| Commit | `2088e86` |
| Version / build | `1.0.4` / `117` |
| Zero-secret contract | All authentication values runtime-only via `scripts/provision_runtime_credentials.py` |

## Blockers observed on the packaging host

- Disk nearly full (`~150 MiB` free on `/System/Volumes/Data`) caused production
  simulation `report_matrix` to fail with `OSError: [Errno 28] No space left on device`
  during the local acceptance matrix run. This is an environmental blocker, not a
  credential-contract regression.
- Live Snowflake reconciliation remains blocked off-VPN (`390422` IP allowlist).
  Keeper AppRole login and private-key retrieval succeeded via the provisioned runtime
  `.env` (digest-only verification).

## Next steps (requires approval)

1. Free several GB on the native build host.
2. `ADOPTIQ_RELEASE_GATE=1 ADOPTIQ_EXPECTED_BRANCH=cursor/work-rebuild-integration-20260908 bash build_mac_dmg.sh`
   with approved CSOne corpus and persistent model cache.
3. Inspect `OUTBOX/AdoptIQ-v1.0.4-build117.dmg` for zero-secret leakage.
4. `scripts/create_release_candidate.py` + `scripts/create_manual_review_template.py`
5. `smoke_frozen_candidate.py` against the mounted DMG (do not replace `/Applications/AdoptIQ.app`).
6. VPN live reconciliation + manual browser/report review before GO.

## Windows handoff

Build the same commit (`2088e86`) on an approved Windows host. Provision runtime
credentials with `python scripts\provision_runtime_credentials.py --apply` into
`%APPDATA%\AdoptIQ\.env` before smoke or acceptance. Do not publish or merge without
separate approval.
