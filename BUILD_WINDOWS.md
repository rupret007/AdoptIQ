# Historical only — do not execute

> **Pending Build 117 override (2026-09-08):** Everything below
> this notice is retained only as historical context. It is not an active
> build, release, installation, or publication procedure. Do not run or reuse
> its credential-embedding, `build_pc.bat`, hosted/tag release, OUTBOX,
> OneDrive, `latest.json`, install, mirror, or publication commands. Build 115
> is invalidated, Build 116 was superseded source-only, and Build 117 is
> source-only with no candidate. The only
> authoritative current instructions are `NEXT_MACHINE_PROMPT.md` and
> `WORK_MACHINE_BUILD117_PROMPT.md`; obey their explicit approval stops.

# Building AdoptIQ for Windows (historical)

PyInstaller builds are OS-specific. Build Windows artifacts on Windows (or via CI on a Windows runner).

> **Pre-build sanity check (recommended).** Run `make verify` before building so you catch test/lint/security/audit regressions on the source tree before they ship in a binary. Current baseline (Round 162 / Build 112 close-out): **7150+ passed / 7 skipped**, ruff clean, no HIGH/MED bandit findings, no pip-audit vulns.

## Local Windows build

1. Open Command Prompt or PowerShell in the repo root (`AdoptIQ_MAC`).
2. Create `secrets.env` from `secrets.env.template` and populate required values.
   - Runtime credentials (including the Keeper AppRole pair) must match the
     latest Mac build's `secrets.env`. Stale Keeper credentials cause the
     packaged EXE to fail at first request with
     `Keeper rejected the runtime KEEPER_ROLE_ID / KEEPER_SECRET_ID as invalid`.
   - `embed_credentials.py` opens the file as `utf-8-sig`, so a UTF-8 BOM
     and CRLF endings are both fine.
3. **Provision runtime-only credentials (required since Build 117).**
   Every authentication value is deliberately **not** embedded in the packaged app,
   so no credential is recoverable from `AdoptIQ.exe` or the installer. The frozen
   app reads them from `%APPDATA%\AdoptIQ\.env` instead,
   which means every machine that runs a packaged build needs this one-time step:
   ```bat
   python scripts\provision_runtime_credentials.py --apply
   ```
   Run without `--apply` first for a dry run. The script never prints secret
   values, writes the file owner-only, and preserves unrelated keys already in
   the `.env`. Skipping this step produces a build that authenticates against
   Keeper with no secret and fails at the first report.
4. Run:
   ```bat
   build_pc.bat
   ```
5. Build outputs are written to `OUTBOX\` as a deterministic 6-file payload:
   - `AdoptIQ.exe` - application binary
   - `Run_AdoptIQ.bat` - **recommended user entry point** (auto-unblocks the folder, then launches `AdoptIQ.exe`)
   - `Unblock_AdoptIQ.bat` - fallback SmartScreen unblock helper
   - `READ_ME_FIRST.txt` - first-run instructions (Notepad-friendly)
   - `README.md` - full user documentation
   - `build_info.txt` - version + build timestamp

   The same payload is mirrored to `%STAGING_DIR%` and the Mac OUTBOX
   staging folder defined at the top of `build_pc.bat`.

## CI build (GitHub Actions)

The shared workflow is `.github/workflows/build.yml` and includes both macOS and Windows jobs.

1. Trigger the workflow with `workflow_dispatch` (optionally set version/build), or push a tag (`v*`).
2. Ensure repository secret `SECRETS_ENV_FILE` is configured with full `secrets.env` content.
   The workflow writes it to disk as UTF-8 **without BOM** via
   `[System.IO.File]::WriteAllText`, so the secret value can contain a
   BOM or CRLF and `embed_credentials.py` will still parse cleanly.
   Since Build 117 the runtime-only credentials in that file (`KEEPER_SECRET_ID`,
   `SNOWFLAKE_PASSWORD`) are **not** baked into the artifact, so a CI-built
   `AdoptIQ.exe` still needs `scripts\provision_runtime_credentials.py --apply`
   on each target machine before it can reach Snowflake.
3. Download the Windows artifact from Actions:
   - `AdoptIQ-Windows-v{VERSION}-build{BUILD}` - contains the same 6-file
     payload as the local build:
     `AdoptIQ.exe`, `Run_AdoptIQ.bat`, `Unblock_AdoptIQ.bat`,
     `READ_ME_FIRST.txt`, `README.md`, `build_info.txt`.

## Notes

- The Windows release is a portable EXE plus its first-run helpers; there is no MSI/NSIS installer in the current workflow.
- If `SECRETS_ENV_FILE` is missing, CI build steps that embed credentials will fail.
- Line endings for the helper scripts are pinned by `.gitattributes`
  (`*.bat` -> CRLF, `*.command`/`*.sh` -> LF, `scripts/win/READ_ME_FIRST.txt`
  -> CRLF). Do not commit changes that flip these to "auto" or the
  helpers will break on the opposite OS.
