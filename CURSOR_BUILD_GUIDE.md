# AdoptIQ PC -- Build Guide for Cursor (Windows)

Use this guide when building AdoptIQ for Windows on a PC. The build must run on Windows because PyInstaller produces platform-specific executables.

> **Before you build:** run `make verify` (or on Windows, `python -m pytest -q && python -m ruff check .` plus `bandit -r . -ll` and `pip-audit -r requirements.txt`). Current baseline (Round 132 / Build 102 close-out): **6251+ passed / 4 skipped**, ruff clean, no HIGH/MED bandit findings.

---

## Prerequisites

- **Windows 10 or 11**
- **Python 3.11** installed and on PATH
- **Git** (to clone the repo)
- **Cursor** (or any IDE)

---

## Build Steps

### 1. Clone / pull the repo

```cmd
git clone https://wwwin-github.cisco.com/jestory/AdoptIQ.git
cd AdoptIQ
```

Or, if already cloned:

```cmd
cd AdoptIQ
git pull
```

### 2. Create a virtual environment (recommended)

```cmd
python -m venv venv
venv\Scripts\activate
pip install --upgrade pip
pip install -r requirements.txt
pip install pyinstaller
```

### 3. Runtime credentials (never embedded)

The build machine does not need service credentials, and release artifacts
must not contain them. Before building, scan the repository:

```cmd
python embed_credentials.py --ci-lint
```

After deployment, provide credentials through process environment variables or
the current user's `%APPDATA%\AdoptIQ\.env`. Use `secrets.env.template` only as
the supported-key reference; never put a populated copy in the source tree,
OUTBOX, or beside the executable. Keeper may supply Snowflake credentials, and
**ADOPTIQ_ADMIN_SECRET_KEY** remains required for the Admin dashboard. Operators
can choose the CircuIT model through Preferences or the runtime environment.
Restart AdoptIQ after changing runtime values.

### 4. Run the build

**From Command Prompt, PowerShell, or Cursor terminal:**

```cmd
build_pc.bat
```

### 5. What the build does

1. Installs dependencies (`pip install -r requirements.txt`, `pyinstaller`)
2. Leaves all credentials outside the build and prints the runtime-configuration reminder
3. Updates `config.py` with version/build (v1.0.4 build 1)
4. Runs PyInstaller with `adoptiq_pc.spec`
5. Creates the OUTBOX release payload (6 files - see "Output location" below)
6. Mirrors the same 6-file payload to the user's OneDrive staging folders for both PC and Mac releases
7. Runs `Unblock-File` on every file in OUTBOX and the staging folders (removes Zone.Identifier so SmartScreen does not block them)

### 6. Output location

| Item | Location | Notes |
|------|----------|-------|
| Application binary | `OUTBOX\AdoptIQ.exe` | Standalone PyInstaller exe |
| User entry point | `OUTBOX\Run_AdoptIQ.bat` | Auto-unblocks the folder, then launches `AdoptIQ.exe`. **Recommended** way to start AdoptIQ. |
| Manual unblock helper | `OUTBOX\Unblock_AdoptIQ.bat` | Use only if SmartScreen still blocks the EXE |
| First-run instructions | `OUTBOX\READ_ME_FIRST.txt` | Plain text, opens in Notepad |
| Full user docs | `OUTBOX\README.md` | Markdown |
| Build metadata | `OUTBOX\build_info.txt` | Version + build timestamp |

---

## Troubleshooting

### "Python is not recognized"

- Install Python 3.11 from [python.org](https://www.python.org/downloads/)
- During install, check **"Add Python to PATH"**
- Restart the terminal

### "pip is not recognized"

```cmd
python -m pip install -r requirements.txt
python -m pip install pyinstaller
python -m PyInstaller --clean --noconfirm adoptiq_pc.spec
```

### Build fails with missing module

```cmd
pip install -r requirements.txt
pip install pyinstaller
```

Then run `build_pc.bat` again.

### "ModuleNotFoundError: No module named '...'" at runtime

Check that the module is listed in `hidden_imports` inside `adoptiq_pc.spec`. Key modules that must be present:

- `incident_storage` (External Intelligence feature)
- `enhanced_admin_dashboard_v2`
- `cisco_internal_integrations`

Add any missing module to the list and rebuild.

---

## Quick reference for Cursor AI

When asking Cursor to build:

> "Run build_pc.bat to compile the Windows app. The output goes to OUTBOX\AdoptIQ.exe."

Or:

> "Build the AdoptIQ Windows executable: run build_pc.bat. See CURSOR_BUILD_GUIDE.md for details."

---

## File structure (for reference)

```
AdoptIQ/
├── app_simple.py              # Main Flask app
├── adoptiq_backend.py         # Backend logic, LLM prompts
├── config.py                  # Version, config
├── incident_storage.py        # External intelligence SQLite storage
├── adoptiq_pc.spec            # PyInstaller spec (Windows)
├── adoptiq_mac.spec           # PyInstaller spec (macOS)
├── build_pc.bat               # Windows build script
├── build_mac.sh               # macOS .app build script
├── build_mac_dmg.sh           # macOS DMG wrapper script
├── Run_AdoptIQ.bat            # User entry point (Windows; copied into OUTBOX)
├── Unblock_AdoptIQ.bat        # SmartScreen unblock helper (Windows; copied into OUTBOX)
├── embed_credentials.py       # Read-only repository credential scanner (--ci-lint)
├── update_version_pc.py       # Stamps version into config.py
├── requirements.txt
├── secrets.env.template       # Supported runtime-key reference; never populate in repo
├── team_config.json
├── templates/
├── static/
├── scripts/
│   ├── mac/
│   │   ├── READ_ME_FIRST.txt          # First-run instructions, copied into the .dmg
│   │   └── Unblock_AdoptIQ.command    # First-run helper, copied into the .dmg
│   └── win/
│       └── READ_ME_FIRST.txt          # First-run instructions, copied into OUTBOX
├── tests/                     # pytest suite (current baseline; run pytest --collect-only -q for the live count)
├── OUTBOX/                    # Build output folder (deterministic 6-file payload)
│   ├── AdoptIQ.exe
│   ├── Run_AdoptIQ.bat
│   ├── Unblock_AdoptIQ.bat
│   ├── READ_ME_FIRST.txt
│   ├── README.md
│   └── build_info.txt
├── BUILD_WINDOWS.md           # End-user Windows build docs
├── CURSOR_BUILD_GUIDE.md      # This file
├── CURSOR_PC_BUILD_INSTRUCTIONS.md  # PC Cursor kickoff instructions
└── CURSOR_MAC_BUILD_INSTRUCTIONS.md # Mac Cursor kickoff instructions
```
