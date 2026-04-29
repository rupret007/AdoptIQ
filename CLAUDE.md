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
python -m pytest -v                         # Full test suite (current floor: 3191 passed / 2 skipped after Round 48 / Build25; Round 0 floor 2570)
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
| Main UI | 5151 | `app_simple.py` | Loopback (`127.0.0.1`) by default; opt-in public bind via `ADOPTIQ_BIND_PUBLIC=1`, or set `ADOPTIQ_BIND_HOST` directly (Round 14 R14-007) |
| Admin | 5152 | `enhanced_admin_dashboard_v2.py` | Loopback (`127.0.0.1`) by default; opt-in public bind via `ADOPTIQ_ADMIN_BIND_PUBLIC=1` |

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
- Subdirs: `uploads/`, `outputs/`, `analysis_status.json`, `external_intelligence.db` (SQLite), `admin_monitoring_v2.db` (SQLite), `settings.json` (Round 32, mode `0600`, parent dir `0700`)

### Settings precedence (Round 32 / Phase 2.E, extended Round 33 / Build8, narrowed Round 35)
Persistent feature flags resolve in this order — highest precedence first:
1. `settings.json` (managed by `adoptiq_settings.py`; allow-listed keys only — currently just `corpus_knowledge_enabled`. The Build8 `sharepoint_folder_url` key was retired in Round 35 along with the user-facing URL paste UI; legacy values are silently dropped on load/save).
2. Environment variable (e.g. `CORPUS_KNOWLEDGE_ENABLED=true|false`, `ADOPTIQ_CORPUS_SHARE_URL=https://...`).
3. `config.py` default.

The Intelligence on/off switch in the analyze-page `[data-intel-banner]` card POSTs to `/api/settings/intelligence` (CSRF-protected via the same dual-path as `/api/corpus/refresh`), which writes `settings.json`, mutates `Config.CORPUS_KNOWLEDGE_ENABLED` in-process, and triggers `corpus_bootstrap.request_refresh` on enable. Round 32 / Phase 2.F flipped the `config.py` default to `true` so fresh installs surface Intelligence in the UI immediately.

The Round 36 "AdoptIQ Knowledge Corpus" sub-panel (formerly the Build8 "SharePoint connection" card and the Round 35 "Connect to Microsoft" card; same `[data-intel-banner]` slot) is now informational only — the MSAL device-code routes (`/api/corpus/sharepoint/signin|signout|refresh`) and the URL paste UI were both retired. The runtime trusts that the OneDrive desktop client handled SSO/MFA/admin-consent and verifies the result by `Path.is_dir()` + counting non-empty files at `Config.CSONE_ONEDRIVE_FOLDER`. `Config.ADOPTIQ_CORPUS_SHARE_URL` is preserved for documentation / bake logging; `Config.ADOPTIQ_SHAREPOINT_FOLDER_URL` is a backward-compat alias.

### Native Knowledge Corpus (Round 35 → Round 36)
The "AdoptIQ Knowledge Corpus" panel is native to every install — there is no per-user URL configuration. **Round 36 retired the MSAL/Graph runtime path entirely** (the default Microsoft Graph client ID required Cisco tenant admin-consent which is not granted; the OneDrive desktop client already handles that auth flow and produces the same files on disk). The lifecycle has three pieces:

1. **OneDrive sync presence as auth signal** — `corpus_bootstrap._check_onedrive_sync_status()` probes `Config.CSONE_ONEDRIVE_FOLDER` and returns `("synced", N, path)` when the folder exists and contains at least one non-empty file (zero-byte Files-On-Demand placeholders are skipped), `("not_synced", 0, path)` otherwise, `("unknown", 0, None)` when the env is unset. The result is surfaced on `CorpusBootState.onedrive_status` / `.onedrive_file_count` and on `/api/intel/status` under `boot.onedrive_status` / `boot.onedrive_file_count`.
2. **Build-time bake (local source only)** — `scripts/bake_corpus.py` runs from `build_mac_dmg.sh` BEFORE PyInstaller and indexes from a local directory: `--source <dir>` CLI flag (preferred), then `ADOPTIQ_BAKE_FIXTURE_DIR` env var (used by `build_mac_dmg.sh` to point at the build operator's local OneDrive sync mirror), then `Config.CSONE_ONEDRIVE_FOLDER`. The legacy `device_code` auth mode and `--share-url` flag are accepted for back-compat but are no-ops; no Graph API calls happen at build time. Four artifacts are staged under `bake/`: `corpus.db.enc`, `sentinel.json`, `corpus.db.salt` (filename pinned by `corpus_crypto._salt_path_for`), `corpus.sentinel.lock.json`. `ADOPTIQ_BAKE_CORPUS=0` (or `--no-bake`) skips and writes a `.bake-skipped` marker; the `adoptiq_mac.spec` `_datas()` hook gracefully omits the four artifacts when they aren't present.
3. **Runtime install + daily refresh (local pass)** — `corpus_bootstrap._install_baked_corpus_if_present()` copies the bundled `<sys._MEIPASS>/baked_corpus/` directory into the user's writable `~/Library/Application Support/AdoptIQ/knowledge/` on the first launch (idempotent). `start_daily_refresh_worker()` spawns a daemon thread that wakes every `_DAILY_REFRESH_TICK_S` (1h) and triggers `request_refresh(rebuild=False)` when (a) `corpus_knowledge_enabled` is on, (b) `_should_refresh()` reports the 24h window has elapsed, AND (c) `_check_onedrive_sync_status() == "synced"`. The legacy MSAL refresh-token gate is gone; refreshes pull from the local OneDrive sync mirror via the existing `_resolve_index_sources()` walker, which now lists `onedrive` (primary) → `user_downloads` → `intel_uploads`. Refreshes piggy-back on `EncryptedCorpusHandle.commit_to_disk` (sibling `.tmp` + `os.replace`); mid-refresh crashes leave the prior corpus byte-identical.

Round 35 also added a WAL checkpoint inside `commit_to_disk` (`PRAGMA wal_checkpoint(TRUNCATE);` before reading the plaintext file). Without it, the encrypted DB would only contain the 4096-byte SQLite header — committed pages live in the WAL until checkpointed — so the bake artifact was effectively empty under the previous code path.

The panel renderer (`static/js/intel_status.js` / `classifyCorpusPanel`) renders one of seven states based on `boot.source` × `boot.onedrive_status` × `boot.in_progress` × `boot.last_refresh_error`: `baked_synced`, `baked_not_synced`, `fresh_indexing`, `fresh_not_synced`, `refreshing`, `refresh_failed`, `unknown`. The `baked_not_synced` state shows the user the canonical OneDrive folder name (`AI Projects/AdoptIQ_CSOne_Reports`) and tells them to sync it to enable daily refresh.

### Corpus encryption sentinel resolution (Round 33 / Build8 → Round 36)
`corpus_crypto.open_corpus_for_user` accepts an `onedrive_root` argument; the Round 33 `sharepoint_root` parameter was retired in Round 36 along with the MSAL/Graph cache it populated. Resolution order, highest precedence first:
1. OneDrive sentinel (`<onedrive_root>/.adoptiq_corpus_sentinel.json`) — written by the OneDrive desktop client when the canonical Cisco-managed sentinel is present in the synced folder.
2. Auto-minted local sentinel (`<encrypted_path>.parent/sentinel.json`, mode `0o600`, parent `0o700`) — created on first run when the OneDrive root carries no sentinel (the common case on personal dev boxes). Operators who require Cisco-managed sentinel material for at-rest enforcement can pass `allow_local_sentinel=False` to preserve the fail-loud behavior.

### Admin Console auto-start (Round 32 / Phase 2.D, hardened in Round 37)
`app_simple._start_admin_server_in_thread()` spawns `enhanced_admin_dashboard_v2.admin_app` on a daemon thread (default `127.0.0.1:5152`) so the packaged `.app` actually exposes the admin UI without a second process. Idempotent (`sys._adoptiq_admin_started` guard), short-circuits under pytest, and quietly downgrades on `OSError` (port already bound — assume an external admin instance owns it). The header link in `templates/base.html` opens it in a new tab with `rel="noopener noreferrer"`.

**Round 37 / Phase 1: live-port handoff.** Before importing the admin module, `_start_admin_server_in_thread()` writes `os.environ['ADOPTIQ_MAIN_URL'] = f"http://127.0.0.1:{_resolve_main_port()}"`. The admin's `_main_app_host_port()` re-reads `os.environ.get('ADOPTIQ_MAIN_URL')` per call (defense in depth — the captured module-level constant could otherwise go stale). Pre-Round-37 the admin defaulted to `localhost:5151`, so when the packaged `.app` ran the main UI on 15152 the admin's Server Status tile probed the wrong port, decided `running=False`, and rendered the "Start Server" button while the server was right there. The legacy `server_status['port'] = 5000` sentinel was also flipped to `None` so the tile renders "N/A" before the first probe instead of falsely advertising port 5000.

**Round 37 / Phase 2-4: Admin Intelligence tile.** The admin "AdoptIQ Intelligence" card now (a) renders `boot.onedrive_status` / `boot.onedrive_file_count` from Round 36 as a colored pill (green `synced`, yellow `not synced`, gray `unknown`) so the operator can see at a glance whether daily refresh is unblocked; (b) drops the dead `corpus_status.boot.sharepoint` sub-block (Round 36 nulled that key) and the `/sharepoint_signin` + `/sharepoint_refresh` proxy routes (their main-app upstreams were deleted in Round 36); (c) renames the "Run incremental" button to "Re-index now" (matches the analyze-page panel labelling) and disables both `/corpus_refresh` buttons while `boot.in_progress` is true so the operator cannot stack refresh requests on an active index pass.

### Leader report two-pass CSOne validation (Round 38)
The leader-report background worker (`app_simple.run_leader_report_generation`) used to call `raise_validation_error_if_invalid` BEFORE loading the CSOne file, which combined with Round 14's "ruff-clean" `locals().get('csone_file')` check turned every OneDrive autodiscovery hit (`get_latest_csone_from_folder()`) into a hard `csone missing or empty` abort -- the validator saw `csone_file_provided=True` paired with an empty placeholder DataFrame and tripped the required-but-empty branch in `data_source_validator.py:248-249`. Round 38 fixes the load-order itself with a two-pass design and an endpoint-level provenance split:

1. **Endpoint provenance split** — `start_leader_report` (`/start_leader_report`) now tracks `csone_file_explicit` (set only when `request.files['csone_file']` was supplied) separately from `csone_file_autopicked` (set only when `get_latest_csone_from_folder()` returned a path). The status dict persists `csone_file_was_uploaded: bool(csone_file_explicit)` and `csone_file_path: <resolved-effective>` alongside the back-compat `csone_file` key. The worker reads `status['csone_file_was_uploaded']` to decide whether to fail loud at Pass 2.
2. **Pass 1 (early)** validates ONLY `snowflake` + `team_subscriptions` against an empty placeholder; hard-codes `csone_file_provided=False` so the Round 14 `locals().get(...)` heuristic regression cannot recur.
3. **CSOne load relocated up** to immediately after Pass 1 (`_resolve_csone_path_safe` → `load_csone_excel` → `_prepare_csone` → `_apply_scope_filter_csone`).
4. **Pass 2 (after load)** is gated by `_csone_was_uploaded`. When True, validates `csone` against the real loaded `csone_df` (empty after scoping is fail-loud — preserves the Round 2 / Phase 4.3 contract for explicit uploads). When False (autodiscovery hit) and the loaded frame is empty, logs a warning naming the autodiscovered file and appends a `partial_data_warnings` entry tagged `kind='autodiscovered_empty_after_scope'` so the Word/Excel banner is honest, but does NOT abort.

The Round 30 / M4 non-raising re-validation block sits between Pass 1 and the CSOne load and now sees the real loaded `csone_df` for the optional-fetch-error promotion. The fix is intentionally scoped to the leader path only — `run_compact_analysis`, `run_comprehensive_analysis`, `run_customer_renewal_analysis` already load CSOne BEFORE validating, and `run_subscription_analysis` excludes `csone` from `required_sources=[...]`, so the same load-order bug is not reachable on those paths (audit-pinned by `tests/test_round38_other_report_paths_unchanged.py`).

### Duplicate-launch UX (Round 38.1 / Build13)
The .app on macOS is a Flask web server with no native window — when the user double-clicks the icon a second time while the first instance is still on `127.0.0.1:5151`, `app_simple.py`'s "port in use" branch used to spawn an `osascript` `display dialog` *behind* other windows (no `activate`) and then `sys.exit(0)` after the 15s `osascript` timeout. The user perceived this as the .app "bouncing for a few minutes then stopping." Round 38.1 adds a tiny HTTP probe (`_probe_existing_adoptiq(port)`) that GETs `http://127.0.0.1:<port>/` and matches an "adoptiq" body marker; on a True result the duplicate-launch path now `webbrowser.open`s to the existing instance and exits cleanly, no dialog. The `osascript` invocation also gained a leading `-e 'tell application "System Events" to activate'` arg so when the dialog IS shown (rare non-AdoptIQ port collision) it actually surfaces to the front. The fix is scoped to the duplicate-launch branch only — boot order, corpus install, Snowflake prefetch, and the Round 38 leader-report code paths are unchanged. Source-shape pinned by `tests/test_round38_1_duplicate_launch_routes_to_existing.py`.

### Latent downstream bugs unblocked by Round 38 (Round 38.2 / Build14)
Round 38's two-pass validator design has a subtle side-effect: report-generation paths that USED to abort at validation when CSOne data was empty/missing now proceed all the way through Document Generation. This unblocks any latent bug in the formatter / writer code that depended on validation as an implicit guard. Round 38.2 fixes the first such bug — `KeyError: '_bu_disp'` in `leader_report_generator._compute_customer_health` (line 3457 pre-fix), where the transient `_bu_disp` column was assigned inside an `if not stalled.empty:` guard but accessed unconditionally on the next line. Trigger condition: any CSSM whose open APs were all <=30 days old (no stalled rows). The fix moves the for-loop INSIDE the guard. A full audit of `leader_report_generator.py`'s 29 `if not df.empty:` sites found 28 already CLEAN and only the one `_bu_disp` site needing fix; the AB block immediately below uses `_age_days` and is the reference-correct example. **When touching any new formatter or writer that consumes possibly-empty AP/AB/CP/TAC/SP DataFrames, follow the same template: outer column-existence guard + inner status/date guard + per-row filter + an `if not <subset>.empty:` block that wraps BOTH the transient column assignment AND every consumer.** Source-shape pinned by `tests/test_round38_2_compute_customer_health_no_stalled.py`. Other large formatters (`compact_report_formatter.py`, `executive_intelligence_formatter.py`, etc.) may have analogous bugs; Round 38.2's audit was scoped to `leader_report_generator.py` only and a follow-on formatter-audit round is tracked as a deferral.

### Corpus crypto self-heal on upgrade (Round 39 / Build15)
Every build's bake script (`scripts/bake_corpus.py`) mints a fresh sentinel when no stable one is found, so the `Resources/baked_corpus/sentinel.json` shipped with Build N+1 almost never matches the sentinel material in a user's existing `~/Library/Application Support/AdoptIQ/knowledge/` from Build N. Pre-Round-39, `corpus_bootstrap._install_baked_corpus_if_present` was strictly one-shot (`if user_db.exists(): return None`), so the new bake never replaced the old DB and `open_corpus_for_user` later raised `InvalidTag`, surfacing as "Last run failed (crypto) — authentication tag mismatch" with no UI escape hatch. Round 39 swaps the existing-corpus branch for **probe-and-recover**: a healthy corpus short-circuits (Round 35 idempotency preserved); a corpus that fails decryption is preserved aside as `<name>.broken-<utc_iso>` (single rolling backup capped at one set, so a stuck-bake-loop cannot fill the disk with 280 MB sidecars on every boot) and the bake snapshot is reinstalled in place. State labels: `_STATE.source = "baked"` for the healthy-existing-corpus branch (so the panel says "Active" not the misleading "Indexing OneDrive…"), `"self_healed_baked"` for a recovered install. The catch is intentionally narrow (`CorpusCryptoError` only) — any other exception bubbles so a transient `PermissionError` / `MemoryError` can never silently overwrite a healthy user corpus. Manual escape hatch: `POST /api/corpus/reset` (CSRF + `X-AdoptIQ-Internal` dual-auth, mirrors `/refresh`) plus `/api/intel/reset` user-facing alias plus `/corpus_reset` admin proxy plus a **hidden** "Reset corpus" button on the analyze panel that `intel_status.js` unhides ONLY when `boot.last_error_kind === 'crypto'`. The button uses `addEventListener` (no inline onclick, CSP-clean) and `confirm()`s before POSTing. Defense in depth: `scripts/bake_corpus.py` now runs a decrypt round-trip self-test after the structural verify (open-and-close + `SELECT count(*) FROM sqlite_master`); a failure deletes all four artifacts and exits with code 5 so `build_mac_dmg.sh` aborts before PyInstaller bundles a malformed bake. **This is the canonical fix for the upgrade-handoff class of bug; future builds that change crypto layout (KDF parameters, salt size, sentinel format) MUST rely on this self-heal rather than introducing new migration code.** Source-shape pinned by `tests/test_round39_self_heal_crypto_failure.py`, `tests/test_round39_reset_corpus_endpoint.py`, and `tests/test_round39_intel_status_panel_reset_button.py` (32 new tests).

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
