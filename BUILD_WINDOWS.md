# Building AdoptIQ for Windows

PyInstaller builds are OS-specific. Build Windows artifacts on Windows (or via CI on a Windows runner).

> **Pre-build sanity check (recommended).** Run `make verify` before building so you catch test/lint/security/audit regressions on the source tree before they ship in a binary. Current baseline (Round 132 / Build 102 close-out): **6251+ passed / 4 skipped**, ruff clean, no HIGH/MED bandit findings, no pip-audit vulns.

## Local Windows build

1. Open Command Prompt or PowerShell in the repo root (`AdoptIQ_MAC`).
2. Confirm the source tree contains no credential material:
   ```bat
   python embed_credentials.py --ci-lint
   ```
   Do not put a populated credential file in the repository or beside the
   executable. The build deliberately contains no service credentials.
3. Run:
   ```bat
   build_pc.bat
   ```
4. Before smoke testing, configure credentials for the user running AdoptIQ
   through process environment variables or `%APPDATA%\AdoptIQ\.env`. Use
   `secrets.env.template` only as the supported-key reference. Keeper may
   supply Snowflake credentials; `ADOPTIQ_ADMIN_SECRET_KEY` is still required
   for the Admin dashboard. Restart AdoptIQ after changing runtime values.
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
2. The quality job runs `python embed_credentials.py --ci-lint`. No repository
   credential file or credential-bearing workflow secret is required, and the
   macOS and Windows build jobs never write service credentials to disk.
3. Download the Windows artifact from Actions:
   - `AdoptIQ-Windows-v{VERSION}-build{BUILD}` - contains the same 6-file
     payload as the local build:
     `AdoptIQ.exe`, `Run_AdoptIQ.bat`, `Unblock_AdoptIQ.bat`,
     `READ_ME_FIRST.txt`, `README.md`, `build_info.txt`.

## Notes

- The Windows release is a portable EXE plus its first-run helpers; there is no MSI/NSIS installer in the current workflow.
- Release artifacts contain no service credentials. Each operator must use
  process environment variables or `%APPDATA%\AdoptIQ\.env` at runtime.
- Line endings for the helper scripts are pinned by `.gitattributes`
  (`*.bat` -> CRLF, `*.command`/`*.sh` -> LF, `scripts/win/READ_ME_FIRST.txt`
  -> CRLF). Do not commit changes that flip these to "auto" or the
  helpers will break on the opposite OS.
