# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This App Does

AdoptIQ is a renewal-risk and adoption intelligence desktop application (Flask + PyInstaller) that generates Word/Excel reports from Snowflake data, CSOne adoption barriers, support cases, and external service intelligence. It ships as a native macOS `.app` (DMG) and Windows `.exe` with a browser UI at `http://localhost:5151`.

## Commands

```bash
# Run the app
python app_simple.py                        # Main app — http://localhost:5151
python enhanced_admin_dashboard_v2.py       # Admin dashboard — http://127.0.0.1:5152

# Tests
python -m pytest -v                         # Full test suite (baseline: 1949 passed / 2 skipped)
python -m pytest tests/test_canonical_metrics.py -v   # Single test file
python -m pytest -k "ask_ai" -v            # Filter by name
python -m pytest tests/test_round16_*.py -v # Round 16 regression suite (cross-format consistency, sort determinism, AI grounding, polish offset)

# Verification pipeline (run all before committing)
make test       # pytest -q
make lint       # ruff check
make lint-fix   # ruff check --fix (safe fixes only)
make security   # bandit HIGH/MED gate
make audit      # pip-audit on requirements.txt
make verify     # all of the above

# Build
bash build_mac.sh    # macOS DMG → OUTBOX/
build_pc.bat         # Windows EXE → OUTBOX/
```

## Linting & Security Configuration

- **Ruff** (`pyproject.toml`): Python 3.11, line-length 120. Rules: `E`, `F`, `B`, `S`. Cosmetic/style rules (UP, I001, RUF) are intentionally deferred to separate PRs.
- **Bandit** (`bandit.yaml`): HIGH/MED severity gate. Skips documented: B101 (assert in tests), B404/B603/B607 (subprocess build helpers), B311 (non-crypto random), B110/B112 (try-except patterns), B608 (SQL strings). Don't add new bandit skips without documenting the rationale.

## Architecture

### Structure
The project is flat — 33 top-level Python modules with no package hierarchy. Templates are in `templates/` (Jinja2), static assets in `static/css/` and `static/js/` (vanilla JS). Tests are in `tests/` with `conftest.py` providing `app` and `client` fixtures.

### Two Flask Apps
| App | Port | File | Access |
|-----|------|------|--------|
| Main UI | 5151 | `app_simple.py` | `ADOPTIQ_BIND_HOST` (default `0.0.0.0`) |
| Admin | 5152 | `enhanced_admin_dashboard_v2.py` | Loopback only by default |

### Data Flow
```
User uploads CSOne .xlsx
  → app_simple.py: validate, save, start background thread
  → snowflake_prefetch.py: pull customer/subscription/case data
  → data_source_validator.py: enforce required sources
  → core analysis (adoptiq_backend.py, advanced_renewal_analyzer.py)
  → canonical_metrics.py: single source of truth for cross-report counts
  → formatter (executive_intelligence_formatter.py, compact_report_formatter.py, leader_report_generator.py)
  → /download/<analysis_id>/<file_type>
```

### Key Module Roles
- **`app_simple.py`** (18k lines): Flask orchestration, all 45+ routes, analysis job lifecycle, progress tracking
- **`adoptiq_backend.py`** (11k lines): Core Word + Excel report generation engine
- **`canonical_metrics.py`**: Single source of truth for counts that appear across multiple reports — always use this, never recompute inline
- **`risk_scoring.py`**: Deterministic weighted risk scoring — changes here affect all reports
- **`ask_ai_grounded.py`**: Grounded retrieval pipeline (retrieval-first, then CircuIT LLM)
- **`ai_narrative_validator.py`** (Round 16): Validates LLM-generated narratives against the source briefing — rejects ungrounded numbers, invented entities, and HTML/JS injection. Wired into the report-narrative gate in `app_simple.py`.
- **`report_export_schema.py`** (Round 15): SSoT for Excel column ordering, headers, dtypes used by all report writers
- **`report_export_styling.py`** (Round 15/16): SSoT for Excel polish — Tables, banded rows, conditional formatting (3-color risk scale, severity bands, status pills, days-open data bar). `apply_excel_polish` accepts `startrow` to honor title-row offsets.
- **`report_word_styling.py`** (Round 15): SSoT for Word top-N tables (`add_banded_top_n_table`) — banded rows, header colors, reproducible formatting
- **`config.py`**: Version constants, all env-var loading, technology filters, CSOne column mappings
- **`data_contracts.py`** + **`data_normalization.py`**: Shared consistency helpers — use these before touching DataFrames
- **`structured_logging.py`**: All logging goes through here. Pipeline-boundary entry/exit logs use manager-digest only (no PII).

### Storage
- macOS: `~/Library/Application Support/AdoptIQ/`
- Windows: `%APPDATA%\AdoptIQ\`
- Subdirs: `uploads/`, `outputs/`, `analysis_status.json`, `external_intelligence.db` (SQLite), `admin_monitoring_v2.db` (SQLite)

### Build Pipeline
1. `embed_credentials.py` reads `secrets.env` → XOR+base64 → `_bundled_secrets.py`
2. `update_version_pc.py` rewrites `ADOPTIQ_VERSION`/`ADOPTIQ_BUILD` in `config.py`
3. PyInstaller with platform `.spec` file → `dist/`
4. Platform post-processing: macOS codesign + DMG; Windows OUTBOX copy + installer

## Critical Rules

**Never commit:** `secrets.env`, `_bundled_secrets.py`, `dist/`, `build/`, `OUTBOX/`, `*.dmg`, `*.exe`

**Data safety (Tier 1 — highest priority):**
- Always validate DataFrame columns before access; guard with `.get()` or `if col in df.columns`
- Use parameterized Snowflake queries — never f-string SQL
- All form submissions require CSRF (Flask-WTF); never disable globally

**Correctness patterns:**
- Cross-report metric counts must go through `canonical_metrics.py` — never recompute in formatters
- Risk dict comparisons use string keys, not enum values
- ARR and severity logic lives in `risk_scoring.py` — don't replicate inline
- Excel column schema goes through `report_export_schema.py`; Excel polish through `apply_excel_polish` — don't re-implement either inline
- Word top-N tables go through `report_word_styling.add_banded_top_n_table` — keep custom paint logic only where leader-report tables need per-cell color overrides
- Top-N sorts must include a stable name-based tiebreaker so `make verify` stays deterministic across runs
- LLM-generated narratives that flow into reports must pass through `ai_narrative_validator.validate_narrative` — don't bypass the gate

**Frontend:**
- All `target="_blank"` links need `rel="noopener noreferrer"`
- Always check `response.ok` before using fetch results
- Null-check DOM elements before accessing `.value` or `.innerHTML`

**Admin security:** Admin dashboard binds to loopback (`127.0.0.1`) by default. Don't change this default without the `ADOPTIQ_ADMIN_BIND_PUBLIC=1` escape hatch.

## Development Workflow

Branch model: develop on machine-specific branches (`pc-sync-YYYY-MM-DD`, `mac-sync-YYYY-MM-DD`), integrate to `master` only after `make verify` passes and platform build + smoke test complete.

Quality gate (from `.cursor/rules/quality-gate.mdc`): Never hide or skip tests. When fixing bugs, add a regression test. Run the narrowest relevant check first (`pytest tests/test_<module>.py`) before the full suite. Changes must report: files changed, tests added/updated, commands run, pass/fail status.

Audit log convention: when adding a Round-N audit, use `# Round N` markers in the affected source so `git diff <file> | grep 'Round N'` gives a per-file footprint; document phase-by-phase findings, residual risks, and follow-ups in `QUALITY_AUDIT.md`. See Round 14/15/16 sections for the established format.

## Loop conventions (Cursor ↔ Claude Code)

This repo runs a two-tool loop: **Cursor generates code, Claude Code audits and writes a review back**. The loop is bootstrapped in Round 0 (see `QUALITY_AUDIT.md`).

**Where things live:**
- LLM-bible (this file): `CLAUDE.md` — invariants, SSoT modules, critical rules.
- Audit journal: `QUALITY_AUDIT.md` — per-round handoff + review log.
- Verify gate: `make verify` — lint + security + audit + test. **Local contract**; CI (`.github/workflows/build.yml`) runs only `pytest -q`.
- Cursor session-close handoff: appended to `QUALITY_AUDIT.md` under `## Round N — handoff <date>`. Format pinned in `.cursor/rules/session-handoff.mdc`.
- Claude session review: appended under the same Round, in a `## Round N — Claude review` subsection.
- Review standards: `.cursor/rules/quality-gate.mdc` (in-session gate) and `.cursor/BUGBOT.md` (PR-style review checklist) — both apply to Claude as well.

**Commit trailers** (existing convention — keep):
- Cursor commits: `Made-with: Cursor`
- Claude commits: `Made-with: Claude Opus 4.7 (1M context)` (or current model)

**Round-N source markers** (existing convention — keep): when changing code as part of Round N, drop a `# Round N` comment so `git diff <file> | grep 'Round N'` shows the per-file footprint.

**Floor:** every round must hold the Round 0 floor — `make verify` clean with at least the test count recorded in Round 0. A round that lowers the floor is a regression and must explain why in the handoff under **Known deferrals**.
