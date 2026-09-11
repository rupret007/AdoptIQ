# Windows Build 117 handoff (same frozen source)

Use commit `2088e86` on branch `cursor/work-rebuild-integration-20260908`.

1. Copy the same repo-root `secrets.env` used for the macOS preflight.
2. `python embed_credentials.py` — confirms non-secret config only is bundled.
3. `python scripts\provision_runtime_credentials.py --apply` — writes
   `%APPDATA%\AdoptIQ\.env` (owner-only).
4. `build_pc.bat` from the frozen commit with publication disabled.
5. Inspect the generated bundle and EXE for runtime credential leakage before any
   distribution.
6. Run native smoke and `run_round146_acceptance.py` work-machine profile only after
   macOS candidate identity is approved.

Do not merge, tag, publish `latest.json`, or replace installed builds without explicit
approval.
