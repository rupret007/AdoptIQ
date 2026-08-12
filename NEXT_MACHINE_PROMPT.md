# AdoptIQ — Next machine session prompt (Build 112 / Round 162)

**For the other computer:** clone/pull `main`, open this repo as the workspace, then copy everything **below the horizontal rule** into a new Cursor / Claude Code / Codex session.

**Git tip after pull:** `git pull origin main && git rev-parse HEAD` — expect **`dde7eaf`** or later.

**Repos (both on `main`, fast-forward only):**

| Remote | URL |
|--------|-----|
| Cisco (primary) | `https://wwwin-github.cisco.com/jestory/AdoptIQ` |
| GitHub (mirror) | `https://github.com/rupret007/AdoptIQ` |

**Baseline:** Round 162 code + Build 112 live smoke @ **`a66b006`**; handoff docs on latest `main` after pull.

---

## Step 0 — On the other computer (before pasting)

```bash
git clone https://wwwin-github.cisco.com/jestory/AdoptIQ.git
# or: git clone https://github.com/rupret007/AdoptIQ.git

cd AdoptIQ
git checkout main
git pull origin main
git rev-parse HEAD
git log -3 --oneline
```

Open this folder in the IDE. You need **`secrets.env`** (or a frozen build with bundled secrets) for VPN/Snowflake live work — **never commit secrets**.

Also read on disk: this file, `HANDOFF_PROMPT.md`, `CODEX_HANDOFF_PROMPT.md`, `CLAUDE.md`, `QUALITY_AUDIT.md` (Round 162 sections).

**Build 112 DMG is not in git.** Copy `OUTBOX/AdoptIQ-v1.0.4-build112.dmg` from the Mac build host (USB/OneDrive) or rebuild after pull.

---

## Your mission

You are taking over **AdoptIQ**, a Cisco CSM renewal-risk and adoption intelligence app (Flask + PyInstaller). It generates **Word + Excel** reports from Snowflake, CSConsole, CSOne, and external intel, plus grounded **Ask AI**.

**North star:** Report-accuracy-first. Every KPI count, risk score, TAC total, health grade, and citation must agree across Word, Excel, Compact, Renewal, Comprehensive, and Leader for the same scope. LLM narratives are **downstream of canonical data** — never the source of truth for counts.

**Quality floor:** **7150** pytest passed / 7 skipped / 14 deselected; `make verify` must stay green before any push.

**Read first (≈15 min):**

1. `CLAUDE.md` — Critical Rules / SSoT
2. `QUALITY_AUDIT.md` — `## Round 162 — handoff`, `## Round 162 — Build 112 live smoke`, `## Round 162 — remote integration`
3. `CODEX_HANDOFF_PROMPT.md` — Phase A audit checklist
4. `.cursor/BUGBOT.md` + `.cursor/rules/quality-gate.mdc`

---

## What Round 162 shipped (audit before new features)

### 1. Comprehensive degraded-continue (main bug fix)

- **Problem before R162:** Comprehensive **hard-aborted** when scoped AB **and** scoped CSOne were both empty — even with **31 team subscriptions** and CSConsole data present.
- **Fix:** `app_simple.py::_r162_comprehensive_integrity_should_abort` (~18581; called ~19453 in comprehensive path).
- **Contract:**
  - **CONTINUE** with `partial_data_warnings` when scoped AB + CSOne empty **but** team subscriptions **or** CSConsole frames exist.
  - **ABORT** when subscriptions **and** CSConsole are truly empty (only external intel).
  - **ABORT** on genuine quality/integrity failures (not empty AB+CSOne alone).
- **Tests:** `tests/test_round162_comprehensive_integrity_degraded_continue.py`

### 2. CSOne OneDrive autodiscovery hardening

- **Problem:** OneDrive folder full of **AdoptIQ Enhanced Premium Collab Summary-*.xlsx** (unreadable/corrupt prior outputs).
- **Fix:** `app_simple.py::get_latest_csone_from_folder_diag` (~4783).
- **Contract:**
  - Skip Enhanced Collab / AdoptIQ output filenames; skip zero-byte placeholders (R68).
  - **openpyxl probe** on `.xlsx`; corrupt newest → fallback to next candidate by mtime.
  - **`.xls` pass-through** without openpyxl probe.
  - No readable file → empty path + honest `csone_load_failure` / `partial_data_warnings` — never fake success.
- **Tests:** `tests/test_round162_csone_autodiscovery.py`, R161/R68 pins

### 3. Progress partial-data warnings

- **Fix:** `templates/progress.html` — show `partial_data_warnings` during `running` **and** `completed`, not only on `error`.
- **Contract:** Must not mask genuine **`error`** status.
- **Tests:** `tests/test_round162_progress_partial_warnings.py`

### 4. Analyze report-type cards

- **Fix:** `templates/analyze.html` + `static/css/manager_decision_workspace.css` — 5-col grid, badge slot on all five cards, flex-pinned radios.
- **Tests:** `tests/test_round146_manager_workspace_ui.py`

---

## Issues **found** (Build 112 live acceptance — facts, not bugs to paper over)

Live run **PASSED**. Do **not** re-run VPN acceptance unless report logic changes.

**Run metadata:**

| Field | Value |
|-------|--------|
| Command | `python3 report_iteration_loop.py --scenarios comprehensive --iterations 1 --baseline-mode off --run-id r162-build112-live --stop-on-failure` |
| Scope | Brian Frazier / All Contact Center / 90d Comprehensive |
| VPN | ON; **no manual CSOne upload** |
| Result | **`all_passed: true`**, ~229s, `status=completed` |
| Analysis id | `Brian_Frazier_All_Contact_Center_90d_1786484044603608000_b3a5a925` |
| Summary (build Mac) | `~/Downloads/AdoptIQ_ReportIterationSummary__data-loop-r162-build112-live__ts-20260811T213752Z.json` |
| Artifacts | `~/Documents/AdoptIQ Reports/Brian_Frazier/Comprehensive/AdoptIQ_Report_*_20260811_213434_*.docx` + `AdoptIQ_Source_Data_*.xlsx` |

### F1 — No readable CSOne autodiscovered

- **Evidence:** `csone_file_path` empty; OneDrive dominated by Enhanced Collab / unreadable `.xlsx`.
- **Behavior:** R162 correct — run continued; TAC source **`unavailable`** (not silent zero).
- **Do NOT:** Load Enhanced Collab as CSOne; do not treat unavailable TAC as real zero.
- **Explore:** Real CSOne export in OneDrive (Help / `CSONE_REPORT_URL`) OR Preferences → CSOne folder override.

### F2 — Scoped data thin vs subscription roster

- **Evidence:** Scoped AB=**2**; CSOne=**0**; subscriptions **31** in team context.
- **Warnings:** `tech_filter_scope_excluded` (AB), `tech_filter_empty_after_scope` (AP) on status + Excel `Report_Info`.
- **Do NOT:** Widen ACC scope without product decision (R93).
- **Explore:** Progress UX copy; Leader `total_customers` drift (P1, out of R162 scope).

### F3 — Stale baked corpus in Build 112 DMG

- **Evidence:** Rebake failed — OneDrive timeout on corrupt Enhanced Collab; manual bake failed reranker SSL.
- **Shipped:** Restored Build **111** `bake/corpus.db.enc` + salt + sentinel before PyInstaller.
- **Impact:** Ask AI works; corpus age/stale vectors possible.
- **Explore (P0 Build 113+):** Clean fixture → `build_mac_dmg.sh` + `ADOPTIQ_RELEASE_GATE=1` (`CURSOR_MAC_BUILD_INSTRUCTIONS.md` §9.7).

### F4 — DMG / latest.json not in git

- **DMG:** `OUTBOX/AdoptIQ-v1.0.4-build112.dmg` (393,479,664 B; sha256 `4ef24c7e7efb448ef5fa7e991d343661b7355c9b2786ca81c1efb34cfe6af61f`) — **Mac build host only**.
- **Explore:** Copy to synced OneDrive OUTBOX; never commit.

### F5 — Windows latest.json pc slot behind

- Mac slot **112** locally; PC has not run `build_pc.bat` with `ADOPTIQ_BUILD=112`.
- **Explore (P0):** Windows build + merge-aware `scripts/write_release_manifest.py`.

**Build 112 smoke (reference):** `scripts/test_build_smoke.sh` pass; `/Applications/AdoptIQ.app` → build **112**; Word footer `App_Build: 112`.

---

## Issues **to explore** (prioritized)

Stop when blocked by VPN, secrets, or platform.

### P0 — Windows Build 112

1. Pull `main` on Windows PC.
2. `ADOPTIQ_BUILD=112` → `build_pc.bat` (`BUILD_WINDOWS.md`).
3. EXE smoke :5151.
4. Merge-aware `scripts/write_release_manifest.py` — do **not** clobber mac slot.
5. Document in `QUALITY_AUDIT.md`; push only if `make verify` green.

### P0 — Corpus rebake (Mac, Build 113+)

1. Clean OneDrive / readable CSOne export (not Enhanced Collab only).
2. `bash build_mac_dmg.sh` + `ADOPTIQ_RELEASE_GATE=1`.
3. Verify `bake/corpus.db.enc`, salt, sentinel — no `.bake-skipped`.
4. Full gate: `CURSOR_MAC_BUILD_INSTRUCTIONS.md` §9.10.

### P0 — DMG distribution

Copy Build 112 DMG to OneDrive OUTBOX for auto-update.

### P1 — Follow-ons (out of R162 scope)

| Item | Notes |
|------|--------|
| Leader `total_customers` drift | e.g. 10 vs 14 — investigate universe predicates separately |
| CSOne folder hygiene | Preferences override; Help export workflow |
| Formatter empty-DF audit | R38.2 pattern in compact/executive formatters (leader done) |
| Grounding ~6.7% | Within ≤10% R139; don't weaken validator without live data |

### P2 — Methodology

- Round 76: pytest ≠ production for Snowflake/narratives/formatters → bake → install → 4 reports → `r114_audit_reports.py --auto`.
- 10-step live checklist in `HANDOFF_PROMPT.md`.

---

## Phase A — Mandatory audit (first)

Append to `QUALITY_AUDIT.md`:

```markdown
## Round 162 — <tool> review <YYYY-MM-DD>
**Verdict:** pass | pass-with-fixes | fail
**Findings:** (numbered, severity)
**Fixes applied:** file:line — or none
**Verify after fixes:** make verify — pass/fail; pytest count
**Trailer:** Made-with: <tool>
```

| Hotspot | File | Verify |
|---------|------|--------|
| Degraded-continue | `app_simple.py::_r162_comprehensive_integrity_should_abort` | Abort if subs+CSConsole empty; continue if either present with empty AB+CSOne; abort on real quality failures |
| CSOne autodiscovery | `get_latest_csone_from_folder_diag` | Skip Enhanced Collab; corrupt fallback; `.xls` pass-through; honest failures |
| Progress UX | `templates/progress.html` | Warnings on running/completed; error not masked |
| Analyze cards | `analyze.html` + `manager_decision_workspace.css` | 5-col grid, badges, narrow viewport |

**Commands (in order):**

```bash
git checkout main && git pull origin main
python3 -m pytest tests/test_round162_*.py -v
python3 -m pytest tests/test_round146_manager_workspace_ui.py \
  tests/test_round161_2_csone_autodiscovery_skip_adoptiq_output.py \
  tests/test_round68_csone_autodiscovery_skip_zero_byte.py -v
git diff 4e4cb71..a66b006 | grep 'Round 162'
make verify
```

**Only if report logic changes (VPN + secrets):**

```bash
bash scripts/test_build_smoke.sh /Applications/AdoptIQ.app
python3 report_iteration_loop.py \
  --scenarios comprehensive --iterations 1 \
  --baseline-mode off --run-id next-machine-r162 --stop-on-failure
python3 scripts/r114_audit_reports.py --auto
```

---

## Hard constraints

- Never skip/weaken tests; never recompute counts outside `canonical_metrics.py`.
- Snowflake: parameterized queries only.
- Never commit: `secrets.env`, `_bundled_secrets.py`, `OUTBOX/`, `*.dmg`, `embeddings/`.
- Do not collapse Leader vs Comprehensive scope without product sign-off.
- No force-push.

---

## Kickoff — paste this as your first user message

Copy the block below into a **new chat** on the other machine (after Step 0):

---

**AdoptIQ work machine — Build 112 / Round 162 kickoff**

I pulled `main` and opened the repo. Read `NEXT_MACHINE_PROMPT.md`, `CLAUDE.md`, and `QUALITY_AUDIT.md` Round 162 sections.

**Your job — Phase A audit first:**

1. Confirm `git rev-parse HEAD` and run narrow tests:
   - `python3 -m pytest tests/test_round162_*.py -v`
   - then `make verify` (floor: 7150 passed)

2. Audit these hotspots in code (read the functions, don't assume tests are enough):
   - `app_simple.py::_r162_comprehensive_integrity_should_abort` — degraded-continue contract
   - `app_simple.py::get_latest_csone_from_folder_diag` — Enhanced Collab skip, corrupt fallback, honest empty path
   - `templates/progress.html` — partial warnings during running/completed without masking error
   - Analyze 5-col cards in `analyze.html` + `manager_decision_workspace.css`

3. Treat these as **known facts from live acceptance** (do not "fix" without intent):
   - **F1:** No readable CSOne in OneDrive (Enhanced Collab junk); TAC unavailable — OK
   - **F2:** Thin scoped AB/CSOne vs 31 subs — partial warnings expected
   - **F3:** Build 112 DMG has stale Build 111 baked corpus (rebake blocked)
   - **F4:** DMG not in git — manual OUTBOX copy for auto-update
   - **F5:** Windows `latest.json` pc slot needs PC build

4. Append `## Round 162 — review <date>` to `QUALITY_AUDIT.md` with verdict, numbered findings, fixes (if any), verify status.

5. **Do not** re-run live VPN comprehensive unless you change report logic.

6. After audit clean, pick **one** P0 from `NEXT_MACHINE_PROMPT.md` (Windows Build 112, corpus rebake, or DMG distribution) only if I say so in a follow-up message.

Fix only real defects: smallest diff, regression test, `# Round 162.1` markers, `make verify` green before push to both remotes.

---

**End of next-machine prompt.**
