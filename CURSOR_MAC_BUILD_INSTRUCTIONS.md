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
3. CircuIT model selection: as of Round 103 / Build 71 the demo default is enforced as `gemini-3.1-flash-lite`. Stale saved settings or bundled env values that still say `gpt-5-nano` are migrated/mapped to Gemini on startup; to deliberately use `gpt-5-nano`, let the operator flip it via the **Preferences** UI page (test-before-save) or the Admin Console settings tile after launch.
4. Round 103.1 / Build 72 ships the Report Jobs theme cleanup. During smoke, confirm Current Running Reports and Historical Reports stay on the existing dark/orange theme and active rows use an orange accent rather than gray/light Bootstrap row styling.
5. Round 104 / Build 73 ships the live report audit fix. During smoke, confirm `/api/settings/report-model` and `/api/settings/ask-ai-model` both resolve to `gemini-3.1-flash-lite`, and prefer a fresh Compact/Renewal same-scope run when time allows to verify risk-score parity after the incident-scoring fix.
6. Generate bundled secrets:

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

## 4b) Pre-build verification (recommended)

```bash
make verify
```

This runs `pytest`, `ruff check`, `bandit -ll`, and `pip-audit -r requirements.txt`. All four gates must pass before producing a release build. Current baseline (Round 48 / Build25 close-out): **3191 passed / 2 skipped**, ruff clean, no HIGH/MED bandit findings, no pip-audit vulns.

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
ADOPTIQ_RELEASE_GATE=1 ./build_mac_dmg.sh
```

As of Round 96 / Build 69, release builds are runtime-only for corpus data.
`ADOPTIQ_RELEASE_GATE=1` verifies that the DMG path skipped corpus baking and
that stale `bake/corpus.db.enc` / `bake/corpus.db.salt` artifacts are absent
before PyInstaller runs. The app indexes the authorized OneDrive corpus locally
after launch; no corpus database, salt, sentinel, or `Resources/baked_corpus/`
payload should ship in the app bundle.

If no Mac script exists yet, ask Cursor:

```text
Create a macOS build script equivalent to build_pc.bat that installs deps, runs embed_credentials.py, updates version/build metadata, runs PyInstaller with a Mac spec, and writes outputs to OUTBOX.
```

## 6) Verify build outputs

`build_mac.sh` should produce:
- `dist/AdoptIQ.app`
- `OUTBOX/README.md`
- `OUTBOX/AdoptIQ-v<version>-build<build>.dmg`

Run the packaged-app smoke against the rebuilt app bundle:

```bash
scripts/test_build_smoke.sh
```

The smoke polls `/ping`, then checks `/`, `/api/version`, `/api/status/all`, `/api/corpus/status`, and confirms the port is free after quit. If you need to smoke a mounted DMG or copied app instead, pass the app path explicitly:

```bash
scripts/test_build_smoke.sh /Applications/AdoptIQ.app
```

Round 102 / Build 70 reminder: the packaged app must not request
Downloads-folder access during startup or corpus refresh. The retired
`CSONE_INCLUDE_USER_DOWNLOADS` setting is ignored; corpus sources are
authorized OneDrive data, sidecar-gated generated reports, and explicit
Intelligence uploads.

For demo-readiness or release-candidate soak validation, launch the rebuilt app
and run the Round 101 time-boxed live report supervisor:

```bash
open dist/AdoptIQ.app
python3 scripts/run_report_soak.py \
  --base-url http://127.0.0.1:5151 \
  --duration-seconds 7200 \
  --scenarios comprehensive,compact,renewal,leader \
  --baseline-mode off \
  --request-timeout 180 \
  --download-timeout 600
```

Use `--baseline-mode manifest` when a fresh manifest exists for the current
Snowflake data. Use `--baseline-mode off` for operational stability soaks when
older manifests have drifted; strict report generation, downloads, structure,
and quality checks still run. The supervisor writes child logs and
`soak_summary.json` under `~/Downloads/adoptiq_report_soak_<run-id>/`.

Verify the build output before shipping the DMG to anyone:

```bash
codesign --verify --deep --strict dist/AdoptIQ.app
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
- App shows the TACTrack-style startup splash, polls `http://localhost:5151/ping`, then redirects to `http://localhost:5151` once Flask is ready.
- The Dock icon does not keep bouncing after Flask is ready. Round 99 backgrounds `AdoptIQ.bin` from the launcher wrapper and exits the wrapper so Finder/Launch Services can complete the app launch.
- No missing module errors at startup
- The app bundle contains no corpus data:

```bash
test ! -e /tmp/adoptiq_dmg/AdoptIQ.app/Contents/Resources/baked_corpus
find /tmp/adoptiq_dmg/AdoptIQ.app -name 'corpus.db.enc' -o -name 'corpus.db.salt' -o -name 'sentinel.json' -o -name 'corpus.sentinel.lock.json'
# Expected: no output
```

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

## 6.5) Round 68 / Build 42 — install-and-quit operator checklist

**Why this matters.** Build 41 acceptance shipped reports that *looked* fixed
in the source but still showed pre-Build-41 behavior in the docx/xlsx output.
Root cause: the operator dragged the new `AdoptIQ.app` over `/Applications/`
without first quitting the running AdoptIQ process, so macOS happily replaced
the bundle on disk while leaving the previously-launched process — running
the *old* code in memory — bound to `127.0.0.1:5151`. Subsequent reports were
generated by the stale process.

Round 68 hardens both ends of this trap (visible build labels in every
report, `/api/version` + restart-required banner, deterministic narrative
escalation), but the *operator step* remains the same: **kill before you
install, verify after you install.**

Use this checklist for every DMG drop. Tick each item; do not skip the
verify step even if the app "looks fine."

```text
[ ] 1. KILL the running app FIRST.
       Choose ONE:
         - Quit from the navbar (the new "Quit AdoptIQ" button), OR
         - Click the AdoptIQ menu bar item -> Quit, OR
         - In Terminal:  pkill -f AdoptIQ
       Confirm no process is bound to 5151:
         lsof -nP -i :5151
       (No output = port is free. If output is shown, repeat the kill.)

[ ] 2. INSTALL the new DMG.
       - Open OUTBOX/AdoptIQ-v<version>-build<N>.dmg
       - If the previous /Applications/AdoptIQ.app is still present,
         either drag the new one over it ("Replace") or rm -rf the old
         one first.
       - If macOS warns about Gatekeeper, double-click the
         "Unblock AdoptIQ.command" helper in the DMG window first.

[ ] 3. RE-LAUNCH the app.
       open /Applications/AdoptIQ.app
       (or double-click in Finder)

[ ] 4. VERIFY the new build is the one that's running.
       In a browser, hit:
         http://localhost:5151/api/version
       Confirm the JSON payload shows the build number you just installed
       (e.g. "build": "42") AND that "restart_required" is false.
       If "restart_required": true, repeat from step 1 — the running
       process is still the previous build.

[ ] 5. (Optional) Cross-check with a generated report.
       After producing any of the four reports (Compact, Renewal,
       Comprehensive, Leader), open the xlsx Report_Info sheet and look
       for the rows:
         App_Version           1.0.4
         App_Build             42
         Process_Started_At_UTC 2026-05-02T...Z
         Report_Generated_At_UTC 2026-05-02T...Z
       For docx reports, the same string appears as a right-aligned
       footer on every section.
       If App_Build does NOT match what the DMG name says, the report
       was produced by a stale process — repeat from step 1.
```

The same checklist is mirrored in `QUALITY_AUDIT.md` Round 68 handoff so
both the build operator and the auditor see it.

## 7) Troubleshooting

- If PyInstaller misses modules, add them to hidden imports in the Mac spec.
- Ensure `report_utils` is included in hidden imports.
- Keep platform path differences in mind:
  - Windows `%APPDATA%\\AdoptIQ`
  - macOS `~/Library/Application Support/AdoptIQ`
- SmartScreen notes are Windows-only; ignore on Mac.
- **App starts and the browser works, but the Dock icon keeps bouncing:** ensure the installed app came from a Round 99+ DMG. The launcher script inside `Contents/MacOS/AdoptIQ` should start `AdoptIQ.bin` with `nohup ... &` and exit; older Build 69 launchers used `exec`, which left the browser-only server as the foreground app process.
- **Corpus panel shows `authentication tag mismatch` after an upgrade:** a stale local encrypted corpus may have been sealed under an older OneDrive sentinel. Round 99+ preserves the stale artifacts as `.broken-<utc>` sidecars and retries a clean runtime index from the synced OneDrive corpus. If the error persists, use the app's Reset Corpus action and confirm `~/Library/CloudStorage/OneDrive-Cisco/.../adoptiq_corpus_sentinel.json` is present and non-empty.
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
2) Confirm build prerequisites and secrets setup. As of Round 77 / Build 53 the hardcoded default is `gemini-3.1-flash-lite` (and `secrets.env.template` ships that value); operators can flip to `gpt-5-nano` via the Preferences UI or by setting `CIRCUIT_MODEL_NAME=gpt-5-nano` in `secrets.env` before bake.
3) Build a macOS artifact using `./build_mac.sh` and optionally `./build_mac_dmg.sh`.
4) Run tests and provide a concise validation report with output paths.
```

