# AdoptIQ PC – Build Guide for Cursor (Windows)

Use this guide when building AdoptIQ for Windows on a PC. The build must run on Windows because PyInstaller produces platform-specific executables.

---

## Prerequisites

- **Windows PC** (10 or 11)
- **Python 3.11** installed
- **Cursor** (or any IDE) with this `AdoptIQ_PC` folder open

---

## Build Steps

### 1. Open the project

Open the `AdoptIQ_PC` folder in Cursor (or your workspace root that contains `AdoptIQ_PC`).

### 2. Credentials (embedded in app)

Credentials are embedded at build time from `secrets.env`:

- Copy `secrets.env.template` to `secrets.env` and fill in values (Snowflake/Keeper, CircuIT, PSIRT, etc.)
- Or use an existing `secrets.env` with POC credentials
- The build runs `embed_credentials.py` and embeds them into the app (no .env needed at runtime)

If `secrets.env` is missing or empty, the build creates an empty stub; the app will fail when connecting to Snowflake.

### 3. Run the build

**Option A – From Command Prompt or PowerShell**

```cmd
cd AdoptIQ_PC
build_pc.bat
```

**Option B – From Cursor terminal**

```cmd
cd AdoptIQ_PC
build_pc.bat
```

### 4. What the build does

1. Installs dependencies (`pip install -r requirements.txt`, `pyinstaller`)
2. Runs `embed_credentials.py` (embeds Snowflake, Keeper, CircuIT, PSIRT from `secrets.env`)
3. Updates `config.py` with version/build (1.0 build 1)
4. Runs PyInstaller with `adoptiq_pc.spec`
5. Creates OUTBOX: `AdoptIQ-Setup.exe` (if Inno Setup installed) or `AdoptIQ.exe`, plus `README.md`
6. Unblocks files (removes Zone.Identifier for SmartScreen)

### 5. Output location

| Item | Location |
|------|----------|
| Installer | `AdoptIQ_PC\OUTBOX\AdoptIQ-Setup.exe` (if Inno Setup 6 installed) |
| Executable | `AdoptIQ_PC\OUTBOX\AdoptIQ.exe` (if no Inno Setup) |
| User guide | `AdoptIQ_PC\OUTBOX\README.md` |

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

### "ModuleNotFoundError: No module named 'enhanced_defect_analyzer'" (or similar)

The leader report generator uses optional analyzers (`enhanced_defect_analyzer`, `bems_escalation_intelligence_engine`, `arr_sentiment_analyzer`). If these modules are not in the project, the app will run with reduced functionality (no defect/BEMS/ARR analysis in leader reports). The imports are optional and gracefully degrade.

### Inno Setup not found

If Inno Setup 6 is not installed, the build copies `AdoptIQ.exe` to OUTBOX instead of creating `AdoptIQ-Setup.exe`. Install [Inno Setup 6](https://jrsoftware.org/isinfo.php) for a one-click installer.

---

## Quick reference for Cursor AI

When asking Cursor to build:

> "Run build_pc.bat in the AdoptIQ_PC folder to compile the Windows app. The output goes to AdoptIQ_PC\OUTBOX\AdoptIQ.exe."

Or:

> "Build the AdoptIQ Windows executable: cd to AdoptIQ_PC and run build_pc.bat. See CURSOR_BUILD_GUIDE.md for details."

---

## File structure (for reference)

```
AdoptIQ_PC/
├── app_simple.py          # Main Flask app
├── adoptiq_backend.py     # Backend logic
├── config.py              # Version, config
├── adoptiq_pc.spec        # PyInstaller spec
├── build_pc.bat           # Build script – run this
├── requirements.txt
├── team_config.json
├── templates/
├── static/
├── OUTBOX/                # Output folder
│   ├── AdoptIQ-Setup.exe  # (after build, if Inno Setup)
│   ├── AdoptIQ.exe        # (if no Inno Setup)
│   └── README.md
└── CURSOR_BUILD_GUIDE.md  # This file
```
