# AdoptIQ — Codex Session Handoff (GPT-5.6 Sol)

Copy everything below the horizontal rule into a new **OpenAI Codex** session using **GPT-5.6 Sol**.

**Start here:** pull current `main`, then read [`NEXT_MACHINE_PROMPT.md`](NEXT_MACHINE_PROMPT.md) and the appended Round 163 entry in [`QUALITY_AUDIT.md`](QUALITY_AUDIT.md). They supersede this Round 162 audit prompt for source-tree work.

**Another machine / current detailed handoff:** [`NEXT_MACHINE_PROMPT.md`](NEXT_MACHINE_PROMPT.md) — Round 163 all-source reporting, Ask AI, predictive/BST work, exact final gates, and the live-reconciliation/backtest/package queue.

For the long-form Cursor/Claude handoff, see [`HANDOFF_PROMPT.md`](HANDOFF_PROMPT.md).

---

> **Historical prompt:** the remaining content describes the pre-Round-163 Build 112
> audit baseline. Build 112 is still the last packaged/live-smoked installer, while
> the current source tree has a newer verified Round 163 integration that still needs
> live Cisco reconciliation and the next package cycle.

## Your role

You are **Codex (GPT-5.6 Sol)** taking over **AdoptIQ** immediately after Cursor shipped **Round 162 / Build 112**.

**North star:** report-accuracy-first. Every KPI count, risk score, TAC total, health grade, and citation must agree across Word, Excel, Compact, Renewal, Comprehensive, and Leader for the same scope. LLM narratives are **downstream of canonical data** — never the source of truth.

**Quality loop:** Cursor generates → **you audit, fix if needed, and optionally pick up P0/P1 follow-on** → append `## Round 162 — Codex review <date>` to [`QUALITY_AUDIT.md`](QUALITY_AUDIT.md). Commits carry trailer `Made-with: Codex`.

---

## Pinned baseline (2026-08-11)

| Item | Value |
|------|--------|
| Version / build | `v1.0.4` **Build 112** |
| Round | **162** (Comprehensive degraded-continue + CSOne autodiscovery + progress UX) |
| Branch / tip | **`main`** — code/live-smoke @ **`a66b006`**; handoff docs on latest `main` after pull |
| Verify floor | **7150 passed** / 7 skipped / 14 deselected; `make verify` green |
| Installed Mac app | `/Applications/AdoptIQ.app`, `GET /api/version` → `build: "112"` |
| DMG (local) | `OUTBOX/AdoptIQ-v1.0.4-build112.dmg` (not in git) |

**Repos (both on `main`, synced 2026-08-11):**

- Cisco: `https://wwwin-github.cisco.com/jestory/AdoptIQ`
- Mirror: `https://github.com/rupret007/AdoptIQ`

---

## Read first (≈10 min)

1. [`CLAUDE.md`](CLAUDE.md) — Critical Rules / SSoT contracts
2. [`QUALITY_AUDIT.md`](QUALITY_AUDIT.md) — `## Round 162 — handoff` + `## Round 162 — Build 112 live smoke` + `## Round 162 — remote integration`
3. [`.cursor/BUGBOT.md`](.cursor/BUGBOT.md) + [`.cursor/rules/quality-gate.mdc`](.cursor/rules/quality-gate.mdc)
4. Round 162 diff: `git diff 4e4cb71..a66b006` and `git diff 4e4cb71..a66b006 | grep 'Round 162'`

---

## Phase A — Audit Round 162 (mandatory)

### What Cursor changed

| Area | Files | Audit focus |
|------|-------|-------------|
| Degraded-continue gate | `app_simple.py` ~19453 `_r162_comprehensive_integrity_should_abort` | Must **NOT** continue when team subscriptions **and** CSConsole are truly empty (only external intel). Must **continue** when subs or CSConsole present with empty scoped AB+CSOne; must still **abort** on genuine quality/integrity failures |
| CSOne autodiscovery | `app_simple.py` ~4854 `get_latest_csone_from_folder_diag` | Skip `AdoptIQ Enhanced Premium Collab Summary-*`; openpyxl readability probe; corrupt-newest fallback to next candidate; `.xls` pass-through; honest `csone_load_failure` / `partial_data_warnings` when load fails |
| Progress UX | `templates/progress.html` | `partial_data_warnings` visible during `running` and `completed`; must not mask genuine `error` status or block progress polling |
| Analyze cards | `templates/analyze.html`, `static/css/manager_decision_workspace.css` | 5-col desktop grid; badge slot on all five report cards; flex-pinned radios — no layout regression on narrow viewports |

### Narrow regression tests (run first)

```bash
python3 -m pytest tests/test_round162_*.py -v
python3 -m pytest tests/test_round146_manager_workspace_ui.py tests/test_round161_2_csone_autodiscovery_skip_adoptiq_output.py tests/test_round68_csone_autodiscovery_skip_zero_byte.py -v
git diff 4e4cb71..a66b006 | grep 'Round 162'
```

### Live acceptance already green (do not re-run unless you change report logic)

- **Comprehensive r162-build112-live:** Brian Frazier / All Contact Center / 90d, **no manual CSOne upload** → `all_passed: true`, ~229s
- Analysis id: `Brian_Frazier_All_Contact_Center_90d_1786484044603608000_b3a5a925`
- Summary: `~/Downloads/AdoptIQ_ReportIterationSummary__data-loop-r162-build112-live__ts-20260811T213752Z.json`
- Validated: degraded-continue (no integrity abort); partial warnings on status + Excel; Word footer `App_Build: 112`

**Round 76 lesson:** synthetic pytest passing is **necessary but not sufficient** for Snowflake error handling, narrative emission, or formatter chrome — use bake → install → regen → `r114_audit_reports.py` if you touch those paths.

### Required output

Append under the Round 162 section in [`QUALITY_AUDIT.md`](QUALITY_AUDIT.md):

```markdown
## Round 162 — Codex review <YYYY-MM-DD>

**Verdict:** <pass | pass-with-fixes | fail>
**Findings:** (numbered, severity-tagged)
**Fixes applied:** (file:line — or `none`)
**Verify after fixes:** make verify — pass/fail; pytest count
**Trailer:** Made-with: Codex
```

If you fix bugs: smallest correct diff, `# Round 162` or `# Round 162.1` source markers, regression test per fix, `make verify` green before commit.

---

## Phase B — Next work (if audit clean)

Stop at the first item blocked by missing VPN or Windows build host.

| Priority | Task | Where |
|----------|------|--------|
| **P0** | Windows Build 112 — `build_pc.bat` with `ADOPTIQ_BUILD=112`, merge-aware `scripts/write_release_manifest.py`, EXE smoke | [`BRANCH_WORKFLOW.md`](BRANCH_WORKFLOW.md); [`CURSOR_MAC_BUILD_INSTRUCTIONS.md`](CURSOR_MAC_BUILD_INSTRUCTIONS.md) §9.10 |
| **P0** | Corpus rebake for next Mac ship — clean OneDrive fixture; full `build_mac_dmg.sh` + `ADOPTIQ_RELEASE_GATE=1` (Build 112 used restored Build 111 baked corpus) | [`CURSOR_MAC_BUILD_INSTRUCTIONS.md`](CURSOR_MAC_BUILD_INSTRUCTIONS.md) §9.7 |
| **P1** | Leader `total_customers` drift — separate follow-on from Round 162 | [`QUALITY_AUDIT.md`](QUALITY_AUDIT.md) Round 162 deferrals |
| **P1** | CSOne folder hygiene — operator-readable export or Preferences → CSOne folder override when OneDrive has only Enhanced Collab artifacts | Preferences + `get_latest_csone_from_folder_diag` |
| **P1** | Formatter empty-DataFrame audit (R38.2 pattern) in `compact_report_formatter.py`, `executive_intelligence_formatter.py` | Round 66 B13 deferral |

---

## Commands

```bash
git checkout main && git pull origin main    # expect a66b006 or later handoff commit
make verify                                  # full gate before push
python3 -m pytest tests/test_round162_*.py -v

# Live acceptance (VPN + secrets.env) — only if you changed report logic:
bash scripts/test_build_smoke.sh /Applications/AdoptIQ.app
python3 report_iteration_loop.py \
  --scenarios comprehensive --iterations 1 \
  --baseline-mode off --run-id codex-r162-verify --stop-on-failure
python3 scripts/r114_audit_reports.py --auto
```

Mac ship gate details: [`CURSOR_MAC_BUILD_INSTRUCTIONS.md`](CURSOR_MAC_BUILD_INSTRUCTIONS.md) §9.10.

---

## Hard constraints

- Never skip/weaken tests to go green; never recompute counts outside [`canonical_metrics.py`](canonical_metrics.py)
- Parameterized Snowflake only (`%s`); CSOne path prefix checks; admin loopback by default
- Never commit: `secrets.env`, `_bundled_secrets.py`, `embeddings/`, `OUTBOX/`, `*.dmg`
- Do not collapse Leader vs Comprehensive scope predicates without explicit product decision
- Never paste/log PATs; rotate if exposed

---

## Minimal architecture

```
app_simple.py                 Flask UI :5151 — orchestration, routes, jobs
adoptiq_backend.py            Core Word/Excel engine
leader_report_generator.py    Leader reports
canonical_metrics.py          SSoT cross-report counts — NEVER bypass
risk_scoring.py               Deterministic risk — affects ALL formats
report_iteration_loop.py      Live harness + quality scorer
decision_report_delivery.py   Concise Word/source workbook delivery
report_export_schema.py       Excel column SSoT
report_source_injector.py     Word citations (captions below matrices)
ai_narrative_validator.py     LLM grounding gate (target ≤10% rejection)
ask_ai_grounded.py            Ask AI retrieval + compose
model_resolver.py             Ask AI vs report LLM model resolution
config.py                     Version, env, feature flags
```

**SSoT quick reference:** counts → `canonical_metrics`; scores → `risk_scoring`; Excel cols → `report_export_schema`; build label → `_r68_build_label.py`; customer aliases → `data_normalization` + `customer_aliases.defaults.json`.

---

## Known deferrals (do not “fix” without intent)

- **Build 112 baked corpus stale** — ships Build 111 snapshot inside DMG; rebake blocked on this host
- **Windows Build 112 / `latest.json` pc slot** — PC host only
- **CSOne OneDrive folder** — unreadable Enhanced Collab artifacts; autodiscovery may return empty path (R162 degraded-continue is intentional)
- **Leader `total_customers` drift** — out of Round 162 scope
- Leader vs Comprehensive scope divergence — by design where documented
- Premium support / upsell KPI templates — legacy strings (Round 67 deferral)

---

**End of Codex handoff.** Paste everything above this line into Codex with model **GPT-5.6 Sol**, then state your goal (e.g. “Run Phase A audit on Round 162” or “Audit clean — start Windows Build 112 checklist”).
