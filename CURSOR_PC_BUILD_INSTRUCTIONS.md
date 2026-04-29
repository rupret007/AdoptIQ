# AdoptIQ PC Build Handoff (for Cursor on Windows)

Use this guide when building AdoptIQ for Windows. The build must run on Windows because PyInstaller produces platform-specific executables.

---

## 1) Clone the repo on Windows

```cmd
git clone https://wwwin-github.cisco.com/jestory/AdoptIQ.git
cd AdoptIQ
```

Or, if already cloned:

```cmd
cd AdoptIQ
git fetch origin
```

## 1b) Use a PC machine branch (recommended)

For cross-machine development, do daily work on a branch like `pc-sync-YYYY-MM-DD` (not directly on `master`).

If creating a new branch:

```cmd
git checkout -b pc-sync-YYYY-MM-DD origin/master
git push -u origin pc-sync-YYYY-MM-DD
```

If branch already exists:

```cmd
git checkout pc-sync-YYYY-MM-DD
git pull
```

If you also work on Mac, follow `BRANCH_WORKFLOW.md` for cherry-pick/merge sync patterns.

## 2) Create Python environment

```cmd
python -m venv venv
venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt
pip install pyinstaller
```

## 3) Configure credentials

1. Copy `secrets.env.template` to `secrets.env`.
2. Fill required values (Snowflake, CircuIT, PSIRT, and **ADOPTIQ_ADMIN_SECRET_KEY** -- the app will not start without it).
3. Generate bundled secrets:

```cmd
python embed_credentials.py
```

> **Important - Keeper credentials must match the latest Mac build.**
> `KEEPER_ROLE_ID` and `KEEPER_SECRET_ID` are rotated periodically. If the
> PC build is started from an older `secrets.env` than the Mac build, the
> packaged app will fail at first request with
> `Keeper rejected the bundled KEEPER_ROLE_ID / KEEPER_SECRET_ID as invalid`.
> Always copy the same `secrets.env` we just used for the most recent Mac
> build (or re-export it from your Desktop/secret store) before running
> `embed_credentials.py`.
>
> `embed_credentials.py` reads the file as `utf-8-sig`, so a UTF-8 BOM
> (which Notepad and PowerShell often add) is silently stripped. CRLF
> line endings are also fine. You do **not** need to scrub the file
> before use.

## 4) Build on Windows

```cmd
build_pc.bat
```

This will:
1. Install dependencies from `requirements.txt`
2. Embed credentials from `secrets.env` (BOM-safe, see section 3)
3. Update version/build metadata in `config.py`
4. Run PyInstaller with `adoptiq_pc.spec`
5. Create the OUTBOX release payload (6 files, see below)
6. Run `Unblock-File` on every file in OUTBOX so SmartScreen does not
   block the freshly built artifacts

## 5) Verify build outputs

Confirm `OUTBOX\` contains exactly these six files:

| File | Purpose |
|------|---------|
| `AdoptIQ.exe` | The application binary |
| `Run_AdoptIQ.bat` | Recommended user entry point (auto-unblocks then launches `AdoptIQ.exe`) |
| `Unblock_AdoptIQ.bat` | Standalone helper if SmartScreen still blocks the EXE |
| `READ_ME_FIRST.txt` | First-run instructions (open in Notepad) |
| `README.md` | Full user documentation |
| `build_info.txt` | Version / build metadata |

Also verify:
- Double-click `Run_AdoptIQ.bat` -- AdoptIQ should start and the default
  browser should open `http://localhost:5151`. The console window stays
  open while AdoptIQ is running; closing it stops the app.
- No missing module errors at startup (check the console window).
- Hit `http://localhost:5151/api/diag/connectivity` in the browser to
  confirm DNS, TLS, AppRole login, secret read, and Snowflake all pass.
  If AppRole login fails here, the bundled Keeper credentials are stale
  (see section 3).

## 6) Where files are stored on Windows

| Purpose | Location |
|---------|----------|
| Uploads (CSOne Excel files) | `%APPDATA%\AdoptIQ\uploads\` |
| Generated reports (Word, Excel) | `%APPDATA%\AdoptIQ\outputs\` |
| Analysis status | `%APPDATA%\AdoptIQ\analysis_status.json` |
| External intelligence history | `%APPDATA%\AdoptIQ\external_intelligence.db` |

In Explorer, type `%APPDATA%\AdoptIQ` in the address bar to open the folder.

## 7) Troubleshooting

- **"Python is not recognized":** Install Python 3.11 from python.org. Check "Add Python to PATH" during install. Restart terminal.
- **Missing modules at runtime:** Ensure the module is in `hidden_imports` inside `adoptiq_pc.spec`. Key modules: `incident_storage`, `enhanced_admin_dashboard_v2`, `cisco_internal_integrations`, `_bundled_secrets`.
- **SmartScreen blocks the app:** Use the helpers shipped in OUTBOX:
  - Easiest: double-click `OUTBOX\Run_AdoptIQ.bat` (it calls `Unblock-File` on the folder before launching `AdoptIQ.exe`).
  - If the SmartScreen dialog still appears, click **More info -> Run anyway**.
  - As a fallback: double-click `OUTBOX\Unblock_AdoptIQ.bat` once, then double-click `AdoptIQ.exe`.
  - Manual equivalent: right-click `AdoptIQ.exe` -> Properties -> tick **Unblock** -> OK.
- **"Could not reach the AdoptIQ server" appears in the browser:** The AdoptIQ console window was closed (or never started). Double-click `Run_AdoptIQ.bat` again. The friendlier message replaces the misleading "verify your input" text from earlier builds.
- **"Keeper rejected the bundled KEEPER_ROLE_ID / KEEPER_SECRET_ID":** The `secrets.env` used at build time has a stale Keeper AppRole. Refresh from the same `secrets.env` used by the most recent Mac build, re-run `embed_credentials.py`, and rebuild. See section 3.
- **Port 5151 in use:** Run `netstat -ano | findstr :5151` to find and stop the conflicting process.
- Keep platform path differences in mind:
  - Windows: `%APPDATA%\AdoptIQ`
  - macOS: `~/Library/Application Support/AdoptIQ`

## 8) Running tests (optional, for development)

```cmd
python -m pytest tests/ -q --tb=short
```

Or, on macOS / a POSIX-like shell, the full audit gate:

```bash
make verify
```

(`make verify` chains pytest + ruff + bandit + pip-audit. On Windows you can run the four tools individually; the Makefile targets are POSIX-only.)

All tests should pass on both Mac and Windows. Current baseline (Round 48 / Build25 close-out): **3191 passed / 2 skipped**. Live count: `pytest --collect-only -q | tail -3`.

## 9) Paste-ready Cursor kickoff prompt (Windows)

```text
You are in the AdoptIQ codebase on Windows. Please:
1) Confirm build prerequisites: Python 3.11 on PATH, venv created, dependencies installed.
2) Verify secrets.env exists with required credentials. Run embed_credentials.py if needed.
3) Build the Windows executable by running build_pc.bat.
4) Run the test suite (python -m pytest tests/ -q) and report results.
5) Verify OUTBOX\AdoptIQ.exe exists and provide a summary.
See CURSOR_BUILD_GUIDE.md and CURSOR_PC_BUILD_INSTRUCTIONS.md for details.
```
