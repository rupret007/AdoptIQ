# AdoptIQ — Next machine session prompt (Build 112 / Round 162)

**Copy everything below the horizontal rule** into a new Cursor, Claude Code, or Codex session on your **work Mac** or **Windows PC**. This is the operator-facing “what to do next” after the dual-remote push on **2026-08-11**.

**Git tip (authoritative after pull):** run `git pull origin main && git rev-parse HEAD` — expect **`807ae96`** or later on both remotes.

**Repos (both on `main`, fast-forward only — no force push):**

| Remote | URL |
|--------|-----|
| Cisco (primary) | `https://wwwin-github.cisco.com/jestory/AdoptIQ` |
| GitHub (mirror) | `https://github.com/rupret007/AdoptIQ` |

**Code / live-smoke baseline:** Round 162 + Build 112 docs @ **`a66b006`** (`939d11d` code + live smoke reconciliation). Handoff + integration commits sit on top of that on `main`.

---

## Your mission

You are continuing **AdoptIQ** immediately after **Round 162 / Build 112** shipped to `main`.

**North star:** report-accuracy-first. Every KPI, risk score, TAC total, health grade, and citation must agree across Word, Excel, Compact, Renewal, Comprehensive, and Leader for the same scope. LLM narratives are **downstream of canonical data**.

**Quality floor:** `7150` pytest passed / 7 skipped / 14 deselected; `make verify` must stay green before any push.

**Read first (≈15 min):**

1. [`CLAUDE.md`](CLAUDE.md) — Critical Rules / SSoT
2. [`QUALITY_AUDIT.md`](QUALITY_AUDIT.md) — `## Round 162 — handoff`, `## Round 162 — Build 112 live smoke`, `## Round 162 — remote integration`
3. [`CODEX_HANDOFF_PROMPT.md`](CODEX_HANDOFF_PROMPT.md) — Phase A audit checklist (Codex) or mirror it below (Cursor/Claude)
4. [`.cursor/BUGBOT.md`](.cursor/BUGBOT.md)

---

## What Round 162 shipped (already on `main`)

| Change | Where | Operator impact |
|--------|-------|-----------------|
| **Comprehensive degraded-continue** | `app_simple.py::_r162_comprehensive_integrity_should_abort` | Jobs no longer hard-abort when scoped AB **and** CSOne are empty **but** team subscriptions **or** CSConsole data exist — run continues with honest `partial_data_warnings` |
| **CSOne autodiscovery hardening** | `app_simple.py::get_latest_csone_from_folder_diag` (~4854) | Skips `AdoptIQ Enhanced Premium Collab Summary-*`; openpyxl readability probe; corrupt-newest fallback; `.xls` pass-through; `csone_load_failure` warnings when load fails |
| **Progress partial-data UX** | `templates/progress.html` | `partial_data_warnings` visible during `running` **and** `completed`, not only on `error` |
| **Analyze card layout** | `templates/analyze.html`, `static/css/manager_decision_workspace.css` | 5-column desktop grid; badge slot on all five report cards; flex-pinned report-type radios |

**Regression tests:** `tests/test_round162_*.py` + updated R146 / R161 / R68 pins.

---

## Issues **found** (Build 112 live acceptance — treat as facts, not bugs to “paper over”)

These were observed on **VPN ON**, **Brian Frazier / All Contact Center / 90d Comprehensive**, **no manual CSOne upload**, run id **`r162-build112-live`** → **`all_passed: true`** (~229s).

| # | Issue | Evidence | Severity | Notes |
|---|-------|----------|----------|-------|
| F1 | **No readable CSOne autodiscovered** | `csone_file_path` empty; OneDrive folder dominated by unreadable **AdoptIQ Enhanced Collab** `.xlsx` artifacts | **Operational** | R162 degraded-continue worked: run completed honestly; TAC source state **`unavailable`** — not silently zero |
| F2 | **Scoped data thin vs subscriptions** | Scoped AB=**2** rows; CSOne=**0**; subscriptions present (**31** customers in team roster context) | **Expected partial** | Partial warnings: `tech_filter_scope_excluded` (AB) + `tech_filter_empty_after_scope` (AP) on status + Excel `Report_Info` |
| F3 | **Stale baked corpus inside Build 112 DMG** | Full rebake failed: OneDrive copy timeout on corrupt Enhanced Collab file; manual fixture bake failed Round 95 reranker SSL on model download | **Ship blocker for corpus freshness** | Build 112 **restored Build 111** `bake/corpus.db.enc` + salt + sentinel before PyInstaller — Ask AI still works but corpus age is stale |
| F4 | **DMG / `latest.json` not in git** | `OUTBOX/AdoptIQ-v1.0.4-build112.dmg` (sha256 `4ef24c7e7efb448ef5fa7e991d343661b7355c9b2786ca81c1efb34cfe6af61f`) exists **locally on Mac build host only** | **Release ops** | Copy DMG to synced OneDrive OUTBOX for auto-update consumers; do not commit |
| F5 | **Windows `latest.json` pc slot behind** | Mac slot updated to build **112** locally; PC host has not run `build_pc.bat` with `ADOPTIQ_BUILD=112` | **Platform parity** | Windows installs may not see Build 112 via auto-update until PC build + merge-aware manifest |

**Live artifact pointers (Mac build host):**

- Analysis id: `Brian_Frazier_All_Contact_Center_90d_1786484044603608000_b3a5a925`
- Summary JSON: `~/Downloads/AdoptIQ_ReportIterationSummary__data-loop-r162-build112-live__ts-20260811T213752Z.json`
- Reports under: `~/Documents/AdoptIQ Reports/Brian_Frazier/Comprehensive/`

**Do not re-run live acceptance** unless you change report logic — synthetic pytest + existing summary are the baseline.

---

## Issues **to explore** (prioritized work queue)

Stop at the first item blocked by missing VPN, secrets, or platform.

### P0 — Ship / platform parity

1. **Windows Build 112**
   - Run `build_pc.bat` with `ADOPTIQ_BUILD=112` on PC host
   - Update `scripts/write_release_manifest.py` (merge-aware — do **not** clobber mac slot)
   - EXE smoke + copy to OUTBOX; verify `latest.json` **pc** slot
   - See [`BUILD_WINDOWS.md`](BUILD_WINDOWS.md), [`BRANCH_WORKFLOW.md`](BRANCH_WORKFLOW.md)

2. **Corpus rebake for Build 113+**
   - Clean OneDrive fixture: operator-readable CSOne export **or** Preferences → CSOne folder override pointing at a folder with a real CSOne workbook (not Enhanced Collab outputs only)
   - Full `build_mac_dmg.sh` + `ADOPTIQ_RELEASE_GATE=1` so DMG carries fresh baked corpus + dense vectors
   - See [`CURSOR_MAC_BUILD_INSTRUCTIONS.md`](CURSOR_MAC_BUILD_INSTRUCTIONS.md) §9.7 and §9.10

3. **DMG distribution**
   - Copy `OUTBOX/AdoptIQ-v1.0.4-build112.dmg` to synced OneDrive OUTBOX (not in git)
   - Confirm auto-update manifest consumers see mac build **112**

### P1 — Accuracy / product follow-ons (out of Round 162 scope but documented)

4. **Leader `total_customers` drift** — investigate separately; do not block Build 112 closure on it ([`QUALITY_AUDIT.md`](QUALITY_AUDIT.md) Round 162 deferrals)

5. **CSOne folder hygiene** — operator workflow: generate real CSOne export via [`Config.CSONE_REPORT_URL`](config.py) / Help page steps; or set Preferences → CSOne OneDrive folder to a path that contains readable `.xlsx` (not only Enhanced Collab)

6. **Formatter empty-DataFrame audit (R38.2 pattern)** — `compact_report_formatter.py`, `executive_intelligence_formatter.py` (leader path audited in R38.2; others deferred)

7. **Comprehensive grounding ~6.7%** — within ≤10% contract (R139); monitor on live runs; do not weaken validator without acceptance data

### P2 — Methodology reminders

8. **Round 76 lesson:** synthetic pytest green is **necessary but not sufficient** for Snowflake error handling, narratives, or formatter chrome — use **bake → install → regen four reports → `scripts/r114_audit_reports.py --auto`**

9. **Live production validation checklist** — full 10-step list in [`HANDOFF_PROMPT.md`](HANDOFF_PROMPT.md) “Required live-source acceptance before a production-accuracy claim”

---

## Phase A — Mandatory audit (before new feature work)

Audit Round 162 logic even if live acceptance already passed — Codex or Claude should append **`## Round 162 — Codex review <date>`** (or Claude review subsection) to [`QUALITY_AUDIT.md`](QUALITY_AUDIT.md).

| Hotspot | File | Question to answer |
|---------|------|-------------------|
| Degraded-continue gate | `app_simple.py` ~19453 `_r162_comprehensive_integrity_should_abort` | Does it **abort** when subs **and** CSConsole are truly empty? Does it **continue** when either exists with empty scoped AB+CSOne? Does it still **abort** on real quality failures? |
| CSOne autodiscovery | `app_simple.py` ~4854 `get_latest_csone_from_folder_diag` | Enhanced Collab skip correct? Corrupt-newest fallback? `.xls` pass-through still surfaces honest load failures? |
| Progress UX | `templates/progress.html` | Partial warnings during `running`/`completed` without masking `error`? |
| Analyze cards | `analyze.html` + `manager_decision_workspace.css` | 5-col grid + badge slots — narrow viewport regression? |

**Narrow tests (run first on any machine):**

```bash
git checkout main && git pull origin main
python3 -m pytest tests/test_round162_*.py -v
python3 -m pytest tests/test_round146_manager_workspace_ui.py \
  tests/test_round161_2_csone_autodiscovery_skip_adoptiq_output.py \
  tests/test_round68_csone_autodiscovery_skip_zero_byte.py -v
git diff 4e4cb71..a66b006 | grep 'Round 162'
make verify
```

**Required audit output template:**

```markdown
## Round 162 — <tool> review <YYYY-MM-DD>

**Verdict:** pass | pass-with-fixes | fail
**Findings:** (numbered, severity: critical/high/medium/low)
**Fixes applied:** file:line — or none
**Verify after fixes:** make verify — pass/fail; pytest count
**Trailer:** Made-with: <tool>
```

---

## Machine-specific quick start

### Mac work machine (VPN + secrets)

```bash
git clone https://wwwin-github.cisco.com/jestory/AdoptIQ.git   # or pull mirror
cd AdoptIQ && git checkout main && git pull origin main
# secrets.env + embed flow per README — never commit secrets
make verify
python3 -m pytest tests/test_round162_*.py -v

# Optional — only if you changed report logic:
bash scripts/test_build_smoke.sh /Applications/AdoptIQ.app
python3 report_iteration_loop.py \
  --scenarios comprehensive --iterations 1 \
  --baseline-mode off --run-id next-machine-r162 --stop-on-failure
python3 scripts/r114_audit_reports.py --auto
```

### Windows PC (Build 112 release)

```bash
git clone https://github.com/rupret007/AdoptIQ.git
cd AdoptIQ && git checkout main && git pull origin main
set ADOPTIQ_BUILD=112
build_pc.bat
# smoke EXE, merge-aware latest.json, push main only after verify green
```

---

## Hard constraints

- Never skip/weaken tests to go green; never recompute counts outside [`canonical_metrics.py`](canonical_metrics.py)
- Parameterized Snowflake only; CSOne path prefix checks; admin loopback by default
- Never commit: `secrets.env`, `_bundled_secrets.py`, `embeddings/`, `OUTBOX/`, `*.dmg`
- Do not collapse Leader vs Comprehensive scope predicates without explicit product decision
- Never paste/log PATs

---

## One-line goal statement (paste after the block above)

Pick **one** and paste as your first user message:

**Audit-only (recommended first):**

> I pulled `main` on the work machine. Run **Phase A** audit on Round 162 (`_r162_comprehensive_integrity_should_abort`, CSOne autodiscovery, progress partial warnings, analyze cards). Document findings F1–F5 and exploration items P0–P1 in `QUALITY_AUDIT.md`. Fix only if audit finds real defects; keep `make verify` green.

**Windows ship:**

> I am on the Windows PC. Execute **P0 Windows Build 112**: `build_pc.bat` with `ADOPTIQ_BUILD=112`, merge-aware `latest.json`, EXE smoke, document results in `QUALITY_AUDIT.md`, push to both remotes if green.

**Corpus rebake:**

> OneDrive fixture is clean / CSOne folder overridden. Execute **P0 corpus rebake** for Build 113+ per `CURSOR_MAC_BUILD_INSTRUCTIONS.md` §9.7 with `ADOPTIQ_RELEASE_GATE=1`, then full acceptance gate §9.10.

---

**End of next-machine prompt.** Paste everything above this line into the new session, then add your one-line goal.
