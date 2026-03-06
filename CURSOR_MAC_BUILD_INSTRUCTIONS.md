# AdoptIQ Mac Build Handoff (for Cursor)

Use this folder as the Mac build source package. The app code is synced from the Windows repo; build output must be created on macOS.

## 1) Open this project on Mac

1. Copy/open this folder on your Mac:
   - `~/OneDrive - Cisco/AI Projects/Staging/AdoptIQ_MAC`
2. Open it in Cursor.

## 1b) Use a Mac machine branch (recommended)

For cross-machine development, do daily work on a branch like `mac-sync-YYYY-MM-DD` (not directly on `master`).

If creating a new branch:

```bash
git fetch origin
git checkout -b mac-sync-YYYY-MM-DD origin/master
git push -u origin mac-sync-YYYY-MM-DD
```

If branch already exists:

```bash
git checkout mac-sync-YYYY-MM-DD
git pull
```

If you also work on PC, follow `BRANCH_WORKFLOW.md` for cherry-pick/merge sync patterns.

## 2) Create Python environment

```bash
cd "~/OneDrive - Cisco/AI Projects/Staging/AdoptIQ_MAC"
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
pip install pyinstaller
```

## 3) Configure credentials

1. Create `secrets.env` from `secrets.env.template`.
2. Fill required values (Snowflake, CircuIT, PSIRT, and **ADOPTIQ_ADMIN_SECRET_KEY** for packaged builds—the app will not start without it).
3. Generate bundled secrets:

```bash
python embed_credentials.py
```

## 4) Apply Mac migration parity

This repo includes Windows->Mac parity guidance:
- `MIGRATION_TO_MAC.md`

In Cursor on Mac, use this prompt:

```text
Apply all required parity updates from MIGRATION_TO_MAC.md to this codebase, including any Mac build spec updates. Then run the test suite and report what changed.
```

## 5) Build on Mac

If your Mac repo already has a script, run it (example names):
- `build_mac_dmg.sh`
- `build_mac.sh`

Example:

```bash
chmod +x build_mac_dmg.sh
./build_mac_dmg.sh
```

If no Mac script exists yet, ask Cursor:

```text
Create a macOS build script equivalent to build_pc.bat that installs deps, runs embed_credentials.py, updates version/build metadata, runs PyInstaller with a Mac spec, and writes outputs to OUTBOX.
```

## 6) Verify build outputs

Primary deliverable for end users: **DMG** in `OUTBOX/` (e.g. `OUTBOX/AdoptIQ-v1.0.2-build1.dmg`). The DMG contains AdoptIQ.app, an Applications shortcut, and README.md for drag-to-install.

Also confirm:
- `OUTBOX/AdoptIQ.app`
- `OUTBOX/README.md`

Also verify:
- App starts and opens `http://localhost:5001`
- No missing module errors at startup

## 7) Troubleshooting

- If PyInstaller misses modules, add them to hidden imports in the Mac spec.
- Ensure `report_utils` is included in hidden imports.
- Keep platform path differences in mind:
  - Windows `%APPDATA%\\AdoptIQ`
  - macOS `~/Library/Application Support/AdoptIQ`
- SmartScreen notes are Windows-only; ignore on Mac.

## 8) Paste-ready Cursor kickoff prompt (Mac)

```text
You are in the AdoptIQ_MAC staging codebase. Please:
1) Implement all parity items from MIGRATION_TO_MAC.md.
2) Confirm build prerequisites and secrets setup.
3) Build a macOS artifact (.app and/or .dmg) using the project build script (or create one if missing).
4) Run tests and provide a concise validation report with output paths.
```

