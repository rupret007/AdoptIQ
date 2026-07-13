# AdoptIQ Code & Logic Review — Latest

**Date:** April 2026
**Scope:** Current quality posture across reports accuracy, AI insights grounding, build pipeline, security gates, and (Round 17) the CSOne knowledge corpus integration.
**Status:** Active. For round-by-round detail (Round 14 → Round 17) see `QUALITY_AUDIT.md`.

> The earlier `CODE_REVIEW_*.md` files in this repo are point-in-time snapshots from the v1.0.x era. They are kept for historical traceability but do not reflect the current codebase. **Use this file plus `QUALITY_AUDIT.md` for current state.**

---

## 1. Quality posture (snapshot)

| Gate | Result |
|------|--------|
| `make test` (`pytest -q`) | **2106 passed / 2 skipped** |
| `make lint` (`ruff check`) | All checks passed |
| `make security` (`bandit -ll`) | 0 HIGH / 0 MED |
| `make audit` (`pip-audit -r requirements.txt`) | No known vulnerabilities |
| `make verify` | All four gates clean, back-to-back idempotent |

Two pre-existing `# nosec B104` rationales remain in `app_simple.py` and `enhanced_admin_dashboard_v2.py` for env-flag-gated public bind. Both are documented and unchanged from Round 14.

---

## 2. Architecture & data flow

| Report Type | Entry Point | Validation |
|-------------|-------------|------------|
| Compact / Executive Intelligence | `create_executive_intelligence_report` / `_create_enhanced_compact_report` | `raise_validation_error_if_invalid('compact')` |
| Renewal (single / portfolio) | `_create_simple_renewal_report` | `raise_validation_error_if_invalid('renewal')` |
| Comprehensive | `ExecutiveReportBuilder` + `create_enhanced_word_report` | `raise_validation_error_if_invalid('comprehensive')` |
| Leader | `generate_leader_report` | `raise_validation_error_if_invalid('leader')` |
| Advanced Renewal | `generate_renewal_report` | None (mock fallback) |
| Enhanced Snowflake Insights | `generate_enhanced_insights_report` | None |

All four user-facing reports route through `data_source_validator.py` before generation; report content is built from `canonical_metrics.py` (counts), `risk_scoring.py` (deterministic weighted scores), `report_export_schema.py` (Excel columns), and `report_word_styling.py` (Word formatting).

---

## 3. Single-source-of-truth modules (Round 15 / 16 introductions)

| Module | Responsibility |
|--------|----------------|
| `canonical_metrics.py` | Cross-report counts (customers, ARR at risk, high-risk count, P1 open, top barriers). Always use these; never recompute inline. |
| `report_export_schema.py` | Excel column ordering, headers, and dtypes — shared by all report writers. |
| `report_export_styling.py` | Excel polish: native Tables, banded rows, frozen header, conditional formatting (3-color risk scale, severity bands, status pills, days-open data bar). Honors `startrow` for title-row offsets. |
| `report_word_styling.py` | Word top-N table renderer (`add_banded_top_n_table`) with consistent header colors, alternating row shading, reproducible formatting. |
| `ai_narrative_validator.py` | Validates LLM-generated narratives against the source briefing: rejects ungrounded numbers, invented entities, and HTML/JS injection. |
| `data_contracts.py` + `data_normalization.py` | Shared DataFrame consistency helpers — use before mutating any DataFrame. |
| `structured_logging.py` | All logging routes through this module. Pipeline-boundary entry/exit logs use manager-digest only (no PII). |
| `knowledge_schema.py` | Round 17 SSoT SQLite DDL for the CSOne knowledge corpus (`corpus_files`, `customers`, `cases`, `barriers`, `resolutions`, `sentiments`, `playbook_chunks`, `term_stats`, `corpus_stats`). |
| `corpus_indexer.py` | Round 17 idempotent file enumeration + schema-aware parsers (`.xlsx`, `.docx`, `.csv`) + entity extraction + in-house BM25 indexing. |
| `corpus_crypto.py` | Round 17 AES-256-GCM at rest with HKDF-SHA-256 derived from the OneDrive sentinel; file mode `0600`; plaintext scrubbed on close. |
| `corpus_retriever.py` | Round 17 read-side facade exposing `get_status`, `get_customer_history`, `get_recurring_themes`, `get_resolutions_for`, `search_playbook`, `list_customers`. Returns frozen dataclasses; raises `CorpusUnavailable`. |
| `corpus_bootstrap.py` | Round 17 startup wiring + background indexer thread + `request_refresh()` + shutdown cleanup. |
| `ask_ai_corpus.py` / `report_corpus_context.py` | Round 17 Ask AI prompt builder and report pre-fill helper. Both route corpus chunks through `ai_narrative_validator.is_corpus_chunk_safe`. |

---

## 4. Recent quality wins (Round 14 → Round 17)

- **Cross-format consistency.** Word and Excel reports now share canonical-metrics output; headline KPIs match exactly. Pinned by `tests/test_round16_cross_format_consistency.py`.
- **Sort determinism.** Top-N rankings use stable name-based tiebreakers; identical input → byte-identical ranking. Pinned by `tests/test_round16_sort_determinism.py`.
- **AI narrative grounding.** `ai_narrative_validator` blocks hallucinated numbers, invented entities, and HTML injection at the report-narrative gate. Pinned by `tests/test_round16_ai_narrative_validator.py` (31 cases incl. R17 surface) + `tests/test_round16_ai_insights_eval.py` (7 fixture-driven cases).
- **Excel polish coverage.** `apply_excel_polish` accepts `startrow` and is wired into all three Excel report writers in `app_simple.py` (compact, renewal, summaries-per-person). Pinned by `tests/test_round16_apply_excel_polish_offset.py`.
- **Word top-N polish.** Banded top-N table helper substituted in `compact_report_formatter` and `executive_intelligence_formatter`. Leader report tables retain their custom per-cell color logic by design (tracked as R16-FOLLOWUP-2).
- **Currency precision.** Audited `canonical_metrics`, `risk_scoring`, `advanced_renewal_analyzer`, and the three formatters — no intermediate-rounding bugs found. Documented and moved on per the audit plan's "zero-finding" constraint.
- **CSOne Knowledge Corpus (Round 17).** Persistent customer / troubleshooting knowledge sourced from the daily CSOne report corpus, surfaced via Ask AI grounded retrieval, Historical Context sections in Executive / Leader reports, a Customer 360 page, a Troubleshooting Playbook page, and an Admin tile. Encrypted local cache (AES-256-GCM, HKDF over OneDrive sentinel + per-install salt, file mode `0600`) — no customer PII ships in the installer. Feature-flagged behind `CORPUS_KNOWLEDGE_ENABLED` and degrades gracefully when OneDrive is not synced. 157 new tests, no new dependencies.

---

## 5. Known residual risks

- `ai_narrative_validator.validate_grounded_numbers` accepts numerics, dollar amounts, percentages, and ISO-8601 quarter labels — it does not yet parse natural-language number words ("twenty-three"). Future model swaps to a verbose-numeric model would need an extension.
- The "common-knowledge" allow-list in the validator covers calendar years 2024–2027; it will need extending past 2027.
- The cross-format consistency test reads numbers from Excel cells and the docx executive-summary table only — narrative-only drift between docx and xlsx is not currently caught at the cell level. Mitigated upstream by the grounding validator.
- Leader report top-N tables retain custom paint logic (color-coded deltas, per-column alignment); they are deliberately not migrated to `add_banded_top_n_table` until that helper supports per-cell color hooks (R16-FOLLOWUP-2, low priority).
- Round 17 corpus indexer is single-threaded and runs in-process; very large corpora (>10k files) may take >60s on first launch. Background-thread design avoids blocking startup, but progress reporting is coarse (`boot.in_progress` only). Tracked as R17-FOLLOWUP-1.
- BM25 lexical retrieval (in-house) is sufficient for v1 but does not handle synonyms or paraphrase-heavy queries. Round 18 may revisit embeddings if the DMG-size budget allows.
- Multi-worker WSGI deployments still flag the in-process Ask AI throttle warning at startup; the playbook page reuses the same throttle and has the same caveat.

---

## 6. Recommended follow-ups (open)

1. **R16-FOLLOWUP-1** — extend `validate_grounded_numbers` to handle natural-language number words.
2. **R16-FOLLOWUP-2** — add a richer `add_banded_top_n_table_with_overrides` helper, then migrate the four leader-report top-N tables.
3. **R16-FOLLOWUP-3** — extend cross-format consistency test to parse docx narrative paragraphs.

All three are tracked in `QUALITY_AUDIT.md` Round 16 § "Recommended follow-ups". None block release.

---

## 7. Build / release status

- **Packaged build:** v1.0.3 build 1 (macOS DMG + Windows EXE).
- **Active branch with Round 14/15/16 work:** `round-13-audit` (locally committed; pending push to corporate GitHub when on VPN — see commit `0543130`).
- **macOS DMG (latest local build):** `OUTBOX/AdoptIQ-v1.0.3-build1.dmg` (~117 MB, ad-hoc-signed, `codesign --verify --deep --strict` clean).

---

## 8. Where to look next

- Round-by-round audit detail: `QUALITY_AUDIT.md`
- User-facing release notes: `README.md` § "What's New since v1.0.3"
- Snowflake table inventory and per-report data flow: `SNOWFLAKE_USAGE.md`
- Build / packaging: `BUILD_WINDOWS.md`, `CURSOR_MAC_BUILD_INSTRUCTIONS.md`, `CURSOR_PC_BUILD_INSTRUCTIONS.md`
- Branch workflow: `BRANCH_WORKFLOW.md`
