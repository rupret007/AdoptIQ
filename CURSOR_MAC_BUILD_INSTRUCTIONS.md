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
3. If you want the packaged Mac build to use the latest faster CircuIT default, set:
   - `CIRCUIT_MODEL_NAME=gemini-3.1-flash-lite`
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

Preferred scripts:
- `./build_mac.sh` for the `.app` bundle payload in `OUTBOX/`
- `./build_mac_dmg.sh` if you also want a drag-to-install DMG

Examples:

```bash
chmod +x build_mac.sh build_mac_dmg.sh
./build_mac.sh
```

Optional DMG packaging:

```bash
./build_mac_dmg.sh
```

If no Mac script exists yet, ask Cursor:

```text
Create a macOS build script equivalent to build_pc.bat that installs deps, runs embed_credentials.py, updates version/build metadata, runs PyInstaller with a Mac spec, and writes outputs to OUTBOX.
```

## 6) Verify build outputs

`build_mac.sh` should produce:
- `OUTBOX/AdoptIQ.app`
- `OUTBOX/README.md`
- `OUTBOX/build_info.txt`

If you run `build_mac_dmg.sh`, also confirm:
- `OUTBOX/AdoptIQ-v<version>-build<build>.dmg`

Verify the build output before shipping the DMG to anyone:

```bash
codesign --verify --deep --strict OUTBOX/AdoptIQ.app
```

Mount the DMG and confirm it contains all four user-facing items:

```bash
hdiutil attach -readonly -nobrowse OUTBOX/AdoptIQ-v*-build*.dmg -mountpoint /tmp/adoptiq_dmg
ls /tmp/adoptiq_dmg
# Expected: AdoptIQ.app  Applications  Unblock AdoptIQ.command  READ_ME_FIRST.txt  README.md
codesign --verify --deep --strict /tmp/adoptiq_dmg/AdoptIQ.app
hdiutil detach /tmp/adoptiq_dmg
```

Also verify:
- App starts and opens `http://localhost:5001`
- No missing module errors at startup

### Staging sync (OneDrive)

`build_mac_dmg.sh` mirrors release artifacts to TWO OneDrive destinations,
each with its own strict whitelist (anything else in the folder is purged
on every build, `.DS_Store` is preserved):

| Destination (env var override) | Default path | Payload |
|---|---|---|
| `MAC_STAGING_DIR` | `~/Library/CloudStorage/OneDrive-Cisco/AI Projects/Staging/AdoptIQ_MAC/OUTBOX/` | DMG + `README.md` + `build_info.txt` |
| `MAC_OUTBOX_DIR` | `~/Library/CloudStorage/OneDrive-Cisco/AI Projects/OUTBOX/AdoptIQ/` | DMG + `README.md` + `AdoptIQ.app` |

- The Staging mirror is the cross-platform drop zone: `build_pc.bat` ALSO
  writes the PC payload (`AdoptIQ.exe`, `Run_AdoptIQ.bat`,
  `Unblock_AdoptIQ.bat`, `READ_ME_FIRST.txt`) into the same folder. Mac and
  PC whitelists do not overlap except on `README.md` / `build_info.txt`,
  so each build refreshes its own artifacts without clobbering the other
  platform's. The Mac build deliberately purges any loose `AdoptIQ.app`
  here, since the DMG is the canonical install path.
- The OUTBOX mirror is Mac-only and includes the `.app` bundle for direct
  install / inspection. `ditto` copies the bundle, then `xattr -cr` strips
  OneDrive-injected metadata and an adhoc deep `codesign` is re-applied so
  the bundle in OneDrive can still be dragged to `/Applications` and run.
  If `codesign --verify` fails afterwards (OneDrive can re-inject xattrs
  during ongoing sync), the script prints a warning and continues; the DMG
  remains the recommended install path.
- OneDrive sync-conflict variants (`README-MACHINENAME-XXXX.md`,
  `build_info-MACHINENAME-XXXX.txt`, `AdoptIQ-MACHINENAME-XXXX.app`) are
  matched explicitly by the prune step so they get cleaned up automatically
  on the next build.
- `rm -rf` and prune steps retry up to four times with a 1-second sleep
  to ride out OneDrive's "Directory not empty" race when sync hasn't
  caught up.
- If a destination's parent doesn't exist (e.g. fresh Mac without OneDrive
  set up) the script prints a warning and skips that mirror; it does not
  fail the build.
- Mirroring `.app` to a OneDrive path is slow (~1-2 minutes) because
  OneDrive throttles writes for the bundle's thousands of small files.
  This is expected. The DMG and README copies are nearly instant.

Quick check after a build:

```bash
ls "$HOME/Library/CloudStorage/OneDrive-Cisco/AI Projects/Staging/AdoptIQ_MAC/OUTBOX/"
ls "$HOME/Library/CloudStorage/OneDrive-Cisco/AI Projects/OUTBOX/AdoptIQ/"
codesign --verify --deep --strict \
  "$HOME/Library/CloudStorage/OneDrive-Cisco/AI Projects/OUTBOX/AdoptIQ/AdoptIQ.app"
```

## 7) Troubleshooting

- If PyInstaller misses modules, add them to hidden imports in the Mac spec.
- Ensure `report_utils` is included in hidden imports.
- Keep platform path differences in mind:
  - Windows `%APPDATA%\\AdoptIQ`
  - macOS `~/Library/Application Support/AdoptIQ`
- SmartScreen notes are Windows-only; ignore on Mac.
- **App bounces in the Dock and exits after dragging to Applications:** This is macOS Gatekeeper / AMFI killing the adhoc-signed bundle because of `com.apple.quarantine`. Two ways to confirm and recover:
  1. Run the `Unblock AdoptIQ.command` helper that ships in the DMG window — it strips the quarantine attribute and launches the app.
  2. Or, manually in Terminal:
     ```bash
     xattr /Applications/AdoptIQ.app                       # confirm com.apple.quarantine is present
     xattr -dr com.apple.quarantine /Applications/AdoptIQ.app
     open /Applications/AdoptIQ.app
     ```
  If the unblock helper is missing from the DMG, regenerate it via `./build_mac_dmg.sh`. The build script re-codesigns the staged bundle adhoc with `codesign --force --deep --sign -` after using `ditto` to copy it; both steps are required to keep the deep signature valid on Apple Silicon.
- **`codesign --verify --deep --strict` fails on `OUTBOX/AdoptIQ.app`:** Almost always caused by a stray extended attribute or by `cp -R` instead of `ditto`. Re-run the build; `build_mac.sh` strips xattrs (`xattr -cr`) and re-signs adhoc as part of staging.

## 8) Paste-ready Cursor kickoff prompt (Mac)

```text
You are in the AdoptIQ_MAC staging codebase. Please:
1) Implement all parity items from MIGRATION_TO_MAC.md.
2) Confirm build prerequisites and secrets setup, including `CIRCUIT_MODEL_NAME=gemini-3.1-flash-lite` if the packaged app should use Gemini by default.
3) Build a macOS artifact using `./build_mac.sh` and optionally `./build_mac_dmg.sh`.
4) Run tests and provide a concise validation report with output paths.
```

