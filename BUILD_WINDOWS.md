# Building AdoptIQ for Windows

PyInstaller builds are OS-specific. Build Windows artifacts on Windows (or via CI on a Windows runner).

## Local Windows build

1. Open Command Prompt or PowerShell in the repo root (`AdoptIQ_MAC`).
2. Create `secrets.env` from `secrets.env.template` and populate required values.
3. Run:
   ```bat
   build_pc.bat
   ```
4. Build outputs are written to `OUTBOX\`:
   - `AdoptIQ.exe`
   - `README.md`

## CI build (GitHub Actions)

The shared workflow is `.github/workflows/build.yml` and includes both macOS and Windows jobs.

1. Trigger the workflow with `workflow_dispatch` (optionally set version/build), or push a tag (`v*`).
2. Ensure repository secret `SECRETS_ENV_FILE` is configured with full `secrets.env` content.
3. Download the Windows artifact from Actions:
   - `AdoptIQ-Windows-Build` (zip containing `AdoptIQ.exe` and `README.md`).

## Notes

- Current Windows packaging output is `AdoptIQ.exe` (no installer step in the current workflow).
- If `SECRETS_ENV_FILE` is missing, CI build steps that embed credentials will fail.
