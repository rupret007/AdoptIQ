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
5. Round 104 / Build 73 ships the first live report audit fix. During smoke, confirm `/api/settings/report-model` and `/api/settings/ask-ai-model` both resolve to `gemini-3.1-flash-lite`.
6. Round 105 / Build 74 ships the follow-up live artifact closeout. Prefer a fresh Compact/Renewal same-scope run when time allows to verify risk-score parity after incident recovery, confirm Compact `Report_Info` carries ACC partial warnings, and spot-check Leader `BE_Priority_Barriers` titles/descriptions are populated instead of all `AMBIGUOUS`.
7. Round 110 / Build 79 ships a Leader `Report_Info` xlsx schema-parity fix. During smoke, regenerate any Leader scope that produces a `partial_data_warning` (e.g. a tech filter that excludes the entire scope, or a scope known to leave a sheet empty) and confirm the resulting `Report_Info` sheet has exactly four columns `(Item, Value, Detail, Generated_At)` with no stray `Field` column. Pre-Build 79 the partial-warning and failed-sheet rows were keyed on `'Field'` while everything else used `'Item'`, silently producing a 5-column NaN-leaked sheet on any non-trivial run.
8. Round 111 / Build 80 ships a Compact ↔ Renewal `Risk_Score_0_10` parity fix. During smoke, generate Compact + Renewal reports for the same manager + technology + days scope, then read back `Risk_Summary.Overall_Risk_Score` (Compact) and `Renewal_Summary.Overall_Risk_Score` (Renewal) for any customer present in both — they MUST agree to one decimal place per the R67/B1 contract. Pre-Build 80, ~70% of common customers showed Renewal scoring ~+1.0 higher on the 0-10 scale because the Compact path's frame classifier dropped the production CSConsole pulse columns; Build 80 widens the classifier and threads explicit pulse / action-plan / subs frames into the Compact scoring path.
9. Round 112 / Build 81 ships a 6-finding Build 80 acceptance fix loop. During smoke: (a) **F1 SECURITY** — if the run hits a CircuIT 429 rate-limit, open the Compact docx and verify the "Non-AI fallback summary" paragraph contains NO raw `appkey=...` / `session_id=...` / `'user': '{...}'` blob (these MUST be redacted as `<redacted>`); (b) **F2** — open the Renewal docx and verify the score-summary box renders as `X.X/10 (label; X.X/100)` (0-10 primary, 0-100 parenthetical) AND every per-customer focus-table row's score column ends in `/10` (NOT `/100`); (c) **F3** — open the Leader docx and search for `Total Activities:` — every match line MUST have ONE citation immediately after the closing `)` of the embedded paren cluster, NOT TWO citations split around the cluster; (d) **F4** — generate a Comprehensive report and verify the "BE Priority Focus Areas" intro paragraph honestly names whether LLM tagging happened (`"the top N of M barriers were also tagged"` on success vs `"LLM classification was attempted... but did not return any tags this run"` on failure vs `"LLM classification was disabled"` if the kill-switch is set); (e) **F5** — for any scope-excluded run (a tech filter that drops all rows, a manager filter that drops all subs), open the Renewal/Compact docx and verify the partial-data warning paragraph says `"data was filtered out by the requested scope"` (NOT `"data sources failed to load"`); (f) **F6** — for an `All Managers` scope run, open the Compact/Renewal docx title and verify it reads `"All Managers Portfolio"` (NOT the pre-R112 ungrammatical `"All Managers's Portfolio"` double possessive).
9.1. Round 113 / Build 82 ships the Ask AI uplift + Preferences fix-and-polish. During smoke: (a) **A1/A2** — open `/ask-ai`, ask a question, toggle "Continue conversation", ask a follow-up, and confirm (i) the prior Q/A renders as a visible thread above the new answer AND (ii) the follow-up answer reflects the prior context even with streaming OFF (pre-R82 the toggle was a no-op on the sync path); (b) **A3** — start a question and confirm the progress line reads "Scanned N adoption barriers / M support cases across K customers; ranking evidence…" rather than a blank wait; (c) **A4** — confirm the question box is a multi-line textarea (Shift+Enter inserts a newline, Enter sends); (d) **A5** — after an answer, click Copy (markdown lands on the clipboard) and Download (.md / .txt files save); (e) **B1** — ask a renewal/ARR question and confirm the answer cites the contracts-expiring / expiring-ARR / renewals-at-risk figures (a multi-currency portfolio shows a per-currency breakdown, NOT one number); (f) **B2** — after asking a renewal question for a scope, reload `/ask-ai` for the SAME scope and confirm a suggestion chip names a real top-risk customer (cold scopes show the template chip, never blank); (g) **B3** — click a customer name in an answer / the evidence drawer and confirm it opens `/customer/<name>` Customer 360 in a new tab; (h) **C1** — open `/preferences` and confirm the CSOne folder card shows the resolved path (NOT "(no path resolved)"); (i) **C2** — flip the Intelligence toggle on `/preferences` and confirm a success/error message appears; (j) **C3** — set a default manager/technology/days in the new "Default analysis scope" card, save, reload `/` and `/ask-ai`, and confirm both pages pre-select the saved scope; (k) **C4** — open `/preferences` with devtools Network open and confirm there is NO recurring `/api/intel/status` poll loop (a single one-shot fetch on the Intelligence toggle is fine).
9.2. Round 114 / Build 83 ships the citation de-clutter. During smoke: generate a Leader report (and ideally all four types), open the Word doc, and confirm (a) the multi-column count matrices (Team Activity Summary, Individual Team Member Performance) have **NO** `[Source: ...]` chrome inside their data cells — clean numbers only; (b) directly **below** each such matrix there is **exactly one** italic caption paragraph beginning `Sources:` naming the table's data systems (e.g. "Sources: Action Plans, Adoption Barriers, Customer Pulse — Snowflake CSConsole; TAC Cases — Snowflake CSOne"); (c) simple two-column key/value tables still carry their single per-row citation (unchanged). Cross-check with the read-only audit: `python3 scripts/r114_audit_reports.py` (point its `TARGETS` at the freshly generated Build-83 reports) should report `per_cell_citations` ≈ 0 on the matrix-heavy reports and `caption_paragraphs` >= 1, with `CRITICAL_ISSUES_FOUND=False`.

9.3. Round 115 / Build 84 ships the report-model re-flip + 2-column citation de-clutter. During smoke: (a) **model** — open `/api/settings/report-model` and confirm it resolves to `gemini-3.1-flash-lite` (NOT `gpt-5-nano`); if this box previously ran nano, the Build-84 one-time re-migration should have flipped it on first launch — confirm the active report model on a freshly generated report's footer/diag chip reads gemini; (b) **2-column cards** — generate a Leader report, open the Word doc, find the per-CSSM "Metric | Value" KPI cards, and confirm they have **NO** `[Source: ...]` chrome inside their value cells — instead there is **exactly one** italic `Sources:` caption directly below each card naming the metrics' data systems (e.g. "Sources: Adoption Barriers, Action Plans, Customer Pulse — Snowflake CSConsole; Support Cases — Snowflake CSOne"); (c) **audit** — run `python3 scripts/r114_audit_reports.py --auto` (auto-discovers the latest report of each type) and confirm `per_cell_citations` ≈ 0 on BOTH matrices and 2-column cards, `caption_paragraphs` >= 1, and `CRITICAL_ISSUES_FOUND=False`. Note: residual `Unknown` / `N/A` cells in case Status/Type, Sentiment, or Customer-Pulse Date columns are **honest empty-source fallbacks** (the raw Snowflake field was null), not bugs — do NOT infer values to "fix" them.

9.4. Round 116 / Build 85 ships the model heal + "All Contact Center" customer-count fix + admin/help/audit sweep. During smoke (VPN ON for the regen steps):
   - **(a) Model heal (A)** — open `/api/settings/report-model` and `/api/settings/ask-ai-model`; BOTH must resolve to `gemini-3.1-flash-lite` even on a box that was previously wedged at `gpt-5-nano`. To prove the deliberate-nano path still works: in Preferences pick `gpt-5-nano` for the report model, click **Test** (must pass `/api/llm/ping`), **Save**, then re-open `/api/settings/report-model` and confirm it now reports `gpt-5-nano` (the `report_model_user_set` flag protects it); clear the override (empty) + Save and confirm it heals back to `gemini-3.1-flash-lite`.
   - **(b) ACC customer-count floor (B)** — with VPN connected, run a **Comprehensive** report for **Brian Frazier / All Contact Center / 90d**. Open the Excel `Summary` sheet and confirm `Customers in portfolio` reflects the full team CC roster (expected floor ~37-49, NOT ~15/24). Confirm the **Word** title page "Customers in portfolio" + the risk-band buckets sum to the SAME number (R64 coherence). Confirm the `AB_Detail_All` sheet is still strictly ACC-scoped (R93 preserved) and any dropped rows show as a `tech_filter_scope_excluded` partial-data note. Cross-check with `python3 scripts/r114_audit_reports.py --auto` — the Comprehensive line prints `customers_in_portfolio: N`; on an All-Contact-Center report it flags `< 30` as `<-- LOW? confirm against team CC roster`. A healthy Build-85 regen should NOT be flagged.
   - **(c) Admin/Quit UX (C)** — confirm the navbar shows a visible **Quit** label with a divider before it, and that clicking **Admin Console** opens `http://127.0.0.1:<resolved-admin-port>/` in a NEW tab WITHOUT stopping the server (the running app stays up). Only the Quit button stops the server.
   - **(d) Help page (D)** — open `/help` and confirm the operator-first sections render (Quick Start, Report Chooser, Data Sources, Preferences, Intelligence, Ask AI, External Intelligence, Finding Reports, Admin Console, Troubleshooting), the in-page search works, and the connectivity self-test button still runs.

9.5. Round 117 / Build 86 ships the post-run form reset + CSOne export instructions. During smoke:
   - **(a) Form reset (A)** — on the main analyze page, fill in a manager/technology/days, optionally pick a non-Comprehensive report type and a subscription/customer, then start a report. Confirm the page STAYS (single-window R91 flow — no redirect to `/progress/`), the new job appears in the live jobs list, AND the form clears back to fresh-page-load defaults: technology back to default, report type back to **Comprehensive**, the uploaded file + subscription/customer filters cleared, no lingering red/green validation borders. Repeat on the **Leader** form (`/leader_report_form`) — manager back to default, days back to 90, file cleared.
   - **(b) CSOne export instructions + link (C)** — under the CSOne upload field on BOTH forms, click **"How do I generate this report?"** and confirm the 5-step collapsible expands (open report → top-right dropdown → Export → Standard → upload) with a working link to the CSOne report (`CSONE_REPORT_URL`). Open `/help` → Data Sources and confirm the same numbered "Generate the CSOne export yourself" workflow + link is present.
   - **(c) File-input pin (B)** — with the optional intel-upload feature on, confirm choosing a `.xlsx` in the CSOne field still shows the file preview / validation against the CSOne field (NOT the intel-upload input).
   - **(d) Report audit (D)** — run `python3 scripts/r114_audit_reports.py --auto`; confirm `CRITICAL_ISSUES_FOUND=False` and (on a freshly regenerated All-Contact-Center Comprehensive) the customer count is not flagged LOW. Open the Compact docx and confirm there are no empty `"<Category>: Data unavailable."` bullets.

9.6. Round 126–127 / Build 95–96 (report accuracy closeout + Ask AI case search). **Mac and PC MUST ship the same `ADOPTIQ_BUILD` from [`config.py`](config.py) (currently **96**). During smoke on VPN:
   - **(a) Build label** — `GET /api/version` → `build: "96"`; report footer shows `v1.0.4 build 96`.
   - **(b) Reports** — regenerate Compact + Renewal + Comprehensive + Leader for a known scope (Brian Frazier / All Contact Center / 90d is the acceptance cohort). Run `python3 scripts/r114_audit_reports.py --auto` on the **new** artifacts only; gate is `CRITICAL_ISSUES_FOUND=False`.
   - **(c) Ask AI (Round 127)** — on `/ask-ai`, ask a case-search question (e.g. compliance / eDiscovery / terminated users). Confirm answer cites case **description** text, chat bubbles render, streaming lands in the assistant bubble, and customer drill-through links work.
   - **(d) PC parity** — after `build_pc.bat` on Windows, confirm OneDrive `AI Projects/OUTBOX/AdoptIQ_PC/` has `AdoptIQ-v1.0.4-build96.exe`, `build_info.txt` build **96**, and `latest.json` carries **both** `mac.build` and `pc.build` = **96**.

10. Generate bundled secrets:

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

As of Round 107 / Build 76, release builds require a prebaked corpus.
`ADOPTIQ_RELEASE_GATE=1` verifies that `bake/corpus.db.enc`,
`bake/corpus.db.salt`, and `bake/sentinel.json` are present before
PyInstaller runs, and that `.bake-skipped` is not present. The app installs
that snapshot into App Support on first launch; OneDrive remains optional
background refresh context.

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
- The app bundle contains the three prebaked corpus artifacts and no runtime lock:

```bash
test -f /tmp/adoptiq_dmg/AdoptIQ.app/Contents/Resources/baked_corpus/corpus.db.enc
test -f /tmp/adoptiq_dmg/AdoptIQ.app/Contents/Resources/baked_corpus/corpus.db.salt
test -f /tmp/adoptiq_dmg/AdoptIQ.app/Contents/Resources/baked_corpus/sentinel.json
test ! -f /tmp/adoptiq_dmg/AdoptIQ.app/Contents/Resources/baked_corpus/corpus.sentinel.lock.json
```
- Round 108 corpus smoothness smoke: after launch, `/api/corpus/status`
  should show an active prebaked/local corpus even when OneDrive is not
  synced. The Knowledge Corpus panel should frame OneDrive as optional
  refresh coverage and should expose dense retrieval status (`hybrid ready`
  or lexical fallback with an error reason).
- Round 109 indexing-hang smoke (Build 78+): on an existing-home install
  whose corpus already has chunks, the panel must NOT stay on "Indexing".
  `/api/corpus/status` should return `boot.in_progress=false`,
  `boot.completed=true`, and `last_finished_at` populated within ~60s of
  launch even when the dense backfill is still running. If a runtime
  vector pass leaves a backlog, the panel should render the corpus as
  "Active" and surface "Dense retrieval is warming • backfilling N chunks"
  as a quality note (status payload exposes `boot.dense_rows_remaining`).
- Round 110 Leader Report_Info schema-parity smoke (Build 79+): generate
  any Leader report whose scope produces at least one `partial_data_warning`
  (the easiest trigger is a tech filter that excludes every row in scope,
  surfacing as a `tech_filter_scope_excluded` warning per Round 93) OR any
  failed sheet. Open the resulting xlsx, navigate to the `Report_Info`
  sheet, and confirm the header is exactly `(Item, Value, Detail,
  Generated_At)` with no stray `Field` column. Pre-Build 79 the dynamic-
  loop row builders for `Partial_Data_Warning_<n>` and `Failed_Sheet`
  used `'Field'` as the first-column key, silently growing a fifth
  `Field` column with NaN on every other row whenever those branches
  fired. `pd.read_excel("Report_Info")[["Item", "Value"]]` must succeed
  cleanly across every report format (the R73/F6 contract).
- Round 111 Compact ↔ Renewal score parity smoke (Build 80+): generate
  Compact + Renewal reports for the same manager + technology + days
  scope (e.g. Brian Frazier, All Contact Center, 90 days) and read back
  `Risk_Summary.Overall_Risk_Score` (Compact) and
  `Renewal_Summary.Overall_Risk_Score` (Renewal) for any customer
  present in both. Per the R67/B1 cross-format parity contract the
  scores MUST agree to one decimal place; pre-Build 80 ~70% of common
  customers showed Renewal scoring ~+1.0 higher on the 0-10 scale
  because `_r66_b8_classify_extra_frames` did not recognise the live
  production `CUSTOMER_PULSE__C` / `CUSTOMER_PULSE_COLOR_IMAGE__C`
  columns and Compact's per-customer pulse data was always empty.
  Build 80 widens the classifier markers AND threads explicit canonical
  pulse / action-plan / subs frames from `app_simple.py` into the
  scoring path, bypassing classification.

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
       - If macOS says "Apple could not verify..." while opening the DMG,
         click Done, open System Settings > Privacy & Security, click
         Open Anyway / Allow for AdoptIQ, then open the DMG again.
       - If the previous /Applications/AdoptIQ.app is still present,
         either drag the new one over it ("Replace") or rm -rf the old
         one first.
       - After dragging the app to Applications, double-click the
         "Unblock AdoptIQ.command" helper in the DMG window once.

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
- **macOS blocks the DMG with "Apple could not verify..." before you can drag the app:** this is expected for adhoc, non-notarized internal builds. Click Done, open System Settings > Privacy & Security, click Open Anyway / Allow for AdoptIQ, then open the DMG again. This DMG-level approval is separate from the post-install `Unblock AdoptIQ.command` step.
- **App starts and the browser works, but the Dock icon keeps bouncing:** ensure the installed app came from a Round 99+ DMG. The launcher script inside `Contents/MacOS/AdoptIQ` should start `AdoptIQ.bin` with `nohup ... &` and exit; older Build 69 launchers used `exec`, which left the browser-only server as the foreground app process.
- **Corpus panel shows `authentication tag mismatch` after an upgrade:** a stale local encrypted corpus may have been sealed under an older key. Round 106+ preserves the stale artifacts as `.broken-<utc>` sidecars and retries a clean local index. If the error persists, use the app's Reset Corpus action.
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

