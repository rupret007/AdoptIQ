# AdoptIQ — Codex Session Handoff (GPT-5.6 Sol)

Copy everything below the horizontal rule into a new **OpenAI Codex** session using **GPT-5.6 Sol**.

For the long-form Cursor/Claude handoff, see [`HANDOFF_PROMPT.md`](HANDOFF_PROMPT.md).

---

## Your role

You are **Codex (GPT-5.6 Sol)** taking over **AdoptIQ** immediately after Cursor shipped **Round 162 / Build 112**.

**North star:** report-accuracy-first. Every KPI count, risk score, TAC total, health grade, and citation must agree across Word, Excel, Compact, Renewal, Comprehensive, and Leader for the same scope. LLM narratives are **downstream of canonical data** — never the source of truth.

**Quality loop:** Cursor generates → **you audit, fix if needed, and optionally pick up P0/P1 follow-on** → append `## Round 149 — Codex review <date>` to [`QUALITY_AUDIT.md`](QUALITY_AUDIT.md). Commits carry trailer `Made-with: Codex`.

---

## Pinned baseline (2026-08-11)

| Item | Value |
|------|--------|
| Version / build | `v1.0.4` **Build 112** |
| Round | **162** (Comprehensive degraded-continue + CSOne autodiscovery) |
| Branch tip | `939d11d` on `mac-sync-2026-08-11` |
| Verify floor | **7150 passed** / 7 skipped / 14 deselected; `make verify` green |
| Installed Mac app | `/Applications/AdoptIQ.app`, `GET /api/version` → `build: "112"` |
| DMG | `OUTBOX/AdoptIQ-v1.0.4-build112.dmg` |

**Repos:**

- Cisco: `https://wwwin-github.cisco.com/jestory/AdoptIQ` — `main`
- Mirror: `https://github.com/rupret007/AdoptIQ` — `main`

---

## Read first (≈10 min)

1. [`CLAUDE.md`](CLAUDE.md) — Critical Rules / SSoT contracts
2. [`QUALITY_AUDIT.md`](QUALITY_AUDIT.md) — `## Round 149 — handoff 2026-08-06`
3. [`.cursor/BUGBOT.md`](.cursor/BUGBOT.md) + [`.cursor/rules/quality-gate.mdc`](.cursor/rules/quality-gate.mdc)
4. Round 149 diff only: `git diff 1e72f44..7763b93`

---

## Phase A — Audit Round 149 (mandatory)

### What Cursor changed

| Area | Files | Audit focus |
|------|-------|-------------|
| Renewal CSOne provenance | `app_simple.py` ~5038–5121, ~16770 | `csone_file_was_uploaded` vs autodiscovery; Pass 2 must **warn-not-fail** when autodiscovered CSOne is empty after scope |
| Harness false positives | `report_iteration_loop.py` ~2657–2660, BEMS lineage ~2137–2181 | Overflow pointers + `Next action:` ordinals excluded from uncited-numeric gate; `kpi.bems` unavailable overrides legacy `bems=0` |
| Compact timeout | `app_simple.py` ~31742 | Worker timeout **1800s** matches harness; daemon-thread cancel semantics |
| Footer fail-closed | `canonical_report_adapter.py` ~2011–2451 | `_ensure_build_footer_on_disk` must not corrupt docx on enforcer failure |
| Packaging | `adoptiq_mac.spec`, `adoptiq_pc.spec` | `docx.enum.text`, `docx.shared` hiddenimports only — no unrelated PC spec churn |

### Narrow regression tests (run first)

```bash
python3 -m pytest tests/test_round149_*.py tests/test_canonical_report_adapter.py -v
git diff 1e72f44..7763b93 | grep 'Round 149'
```

### Live acceptance already green (do not re-run unless you change report logic)

- **Four-report harness r149d:** `RUN_ID=build111-final-sweep-r149d-20260806T154551Z` → `all_passed: true`
- **R114 audit:** exit 0, `CRITICAL_ISSUES_FOUND=False`; ACC Comprehensive `total_customers=31`
- **45m soak:** `~/Downloads/adoptiq_report_soak_round101-soak-20260806T175854Z/soak_summary.json` → `passed: true`, `failed_events: 0` (leader skipped — `deadline_remaining_too_small`; documented deferral)

**Round 76 lesson:** synthetic pytest passing is **necessary but not sufficient** for Snowflake error handling, narrative emission, or formatter chrome — use bake → install → regen → `r114_audit_reports.py` if you touch those paths.

### Required output

Append under the existing Round 149 section in [`QUALITY_AUDIT.md`](QUALITY_AUDIT.md):

```markdown
## Round 149 — Codex review <YYYY-MM-DD>

**Verdict:** <pass | pass-with-fixes | fail>
**Findings:** (numbered, severity-tagged)
**Fixes applied:** (file:line — or `none`)
**Verify after fixes:** make verify — pass/fail; pytest count
**Trailer:** Made-with: Codex
```

If you fix bugs: smallest correct diff, `# Round 149` or `# Round 149.1` source markers, regression test per fix, `make verify` green before commit.

---

## Phase B — Next work (if audit clean)

Stop at the first item blocked by missing VPN or Windows build host.

| Priority | Task | Where |
|----------|------|--------|
| **P0** | Windows Build 111 — `build_pc.bat`, merge-aware `scripts/write_release_manifest.py`, EXE smoke | [`CURSOR_MAC_BUILD_INSTRUCTIONS.md`](CURSOR_MAC_BUILD_INSTRUCTIONS.md) §9.9(g); §9.7(d) pattern with build **111** |
| **P0** | Extended soak (optional) — 7200s, idle app, aim for full 4-scenario cycle incl. leader | [`scripts/run_report_soak.py`](scripts/run_report_soak.py) |
| **P1** | Cross-format drift CI on fixture baselines | [`baselines/round72/`](baselines/round72/) |
| **P1** | Formatter empty-DataFrame audit (R38.2 pattern) in `compact_report_formatter.py`, `executive_intelligence_formatter.py` | Round 66 B13 deferral |

---

## Commands

```bash
git checkout main && git pull origin main    # expect 01d9c14
make verify                                  # full gate before push
python3 -m pytest tests/test_round149_*.py -v

# Live acceptance (VPN + secrets.env) — only if you changed report logic:
bash scripts/test_build_smoke.sh /Applications/AdoptIQ.app
python3 scripts/run_report_iteration_loop.py \
  --base-url http://127.0.0.1:5151 --iterations 1 \
  --scenarios comprehensive,compact,renewal,leader \
  --baseline-mode off --strict --stop-on-failure \
  --timeout 1800 --download-timeout 600
python3 scripts/r114_audit_reports.py --auto
make preflight-acceptance && python3 scripts/run_report_soak.py \
  --duration-seconds 2700 --baseline-mode off \
  --scenarios comprehensive,compact,renewal,leader
```

Mac ship gate details: [`CURSOR_MAC_BUILD_INSTRUCTIONS.md`](CURSOR_MAC_BUILD_INSTRUCTIONS.md) §9.9.

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
canonical_report_adapter.py   Concise Word/source workbook delivery
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

- Windows Build 111 / `latest.json` **pc** slot — PC host only
- 45m soak skipped **leader** (`deadline_remaining_too_small`); prior 2h soak aborted at 11/11 green
- Leader vs Comprehensive scope divergence — by design where documented
- `embeddings/` local cache — not in git (PyInstaller symlink footgun)
- Premium support / upsell KPI templates — legacy strings (Round 67 deferral)

---

**End of Codex handoff.** Paste everything above this line into Codex with model **GPT-5.6 Sol**, then state your goal (e.g. “Run Phase A audit on Round 149” or “Audit clean — start Windows Build 111 checklist”).
