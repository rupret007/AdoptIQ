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

## 3) Keep credentials out of the build

Builds never generate or package service credentials. Confirm the repository is
clean before building:

```cmd
python embed_credentials.py --ci-lint
```

Do not put a populated credential file in the source tree, OUTBOX, or beside
the executable. Before smoke testing, provide values through process
environment variables or `%APPDATA%\AdoptIQ\.env`; use
`secrets.env.template` only as the supported-key reference. Keeper may supply
Snowflake credentials, and **ADOPTIQ_ADMIN_SECRET_KEY** remains required for
the Admin dashboard. Restart AdoptIQ after changing runtime values.

## 4) Build on Windows

```cmd
build_pc.bat
```

This will:
1. Install dependencies from `requirements.txt`
2. Leave credentials outside the build and print the runtime-configuration reminder
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
  If AppRole login fails here, refresh the runtime Keeper values and restart
  AdoptIQ (see section 3); rebuilding does not configure credentials.

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
- **Missing modules at runtime:** Ensure the module is in `hidden_imports` inside `adoptiq_pc.spec`. Key modules: `incident_storage`, `enhanced_admin_dashboard_v2`, `cisco_internal_integrations`.
- **SmartScreen blocks the app:** Use the helpers shipped in OUTBOX:
  - Easiest: double-click `OUTBOX\Run_AdoptIQ.bat` (it calls `Unblock-File` on the folder before launching `AdoptIQ.exe`).
  - If the SmartScreen dialog still appears, click **More info -> Run anyway**.
  - As a fallback: double-click `OUTBOX\Unblock_AdoptIQ.bat` once, then double-click `AdoptIQ.exe`.
  - Manual equivalent: right-click `AdoptIQ.exe` -> Properties -> tick **Unblock** -> OK.
- **"Could not reach the AdoptIQ server" appears in the browser:** The AdoptIQ console window was closed (or never started). Double-click `Run_AdoptIQ.bat` again. The friendlier message replaces the misleading "verify your input" text from earlier builds.
- **Keeper rejects the AppRole:** Refresh `KEEPER_ROLE_ID` and
  `KEEPER_SECRET_ID` in the process environment or
  `%APPDATA%\AdoptIQ\.env`, then restart AdoptIQ. Do not rebuild or copy
  credentials into the release artifact. See section 3.
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

All tests should pass on both Mac and Windows. Current baseline (Round 127 / Build 96): **`make verify` → 6187 passed / 4 skipped** (pytest + ruff + bandit + pip-audit). On Windows without `make`, run the four tools individually (see section 8).

## 8b) Build 96 parity with Mac (Round 127 close-out)

**Goal:** Windows ships the **same build number** as Mac (`ADOPTIQ_BUILD = "96"` in `config.py`). Mac DMG is already `AdoptIQ-v1.0.4-build96.dmg`; PC must publish `AdoptIQ-v1.0.4-build96.exe`.

**Before `build_pc.bat`:**
1. `git pull origin main` (includes Round 126–127 code + README Build 95/96 notes).
2. Run `python embed_credentials.py --ci-lint`; the build tree must contain no credentials.
3. Confirm `python -c "from config import ADOPTIQ_BUILD; print(ADOPTIQ_BUILD)"` prints **96**.

**Run:**
```cmd
build_pc.bat
```

**After build — verify (all must pass):**
| Check | Expected |
|-------|----------|
| `OUTBOX\build_info.txt` | `AdoptIQ v1.0.4 build 100` (or current `ADOPTIQ_BUILD`) |
| `OUTBOX\AdoptIQ-v1.0.4-build100.exe` | exists (build number tracks `config.py`) |
| `%RELEASES_ROOT%\AdoptIQ_PC\AdoptIQ-v1.0.4-build100.exe` | mirrored (OneDrive `AI Projects\OUTBOX\AdoptIQ_PC\`) |
| `%RELEASES_ROOT%\latest.json` | **`mac.build`** AND **`pc.build`** match current build (merge-aware) |
| `Run_AdoptIQ.bat` | starts app; `/api/version` → `"build": "<current>"` |
| VPN | `/api/diag/connectivity` all green |

**Mac already synced README** to OneDrive `AdoptIQ_PC\README.md` (header tracks current build). PC build refreshes it again from repo.

## 9) Paste-ready Cursor kickoff prompt (Windows)

```text
You are in the AdoptIQ codebase on Windows. Please:
1) Confirm build prerequisites: Python 3.11 on PATH, venv created, dependencies installed.
2) Run python embed_credentials.py --ci-lint and verify no credential material is in the build tree.
3) Build the Windows executable by running build_pc.bat.
4) Run the test suite (python -m pytest tests/ -q) and report results.
5) Verify OUTBOX\AdoptIQ.exe exists, then configure runtime credentials in %APPDATA%\AdoptIQ\.env for smoke testing and provide a summary.
See CURSOR_BUILD_GUIDE.md and CURSOR_PC_BUILD_INSTRUCTIONS.md for details.
```
