# AdoptIQ PC -- Build Guide for Cursor (Windows)

Use this guide when building AdoptIQ for Windows on a PC. The build must run on Windows because PyInstaller produces platform-specific executables.

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

### 3. Credentials (embedded in app)

Credentials are embedded at build time from `secrets.env`:

- Copy `secrets.env.template` to `secrets.env` and fill in values (Snowflake/Keeper, CircuIT, PSIRT, and **ADOPTIQ_ADMIN_SECRET_KEY**)
- Or copy an existing `secrets.env` from a previous build
- The build runs `embed_credentials.py` and embeds them into the app (no .env needed at runtime)

If `secrets.env` is missing or empty, the build creates an empty stub; the app will fail when connecting to Snowflake.

### 4. Run the build

**From Command Prompt, PowerShell, or Cursor terminal:**

```cmd
build_pc.bat
```

### 5. What the build does

1. Installs dependencies (`pip install -r requirements.txt`, `pyinstaller`)
2. Runs `embed_credentials.py` (embeds Snowflake, Keeper, CircuIT, PSIRT from `secrets.env`)
3. Updates `config.py` with version/build (v1.0.3 build 1)
4. Runs PyInstaller with `adoptiq_pc.spec`
5. Creates OUTBOX: `AdoptIQ.exe` plus `README.md`
6. Unblocks files (removes Zone.Identifier for SmartScreen)

### 6. Output location

| Item | Location |
|------|----------|
| Executable | `OUTBOX\AdoptIQ.exe` |
| User guide | `OUTBOX\README.md` |

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
- `_bundled_secrets`

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
├── build_mac_dmg.sh           # macOS build script
├── embed_credentials.py       # Embeds secrets.env into _bundled_secrets.py
├── update_version_pc.py       # Stamps version into config.py
├── requirements.txt
├── secrets.env.template       # Template for credentials
├── team_config.json
├── templates/
├── static/
├── tests/                     # pytest suite (551 tests)
├── OUTBOX/                    # Build output folder
│   ├── AdoptIQ.exe            # (Windows, after build)
│   └── README.md
├── BUILD_WINDOWS.md           # End-user Windows build docs
├── CURSOR_BUILD_GUIDE.md      # This file
├── CURSOR_PC_BUILD_INSTRUCTIONS.md  # PC Cursor kickoff instructions
└── CURSOR_MAC_BUILD_INSTRUCTIONS.md # Mac Cursor kickoff instructions
```
