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

## 4) Build on Windows

```cmd
build_pc.bat
```

This will:
1. Install dependencies from `requirements.txt`
2. Embed credentials from `secrets.env`
3. Update version/build metadata in `config.py`
4. Run PyInstaller with `adoptiq_pc.spec`
5. Create `OUTBOX\AdoptIQ.exe` and `OUTBOX\README.md`

## 5) Verify build outputs

Confirm:
- `OUTBOX\AdoptIQ.exe` exists
- `OUTBOX\README.md` exists

Also verify:
- Double-click `AdoptIQ.exe` -- it should start and open `http://localhost:5001`
- No missing module errors at startup (check the console window)

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
- **SmartScreen blocks the app:** Right-click `AdoptIQ.exe` -> Properties -> check "Unblock" -> OK.
- **Port 5001 in use:** Run `netstat -ano | findstr :5001` to find and stop the conflicting process.
- Keep platform path differences in mind:
  - Windows: `%APPDATA%\AdoptIQ`
  - macOS: `~/Library/Application Support/AdoptIQ`

## 8) Running tests (optional, for development)

```cmd
python -m pytest tests/ -q --tb=short
```

All 551 tests should pass on both Mac and Windows.

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
