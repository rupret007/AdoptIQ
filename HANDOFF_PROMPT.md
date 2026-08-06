# AdoptIQ — Session Handoff Prompt

Copy everything below the horizontal rule into a new Cursor/Claude session to continue development with full context.

---

## Your mission

You are taking over **AdoptIQ**, a **renewal-risk and adoption intelligence** desktop application for Cisco Customer Success managers. It is **not** a generic BI tool — it is a **report-accuracy-first** product that turns messy operational data into **Word + Excel** deliverables CSMs can send to customers and leadership, plus a **grounded Ask AI** surface for portfolio questions.

**North star:** Every number in a report (KPI counts, risk scores, TAC totals, health grades, citations) must agree across Word, Excel, Compact, Renewal, Comprehensive, and Leader formats for the same scope. LLM narratives are **downstream of canonical data** — never the source of truth.

**Current shipping baseline:** `v1.0.4` **Build 111** (Round 147–149), commit `7763b93`.  
**Previous baseline:** Build 109 @ `909e9ab` (Round 139–140).  
**Quality floor:** `6859` pytest passed / 7 skipped / 14 deselected after Round 149; `make verify` must stay green (ruff, bandit HIGH/MED, pip-audit, pytest).
**Frozen dependency floor:** `cryptography>=50.0.0` and `aiohttp>=3.14.3`; do not build a candidate from older globally visible copies.

**Repos (synced 2026-08-03):**

- **Primary (Cisco):** `https://wwwin-github.cisco.com/jestory/AdoptIQ` — branch `main`
- **Mirror (GitHub):** `https://github.com/rupret007/AdoptIQ` — branch `main` (production line)
- **Archived fork:** `github.com/rupret007/AdoptIQ` branch `decisionops-backup-2026-08-03` @ `099c3b2` — older **DecisionOps / Decision Intelligence** experiment (Build 107); **not** the active product line. Recover only if merging that work back in.

---

## What AdoptIQ does (operator view)

1. CSM selects **manager**, **technology**, **days**, optionally uploads **CSOne `.xlsx`**, starts a report job.
2. Background thread: Snowflake prefetch → scope filters → risk scoring → formatters → Word/XLSX output.
3. Reports land under `~/Documents/AdoptIQ Reports/<manager>/<report_type>/` (packaged builds).
4. **Ask AI** answers portfolio questions using **retrieval-first** grounding (Snowflake + CSOne + encrypted knowledge corpus), then CircuIT LLM.
5. **Admin console** (port 5152, loopback) monitors jobs, grounding rates, corpus status, audits.

**Report types (maintain all four):**

| Type | Purpose |
|------|---------|
| **Comprehensive** | Concise portfolio decision report + separately named 16-sheet Source Data File; `Risk_Components` per customer |
| **Compact** | Executive summary; high-risk focus; faster |
| **Renewal** | Per-customer renewal risk; portfolio or single-customer |
| **Leader** | Concise Team, individual team-member, or customer decision report + separately named Source Data File; scope is validated against the selected manager |

**Retired (do not reintroduce without explicit product decision):** WxCC Health Check report type (Round 139) — routes, exporter, UI removed; historical artifacts on disk preserved.

---

## Architecture (flat Python monolith)

```
app_simple.py              Flask main UI :5151 (~32.6k lines) — orchestration, routes, job lifecycle
enhanced_admin_dashboard_v2.py   Admin :5152 — monitoring, audits, proxies
adoptiq_backend.py         Core Word/Excel engine (~14.6k lines)
leader_report_generator.py Leader reports (~8.3k lines)
compact_report_formatter.py / executive_intelligence_formatter.py
canonical_metrics.py       SSoT for cross-report counts — NEVER recompute inline
risk_scoring.py            Deterministic weighted risk — changes affect ALL reports
decision_report_delivery.py Shared concise Word, chart, lineage, workbook, and parity contract
leader_scope.py            Validated Team/Member/Customer scope resolution and row attribution
report_export_schema.py    Excel column ordering/dtypes SSoT
report_export_styling.py   Excel polish (tables, conditional formatting)
report_source_injector.py  Inline source citations in Word
ai_narrative_validator.py  Grounding gate for LLM narratives
ask_ai_grounded.py         Ask AI retrieval + compose pipeline
corpus_bootstrap.py        Encrypted corpus install/refresh
config.py                  Version, env vars, feature flags
```

**Two-tool quality loop:** Cursor generates → Claude audits. Every non-trivial session ends with a handoff block in `QUALITY_AUDIT.md`. Read `CLAUDE.md` first — it is the invariant bible (140+ rounds of Critical Rules).

**Verification:**

```bash
make verify          # full gate — required before ship
make eval-ask-ai     # offline Ask AI eval (NOT in make verify)
bash scripts/test_build_smoke.sh dist/AdoptIQ.app
python3 scripts/r114_audit_reports.py --auto   # fail-closed artifact audit
python3 scripts/run_report_soak.py --duration-seconds 7200 ...  # stability soak
bash scripts/preflight_acceptance.sh           # disk space before bake/soak
```

---

## Integrations you MUST maintain

### 1. Snowflake (primary data warehouse)

- **Modules:** `snowflake_prefetch.py`, `adoptiq_backend.py`, `enhanced_snowflake_insights.py`, `leader_report_generator.py`
- **Auth:** Keeper → env vars → `_bundled_secrets.py` at build time
- **Rules:** Parameterized queries only (`%s` placeholders). Table access via `snowflake_table_policy.py` allowlist.
- **DSM attribution:** `get_subscriptions_for_team()` fans out primary + secondary email columns (`DSM_EMAIL1`–`DSM_EMAIL5`, etc.) — do not roll your own subscription SQL.
- **Global-config errors:** Route through `EnhancedSnowflakeInsights._is_globally_unavailable_error()` — never per-customer spam for schema/policy failures.
- **Requires VPN** for live acceptance; synthetic pytest alone is **insufficient** for Snowflake/narrative changes.

### 2. CSOne (Excel upload + OneDrive autodiscovery)

- **Upload path:** `/start_*` endpoints → `UPLOAD_FOLDER` → `_prepare_csone` → `_apply_scope_filter_csone`
- **OneDrive:** `config._csone_onedrive_candidates()` — shared-folder shortcut first; operator override via Preferences → CSOne folder (`/api/settings/csone-onedrive-folder`)
- **Skip zero-byte Files-On-Demand placeholders** (`get_latest_csone_from_folder_diag`)
- **Customer aliases:** `customer_aliases.defaults.json` + App Support `customer_aliases.json` — NYU/NYULH-style name equivalence across CSOne/Snowflake

### 3. Snowflake CSConsole (AB, AP, Pulse, Success Priorities)

- Fetched in backend; scoped by manager/technology via `_filter_csconsole_data_by_technology`
- **Action Plans:** `_scope_action_plans_for_report()` — account scope + authoritative tech columns only (Round 139)
- **HTML strip:** Rich-text fields through `data_normalization._strip_html_safe` before Word/Excel/AI prompts

### 4. CircuIT LLM (Cisco internal, OpenAI-compatible)

- **Default model:** `gemini-3.1-flash-lite` (operator-flippable via Preferences; test-before-save via `/api/llm/ping`)
- **Resolution:** `model_resolver.get_active_report_model()` / `get_active_ask_ai_model()` — separate Ask AI vs report paths
- **Grounding:** All report narratives through `ai_narrative_validator.validate_narrative` — target rejection rate ≤10%
- **Sanitization:** `_r69_sanitize_llm_error` on any user-facing LLM error text (never leak `appkey`/`session_id`)
- **Rate limits:** Report paths use backoff on 429 (Round 126 G3)

### 5. AdoptIQ Knowledge Corpus (encrypted SQLite + vectors)

- **Prebaked in DMG:** `Resources/baked_corpus/` → App Support on first launch (Build 76+)
- **Encryption:** AES-GCM; OneDrive sentinel OR local sentinel (`allow_local_sentinel=True`)
- **Hybrid retrieval:** BM25 + dense (`BAAI/bge-small-en-v1.5`) + RRF; vectors in `chunk_vectors` table inside encrypted DB
- **Runtime refresh:** Generated reports (`local_outputs`), intel uploads, OneDrive when present — **OneDrive is optional**, not a blocker
- **Bounded dense backfill:** `ADOPTIQ_RUNTIME_VECTOR_MAX_CHUNKS` (default 2000) — must not hang `boot.in_progress`
- **UI:** `static/js/intel_status.js` — corpus panel states; Preferences for share URL

### 6. External intelligence

- **Module:** `incident_storage.py` — SQLite `external_intelligence.db`
- **Sources:** Webex Status, help.webex.com, PSIRT — feeds incident component in risk scoring
- **Per-customer filter:** `_r65_filter_customer_tagged_incidents` — portfolio-wide incidents pass through when untagged

### 7. Cisco internal integrations

- **Module:** `cisco_internal_integrations.py` — BST, PSIRT search, Circuit client
- **Keeper:** `hvac` for Snowflake creds at runtime

### 8. Auto-update (OneDrive Releases manifest)

- **Module:** `auto_updater.py` — reads `latest.json` from synced OneDrive OUTBOX
- **Safety:** Never swap unverified artifact; idle-gated; SIGTERM not SIGKILL; mac slot merge-aware (don't clobber Windows slot)
- **Current gap:** Mac Build 109 published; **Windows `latest.json` slot still Build 105** — needs Windows host + `build_pc.bat`

### 9. Admin console (auto-started daemon thread)

- Proxies corpus refresh, grounding diagnostics, DSM column introspection (`/api/diag/dsm-columns`)
- **Must use** `_live_main_url()` for "Back to AdoptIQ" link (port-aware)
- Loopback-only by default; CSRF + `X-AdoptIQ-Internal` dual-auth on sensitive routes

### 10. PyInstaller packaging (macOS DMG + Windows EXE)

- **Build:** `build_mac_dmg.sh` with `ADOPTIQ_RELEASE_GATE=1` + corpus bake mandatory
- **Never commit:** `secrets.env`, `_bundled_secrets.py`, `OUTBOX/`, `*.dmg`
- **macOS launcher:** Shell wrapper opens splash, polls `/ping`, starts `AdoptIQ.bin` in background (Round 97)
- **Codesign + DMG:** `Unblock AdoptIQ.command`, `READ_ME_FIRST.txt` required in release gate

---

## Historical arc (what we've done — condensed)

| Era | Builds | Theme |
|-----|--------|-------|
| **R0–R14** | pre-40 | Quality gate born (`make verify`), bind loopback, data contracts, admin fixes |
| **R15–R16** | 40–42 | Excel schema SSoT, AI narrative validator, citation injection |
| **R38–R39** | 12–15 | Leader two-pass CSOne validation; corpus crypto self-heal on upgrade |
| **R64–R67** | 37–41 | Comprehensive accuracy sweep; cross-format score parity; Report_Info schema |
| **R66–R66.2** | 39–40 | Citation units; hybrid Ask AI retrieval; offline eval framework |
| **R68–R69** | 42–43 | Build labels in reports; restart banner; operator-flippable LLM models |
| **R76–R78** | 50–54 | Citation bullet/paren layouts; Snowflake global-error suppression; stub-bullet filter; Leader AP dedup |
| **R80–R88** | 56–64 | Team roster SSoT; outputs per manager; secondary CSSM attribution; per-source citations |
| **R91–R94** | 67 | Single-window jobs UI; TACTrack-style storage + corpus sidecar gate; ACC AB strict scope |
| **R95–R101** | 68–74 | Ask AI reranker; runtime-only corpus era; startup splash; report soak supervisor |
| **R102–R111** | 70–80 | No Downloads prompt; UX cleanup; customer aliases; Compact↔Renewal score parity |
| **R112–R118** | 81–87 | LLM error sanitization; citation paren clusters; ACC secondary DSM slots; ACC customer count |
| **R119–R128** | 88–97 | Auto-update; report accuracy sweeps; health grade grounding; Ask AI case search |
| **R129–R132** | 98–102 | Proper Mac release w/ full bake; relaunch-to-update; customer alias normalization |
| **R135–R139** | 108–109 | WxCC added then **retired**; TAC collapse; AP scope parity; fail-closed `r114` audit |
| **R140** | 109+ | Disk preflight; provenance row stripping for KPI parity; 2h soak green (49/49 events) |

**Methodology lesson (Round 76):** Synthetic pytest passing is **necessary but not sufficient**. Changes touching Snowflake errors, narratives, or formatter chrome require **bake → install → regen four reports → `r114_audit_reports.py`** before claiming closure.

## Round 142 decision-report redesign (current working contract)

- Leader and Comprehensive now default to a concise, decision-oriented Word report. Raw Action Plan, barrier, pulse, TAC/BEMS, subscription, priority, incident, bug, and risk-component records belong in the paired `AdoptIQ_Source_Data_*.xlsx`, not in Word.
- Word uses the shared canonical facts bundle and carries four actual embedded charts: activity mix, Action Plan status/aging, risk distribution, and dated activity trend. A missing series is disclosed as unavailable/partial rather than converted silently to zero.
- Action Plans remain prominent: distinct stable IDs, total/open/overdue/due-soon/completed/blocked/unknown lifecycle, owner/account/due date/age/priority/next action, `Title unavailable` for a missing source title, and explicit ID/title data-quality flags.
- Leader scope selector supports `team`, `member`, and `customer`. Backend validation rejects an unknown manager child/customer and ambiguous customer labels; account/subscription IDs are authoritative. Shared records are visible to each attributed member but counted once at team level.
- Round 148 documentation correction: the Source Data File has exactly 16 canonical sheets: `Report_Info`, `Metric_Lineage`, `Chart_Data`, `Evidence_Links`, `Action_Plans`, `Adoption_Barriers`, `Customer_Pulse`, `TAC_Cases`, `BEMS`, `Subscriptions`, `Success_Priorities`, `External_Incidents`, `External_Bugs`, `Risk_Components`, `Member_Summary`, `Account_Summary`.
- Every visible KPI/chart series has a `Metric_Lineage` row. `Report_Info` records explicit `Data_As_Of_UTC`, source states/warnings, a fact-contract hash, and per-sheet semantic hashes. Workbooks contain no formulas and defang formula-like source text.
- The offline acceptance harness is `scripts/generate_offline_acceptance_artifacts.py`. With the fixture clock fixed at `2026-08-03T12:00:00Z`, two post-gate generations were byte-identical across all four scopes; every parity manifest passed.
- Local acceptance is intentionally honest: fixture sources are marked `partial`; there was no Snowflake, CSConsole, or CSOne access. Round 142 proves internal parity, traceability, deterministic output, scope rejection, and artifact quality—not 100% live production completeness.

## Round 143 acceptance runner and work-machine rollout

- Run `scripts/run_decision_report_acceptance.py` for the release decision. It requires `--manager`, `--days`, `--as-of`, and `--output-dir`, runs Team/Member/Customer/Comprehensive twice, validates both artifact formats, and emits `decision_report_acceptance_summary.json`.
- `--mode live` never falls back. If the local app or Snowflake preflight is not healthy, it exits nonzero and records the failure. `--mode auto` may select the sanitized offline path, but the summary explicitly says live validation was not performed.
- The runner checks the exact 16-sheet order, filenames/pairing, manager/scope/window/as-of metadata, canonical fact and sheet hashes, source states/counts, Action Plan lifecycle, ID/title quality, chart/lineage/evidence parity, TAC stable account association, BEMS subset membership, formula absence, filters/frozen headers/dimensions, chart accessibility, and two-pass repeatability.
- Live member/customer selection comes from server-authorized roster/customer options. Outside-manager membership, unavailable customer authorization, and ambiguous shared-account customer resolution fail closed.
- Comprehensive now publishes its actual canonical prefetch `data_retrieved_at` in report status. TAC/BEMS public detail retains `Account ID`, closing the traceability gap found by the first Round 143 acceptance run.
- Local final evidence: all eight offline pairs passed with byte-identical repeats and no failures; 19 Word pages and all 60 workbook sheets were visually reviewed; all four DOCX accessibility audits had zero findings; all four R114 audits reported no critical issues; Ruff, Bandit HIGH/MED, strict pip-audit, and pytest all passed (`6399 passed / 6 skipped / 6 deselected`).
- This machine still had no Snowflake, CSConsole, or CSOne access. Use `WORK_MACHINE_ROLLOUT.md` on the work machine, pinned to the exact commit in the final handoff, before deployment or any 100% live-accuracy claim.

## Round 145 local parity and product hardening

- Added an explicit source-only local acceptance runtime. It requires `--enable-local-fixtures`, loopback binding, a non-production environment, and a non-frozen process; it cannot activate from an environment variable or normal app startup. Every fixture result is stamped `local_acceptance_fixture`, `sanitized=true`, and `live_validation_performed=false`.
- The versioned manifest covers 19 source datasets and 21 scenarios: healthy, true zero, partial/stale/failed/unavailable/truncated, duplicates, ambiguous/conflicting identity, missing ID, malformed value, timezone boundary, multicurrency, large volume, long text, prompt injection, and four provider failures.
- Real loopback Flask acceptance covers reports/downloads/Previous Reports, Ask AI sync+stream, citations/evidence/diagnostics, Ask Intel, External Intelligence, Customer 360, Playbook, corpus/model status, and degraded states. The complete 36-option report matrix and fixed AI set passed twice on an unchanged snapshot.
- Leader remains the primary manager report: decisions, canonical KPIs, charts, risks, Action Plans, and drill-downs stay in Word; repetitive activities and raw records stay in the paired Source Data workbook. Comprehensive uses the same concise delivery contract where appropriate without making every report identical.
- Renewal score exports now publish reconciled `Risk_Score_0_10` and `Risk_Score_0_100` values. Renewal chart labels use a readable donut center; blank-page breaks are heading-based; subscription AI Markdown renders as Word structure; report tables/headings receive accessibility semantics.
- Source-state warnings now flow through Ask AI and Ask Intel, sync/stream delivery is checked for semantic parity, prompt-injection text is treated as data, and exact local canonical headlines reconcile to the independent fixture oracle.
- Final local evidence: 21/21 HTTP scenarios; 36/36 report scenarios twice; two AI passes with repeatability; 75/75 Ask AI questions; two-pass Team/Member/Customer/Comprehensive acceptance; 8 DOCX files/58 pages and 98 workbook sheets visually reviewed; zero Word accessibility findings and zero workbook formula/render failures.
- A developer-only macOS Build 109 candidate was rebuilt without live credentials or a baked corpus. Signature/DMG integrity, loopback startup/shutdown, HTTP routes, Secure-cookie behavior, sanitized AI/report failures, and absence of fixture/generated evidence were verified. It is not production-ready.
- No Snowflake, CSConsole, CSOne, or live CircuIT success path was available. Round 145 proves controlled-fixture reconciliation and safe packaged degradation, not live production completeness.

### Required live-source acceptance before a production-accuracy claim

1. On VPN with Snowflake, CSConsole, and CSOne available, preflight connectivity and generate Team, Member, Customer, and Comprehensive for one manager using the same explicit window/as-of clock.
2. Confirm every selected member/customer is within that manager's current roster/account-ID scope; intentionally submit one outside-scope and one ambiguous-name request and confirm both fail closed.
3. Reconcile Word KPIs, all four chart series, `Metric_Lineage`, `Chart_Data`, `Member_Summary`/`Account_Summary`, and detail-sheet distinct IDs. Verify shared attribution does not inflate team totals.
4. Compare Action Plan lifecycle buckets to live source IDs/status/due dates; inspect missing-ID/title rows, boundary dates, unknown statuses, duplicates, and the documented 14-day due-soon horizon.
5. Verify TAC joins by stable subscription/account IDs, BEMS as a non-additive TAC subset, and quarantining of ambiguous/unmatched cases.
6. Force or observe zero, partial, failed, filtered, and stale source conditions and confirm Word/workbook disclosures distinguish them accurately.
7. Render every Word page and every workbook sheet, run DOCX accessibility checks, inspect chart titles/labels/alt text, and run `scripts/r114_audit_reports.py` on the live artifacts.
8. Generate the same live scope twice without source drift and compare semantic facts/sheet hashes. Any mismatch must be explained before release.
9. Run Compact and Renewal for matching scopes and confirm their established canonical/risk contracts did not regress.
10. Only after all checks pass may the live dataset be described as production-validated; retain the as-of time, source query IDs/counts, artifacts, and audit results in `QUALITY_AUDIT.md`.

---

## Single sources of truth (never bypass)

| Concern | Module |
|---------|--------|
| Cross-report counts | `canonical_metrics.py` |
| Risk scores/bands | `risk_scoring.py` |
| Concise Word/source workbook/chart facts | `decision_report_delivery.py` |
| Leader Team/Member/Customer scope | `leader_scope.py` |
| Excel columns | `report_export_schema.py` |
| Excel polish | `report_export_styling.apply_excel_polish` |
| Word top-N tables | `report_word_styling.add_banded_top_n_table` |
| TAC dedup/collapse | `data_normalization.collapse_tac_cases` |
| Provenance rows | `data_normalization.drop_provenance_rows` |
| Customer name aliases | `data_normalization.canonical_customer_name` / `customer_aliases.defaults.json` |
| LLM model resolution | `model_resolver.py` |
| Report output paths | `report_output_paths.py` / `_r81_resolve_report_output_dir` |
| Build label in reports | `_r68_build_label.py` |
| Word citations | `report_source_injector.py` (table captions below matrices, not per-cell clutter) |
| Health grades A–F | `risk_scoring.band_to_health_grade` + post-gen stamp helpers |

---

## Current known deferrals (do not "fix" without intent)

1. **Windows Build 109** — blocked on Windows build host; `latest.json` pc slot at Build 105.
2. **Leader vs Comprehensive scope divergence** — preserve only where documented (Leader attribution/roster vs Comprehensive account-ID portfolio). Round 142 uses stable IDs and fails closed; do not collapse predicates or reintroduce fuzzy first-match behavior.
3. **Comprehensive grounding ~6.7%** — ACCEPT per R139 (≤10% contract); portfolio `invented_entity` on edge cases.
4. **AI validator small-integer trade-off** — integers 0–100 auto-grounded to reduce false rejections; hallucinated counts may slip through — monitor in live acceptance.
5. **Premium support / upsell KPI templates** — still legacy strings (Round 67 deferral).
6. **Formatter empty-DataFrame audit** — only `leader_report_generator.py` fully audited for R38.2 pattern; other large formatters may have latent bugs.
7. **DecisionOps fork** — on `decisionops-backup-2026-08-03` only; not merged into production line.

---

## Where improvements are needed (push here)

### P0 — Ship parity and ops

- **Windows Build 109 release:** Run `build_pc.bat` on PC host, merge-aware `scripts/write_release_manifest.py`, verify EXE smoke.
- **Live acceptance on every material change:** VPN + Brian Frazier ACC 90d four-report harness + `r114_audit_reports.py --auto` + cross-format parity script.
- **Disk hygiene automation:** `preflight_acceptance.sh` exists — integrate into CI/release scripts so bake never fails silently on full disk.

### P1 — Report accuracy (highest product value)

- **Cross-format drift detection in CI:** Automate Compact↔Renewal `Risk_Score_0_10` + `Risk_Band` parity check on fixture baselines under `baselines/round*/`.
- **Leader internal consistency:** Continue dedup-by-ID pattern for any new per-CSSM concat sheets (AP/CP/AB/TAC).
- **Scope banner honesty:** Extend `_r112_scope_kinds` when adding new partial-data warning types.
- **Comprehensive customer universe:** ACC headline count vs title-page coherence — R116/R118 fixed secondary DSM; re-verify on live VPN after roster changes.
- **Formatter audit sweep:** Replicate R38.2 empty-DataFrame guard pattern across `compact_report_formatter.py`, `executive_intelligence_formatter.py` (R66 B13 was Leader-only).

### P1 — Ask AI

- **Live CircuIT eval recordings:** Synthetic eval is 100% ceiling; run `MOCK_CIRCUIT_MODE=record` for real scorecard uplift measurement.
- **Case-search recall:** R127 widened evidence; validate on VPN with "find the eDiscovery case" style queries.
- **Corpus freshness UX:** Surface `dense_rows_remaining` and `last_successful_update_at` more prominently when hybrid degrades to lexical.
- **Streaming mode for 500+ customer portfolios:** Honor `STREAMING` disclosure; never fabricate per-customer risk in Ask AI.

### P2 — Architecture and maintainability

- **Flat module tree (~60 root `.py` files, ~32.6k-line `app_simple.py`):** `REPO_STRUCTURE_PLAN.md` exists — any package migration is high-risk; prefer surgical extractions.
- **Ruff cosmetic rules (UP/I001):** ~1000+ deferred modernizations — separate PR, not mixed with behavior fixes.
- **DecisionOps merge decision:** If product wants Decision Intelligence back, cherry-pick from `decisionops-backup-2026-08-03` onto current `main` — do not revert WxCC retirement or AP scope contracts.

### P2 — Security and compliance

- **Token hygiene:** Never paste PATs in chat; rotate if exposed.
- **Loopback default preserved:** Don't widen bind without `ADOPTIQ_BIND_PUBLIC=1`.
- **Corpus crypto:** Document "ephemeral plaintext, scrubbed on clean exit" — never claim "never on disk."

### P3 — Observability

- **Grounding diagnostics:** `/api/grounding-diagnostics/<id>` exists — build operator dashboard for rejection root-cause trends.
- **Phase timings in jobs panel:** Already wired — use for diagnosing slow Comprehensive runs.
- **Admin audit scoring:** R139 made honest (no false-green for unimplemented checks) — extend checklist as new quality dimensions land.

---

## How to work in this repo

1. Read **`CLAUDE.md`** + latest **`QUALITY_AUDIT.md`** handoff (Round 145).
2. Make **smallest correct change**; add regression test; run narrow pytest then `make verify`.
3. Mark changed lines with `# Round N` comment for audit grep.
4. Write handoff to **`QUALITY_AUDIT.md`** at session end (template in `.cursor/rules/session-handoff.mdc`).
5. Commits: trailer `Made-with: Cursor` or `Made-with: Claude ...`.
6. Branch model: machine branches (`mac-sync-*`, `pc-sync-*`) → integrate to `main` after verify + build smoke.
7. **Never** skip/weaken tests to go green. **Never** recompute canonical counts in formatters.

---

## First tasks for the next agent (recommended)

1. Confirm local `main` matches remotes: `git log -1 --oneline` → expect latest on `main`.
2. Run `make verify` — establish the current floor (expect 6506 passed / 6 skipped / 6 deselected before new tests).
3. If changing report logic: read hot spots from Round 140 handoff (`drop_provenance_rows`, `_collapsed_tac_df`, `_scope_action_plans_for_report`).
4. If shipping: follow `CURSOR_MAC_BUILD_INSTRUCTIONS.md` §9.7 (bake → smoke → four-report harness → r114 audit → soak).
5. If unblocking Windows: execute PC Build 109 checklist in `BRANCH_WORKFLOW.md`.

---

## Key file paths

| Path | Role |
|------|------|
| `CLAUDE.md` | Invariant bible |
| `QUALITY_AUDIT.md` | Round-by-round audit journal + handoffs |
| `HANDOFF_PROMPT.md` | This document — paste into new AI sessions |
| `README.md` | Operator-facing release notes |
| `CURSOR_MAC_BUILD_INSTRUCTIONS.md` | Mac bake/acceptance gate |
| `scripts/r114_audit_reports.py` | Fail-closed DOCX/XLSX audit |
| `scripts/run_report_soak.py` | 2h stability soak |
| `tests/ask_ai_eval/` | Offline Ask AI eval (excluded from default pytest) |
| `customer_aliases.defaults.json` | Bundled alias groups |
| `team_config.json` | Manager/CSSM roster SSoT |

---

**End of handoff prompt.** Paste the content above the "End of handoff prompt" line into a new session, then state your specific goal.
