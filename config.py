# AdoptIQ Executive Analyzer - Configuration
# All secrets and DB credentials come from environment variables (.env). No hardcoded credentials.
# Version and build - updated by build scripts (macOS/Windows) before packaging.
ADOPTIQ_VERSION = "1.0.4"
# Round 38 / Phase 5: Build12 ships the leader-report
# `csone missing or empty` fix.  Pre-Round-38 the leader worker
# called the data-source validator BEFORE loading the CSOne file,
# and the OneDrive autodiscovery fallback would set
# csone_file_provided=True against an empty placeholder
# DataFrame, hard-aborting the report with "csone missing or
# empty" before the worker ever opened the file.  Round 38 splits
# the leader path into a two-pass design (snowflake/team_subs
# early; csone after the file is loaded) and distinguishes
# explicit uploads (fail loud on empty) from OneDrive
# autodiscovery hits (soft-fail with a partial_data_warning).
#
# Round 37's Build11 admin-console fixes (Start Server tile,
# OneDrive sync surfacing, SharePoint sub-block removal,
# Re-index now button) ship in this build too.
#
# Round 38.1 / Build13 ships a tiny duplicate-launch UX fix:
# the user reported the .app "just bounced and would not open"
# but the actual ``adoptiq.<pid>.log`` showed the FIRST launch
# was healthy and serving traffic; the symptom came from a
# SECOND double-click while the first instance was still on
# port 5151.  The previous behaviour was to spawn an osascript
# ``display dialog`` that opened *behind* other windows, then
# silently exit after a 15s timeout.  Build13 adds a tiny HTTP
# probe of 127.0.0.1:<port>/ -- when the existing listener
# returns AdoptIQ-marked HTML we just ``webbrowser.open`` to
# the running instance and exit cleanly, no dialog.  And when
# the dialog IS shown (rare non-AdoptIQ port collision) we
# now ``tell application "System Events" to activate`` first
# so it surfaces to the front.  No boot-order, corpus, or
# Round 38 leader-report changes ship in this build.
#
# Round 38.2 / Build14 ships a one-line indentation fix in
# ``leader_report_generator._compute_customer_health`` plus a
# full audit of the file's 29 ``if not df.empty:`` sites.
# The user re-tested the leader report against Build13 and hit
# ``KeyError: '_bu_disp'`` deep in Document Generation: the
# ``_bu_disp`` transient column was assigned INSIDE an
# ``if not stalled.empty:`` block but accessed UNCONDITIONALLY
# on the next line, raising for any CSSM whose open APs were
# all <=30 days old.  The bug was masked pre-Round-38 because
# the leader report aborted at validation when CSOne was
# empty/missing, so Document Generation rarely ran on real
# data.  Round 38's two-pass fix correctly let the report
# through and surfaced the latent indentation issue.  Build14
# moves the for-loop INSIDE the guard.  The Phase 2 audit
# verified 28 of the file's 29 if-not-empty sites already use
# the correct guard pattern; only the one site needed fixing.
# No boot-order, corpus, validation, or Round 38.1 duplicate-
# launch changes ship in this build.
#
# Round 39 / Build15 ships the corpus-crypto self-heal fix.  Every
# user upgrading from a prior build hit
# "Last run failed (crypto) -- authentication tag mismatch (wrong key,
# tampered ciphertext, or sentinel changed)" because each bake mints a
# fresh sentinel and the previous install's encrypted DB sat on disk
# undecryptable.  The Round 35 install path was strictly one-shot
# (``if user_db.exists(): return None``) so the bundled snapshot was
# never copied over the broken file.  Round 39 adds a probe-decrypt
# step: a healthy corpus is left alone (Round 35 idempotency
# preserved), a broken corpus is preserved as ``<name>.broken-<utc>``
# (single rolling backup, ~280 MB cap) and the bake snapshot is
# reinstalled in place.  Defense in depth: the bake script now does a
# decrypt round-trip self-test so an internally inconsistent bake
# cannot reach users; ``/api/corpus/reset`` (with admin proxy
# ``/corpus_reset`` and user-facing alias ``/api/intel/reset``) and a
# Reset corpus button on the analyze panel give the user a manual
# escape hatch when self-heal cannot run (no bake bundled, e.g.
# dev-checkout build).  32 new tests pin every branch.
#
# Round 39 / Phase B / Build16 ships the leader-report accuracy
# wave triggered by the Brian Frazier 90d audit (two real reports
# in ~/Downloads/).  Phase B addresses one catastrophic accuracy
# bug, three "internally inconsistent numbers" bugs, plus 200+
# leaks of raw Snowflake error strings and dev-phase markers into
# customer-facing text.  Concretely:
#   * TAC cases are now joined to CSSMs via the authoritative
#     ``SUBSCRIPTION_ID`` (with ``ACCOUNT_ID_C`` and exact
#     normalized customer name as second/third tier) instead of
#     a 2-word name-overlap fuzzy matcher that double-counted
#     ERIE INSURANCE GROUP <-> FARMERS INSURANCE GROUP and
#     overstated Angelica's 63 by ~5x.
#   * "Total Activities" is unified across all three writers
#     (Team Activity Summary, per-member Activity Summary, and
#     the Activity Counts Cross-Check) to the canonical
#     ``cm.ACTIVITIES_MODE_FULL`` formula (AP+AB+CP+TAC+BEMS).
#   * The hard-coded "requires immediate attention with high
#     volumes ..." narrative branch is replaced with rate +
#     absolute-floor health assessment so a low-volume CSSM
#     (e.g. William Phillips: 0 ABs, 5 TAC, 2 customers) gets
#     "manageable load" rather than the boilerplate alarm.
#   * Snowflake ``section_errors`` are sanitized through
#     ``_sanitize_snowflake_error`` (strips error codes, trace
#     IDs, ``Round N / Phase X.Y`` markers, ``__C`` column
#     identifiers, and SQL bodies) and rendered as a single
#     friendly line.  The validator now drops Data Quality
#     Score and flips overall_status to DEGRADED when section
#     errors fired during the run.
#   * ``enhanced_snowflake_insights`` queries gracefully degrade
#     when the Snowflake schema dropped a column
#     (``CISCO_TIER_RANKING__C``, ``ARR_AMOUNT``, ``SUBJECT_C``):
#     missing non-critical columns become NULL, missing critical
#     columns short-circuit to a typed empty-result row.
#   * Historical-context corpus rendering: SharePoint legacy
#     paragraph removed, ``status?`` / ``sev?`` placeholders
#     replaced with em-dashes, prior cases deduped by case
#     number (most-recent-wins), and float case numbers coerced
#     to int-shaped strings.
#   * Per-account polish: severity/category counts split by
#     record type so AB string-severity isn't averaged with
#     numeric TAC priority, empty "Technology Assignment
#     Breakdown" heading is dropped, account names with ``__``
#     separators are rendered with comma-space via the new
#     ``data_normalization.normalize_for_display`` helper, and
#     ``Sheets_Written`` in Excel ``Report_Info`` correctly
#     includes the Report_Info sheet itself.
# Seven new ``tests/test_round39_*`` files (88 new asserts) pin
# each fix.  No boot order, corpus self-heal, or Round 38.x
# changes ship in this build.
#
# Round 40 / Build17 ships the per-customer TAC drilldown fix
# triggered by the Brian Frazier 90d audit (1777421123).  Build16's
# Phase B fix correctly attributed TAC cases to CSSMs at the
# team-summary level via SUBSCRIPTION_ID, but the per-customer
# drilldown (rendered by ``_add_customer_summary_with_sources``)
# kept using an exact-normalized customer-name compare against the
# CSOne ``Customer Name: Customer Name`` column.  Verified
# production data shows the team roster (Snowflake ``BU_NAME``)
# and CSOne consistently disagree on suffixes -- e.g.
# ``FARMERS INSURANCE GROUP US`` (roster) vs
# ``FARMERS INSURANCE GROUP`` (TAC),
# ``WINTRUST FINANCIAL CORPORATION US`` vs ``WINTRUST FINANCIAL``,
# ``NATIONAL GRID PLC US`` vs ``NATIONAL GRID``,
# ``NYU MEDICAL CENTERS`` vs ``NYU MEDICAL CENTER``.  The compare
# returned 0 rows for ~75% of customers (46 of 61 per-customer
# tables in the audited docx) so the per-customer "TAC Cases" cell
# collapsed to 0 even when the CSSM-level total was correct.
# Build17 promotes the per-customer drilldown to the same
# three-tier authoritative join Phase B uses at the team level:
# (1) SUBSCRIPTION_ID lookup against the customer's subscriptions,
# (2) ACCOUNT_ID_C fallback, (3) exact normalized customer name
# (preserved so customers with matching names like
# ``ZURICH NORTH AMERICA`` still resolve when the subscription
# roster lacks coverage).  CSOne ``.0`` float-id suffixes are
# stripped before SUBSCRIPTION_ID compare (mirrors Phase B
# line ~1688).  ``tests/test_round40_per_customer_tac_subscription_join.py``
# (6 asserts) pins the FARMERS suffix-drift case, sibling-sub
# leak prevention, ACCOUNT_ID_C fallback, name-fallback, the
# ``.0`` float strip, and the source-level Round 40 marker.
# Also corrects the Phase B handoff in ``QUALITY_AUDIT.md``: the
# original "team total drops 434 -> ~383" claim was a misread; the
# prior fuzzy matcher mis-attributed individual cases but did NOT
# duplicate them (sum-of-CSSM = 434 = unique TAC rows both pre-
# and post-fix).  Phase B's effect is redistribution, not de-dup
# (Angelica 63 -> 13, Samuel 132 -> 55, Jeffrey 57 -> 124).
# No boot-order, corpus, validator, or admin-console changes
# ship in Build17.
#
# Round 41 / Build18 ships four leader-report rendering accuracy
# fixes triggered by the Brian Frazier 90d audit (1777423525).
# The audit confirmed Round 39 / Phase B + Round 40 / Build17
# fixes were live and working (per-customer TAC drilldown
# populated, no raw SQL errors in body, Sheets_Written = 11),
# but surfaced four NEW accuracy bugs that survived prior rounds:
#   * ``Tier: None`` rendered for every account block (50/50
#     occurrences).  Root cause: ``first_acct.get(key, 'N/A')``
#     only substitutes the default when the key is absent --
#     a present-but-NULL value (Snowflake NULL projected via
#     Phase B's ``_resolve_columns``) renders as the literal
#     string ``"None"``.  Build18 routes Tier and Renewal Risk
#     through a ``pd.notna`` + ``or 'N/A'`` chain.
#   * ``<Customer> - None (Status: ...)`` for AP/AB body
#     bullets (344 occurrences).  Same antipattern in the
#     High-Severity Barriers and All Action Plans rendering
#     paths; SUBJECT_C / SEVERITY_C / STATUS_C now use the
#     same NULL-safe form.  Customer Pulse rendering already
#     used the correct ``or``-chain pattern (no change).
#   * ``"requires immediate attention"`` boilerplate fired for
#     all 9 of 9 CSSMs, including Angelica's 0.3 AB/customer,
#     1.2 TAC/customer portfolio.  Round 39's tightened
#     threshold ``(AB >= 10 AND rate > 1.0) OR (TAC >= 10 AND
#     rate > 0.5)`` was still too permissive.  Build18
#     tightens to ``(AB >= 15 AND rate >= 3.0) OR (TAC >= 30
#     AND rate >= 5.0)`` AND inserts a NEW intermediate
#     ``"elevated activity"`` tier between generally-healthy
#     and immediate-attention so mid-volume CSSMs (Angelica,
#     Samuel, Arpit on the audited dataset) get an honest
#     "warrant close monitoring" framing.
#   * ``__`` separators leak in body bullets (4 occurrences of
#     ``TRIBUNAL...__GOBIERNO...__MX``).  Round 39 / Phase 4.4
#     wired ``normalize_for_display`` into the customer-summary
#     heading but missed the AB / AP / CP body bullets and the
#     per-customer Snowflake-insights heading.  Build18 closes
#     the gap.
# ``tests/test_round41_leader_render_null_safe.py`` (6 asserts)
# pins the four fixes; ``tests/test_round39_narrative_grounded.py
# ::test_high_volume_only_when_absolute_floor_and_rate`` was
# updated to use the new threshold (12 AB / 5 customers ->
# 18 AB / 5 customers; rate 3.6 trips the new floor cleanly).
# No SSoT module changes, no upstream Snowflake query changes,
# no boot-order / corpus / validator / admin-console changes
# ship in Build18.  Compact / executive formatters likely have
# the same NULL-leak antipattern -- tracked as a follow-up
# round.
#
# Round 42 / Build19 ships the compact-validator crash fix plus
# six demo-polish items triggered by the user-reported
# ``Portfolio metric mismatch: total_barriers does not match
# normalized adoption barriers.`` ValueError on a build-18
# Compact run for "All Managers / All Contact Center / 90d"
# (2026-04-28).  Phase 1 root cause: ``report_consistency.py``
# computed ``ab_count = _safe_count(ab_df)`` (raw rowcount, 71)
# while ``canonical_metrics.build_portfolio_metrics`` set
# ``portfolio_metrics["total_barriers"]`` via
# ``count_total_barriers(ab_df)`` (Round 25 / Phase F.1
# distinct-ID dedupe, 67) -- mismatch on every dataset where
# barriers fan out per-assignee, which is essentially every
# real run.  Six additional demo-polish items audited from the
# four reports the user attached:
#   * Phase 3: ``app_simple.run_leader_report_generation``
#     called ``cm.build_portfolio_metrics`` with two kwargs
#     (``customer_subs=``, ``customer_pulse_df=``) that don't
#     exist on the canonical signature -- ALWAYS raised
#     ``TypeError`` swallowed at ``logger.debug``, leaving
#     ``_leader_portfolio_metrics = None`` and silently
#     bypassing every PM parity gate compact / EI / renewal
#     enforce.  The ``except`` is also promoted to
#     ``logger.warning`` so future signature drift surfaces.
#   * Phase 5: 77 raw ``ESA_C360_*`` Snowflake table identifiers
#     and 33 raw ``ACCOUNT_ID_C`` / 11 raw
#     ``RENEWAL_RISK_CATEGORY`` column names in the leader
#     Word "Source Verification" tables (rendered per-CSSM,
#     so 11 CSSMs ~= 363+ leaks per run) replaced with friendly
#     business labels (CSOne Tasks feed, Customer Pulse feed,
#     Support Cases feed, etc.).
#   * Phase 6: tiny ``_strip_markdown_chrome(text)`` helper
#     applied to the AP / AB / CP body-bullet ``add_run`` sites
#     so leftover Markdown emphasis (``**Classic Calabrio***``)
#     in CSOne titles renders as plain prose, not literal
#     asterisks.  Preserves ``snake_case`` identifiers and
#     legitimate single asterisks in punctuation.
#   * Phase 7: comprehensive Word missed the ``Partial Data
#     Warning`` banner (3 persisted entries on the audited
#     run, ZERO banners) because ``snowflake_prefetch`` writes
#     warnings directly to ``analysis_status.json`` BEFORE the
#     local ``partial_data_warnings`` list is initialized.
#     The ``_r23_ctx`` build now harvests the persisted entries
#     and merges them in idempotently right before
#     ``executive_intelligence_formatter`` evaluates the
#     ``if partial_data_warnings:`` banner branch.
# Phase 2 + Phase 4 add four new regression tests (validator
# parity on the 71/67 reference shape; signature parity on the
# leader path).  No SSoT module changes, no upstream Snowflake
# query changes, no boot-order / corpus / admin-console changes
# ship in Build19.  Compact / executive formatters' NULL-leak
# antipattern from Build18 follow-up still tracked as deferred.
#
# Round 43 / Build20 ships the demo-stability bundle triggered by
# the user-reported failures on three of four report types in the
# Build19 demo dry-run (2026-04-28):
#   * Comprehensive: STILL crashed with the SAME
#     ``Portfolio metric mismatch: total_barriers does not match
#     normalized adoption barriers.`` error -- Round 42 / Phase 1
#     hardened the validator's ``ab_count`` to use
#     ``count_total_barriers`` (distinct ID, 68) but the
#     COMPREHENSIVE path at ``app_simple.py:12894-12897``
#     hand-rolled ``portfolio_metrics`` with ``len(_ab)`` (raw
#     rowcount, 72), so the post-Round-42-Phase-1 validator
#     blocked every comprehensive run on a multi-assignee dataset.
#     Phase 1 swaps the three offending values
#     (``total_barriers``, ``total_cases``, ``bems_count``) to
#     canonical helpers operating on the SAME frames the validator
#     sees so the two sides agree by construction.
#   * Compact: crashed at "Excel Report Generation" (90%) with
#     ``Worksheet autofilter range 'A2:F53' overlaps previous
#     Table autofilter range 'A2:F54'`` -- ``apply_excel_polish``
#     adds a Table whose declared range carries an implicit
#     autofilter, then the legacy ``worksheet.autofilter(...)``
#     call at L9156 declares a SECOND autofilter (off by one
#     because ``len(df_clean)`` excludes the header row).
#     Phase 2 captures the polish return dict and skips the legacy
#     call when ``table_added`` is True.
#   * Customer Renewal: crashed at "Renewal Risk Analysis" (74%)
#     with ``Portfolio metric mismatch: total_customers=188 (Word
#     headline) != 70 (canonical AB ∪ CSOne ∪ Pulse universe).``
#     The renewal call at L11719 was passing
#     ``extra_customer_frames=_ren_extra_frames`` and
#     ``account_to_customer=_ren_account_to_customer`` to
#     ``cm.build_portfolio_metrics``, widening the headline to
#     188 (subscriptions universe) while the validator's Round 25
#     / Phase A enforcement keeps total_customers narrow at 70.
#     Phase 3 drops both kwargs from the headline call site (the
#     validator below still receives them for per-section linkage).
#     Phase 4 applies the same fix to the leader path (which
#     emitted an analogous warning at L4129 of the demo log).
#   * Debug gap: every error in ``analysis_status.json`` carried
#     only the message "Analysis failed.  Please check the Admin
#     page for details." with NO traceback, NO exception class --
#     the actual stack lived in ``~/.adoptiq/adoptiq.<pid>.log``
#     and required a deep log archaeology to triage.  Phase 5
#     auto-attaches ``traceback.format_exc()`` (truncated to 8 KB)
#     under the ``error_traceback`` key whenever
#     ``update_analysis_status`` is called inside an active
#     ``except`` block with ``status='error'``.  Phase 6 emits a
#     structured ``[CONSISTENCY] PM drift key=<key> portfolio=<v>
#     canonical=<v>`` log line BEFORE every PM-mismatch
#     ``errors.append`` so the next failure is one
#     ``grep '[CONSISTENCY] PM drift'`` away from the failing key
#     plus both sides of the comparison.
# Phase 8 adds a meta-test that fails CI if any future PM
# literal-dict assignment in ``app_simple.py`` reverts to a raw
# ``len(...)`` source for the canonical-helper-sourced keys, or if
# any ``cm.build_portfolio_metrics`` call outside the compact
# allow-list passes ``extra_customer_frames=``.  Six new regression
# tests pin the four crash fixes plus the two debugging additions
# (~2961 total tests, +6 over Build19).  No SSoT module changes,
# no upstream Snowflake query changes, no boot-order / corpus /
# admin-console changes ship in Build20.  Compact / executive
# formatters' NULL-leak antipattern follow-up still tracked.
#
# Round 44 / Build21 ships the demo-accuracy bundle triggered by the
# user's Build-20 walkthrough of four reports + the admin console
# (2026-04-28).  The walkthrough confirmed Round 43's four crash
# fixes are live (all four reports completed successfully) but
# surfaced one P0 numeric/rendering accuracy bug, four P1 director-
# facing chrome leaks Round 42 missed, and one P2 admin-console
# tile defect.  Concretely:
#   * Phase 1: Compact "Days Open" column rendered literal "nan" in
#     100% of the 20 sampled lifecycle rows -- even rows whose
#     ``open_date`` and ``closed_date`` were both populated and
#     parseable.  Root cause:
#     ``executive_intelligence_formatter.py:913`` did
#     ``str(row.get('open_age_days', 'N/A'))`` and
#     ``str(float('nan')) == 'nan'``.  Phase 1 introduces a
#     ``_format_open_age_days(row)`` helper that backfills from
#     open/closed dates and never emits the literal string ``"nan"``
#     (returns the em-dash ``"\u2014"`` when nothing parses).
#   * Phase 4: Round 42 / Phase 5 friendlied the *Comprehensive
#     Data Source Summary* table headers but missed two leader Word
#     paragraph sites at ``leader_report_generator.py:7008/7018/7041``
#     (``add_paragraph(f"  Source: {source['table']} ...")``).  The
#     audited Build-20 leader artifact rendered the raw
#     ``EDW_SALES_ETL_DB.SS.ESA_C360_CUSTOMER_PULSE__C`` 34 times --
#     once per CSSM engagement bullet.  Phase 4 introduces a
#     ``_friendly_source_label(table_id)`` map and applies it at
#     all three sites.
#   * Phase 5: Renewal Word source-citation italics at
#     ``app_simple.py:9981`` and ``:10417`` rendered raw
#     ``[Field(s): SEVERITY_C, AB_STATUS_C, ...]`` and
#     ``[Field(s): PULSE_RATING__C, COMMENTS__C, ...]``.  Phase 5
#     swaps to ``Severity, Status, Created/Closed dates`` and
#     ``Pulse Rating, Comments, Created/Closed dates``.
#   * Phase 6: Compact + EI source-citation paragraphs had the
#     same ``_C``-suffixed leak (``BU_NAME, customer_name,
#     ACCOUNT_ID_C`` for Total Customers; ``Severity,
#     PULSE_RATING__C, CREATEDDATE`` for severity provenance).
#     Phase 6 swaps to ``Customer Name, Account ID`` and ``Pulse
#     Rating, Created Date``.
#   * Phase 7: Round 42 / Phase 6 wired ``_strip_markdown_chrome``
#     at the AB / AP / CP body-bullet sites only; the audited
#     Build-20 leader artifact still rendered ``**Classic
#     Calabrio***delete old report - Calabrio WFO# 00179474`` in
#     two leader Word table cells (T73R20C2 + T75R36C2) and one
#     renewal Word paragraph (per-customer TAC bullet).  Phase 7
#     extends the strip to those three sites; the renewal site
#     re-imports the helper from ``leader_report_generator`` under
#     the local alias ``_r44_strip_markdown_chrome`` to keep the
#     strip semantics identical across reports.
#   * Phase 8: Admin Console "Currently Running Reports" tile
#     rendered a red "n/a -- main app unreachable" banner even
#     when the main app was up.  Root cause:
#     ``enhanced_admin_dashboard_v2.py:125`` captures
#     ``MAIN_APP_URL = os.environ.get('ADOPTIQ_MAIN_URL',
#     'http://localhost:5151')`` at IMPORT time, but
#     ``app_simple.py:157-161`` eagerly imports the admin module
#     BEFORE ``app_simple.py:210`` writes
#     ``os.environ['ADOPTIQ_MAIN_URL']`` -- so the constant
#     captures the unset default and the two
#     ``requests.get(f'{MAIN_APP_URL.rstrip("/")}...')`` sites at
#     L2988/L3002 fetch the wrong port.  Round 37 / Phase 1 fixed
#     the socket-probe path (``_main_app_host_port`` re-reads the
#     env per call); Phase 8 introduces the analogous
#     ``_live_main_url()`` helper and routes both HTTP fetches
#     through it.
# Phases 2 + 3 of the original plan (leader Word/Excel headline
# off-by-1; comprehensive vs compact AB drift) were CANCELLED as
# false positives -- the Build-20 audit miscounted Excel data
# rows by treating ``max_row - 1`` as the data row count when the
# leader sheets have BOTH a title row AND a header row (2 chrome
# rows).  Re-validation against the actual Build-20 artifacts
# confirmed Word headlines (AB=68, TAC=344, CP=87) match Excel
# data-row counts exactly, and comprehensive ``AB_Detail_All``
# (no title row, 73 raw rows = 1 header + 72 data) and compact
# ``All_Adoption_Barriers`` (1 title + 1 header + 72 data) BOTH
# contain 72 data rows for the same scope.
# 30 new regression tests under ``tests/test_round44_*.py`` pin
# the six implemented fixes (~2991 total tests, +30 over Build20).
# No SSoT module changes, no upstream Snowflake query changes, no
# boot-order / corpus / validator changes ship in Build21.
#
# Round 45 / Build22 ships the "demo accuracy bundle 2" triggered
# by the user's Build-21 walkthrough on 2026-04-28.  Build-21 was
# never installed on the user's machine when the walkthrough hit:
# the live process was Build-20 from ``~/.Trash/AdoptIQ.app``, so
# the chrome leaks audited that night were a 50/50 mix of
# already-fixed (Round 44) and never-fixed (Round 45 below)
# defects.  Phase 0 of Round 45 closed the meta-issue by killing
# the Trash process and installing the Build-21 DMG before
# scoping new code, then a code-level audit (no second user run)
# confirmed which Build-20 leaks Round 44 actually covered vs
# which still required new code.  Concretely:
#   * Phase 3 (P0): Compact CSOne autodiscovery gap.  The compact
#     path in ``start_compact_analysis`` was the ONLY report
#     entrypoint that did not autodiscover the latest CSOne file
#     from the configured OneDrive folder when the user did not
#     attach one (the comprehensive path at L2480 and the leader
#     path via Round 38 both did).  The compact worker also
#     treated CSOne as strictly-required against an empty
#     placeholder DataFrame, so even when autodiscovery later
#     succeeded, the validator still aborted at "csone missing or
#     empty".  Phase 3 mirrors the Round 38 leader fix: the
#     endpoint splits ``csone_file_explicit`` (uploaded) vs
#     ``csone_file_autopicked`` (autodiscovered) provenance and
#     persists ``csone_file_was_uploaded`` on the status dict;
#     the worker uses a two-pass validator that hard-fails only
#     when the user explicitly uploaded a CSOne file that scoped
#     to empty, and otherwise appends a partial-data warning and
#     proceeds.
#   * Phase 4 (P0): Validation failures rendered the generic
#     "Data validation failed - see logs for details" banner with
#     no indication WHICH source failed or WHAT to do.  Phase 4
#     adds ``_r45_render_validation_remediation_message`` which
#     names the missing source, the autodiscovery path that was
#     attempted (or "not configured"), and a one-line remediation
#     hint.  Surfaced in ``analysis_status['message']`` for the
#     UI banner; ``analysis_status['error']`` keeps the Round 27
#     H1 generic sanitized string so JSON consumers do not break.
#   * Phase 5 (P1): Excel headers leaked raw Snowflake schema
#     names (``BU_NAME``, ``ACCOUNT_ID_C``, ``SEVERITY_C``,
#     ``OPEN_DATE_C``, ``CLOSED_DATE_C``, ``COMMENTS__C``,
#     ``PULSE_RATING__C``, ...) across all three reports.  Phase 5
#     introduces ``_FRIENDLY_HEADER_LABELS`` in
#     ``report_export_schema.py`` and applies the rename as the
#     LAST step of ``apply_export_schema`` so curated allowlists
#     and internal sort/filter logic still key on the raw column
#     names, but every visible workbook header text is friendlied.
#   * Phase 6 (P1): The comprehensive narrative LLM prompt at
#     ``adoptiq_backend.py:10682`` instructed the model to write
#     "None detected" while the same prompt at L10657 instructed
#     "data unavailable" -- the LLM picked both, producing 8 mixed
#     leaks per report.  Phase 6 unifies the prompt to "data
#     unavailable" and adds ``_r45_clean_llm_chrome`` as a
#     belt-and-suspenders post-process that converts any residual
#     "None detected" / "(SP-ID: None detected)" in the generated
#     narrative.
#   * Phase 7 (P1, conditional): Confirmed Round 44 / Phase 4 caught
#     all three leader EDW emit sites (L7065/7077/7104).  The only
#     remaining ``EDW_*`` reference is a Salesforce Lightning URL
#     API name at L5152, not visible text.  No new code shipped
#     in Phase 7; existing Round 44 regression tests still pin.
#   * Phases 8 + 9 (P2): Excel body cells in the renewal
#     ``Customer_Action_Plans`` / ``Customer_Adoption_Barriers``
#     and leader ``Action_Plans`` sheets bled raw markdown
#     ``**bold**`` chrome from CSOne meeting-notes / case-summary
#     fields (4 cells across the audited workbooks) and literal
#     "None" / "nan" / "<NA>" body cells (8 cells).  Phases 8 + 9
#     extend the SSoT in ``report_export_schema.py`` with
#     ``_r45_clean_excel_body_cell`` (strips ``**``/``__`` chrome
#     and coerces None/NaN/literal-None to empty string) and
#     ``_r45_clean_body_columns`` (applies it across the friendly
#     body-text columns Comments, Description, Subject, ...) as
#     the final pass in ``apply_export_schema``.
# 47 new regression tests under ``tests/test_round45_*.py`` pin
# all five code-level Phase changes (3057 passed total, +66 over
# Build21).  Plus a ``__all__`` extension test in
# ``test_round15_excel_columns.py`` to recognise the new public
# helper ``friendly_header``.  ``make verify`` clean (lint,
# bandit HIGH/MED, pip-audit, 3057 pytest pass, 2 skipped).  No
# upstream Snowflake query changes, no boot-order / corpus /
# admin-console changes, no Round 38 leader-report or Round 38.2
# customer-health changes ship in Build22.
#
# Build23 (Round 46 — Report Accuracy Deep Reconciliation):
# Closes the Round 45 Hot Spot #5 deferral.  The compact-report
# fallback path ``app_simple._create_enhanced_compact_report``
# silently dropped ``partial_data_warnings`` because the function
# signature did not accept them and there was no banner-render
# code -- confirmed against the user's
# ``Brian_Frazier_All_Contact_Center_90d_1777438120`` run where
# ``analysis_status.json`` recorded 3 warnings (column-introspection
# block on EDW_SALES_ETL_DB.SS.ESA_C360_CS_TASK__C, and two
# schema-drift entries on customer_pulse and adoption_barriers)
# but the produced docx had zero "Partial Data Warning" text.
# Round 46 / F-COMP-DQ-BANNER threads ``partial_data_warnings``
# through the fallback signature and renders a
# ``⚠ Partial Data Warning`` heading + one bullet per warning
# (dataset / kind / human-readable error tail) before any number
# is shown -- mirroring the banner emitted by the primary path
# in ``compact_report_formatter.create_compact_executive_report``
# and ``executive_intelligence_formatter``.  Both call sites of
# the fallback in ``app_simple.py`` were updated to pass the
# ``partial_data_warnings`` from the runtime context.
#
# Phase 2 of Round 46 also performed a deep reconciliation audit
# of every numeric claim in the user's three Build22 reports
# (renewal portfolio All_Managers, leader Brian_Frazier, compact
# Brian_Frazier) against the matching xlsx data dumps.  Result:
# zero P0 / zero false customer-level claims found -- the AI
# grounding gates from Round 27 (R27-AI-GATE-CUSTOMER) are
# correctly substituting placeholders for ungrounded narrative,
# top-10 lists match xlsx universe, headline counts (total
# customers / active barriers / support cases) match the
# underlying frames.  Only finding above informational was the
# F-COMP-DQ-BANNER P1 above.
#
# 3 new regression tests in
# ``tests/test_round46_compact_fallback_renders_partial_data_banner.py``
# pin: (a) the fallback signature accepts the new kw-arg with
# ``None`` default, (b) the banner renders with one bullet per
# warning when the kw-arg is non-empty, (c) no banner appears on
# the happy path so existing reports stay clean.  ``make verify``
# clean (3060 pytest pass / 2 skipped, +3 over Build22).  No
# upstream Snowflake query changes, no boot-order / corpus /
# admin-console changes ship in Build23.
#
# Round 47 / Build24 ships four demo-blocking P0 fixes the Build23
# Brian Frazier audit (run IDs 1777445562 / 1777445582 / 1777445600)
# captured:
#
# 1. F-COMP-AI-WITHHOLD-EXPLOSION (R47-AI-GATE-COMMON +
#    R47-AI-GATE-COUNTRY): the comprehensive customer storyboards
#    were emitting 32 ``AI insight could not be grounded`` placeholders
#    (vs the historical ~5 ceiling) because the validator's common-
#    reference number set jumped from 10 to 14 (so "12 months" / "the
#    past 12 months" were always rejected) and the entity allow-list
#    compared candidates exact-equal to allowed names that carried a
#    trailing 2-letter ISO country-code token (so
#    ``"Equitable Holdings LLC"`` mismatched
#    ``"EQUITABLE HOLDINGS LLC US"``).  Diagnosis was anchored on
#    ``adoptiq.20569.log`` 2026-04-29 01:54-01:58 (21 of 32 rejections
#    cited ``'12'``, 7 cited country-stripped invented_entity).  Fix
#    expands ``_COMMON_REFERENCE_NUMBERS`` to cover small ints 0-31
#    (calendar / months / quarters / day-of-month), common multiples
#    of 5 / 10 through 365, plus the common fractional percentages
#    1/3 1/4 2/3 etc., and adds country-code-tolerant entity matching
#    via a curated 50-entry ISO suffix list + symmetric substring
#    fallback (with a 6-character floor to prevent ``"Inc"`` matching
#    every Inc-suffixed customer).  35 new regression tests pin the
#    behavior; the safety property (arbitrary 5-digit numbers,
#    truly-invented entities) still trips the validator.
#
# 2. F-RP-PULSE-DUAL-TRUTH (R47-RP-PULSE-PARITY): the renewal Word
#    body was citing ``Total Customer Pulse Records: 186`` plus 10
#    sample rows while the matching Excel ``Customer_Customer_Pulse``
#    sheet was a Data_Unavailable envelope (``schema_drift:
#    customer_pulse: missing slot(s) rating on a non-empty result
#    (186 row(s))``).  Word now honors ``df.attrs['fetch_error']`` /
#    ``fetch_error_kind`` on the pulse frame and renders the same
#    "data unavailable - reason - see the matching Excel envelope"
#    line instead of the row-count + sample.  2 new tests prove the
#    gate engages when the attrs are set and stays out of the way
#    when the frame is healthy.
#
# 3. F-RP-RISK-DUAL-TRUTH (R47-RP-RISK-PARITY): the renewal Word
#    body advertised a multi-component risk breakdown (Adoption
#    Barriers 28%, Support Cases 27%, Customer Pulse 15%, ...) while
#    Excel ``Risk_Components`` was a single Data_Unavailable row
#    saying ``risk_components were not produced by the renewal
#    analyzer``.  ``_calculate_simple_renewal_risk`` now passes
#    through ``profile['components']`` from the deterministic weighted
#    scorer as ``renewal_analysis['risk_components']``, and the
#    portfolio Excel writer averages per-customer components into a
#    portfolio-mean view when the top-level dict is empty.  3 new
#    tests prove the schema and the aggregator math.
#
# 4. F-COMP-CUSTCOUNT-DELTA-14 (R47-COMP-CUSTCOUNT-PARITY): the
#    comprehensive Word title page + Executive Summary table cited
#    ``Total Customers: 52`` while Excel ``Summary`` said
#    ``Customers in portfolio: 38`` (delta = 14).  Word used the
#    wide ``_get_all_customers_from_all_sources`` universe (AB ∪
#    CSOne ∪ team_subs ∪ every CSConsole frame); Excel used the
#    canonical-narrow ``cm.count_customers(ab, csone, pulse)``.  Word
#    headline + comprehensive ``portfolio_metrics['total_customers']``
#    are now pinned to the same canonical-narrow value Excel uses;
#    the wide value is preserved as ``total_customers_with_extras``
#    for downstream consumers that legitimately need it.  3 new tests
#    pin the property + assert the wide universe is still accessible.
#
# 43 new R47 regression tests; ``make verify`` clean (3103 pytest
# pass / 2 skipped, +43 over Build23).  No upstream Snowflake query
# changes, no boot-order / corpus / admin-console changes ship in
# Build24.
#
# Round 48 / Build25 ships the eight P1 demo-readiness cleanups that
# the Build24 audit flagged:
#
# 5. F-COMP-AB-SUMMARY-VS-DETAIL-4 (R48-AB-DELTA-DISCLOSURE): the
#    comprehensive Excel ``Summary`` sheet showed
#    ``Adoption barriers (total): 68`` while the ``AB_Detail_All``
#    sheet had 72 rows.  The 4-row delta was the well-understood
#    multi-assignee duplication (Round 35 / Phase D), but a reader
#    seeing the two artifacts side-by-side had no way to know the
#    delta was deliberate.  ``build_summary_rows`` now emits an
#    additive ``Adoption barriers (detail rows): 72 (4 multi-
#    assignee duplicates)`` row immediately after the existing
#    ``(total)`` row so the reconciliation is self-explanatory.
#    Existing tests pinning the (total) label still pass; the
#    Round-19 golden-fixture label order was updated to add the
#    new row.  9 new R48 tests pin the disclosure shape.
#
# 6. F-COMP-TAC-LABEL-AMBIGUITY (R48-COMP-TAC-LABELS): two compact
#    LLM prompt templates emitted ``TAC Cases: [Y cases, Z are
#    P1/P2]`` which the Round 47 audit flagged as ambiguous --
#    "TAC Cases" appeared three times in the same paragraph with
#    three different denominators.  Replaced with the canonical
#    pair ``Total Support Cases (90d): [Y]`` + ``Open + critical
#    (P1+P2): [Z] ([W are P2])`` and added inline guard comments
#    referencing the round/defect ID so future template edits do
#    not regress.  5 new R48 tests assert canonical labels are
#    present and ambiguous labels are absent.
#
# 7. F-COMP-BEMS-MD-LEAK (R48-COMP-BEMS-BARE-IDS): five Word
#    renderers (compact x2, executive intelligence, renewal,
#    leader) were emitting BEMS IDs wrapped in markdown brackets
#    (``[BEMS01916938], [BEMS01952872]``).  The brackets are
#    appropriate inside an LLM briefing-book citation but render
#    as literal characters in Word.  Stripped the brackets at the
#    Word-rendering wire only, leaving the briefing-book template
#    untouched (LLM citations still need them).  6 new R48 tests
#    pin bare IDs in Word and bracketed IDs in briefing prompts.
#
# 8. F-RP-MD-LEAK (R48-RP-NO-MD-CHROME): the renewal Word report
#    was rendering customer names with raw markdown chrome leaking
#    in -- e.g. ``ATLANTIA SPA__AEROPORTI DI ROMA SPA__IT`` and
#    other ``__name__`` fragments produced by the upstream LLM
#    naming layer.  Added ``_strip_markdown_chrome`` helper that
#    intelligently strips bold / italic / strikethrough / link /
#    code chrome while preserving word boundaries (so
#    ``SPA__AEROPORTI`` becomes ``SPA AEROPORTI``, not
#    ``SPAAEROPORTI``).  Applied at 7 sites in ``app_simple.py``
#    where customer names are rendered (renewal headers, single-
#    customer summary, subscription analysis).  16 new R48 tests
#    pin the helper and the wire sites.
#
# 9. F-RP-WARNING-COUNT-WRONG (R48-RP-PDW-EXCEL-PARITY): the
#    renewal Excel ``Report_Info`` sheet was hardcoded to emit
#    ``Partial_Data_Warning_Count: 0`` even when two real
#    schema-drift warnings existed (audit baseline run
#    1777445582).  Refactored renewal warning harvest into a
#    local helper that walks every renewal data frame's
#    ``df.attrs['fetch_error']`` annotation, persists the
#    de-duped list onto ``analysis_status[*][
#    'partial_data_warnings']``, and the Excel writer reads from
#    that list.  11 new R48 tests pin the harvest scope, the
#    persist + save_analysis_status() wire, and the Excel
#    Partial_Data_Warning_Count match.
#
# 10. F-RP-PARTIAL-BANNER-MISSING (R48-RP-WORD-BANNER): the
#     renewal Word report had no Partial Data Warning banner --
#     compact / EI / leader reports already had one.  Added the
#     banner to ``_create_simple_renewal_report`` (page 2, after
#     title) and threaded the harvested warnings as a kw-arg.
#     5 new R48 tests pin the banner including a real .docx
#     render verification.
#
# 11. F-COMP-PARTIAL-BANNER-MISSING (R48-COMP-WORD-BANNER):
#     comprehensive Word same gap.  Added
#     ``ExecutiveReportBuilder.add_partial_data_warning_banner``
#     and called it immediately after the title page.  5 new
#     R48 tests pin the method existence + render order +
#     real .docx contents.
#
# 12. F-DV-PULSE-CONTRACT-DRIFT (R48-CONTRACT-ALIASES): root-
#     cause fix for the customer_pulse / adoption_barriers
#     schema_drift escalations the Round 47 banner surfaced.
#     Expanded ``data_contracts.ROW_CONTRACTS`` aliases:
#       * customer_pulse rating slot now also accepts
#         ``Pulse Rating`` (Excel friendly), ``SCORE__C`` /
#         ``SCORE_C`` (historic, documented in ask_ai_grounded.py
#         ~L723), and ``OVERALL_RATING__C`` / ``OVERALL_RATING``.
#       * customer_pulse customer slot also accepts
#         ``CUSTOMER_NAME__C`` (raw), ``Customer`` /
#         ``Customer Name`` (friendly).
#       * adoption_barriers + tac_cases customer slots also
#         accept ``Customer`` / ``Customer Name`` /
#         ``BU_ACCOUNT_NAME`` (post-fetch resolved variants).
#     Negative cases (NO rating column / NO customer column)
#     still escalate to ``fetch_error: schema_drift`` so genuine
#     drift is not silenced.  31 new R48 tests pin every alias
#     and the negative cases; the Round 32 contract-autoremap
#     suite still passes.
#
# 75 new R48 regression tests across the 8 P1 fixes; ``make
# verify`` clean (3178 pytest pass / 2 skipped, +75 over
# Build24).  No upstream Snowflake query changes, no boot-order /
# corpus / admin-console changes ship in Build25.
#
# Round 49 / Build26 closes the two P0 demo-blockers + four P1
# surface cleanups the Build25 re-audit caught.  The Build25
# audit baseline was Comprehensive ``Brian_Frazier_90d``,
# Compact ``All_Managers``, Renewal_Portfolio ``All_Managers``,
# Leader ``All_Managers``.
#
# 13. F-COMP-CONSIST-WIDTH-MISMATCH (R49-A1): the comprehensive
#     report failed at ``validate_report_consistency`` with
#     ``total_customers=38 (Word headline) != 52 (canonical
#     count_customers(ab, csone, pulse))``.  Root cause: the
#     comprehensive call site passed ``customer_universe=
#     all_customers_comprehensive`` (the wide
#     ``_get_all_customers_from_all_sources`` roster of 52)
#     and the validator built ``metrics["total_customers"]``
#     from that wide arg via a synthetic frame -- so its
#     "canonical" reading was 52 while R47-B4's correctly
#     narrowed Word headline + Excel Summary stayed at 38.
#     Round 49 narrows the validator: ``metrics["total_customers"]``
#     is ALWAYS computed from ``count_customers(ab_df, csone_df,
#     pulse_df)`` regardless of whether ``customer_universe`` is
#     passed; the wider count is preserved as ``metrics[
#     "customer_universe_total"]`` for telemetry callers (defect-
#     customer coverage, per-customer narrative passes) that
#     legitimately need the broader iteration roster.  The error
#     string is updated so the displayed "(canonical)" label
#     matches the actual computation.  3 new R49 tests pin the
#     narrow parity + the diagnostic surface.
#
# 14. F-DV-CONTRACT-DRIFT-R49 (R49-A2): three persistent
#     ``schema_drift`` warnings still escalated even after the
#     R48-D12 alias expansion -- (a) ``customer_pulse.rating``
#     because the live Snowflake CSConsole pulse view emits
#     ``CUSTOMER_PULSE__C`` not ``PULSE_RATING__C``; (b)
#     ``adoption_barriers.customer`` because the
#     ``annotate_with_contract`` call ran in
#     ``snowflake_prefetch.py`` on the raw ``C360_CS_TASK_C_VW``
#     frame whose only customer-bearing column is
#     ``ACCOUNT_ID_C`` -- the team_subs merge that materializes
#     ``BU_NAME`` happens later in ``app_simple.py``; (c)
#     ``tac_cases.customer`` because the CSOne export column
#     literal is ``Customer Name: Customer Name`` (Salesforce
#     SOQL self-join relationship label).  Round 49 fixes all
#     three: added ``CUSTOMER_PULSE__C`` / ``CUSTOMER_PULSE`` to
#     ``customer_pulse.rating`` aliases; added
#     ``"Customer Name: Customer Name"`` to ``tac_cases.customer``
#     aliases; extended ``annotate_with_contract`` to self-heal
#     by clearing prior ``schema_drift`` ``fetch_error`` stamps
#     when a re-annotation passes for the same dataset; and
#     wired post-merge re-annotate calls at the four AB merge
#     sites in ``app_simple.py`` (renewal raw / renewal
#     CSConsole / compact / comprehensive) so the
#     ``adoption_barriers`` contract re-evaluates after
#     ``BU_NAME`` is materialized.  3 new R49 test files pin the
#     aliases + the self-heal wire.
#
# 15. F-COMP-BEMS-MD-LEAK-R49 (R49-B1): the R48-D7 sweep cleaned
#     5 Word renderers but missed two more high-traffic
#     surfaces: the compact Critical Trouble Spots renderer
#     was still emitting ``IDs: [BEMS01943186], [BEMS01946483]``
#     (~65 sites in the Build25 reproducer) and the renewal
#     narrative was still emitting ~182 bracketed BEMS
#     references via direct rendering sites in ``app_simple.py``
#     (Defect IDs, BEMS case header) and via LLM-generated
#     narrative that bypassed the bare-ID helper.  Round 49 adds
#     ``report_utils.strip_bems_brackets_from_llm_text`` (a small
#     regex-based stripper for ``[BEMS\\d+]`` / ``[CSC<XX>\\d+]``
#     / ``[CSC\\d+]`` patterns), wires it into the LLM-output
#     contract enforcers in ``executive_report_builder.py``,
#     ``compact_report_formatter.py``, and the renewal narrative
#     fallback in ``app_simple._parse_markdown_for_fallback``,
#     and updates the four direct render sites in renewal to
#     emit bare IDs.  Briefing-book templates remain bracketed
#     (those are explicitly preserved by R48).  4 new R49 tests
#     pin the stripper behavior + the wires + the bare-ID
#     direct sites.
#
# 16. F-RP-COMPOSITE-KEY-BLEED (R49-B2): renewal Excel
#     ``Renewal_Summary`` / ``Risk_Summary`` cells showed
#     ``MARUBENI CORPORATION__JAMAICA PUBLIC SERVICE CO__JM`` and
#     the renewal narrative showed ``ELEVANCE_ELEVANCE HEALTH_US``
#     -- raw Snowflake composite-key strings produced by an
#     upstream merge that joined two name slots with a region
#     suffix.  R48-D8's ``_strip_markdown_chrome`` handles
#     ``__bold__`` markdown but not these structural keys.
#     Round 49 adds ``_normalize_composite_customer_key`` (split
#     on ``__`` -> first segment; or single ``_<REGION>$``
#     pattern -> drop the suffix), wires it ahead of
#     ``_strip_markdown_chrome`` at the 8 renewal-renderer
#     customer-name sites, and applies it to the ``Customer``
#     column of the ``Risk_Summary`` and ``Renewal_Summary``
#     Excel sheet builders so the bleed never reaches the
#     workbook either.  Idempotent and a no-op for normal
#     ``Single Customer Name`` inputs.  10 new R49 tests pin
#     edge cases + idempotency + the wires.
#
# 17. F-RP-AB-RAW-HEADERS (R49-B3): renewal portfolio
#     ``Customer_Adoption_Barriers`` xlsx leaked raw Snowflake
#     ``_C`` headers (``AB_COMPETITOR_C``,
#     ``AB_SOLUTION_ATTEMPT_C``, ``CSDF_SYNC_ID_C``,
#     ``CX_TASK_ID_C``, ``GS_C_360_SUCCESS_PRIORITY_C`` etc.) --
#     259 columns total, most ungoverned -- because the writer
#     routed through ``apply_export_schema`` but no curated
#     allowlist was registered for the
#     ``Customer_Adoption_Barriers`` sheet name (only the
#     comprehensive ``AB_Detail_All`` sheet had one).  Same
#     defect applied to the compact ``All_Adoption_Barriers``
#     sheet.  Round 49 registers
#     ``Customer_Adoption_Barriers``, ``All_Adoption_Barriers``,
#     and ``Adoption_Barriers`` in ``CURATED_COLUMNS`` against
#     the SSoT ``_CURATED_AB_DETAIL_ALL`` projection, and adds
#     ``BU_NAME`` + ``ACCOUNT_ID_C`` to that curated set so the
#     post-projection friendly-rename pass can collapse them to
#     ``Customer Name`` / ``Account ID``.  10 new R49 tests pin
#     curation registration + zero raw ``_C`` survivors + every
#     audit-flagged raw name dropped.
#
# 18. F-COMP-AAG-SCOPE-LABEL (R49-B4): the compact docx
#     At-a-Glance dashboard tile read ``Support Cases: 294``
#     while the per-customer deep-dive used ``Total Support
#     Cases (90d): 36 - Open + critical (P1+P2): 1 (P2)`` for
#     the same portfolio.  Both lines were truthful but read as
#     off-by-scope side-by-side; the tile is portfolio-wide
#     and the deep-dive is per-customer.  Round 49 makes the
#     dashboard tile's scope explicit: table header is now
#     ``Support Cases (90d, portfolio-wide)`` and the metric-
#     source bullet is ``Total Support Cases (90d, portfolio-
#     wide): N``.  5 new R49 tests pin the labels in the source
#     + the rendered docx.
#
# ~35 new R49 regression tests across the 6 fixes (2 P0 + 4 P1);
# ``make verify`` clean.  No upstream Snowflake query changes,
# no boot-order / corpus / admin-console changes ship in
# Build26.
#
# 19. F-COMP-CONSIST-PULSE-THREAD (R50-A1): Round 49 narrowed the
#     validator to ``count_customers(ab, csone, pulse)`` regardless
#     of ``customer_universe``, but the comprehensive call site at
#     ``app_simple.py:13946`` did not pass ``customer_pulse_df=`` so
#     the validator's narrow count fell back to
#     ``count_customers(ab, csone, None)`` and diverged from the
#     Word headline by exactly the pulse-only customer count.
#     Brian Frazier 90d demo (2026-04-29) reproduced as
#     ``total_customers=38 (Word headline) != 28 (canonical
#     count_customers(ab_df, csone_df, pulse_df))``; Dee Kindrick
#     90d as ``30 != 21``.  Round 50 threads
#     ``customer_pulse_df=csconsole_customer_pulse`` into the
#     comprehensive ``validate_report_consistency(...)`` call so
#     the validator's narrow count matches the Word headline by
#     construction.  The leader path at L21307-21316 has done
#     this since Round 6 / Phase 5.8; this brings comprehensive
#     into the same shape.  4 new R50 tests pin source-shape +
#     behavioural parity + regression reproducer.
#
# 20. F-ANALYZE-DARK-THEME-BG-LIGHT (R50-A2): three
#     ``<span class="input-group-text bg-light">`` adornments on
#     the analyze page (Customer Name, Analysis Time Range, CSOne
#     Excel File) rendered as bright-white boxes in the dark theme
#     because Bootstrap's ``.bg-light { background-color:
#     #f8f9fa !important; }`` overrode the shared dark
#     ``.input-group-text`` rule from ``base.html``.  Round 50
#     drops ``bg-light`` from the three adornments so the dark
#     surface (``var(--bg-surface-raised)``) paints correctly.
#     2 new R50 tests pin the dark-theme contract + sanity guard
#     that the spans themselves are not deleted.
#
# 6 new R50 regression tests across the 2 fixes (1 P0 + 1 P1);
# 3268 total green.
# Round 52.1 / Build28 ships the visible Admin Console Reset corpus
# control and hardens the Mac packaging wrapper so the final richer DMG
# is signed and accompanied by build_info.txt in the staging mirror.
#
# Round 52 / Build29 ships the accuracy-fix loop (leader DOCX KPI
# extractor: multi-row 3+col tables now prefer a TOTAL footer instead
# of pairing headers with the first per-CSSM row, fixing the leader
# 14-vs-381 support_cases mismatch), the partial-data-warning trio
# (team_subscriptions contract, blocked-table introspection guard,
# dtype-safe customer-key merge), and the harness residual fixes
# (corpus-context paragraphs no longer leak into the Technology
# canonical; per-scenario DOCX similarity thresholds let the AI-
# narrative-heavy comprehensive report pass strict gates without
# weakening the binding numeric_sim guard for compact / renewal /
# leader).  3351 pytest passing; manifest-pinned baselines under
# ``baselines/round52/`` for repeatable strict 1-pass + multi-iter
# repeatability runs.
#
# Round 52.1 / Build30 ships the live-data drift hardening: the
# strict 3-iter repeatability proof against the round52 manifest
# surfaced iter2 comprehensive numeric_sim 0.7094 (< 0.80) while the
# table-only numeric fingerprint stayed at 1.0 across baseline + 3
# iterations.  Root cause: the AI-generated insights section
# regenerates run-to-run (different bug IDs cited, different
# percentages computed, different TAC case IDs as evidence) and
# pollutes the overall numeric fingerprint.  Tables hold the actual
# Snowflake-derived KPI counts and stay byte-stable.  Build30 adds a
# table-only numeric similarity gate (``_extract_docx_table_text`` +
# ``_numeric_fingerprint`` over table cell text only) as a new noise-
# immune binding signal for real Snowflake data drift; the gate is
# wired into ``compare_docx_against_baseline``, ``RunnerConfig``, the
# CLI (``--min-docx-table-numeric-similarity``, default 0.95), and
# the per-scenario threshold override (comprehensive's overall
# numeric_sim relaxed to informational 0.55).  3371 pytest passing
# (above the round52 floor of 3351); strict 1-pass against the
# round52 manifest 4/4 green with all four
# ``table_numeric_similarity == 1.0``; strict 3-iter repeatability
# 12/12 green.  No upstream Snowflake query, formatter, boot-order,
# corpus, or admin-console changes ship in this build.
# Round 85 / Build 61: shipping-default share URL refreshed to the
# new ``:f:/p/`` guest-pass URL the team owner now uses
# (``https://cisco-my.sharepoint.com/:f:/p/jestory/IgBm46pU_P9aTpkxyEQ13ZgYAT4BhGVKNfcUsN7DA7zkRJI``);
# fundamentally different shape from the legacy
# ``:f:/r/personal/<owner>/...?csf=1&web=1&e=...`` render-link with
# rotating ``e=...`` token (R81 -> R83 -> Build 60).  Guest-pass URLs
# carry no query string so future rotations require a new path
# entirely, not a token swap; the regression guard in
# ``tests/test_round81_sharepoint_url_refresh.py`` (renamed-in-place
# to round85_*) now rejects ALL legacy shapes.  R84's three-tier
# resolver is unchanged: ``corpus_share_url_resolver.get_active_corpus_share_url()``
# still walks settings.json -> env -> config.py default with per-tier
# ``_is_valid_sharepoint_url`` validation.  The R84 analyze-page card
# stays where it is, AND a NEW mirror lands on
# ``templates/preferences.html`` (the R73 Preferences hub) so
# operators can rotate the URL from the documented Preferences
# surface; both cards bind to the same ``[data-corpus-share-url-card]``
# data marker via the existing ``static/js/corpus_share_url.js`` IIFE
# module (idempotent single-querySelector init, one card per page).
# THE R107 PREBAKED CORPUS CONTRACT:
# The bundled DMG ships a prebaked encrypted corpus plus its local
# sentinel under Resources/baked_corpus, so first launch does not depend
# on OneDrive. The URL remains useful only for optional background
# refresh source setup.
# Pinned by the rewritten ``tests/test_round35_corpus_url_hardcoded.py``
# + ``tests/test_round81_sharepoint_url_refresh.py`` + a new
# ``tests/test_round85_url_refresh_and_preferences_card.py`` (~19
# tests across validator, resolver, ``_r83_safe_share_url``,
# preferences-page source-shape, analyze-card regression guard, and
# cross-pin against the R35 + R81 fixtures).
# Round 114 / Build 83: citation de-clutter + Build 82 acceptance audit.
# The Build 82 manual acceptance (Leader screenshot) showed
# ``[Source: AdoptIQ Report Data Sources]`` appended to EVERY numeric
# cell of EVERY member row in multi-column count matrices (Team Activity
# Summary, Individual Team Member Performance) -- the read-only audit
# script ``scripts/r114_audit_reports.py`` measured 1392 per-cell
# citations in the Leader docx alone (Compact 66, Renewal 35,
# Comprehensive 11).  Root cause: the Round 57 post-render injector
# ``report_source_injector.inject_source_citations_into_docx`` cited every
# data row of every >=3-col matrix even though the quality scorer only
# needs the TOTAL row backed.  Build 83 replaces per-cell injection on
# multi-column matrices with ONE compact italic "Sources: ..." caption
# paragraph inserted directly below the table (aggregating the R82 KPI
# source taxonomy; degrades to the generic ``AdoptIQ Report Data Sources``
# when columns do not resolve).  Two-column ``label | value`` rows keep
# their per-row citation (the R82 two-column contract -- not part of the
# clutter complaint).  ``report_iteration_loop._extract_docx_metric_claims``
# now treats a source-caption paragraph immediately after a matrix as
# backing for that matrix's claims so the diagnostic quality scorer stays
# green WITHOUT per-cell citations (contract evolution backed by tests,
# not a weakened check).  The Build 82 acceptance audit otherwise came
# back clean: 0 mid-string injections, 0 markdown chrome, 0 stub bullets,
# 0 genuine Snowflake global-config tokens, 0 HTML leakage, 0 duplicate
# XLSX IDs, 0 risk-score saturation, 0 NaN/Unknown customer rows across
# all four reports.  Pinned by
# ``tests/test_round114_citation_caption_below_matrix.py`` (10 tests) +
# updated ``tests/test_round57_source_citation_injector.py`` count-dict
# assertions; R82/R52/R53/R76/R90/R112 suites re-run green.
# Round 115 / Build 84: report-model re-flip to gemini + 2-column citation
# de-clutter.  Part A: an upgraded install carried a stale
# ``report_model_name: gpt-5-nano`` override in App-Support ``settings.json``
# (highest-precedence layer in ``model_resolver``); the R103/R108 stale-nano
# migrations had already stamped their markers, so they early-returned and
# treated the value as deliberate.  ``adoptiq_settings.migrate_round115_model_
# defaults`` adds a new ``r115_model_default_migrated`` marker that re-stomps
# the exact stale ``gpt-5-nano`` value back to ``gemini-3.1-flash-lite`` once
# more for upgraded installs (a deliberate post-R115 nano selection still
# survives via the new marker).  Part B extends the Build-83 caption-below-
# table de-clutter to the 2-column ``Metric | Value`` KPI cards: the R82
# Phase B3 per-row IN-CELL ``[Source: ...]`` citation (Build 83 live audit
# found 125 of them in the Leader docx) is replaced with ONE aggregated
# ``Sources: ...`` caption directly below each card via
# ``report_source_injector._build_two_column_source_caption`` (R82 per-source
# taxonomy preserved -- placement inverted, cells stay clean).  The quality
# gate (``report_iteration_loop``) now treats a 2-column KPI card as source-
# backed by EITHER an in-cell citation OR the following ``Sources:`` caption
# so strict mode stays green without per-cell clutter.  Pinned by
# ``tests/test_round115_model_remigration.py`` (8 tests) +
# ``tests/test_round115_two_column_caption.py`` (9 tests) + updated
# ``tests/test_round82_per_source_citation_taxonomy.py`` (caption-based
# provenance for 2-column cards) + updated R114/R57/R103/R51 suites.  The
# read-only ``scripts/r114_audit_reports.py`` gained ``--auto`` discovery +
# ``--target NAME=BASE`` so the acceptance audit always targets the latest
# artifact of each type; the existing-report sweep came back clean (0
# mid-string, 0 markdown, 0 stub, 0 global-config, 0 HTML, 0 dup IDs, 0
# saturation; nanish cells are honest empty-source fallbacks, NOT bugs).
#
# Round 116 / Build 85 -- model heal + ACC count fix + admin/help/audit sweep.
# (A) Robust model resolution: Gemini is the default and a wedged/leftover
# ``gpt-5-nano`` in settings.json now ALWAYS heals back to Gemini at runtime
# unless the operator explicitly picked nano from the Preferences dropdown
# (tracked by new ``report_model_user_set`` / ``ask_ai_model_user_set`` flags;
# ``model_resolver`` coerces stale nano when the flag is not True).  Retires
# reliance on per-round migration markers for coercion.  (B) Comprehensive
# "All Contact Center" customer-count regression: R93 strict ACC scoping drops
# AB rows with no Contact-Center tech evidence, so customers present only via
# those rows vanished from the narrow AB-CSOne-Pulse headline universe (Brian
# Frazier / All Contact Center 90d showed "15"/"24" where the team carries
# ~37-49 CC customers).  The ACC headline universe is now anchored on the team
# Contact-Center SUBSCRIPTION roster as well, threaded coherently through the
# Word headline, risk-band buckets, the consistency validator, AND the Excel
# Summary so all four agree by construction; the AB_Detail_All sheet stays
# strictly scoped (R93 contract preserved) and exclusions surface as
# ``tech_filter_scope_excluded`` warnings.  Gated to "All Contact Center" ONLY
# -- named-tech / Compact / Renewal / Leader keep the pre-R116 universe.
# (C) Admin/Quit UX hardening: the navbar Quit control now carries a visible
# "Quit" label + a divider separating it from the nav links (the icon-only
# button mis-click that read as "Admin Console tries to shut down"); the Admin
# Console link is now port-aware via ``_resolve_admin_port`` instead of a
# hardcoded ``:5152``.  (D) Help page fully rewritten operator-first.  (E)
# Settings test-isolation: an autouse conftest fixture redirects
# ``adoptiq_settings._app_support_dir`` to a per-test tmp_path so no test can
# touch the real settings.json (the corruption that wedged the live file).
# Pinned by tests/test_round116_model_heal_robust.py,
# tests/test_round116_acc_customer_count_floor.py,
# tests/test_round116_help_page_content.py, and the extended
# tests/test_round60_quit_button_template.py.
ADOPTIQ_BUILD = "85"  # Round 116 / Build 85
# Round 113 / Build 82: Ask AI uplift + Preferences fix-and-polish.
# Phase A (Ask AI UX): unified conversation history across the sync +
# stream paths (A1), visible browser-local conversation thread (A2),
# progressive retrieval-footprint feedback in the SSE meta event (A3),
# multi-line textarea question input (A4), per-answer copy/download
# controls (A5).  Phase B (Ask AI accuracy): renewal/expiry/ARR
# aggregates serialized into CANONICAL_HEADLINE from the already-
# prefetched enhanced_account_insights, multi-currency aware (B1); a
# bounded in-memory per-scope top-risk cache so the suggestion chips
# name a real top-risk customer when warm and fall back to templates
# when cold (B2); customer drill-through wiring Ask AI customer names
# to the /customer/<name> Customer 360 route, XSS-safe (B3).  Phase C
# (Preferences): fixed the CSOne folder card display bug (active_path
# -> folder_path) (C1); Intelligence-toggle feedback target on the
# Preferences page (C2); persisted default analysis scope
# (default_days / default_manager / default_technology) with schema +
# validators + a re-validating resolver + GET|POST
# /api/settings/report-defaults + a Preferences card, wired into the
# analyze + ask-ai pages and the suggestion-chip fetch (C3); guarded
# intel_status.js polling so the recurring loop only runs on pages
# that carry the banner/panel DOM (C4).
# Round 112 / Build 81: Build 80 acceptance audit + 6-finding fix loop.
# 1 P0 + 4 P1 + 1 P2 found by the parallel multi-agent audit on the
# four fresh Build 80 report pairs (Comprehensive / Compact /
# Renewal Portfolio / Leader, all on the All Contact Center / 90d
# scope).  The R111 cross-format Risk_Score_0_10 parity fix held
# (max delta 0.04 across 257 common customers; ZERO MEDIUM/MODERATE
# vocab leaks; ZERO 0-100 saturation) -- this round closes the
# remaining gaps surfaced by that audit.
#
# F1 (P0 / SECURITY): Compact docx P25 leaked the upstream CircuIT
# 429 rate-limit response body containing ``appkey=...`` +
# ``session_id=...`` + a stringified ``user`` JSON blob -- enough
# for an attacker to attempt session-replay against the upstream API.
# Root cause: the ``llm_error`` payload at ``app_simple.py:5776`` was
# concatenated raw into the "Non-AI fallback summary" paragraph
# instead of routed through the existing ``_r69_sanitize_llm_error``
# helper, AND that helper's regex pattern set lacked CircuIT-specific
# coverage (only Bearer / JWT / api_key / Authorization were
# redacted).  Fix: route the call site through the sanitizer AND
# extend the redaction set with three new patterns (appkey,
# session_id, full ``user`` JSON blob).  Pinned by the new
# ``tests/test_round112_build80_acceptance_fixes.py::TestF1*``
# (6 tests).
#
# F2 (P1): Renewal docx mixed 0-10 (executive-summary scale) with
# 0-100 (main KPI box + focus-table cells), violating the R67/B1 +
# R88/F2 + R111 cross-format parity contract.  Root cause: the
# Renewal Word writer in ``app_simple.py`` had two display sites
# (the score-summary box at L12970 and the focus-table cells at
# L13089) that hard-coded the 0-100 scale even after R111 made
# 0-10 canonical for the published columns.  Fix: convert at the
# render site (``score_value / 10.0`` for the box, scale the
# focus-row value before formatting); preserve the 0-100 reference
# parenthetically in the box for back-compat readability.  Pinned
# by ``TestF2RenewalScoreScale`` (2 tests).
#
# F3 (P1): Leader docx P117 ``"Total Activities: 42 [Source: ...]
# (APs: 18, ABs: 3, CPs: 1, TAC: 20) [Source: ...]"`` -- the wrapper
# KPI's citation interrupted the embedded paren cluster as if the
# wrapper and cluster were two separate semantic units.  They aren't:
# ``"Total Activities: 42 (APs: 18, ABs: 3, ...)"`` is one assertion.
# Fix: in ``report_source_injector._rewrite_paragraph_with_inline_citations``,
# when the next match is in an embedded paren cluster AND the
# wrapper-KPI value sits IMMEDIATELY before the cluster's open paren
# (whitespace only between value-end and ``(``), suppress the wrapper's
# citation -- the cluster's post-``)`` citation already covers BOTH.
# When the boundary contains non-whitespace (e.g. ``"42 then breakdown
# (APs: ...)"``), preserve pre-R112 behavior.  Pinned by
# ``TestF3CitationInjectorWrapperSuppression`` (3 tests) +
# updated ``test_round76_build51_embedded_paren_cluster.py`` (1 test).
#
# F4 (P1): Comprehensive docx P210 rendered ``"the top 11 barriers
# were also tagged by an LLM classifier"`` while
# ``analysis_status['be_classifier_diag']['classified_count'] == 0``
# AND ``llm_error`` carried the rate-limit body.  The narrative
# was honest about which barriers were ranked deterministically but
# hallucinated about LLM tagging that never happened.  Fix:
# ``be_priority_word_section._intro_text`` now reads the diag's
# ``classified_count``, ``llm_disabled``, and ``llm_error`` fields
# and emits one of three honest variants: success ("the top N were
# tagged"), disabled ("LLM classification was disabled"), or failed
# ("LLM classification was attempted on the top N but did not
# return any tags").  Pinned by ``TestF4BEClassifierHonesty`` (3 tests).
#
# F5 (P1): Renewal + Compact docx partial-data warning paragraphs
# said ``"One or more upstream data sources failed to load..."``
# even when the underlying ``partial_data_warnings`` carried only
# ``kind='tech_filter_scope_excluded'`` (R93 contract) -- a SCOPE
# decision, not a data-loading failure.  Fix: detect when ALL
# warnings are scope-kind (the new ``_r112_scope_kinds`` set:
# ``tech_filter_scope_excluded``, ``manager_filter_scope_excluded``,
# ``time_window_scope_excluded``, ``no_onedrive_sync``,
# ``autodiscovered_empty_after_scope``) and emit an honest preamble
# (``"data was filtered out by the requested scope"``); preserve
# the legacy preamble for genuine load failures or mixed warnings.
# Pinned by ``TestF5PartialDataWarningKindAware`` (1 test).
#
# F6 (P2): Report titles for the literal sentinel ``"All Managers"``
# rendered as ``"All Managers's Portfolio"`` (double possessive --
# ``All Managers`` is already pluralised; appending apostrophe-s
# is ungrammatical).  Fix: new ``report_utils.r112_smart_possessive``
# helper that returns the bare name for the literal ``"All Managers"``
# / ``"all"`` / ``"portfolio"`` sentinels (case-insensitive),
# apostrophe-only for sibilant-final names (``Alex'``), and
# apostrophe-s for everything else (``Brian Frazier's``).  Wired
# into the 5 ``app_simple.py`` call sites + 2 ``adoptiq_backend.py``
# call sites that produced the bug.  Pinned by
# ``TestF6SmartPossessive`` (7 tests).
#
# All 4 ``make verify`` gates green: ruff clean, bandit 0 HIGH/MED,
# pip-audit no vulnerabilities, 5664 pytest passed (R94 floor was
# 5512; +152 net).
# Round 111 / Build 80: Compact <-> Renewal Risk_Score_0_10 parity (R67/B1
# root-cause fix). Build 79 acceptance audit measured 182 of 257 common
# customers (~70%) with Renewal ``Overall_Risk_Score`` higher than Compact
# for the same customer + scope, typically by ~+1.0 on the 0-10 scale --
# violating R67/B1's cross-format parity contract. Root cause:
# ``compact_report_formatter._r66_b8_classify_extra_frames`` did NOT
# recognise the live ``CUSTOMER_PULSE__C`` / ``CUSTOMER_PULSE_COLOR_IMAGE__C``
# columns emitted by ``adoptiq_backend.fetch_csconsole_customer_pulse``, so
# Compact's per-customer ``pulse_df`` was always None even when the data
# was available -- shifting ``_score_engagement`` (via
# ``total_activity = AB + SC + pulse + AP``) and producing the systematic
# +1.0/10 drift. Renewal's ``_calculate_simple_renewal_risk`` filters
# ``csconsole_customer_pulse`` by ``BU_NAME`` directly (no classifier), so
# it was unaffected. Fix is two-pronged: (a) widen the classifier's pulse
# marker set to include the live production columns, with the same widening
# in the AP disqualifier set so a future schema rotation cannot re-route
# the frame; (b) defense-in-depth -- ``calculate_renewal_risk_scores``
# accepts new keyword-only ``pulse_df`` / ``action_plans_df`` / ``subs_df``
# kwargs that, when provided, OVERRIDE the classifier output, and all four
# Compact call sites in ``app_simple.py`` (Word success / Word fallback /
# XLSX success / XLSX fallback) thread the canonical CSConsole frames the
# Renewal path already feeds. The Build 79 audit also surfaced two
# false-positive items: B2 (Compact ``Critical_Adoption_Barriers`` at 73
# cols matches the canonical ``_CURATED_AB_DETAIL_ALL`` tuple by design --
# the audit script's "~30 cols" expectation was the
# ``_CURATED_ACTION_PLANS`` shape) and B3 (Renewal ``Risk_Components`` is
# a portfolio-level rollup, distinct from Comprehensive's per-customer
# detail). B3 is documented as a Critical Rule in CLAUDE.md so future
# audits don't re-flag the design choice. Pinned by
# ``tests/test_round111_compact_renewal_score_parity_live_data.py`` (12
# tests covering the widened classifier markers, disqualifier expansion,
# explicit-kwarg signature + override semantics, per-customer parity with
# the Renewal path on synthetic data, pulse-not-lost regression,
# empty-data and subs-only edge cases, legacy classifier back-compat, and
# the app_simple call-site coverage). Net pytest delta +12.
#
# Round 110 / Build 79: Leader Report_Info schema parity (latent-bug fix).
# Pre-R110 the Leader writer's two dynamic-loop branches in app_simple.py
# (Partial_Data_Warning_<n> rows and Failed_Sheet rows) appended dicts
# keyed on ``'Field':`` while every other row in the same _info_rows list
# used the canonical ``'Item':`` key per Round 73 / F6.
# ``pd.DataFrame(_info_rows)`` builds the union of all dict keys, so any
# Leader run with a partial-data warning OR a failed sheet silently grew
# a stray fifth ``Field`` column with NaN on every other row, violating
# R73/F6's promise that ``pd.read_excel("Report_Info")[["Item", "Value"]]``
# projects cleanly across every report format. Today's clean Build 78
# Brian Frazier 90d Leader cohort had ``Partial_Data_Warning_Count=0``
# and ``_failed_sheets=[]`` so the buggy rows never fired and the audit
# verdict was clean -- the latent defect was found on inspection. R110
# normalises both dynamic-loop branches onto the canonical ``'Item':``
# key so the four-column schema holds in every Leader run.
#
# Round 109 / Build 78: fix indexing hang. Runtime dense-vector upsert
# is bounded by ``ask_ai_vector_store._runtime_max_chunks_default``
# (default 2,000, env-overridable via
# ``ADOPTIQ_RUNTIME_VECTOR_MAX_CHUNKS``) so a 500k+ chunk corpus cannot
# keep the boot panel stuck on "Indexing"; bake-time/strict callers
# remain unbounded so release DMGs still ship with full dense vectors.
# The corpus panel renders an already-serving corpus as "Active" and
# surfaces "Dense retrieval is warming • backfilling N chunks" as a
# quality note. The analyze-page "Active report model" label resolves
# from active jobs only and falls back to the server-rendered Gemini
# default so historical ``gpt-5-nano`` snapshots cannot pin a stale
# label across sessions.
#
# Round 108 / Build 77: corpus smoothness. The prebaked corpus remains
# the baseline, OneDrive is optional refresh context, runtime refreshes
# backfill dense vectors when the embedder is available, and stale
# persisted nano defaults migrate back to Gemini one more time.
# Round 107 / Build 76: restores the prebaked AdoptIQ Knowledge Corpus
# in the app bundle so Ask AI has corpus data immediately on first
# launch.
# Round 90 / Build 66: hyperfocused fix for the Build-65 acceptance bug
# where the Compact Risk Summary tile rendered
# ``"Score 4-6 (Watch, 0-10 [Source: AdoptIQ Report Data Sources] scale): 9"``
# -- the citation was jammed mid-string between ``0-10`` and ``scale``.
# Root cause: ``_PARAGRAPH_KPI_NUMERIC_RE`` in ``report_source_injector``
# matches ``label='scale)' value='9'`` as a fourth, spurious match on
# the line because ``)`` is in the regex's label charset and there is no
# guard against a label that starts inside an unmatched parenthetical.
# The R66/B1 unit-deferral branch then sees alphabetic tokens in the
# boundary segment between match #3 and match #4 and lands the citation
# at the spurious match's next-label-start position -- which is right
# BEFORE ``scale``.  Fix: a new ``_paragraph_match_is_well_formed`` helper
# rejects matches whose label has more ``)`` than ``(``.  Real KPI labels
# never start inside an unmatched ``(``, so this is a strict, conservative
# filter applied in TWO sites (caller's ``all_matches`` builder + the
# rewriter's per-line ``line_matches``).  Pinned by
# ``tests/test_round90_paren_label_filter.py`` (16 tests covering the
# helper unit semantics, the regex source-shape pin, the end-to-end pin
# on the Build-65 failing line, and the R76 paren-cluster + R66/B1
# single-line regression guards).  Net pytest delta +16 (5443 -> 5459).
# Round 88 / Build 64: Build 63 acceptance audit + targeted fixes.
# F1 strips literal markdown asterisks from Comprehensive narratives by
# (a) extending the ``append_to_word_report.clean_and_format_text``
# sanitiser with a new ``_r88_italic_re = re.compile(r"(?<![*\\w])\\*([^*\\n]+?)\\*(?![*\\w])")``
# pass so single-star ``*italic*`` markers convert to docx italic runs
# instead of leaking literal asterisks (the ``**bold**`` path was already
# handled), AND (b) removing 19 ``*...*`` instructional wrappers from
# the LLM prompt templates (``PROMPT_COMPREHENSIVE_TEMPLATE``,
# ``PROMPT_CUSTOMER_TEMPLATE``, ``PROMPT_COMPACT_EXECUTIVE_TEMPLATE``)
# so the model stops mirroring our own emphasis markup back into the
# narrative.  F2 adds a dedicated ``Risk_Score_0_10`` column to BOTH the
# Compact ``Risk_Summary`` AND Renewal ``Renewal_Summary`` XLSX sheets
# (sourced from ``Overall_Risk_Score`` on the Compact side and either
# ``Overall_Risk_Score`` or a derivation of ``Risk_Score_0_100`` on the
# Renewal side) so an operator parsing the workbook downstream has an
# unambiguous 0-10 column name -- the legacy ``Overall_Risk_Score`` and
# back-compat ``Risk_Score`` aliases are preserved.  F3 drops the
# redundant ``_Renewal_Report`` filename suffix on the Renewal docx
# (pre-R88 produced ``AdoptIQ_Report_Renewal_..._Renewal_Report.docx``);
# the saved name is now just ``<base_path>.docx`` matching Compact /
# Comprehensive.  F4 (P0) wires
# ``LeaderReportGenerator._get_subscriptions_for_cssm`` to delegate to
# ``adoptiq_backend.get_subscriptions_for_team`` so the R82 multi-column
# UNION (primary + secondary DSM email columns) is honoured -- closes
# Brian's Build 61 acceptance gap where Greg Dolberry's secondary
# accounts (BU - Cisco Systems INC CA, BU - Wells Fargo, BU - Apple INC
# US) were missing from his Leader report.  F5 (P1) adds an
# operator-configurable CSOne OneDrive folder override.  New
# ``settings.json`` key ``csone_onedrive_folder`` (validated via
# ``adoptiq_settings._is_valid_csone_folder_path`` -- absolute or
# tilde-prefixed only, NUL/control/shell-meta-free, 4096-byte cap),
# new ``GET / POST /api/settings/csone-onedrive-folder`` endpoint
# (CSRF dual-auth + atomic 0o600 write + in-process
# ``Config.CSONE_ONEDRIVE_FOLDER`` mutation so the next index pass
# uses the new path WITHOUT a restart), and a new card on
# ``templates/preferences.html`` (modeled on the R85 corpus-share-url
# card; same IIFE-wrapped JS module, textContent rendering, no
# innerHTML / eval).  Resolution precedence in
# ``config._resolve_csone_onedrive_folder``:
# ``settings.json`` -> ``CSONE_ONEDRIVE_FOLDER`` env ->
# ``_csone_onedrive_candidates()`` auto-discovery.  Closes Brian's
# Build 61 acceptance gap where his sharer-prefixed sync folder
# ``/Users/brfrazie/Library/CloudStorage/OneDrive-Cisco/Jeffrey Story (jestory) - AdoptIQ_CSOne_Reports``
# did not match the canonical ``AI Projects/AdoptIQ_CSOne_Reports``
# shape and AdoptIQ silently fell through to the bundled baked
# artifact only.
#
# Pinned by:
#   - ``tests/test_round88_comprehensive_no_markdown_asterisks.py``
#   - ``tests/test_round88_risk_score_0_10_populated.py``
#   - ``tests/test_round88_renewal_filename_no_redundant_suffix.py``
#   - ``tests/test_round88_cssm_subscriptions_union.py``
#   - ``tests/test_round88_csone_folder_override.py`` (44 tests)
#   - ``tests/test_round88_csone_folder_override_ui.py`` (17 tests)
# Round 89 / Build 65: post-Build-63 acceptance sweep + alias retirement.
# F1 retires the R67/B6 ``Risk_Score`` back-compat alias from Compact
# ``Risk_Summary`` (``app_simple.run_compact_analysis`` row dict +
# ``columns=`` tuple + four reader sites: the canonical-high-risk sort,
# the ``Risk_Band`` fallback sort, the bare-cutoff filter+sort, and the
# mean fallback).  The R67/B6 contract said "kept as a back-compat
# alias for one build then deprecated"; R88/F2 already shipped the
# explicit ``Risk_Score_0_10`` column adjacent to ``Overall_Risk_Score``,
# so the alias has no remaining consumers and removing it eliminates
# the ambiguity that R86/F1 had to spend a round un-saturating
# (operators were reading ``Risk_Score`` thinking it was the canonical
# headline).  Future Compact ``Risk_Summary`` consumers MUST read
# ``Overall_Risk_Score`` (canonical) or ``Risk_Score_0_10``
# (explicit-scale).  The bake also re-runs the R88 fixes (markdown
# asterisks, ``Risk_Score_0_10`` column, Renewal docx filename, Leader
# CSSM UNION, CSOne folder override) into the production .app for the
# first time -- pre-Build-65 those were source-only.  Pinned by
# ``tests/test_round89_compact_risk_score_alias_removed.py`` plus
# updated assertions in ``tests/test_round70_compact_risk_summary_overall_score.py``
# + ``tests/test_round88_risk_score_0_10_populated.py``.
# Round 87 / Build 63: corpus-tightening + upgrade-launch UX +
# corpus-indexing UX + URL-card relocation.  Phase 1 originally added
# an opt-in ``ADOPTIQ_RELEASE_GATE=1`` block to require baked artifacts;
# Round 96 inverted that release gate so shipping builds now fail if
# corpus artifacts are present.  Phase 2 adds a "Corpus security
# model" subsection to README.md documenting the at-rest AES-256-GCM
# contract (durable artifacts in ``~/Library/Application Support/AdoptIQ/knowledge``),
# the access-gate via OneDrive sentinel + Microsoft tenant ACL, and
# the runtime ephemeral plaintext SQLite file in
# ``$TMPDIR/adoptiq_corpus/`` (mode ``0o600``, parent dir ``0o700``,
# scrubbed via ``EncryptedCorpusHandle.close`` registered as an
# ``atexit`` handler) -- closes the R86 audit's PARTIAL on
# "ciphertext at rest / ephemeral plaintext at runtime" with an
# honest claim instead of an unverifiable "never plaintext on disk"
# assertion.  Phase 3 detects the stale-binary upgrade-launch case
# in ``app_simple.py``: when a frozen build is double-clicked AND
# the running instance's ``process_started_at_utc`` is older than
# our ``Path(sys.executable).stat().st_mtime`` (1.0 s tolerance),
# ``_force_quit_existing_adoptiq`` sends ``SIGTERM`` to the listener
# pid (NOT ``SIGKILL`` -- the running instance's ``atexit`` handlers
# must fire so ``analysis_status.json`` saves and the corpus temp
# file scrubs) and the new build boots fresh.  Closes the user's
# Build 62 acceptance feedback that double-clicking a fresh DMG
# silently re-routed back to the older running build with a
# "Restart required" banner.  Phase 4 moves the operator-configurable
# corpus share URL card from ``templates/analyze.html`` to
# ``templates/preferences.html`` exclusively (admin-config doesn't
# belong on the per-run report tile -- mirrors the R73 UX-2 split
# that moved the report-narrative model picker off this page); the
# R84 endpoint + JS handler + JSON contract are unchanged, only the
# visual surface moved.  Phase 5 adds an italic clarifier subtitle
# to the AdoptIQ Intelligence panel ("Reports remain safe to run
# during indexing -- only Ask AI grounding waits for the index pass
# to finish.") that ``intel_status.js::paintBanner`` unhides only
# while ``state === 'running'`` (mirroring the SSR
# ``boot.in_progress`` conditional) -- closes the user's Build 62
# acceptance feedback that the panel showed only "Indexing CSOne
# reports..." while the user was about to run reports, with no
# signal that report generation was unaffected.
#
# Pinned by ~30 new tests across ``tests/test_round87_release_gate_corpus_bake.py``,
# ``tests/test_round87_corpus_security_doc.py``,
# ``tests/test_round87_launcher_auto_kill_stale.py``,
# ``tests/test_round87_card_removed_from_analyze.py``, and
# ``tests/test_round87_corpus_indexing_ux_subtitle.py``.  R84 + R85
# UI tests were repurposed in Phase 4 to point at preferences.html
# (the canonical surface post-relocation) so the same source-shape
# guarantees survive the move.
# Round 84 / Build 60: operator-configurable corpus share URL.  The hardcoded `Config.ADOPTIQ_CORPUS_SHARE_URL` default is now the LAST tier in a three-tier resolver (`corpus_share_url_resolver.get_active_corpus_share_url`); the active URL resolves settings.json -> env -> config.py default with per-tier validation (`adoptiq_settings._is_valid_sharepoint_url`: HTTPS-only, ``*.sharepoint.com`` host, max 2048 bytes).  A new analyze-page card (`[data-corpus-share-url-card]`) lets the operator paste a new URL, click Test (opens the SAVED URL via `/api/corpus/bootstrap-shortcut` -- never the raw input), Save (POST `/api/settings/corpus-share-url`, CSRF dual-auth + atomic 0o600 write), or Clear override (empty-string POST falls through to env/config).  Lets us rotate the SharePoint share token (`e=...`) without rebuilding the DMG.  Round 108 supersedes the old R83 encryption comment: the prebaked/local corpus uses the bundled/per-user sentinel path, and OneDrive is optional shared-source refresh coverage rather than the first-launch decryption gate. The URL is not a secret -- Cisco SharePoint ACL gates only the optional share.  Pinned by `tests/test_round84_corpus_share_url_setting.py` (~22 tests) + `test_round84_corpus_share_url_endpoint.py` (~17 tests) + `test_round84_corpus_share_url_resolver.py` (~12 tests) + `test_round84_corpus_share_url_ui_source_shape.py` (~21 tests) covering schema, validator wiring, resolver precedence with no-caching, POST CSRF dual-auth + 0o600 mode + validation rejection, GET source labels, UI marker presence, and textContent XSS guard.
# Round 83 / Build 59: OneDrive sign-in proxy + one-click corpus bootstrap. New `signed_in_no_corpus` panel state surfaces when the OneDrive desktop client is signed in to Cisco BUT the corpus shared folder isn't in the user's tree yet - panel renders an "Add corpus share to my OneDrive" CTA that opens the SharePoint share via the new `/api/corpus/bootstrap-shortcut` endpoint (https-only allow-list, 2048-byte cap). Daily refresh worker accelerates polling for the new state alongside the legacy `blocked_no_onedrive` state so the user sees the panel transition out promptly once the share materializes. Owner-style path tier 3-4 fallbacks (~/Library/CloudStorage/OneDrive-Cisco/AI Projects/AdoptIQ_CSOne_Reports) added to `_csone_onedrive_candidates()` so the corpus owner's machine resolves automatically without env-var overrides; bake_corpus.py walks the candidate list explicitly. macOS proxy uses CloudStorage glob (OneDrive-Cisco* as Cisco signal); Windows uses winreg HKCU\Software\Microsoft\OneDrive\Accounts probe.
# Round 80 / Build 56 ships four user-visible fixes that landed
# together because they share the same SSoT modules.  (1) Roster
# expansion: ``team_config.json`` and the
# ``adoptiq_backend._get_default_team_config`` fallback both gain two
# new managers (Paresh Jadhav + Mithun Sakthivel Subramanian) and 16
# direct reports; the fallback was drifted (referenced a nonexistent
# ``Josh Horowitz`` manager and placed ``Asad Sarfaraz`` under Dee
# Kindrick) and is now in byte-for-byte parity with the JSON.  Two
# names move from Brian Frazier to Mithun's team.  (2) Admin "Back
# to AdoptIQ" link: pre-R80 the dashboard's render-template-string
# context passed the stale module-level ``MAIN_APP_URL`` constant
# captured at import time; R80 routes through ``_live_main_url()``
# (the same R44 / Phase 8 helper the HTTP fetches use) so the
# rendered anchor href reflects the live ``ADOPTIQ_MAIN_URL`` env
# value.  Brian's bug is fixed.  (3) OneDrive shared-folder
# discovery: pre-R80 ``_csone_onedrive_candidates`` returned four
# paths under ``OneDrive-Cisco/AI Projects/AdoptIQ_CSOne_Reports``
# -- a tree only the corpus owner can sync.  R80 narrows discovery
# to the SharePoint shortcut leaf
# ``Jeffrey Story (jestory) - AdoptIQ_CSOne_Reports`` that the
# OneDrive desktop client materialises when a user clicks
# "Add shortcut to OneDrive" against the share.  The corpus owner
# now dogfoods the same shortcut workflow so there is no owner /
# user code path drift.  (4) Reports-folder indexing: pre-R80
# ``corpus_bootstrap`` indexed ``~/Downloads`` (triggered the macOS
# Files-and-Folders permission prompt that confused users); R80 added
# ``_APP_SUPPORT/outputs/`` as a permission-clean source, and Round 102
# retires the old Downloads opt-in path completely.
# Pinned by tests/test_round80_team_roster_includes_new_managers.py
# (6) + tests/test_round80_admin_back_link_uses_live_url.py (3)
# + tests/test_round80_onedrive_shared_folder_only.py (6)
# + tests/test_round80_local_outputs_corpus_source.py (7).
# Round 79 / Build 55: BE-engineering priority barrier analysis.  Implements an independent priority signal for adoption barriers that is decoupled from CSConsole's user-set priority and grounded in a deterministic risk + customer-sentiment formula.  Architecture is hybrid: ``be_priority_scorer.compute_be_priority_score`` deterministically ranks ALL ABs by a weighted formula combining capped severity (P1=1.0..P4=0.4), customer composite risk (0..1 from ``risk_profiles``), pulse negativity (0..1 derived from per-customer pulse score), open age (linearly scaled, 90d cap), content signal (regex hits in title + description for blocker / outage / cannot / urgent / impacting / etc.), and the explicit ``AB_ESCALATE_C`` flag; only the top 50 (operator-tunable via ``Config.BE_PRIORITY_LLM_TOP_N``) are then tagged by a second-pass LLM into one of ``TRUE_BLOCKER`` / ``TRAINING_GAP`` / ``FEATURE_REQUEST`` / ``USER_BEHAVIOR`` / ``DUPLICATE`` / ``OBSERVATION_ONLY``.  ``be_priority_scorer.compute_be_focus_areas`` then groups the scored frame by ``(sub_technology, ab_category_final)`` to emit the 5-10 most important focus areas per technology for the BE group, sorted by cluster_focus_score DESC with a min-score floor of 30.0 (operator-tunable).  Output ships in BOTH the Comprehensive AND Leader reports, in BOTH XLSX and DOCX: two new XLSX sheets (``BE_Priority_Barriers`` curated to ~12 columns, ``BE_Focus_Areas`` with technology + theme + cluster score + customers affected + sample issues) plus a new Word section ``BE Engineering Priority Focus Areas`` that renders one banded table per technology with the canonical helper ``be_priority_word_section.add_be_priority_focus_areas_section``.  The BE-priority pipeline is hoisted ABOVE the per-customer narrative loop so the per-customer briefing book emits four new R79 fields (``CSConsole_Severity``, ``Independent_BE_Priority``, ``Independent_BE_Class``, ``Days_Open``) on the AB detail records the LLM sees -- giving the per-customer narrative LLM access to deterministic priority and the LLM's classification verdict for every barrier.  Operator kill-switches: ``Config.BE_PRIORITY_LLM_ENABLED=false`` skips the LLM call (deterministic scoring still runs, ``BE_Class`` is empty in the XLSX); ``Config.BE_PRIORITY_BRIEFING_ENABLED=false`` falls back to the pre-R79 4-line briefing shape.  Failures in the pipeline short-circuit to provenance rows (``_adoptiq_provenance_row=True`` + ``AdoptIQ_Status="ERROR"`` + truncated message) so neither the XLSX sheets nor the Word section ever ship empty or partial.  Compact and Renewal reports remain unchanged in this round (deferred follow-on).  Pinned by 6 new test files under ``tests/test_round79_b{1..6}_*.py``: B1 (scorer formula determinism / severity capping / age scaling / vectorized helper -- 41 tests), B2 (LLM classifier kill-switch / retry / hallucinated-ID rejection / system-prompt content -- 38 tests), B3 (focus-areas rollup determinism / true-blocker boost / max-per-tech cap -- 15 tests), B4 (orchestrator integration into Comprehensive + Leader / provenance fallback / column shape / source-shape pin -- 27 tests), B5 (Word section round-trip / table structure / heading sort -- 23 tests), B6 (briefing book emits four R79 fields / kill-switch / NaN / negative days handling / source-shape pin that the pipeline is hoisted ABOVE the per-customer loop -- 17 tests).  All four ``make verify`` gates green: ruff clean, bandit 0 HIGH/MED, pip-audit clean.  No Snowflake query schema changes, no formatter rewrites, no boot-order changes, no corpus or admin-console structural changes ship in this build.  # Round 78 / Build 54: closes two regressions found by the Build 53 acceptance audit on the canonical 4-pair report set.  F1/B1 (P1 narrative quality): the per-customer storyboard LLM was emitting ~170 stub bullets per Comprehensive run of the form ``"<Category>: Data unavailable."`` -- pure empty-bucket acknowledgements with no narrative content (``PROMPT_CUSTOMER_TEMPLATE`` asks the model to fill 8 sub-categories and tells it to state "data unavailable" if a section is empty, which is honest but noise-amplifying when the briefing has many gaps).  Fix adds a new module-level ``_R78_STUB_RE`` regex constant in ``adoptiq_backend.py`` and a 5-line skip path in ``append_to_word_report``'s bullet branch (~L8366) that drops a bullet whose entire text matches the stub pattern.  The regex is anchored ``^...$`` and includes optional ``**`` bold-wrap permutations (4 combinations covered: ``**Cat:** stub``, ``**Cat**: stub``, ``Cat: **stub.**``, ``**Cat: stub.**``); the prefix is constrained to start with an uppercase letter so generic lowercase user text never matches; ``[Dd]ata\s+[Uu]navailable`` allows case-insensitive matching of the marker phrase only.  Bullets with substantive narrative AFTER the marker (e.g. ``"Operational Disruption: Data unavailable. No active incidents."``), parenthetical mid-sentence usage (e.g. ``"... (SP-ID: data unavailable) ..."``), and ARR/financial commentary that uses the marker as preamble are ALL preserved.  The skip path mirrors the empty-line-skip convention at the top of the writer's while loop (``i += 1`` BEFORE ``continue``) so the loop never infinite-loops on the unincremented index.  F2/B2 (P2 regression of R75/B2 on a different sheet): the leader Action_Plans XLSX sheet emitted 366 rows / 344 unique IDs (22 duplicate AP rows) for Brian Frazier 90d -- same root cause as R75/B2 (Adoption_Barriers) but on the AP sheet: when a single Action Plan is attributed to multiple CSSMs via the R72 ``_ATTRIBUTED_BY_ACCOUNT`` shared-account pathway in ``_slice_by_owner_or_account``, per-CSSM AP frames each carry the SAME ID and the raw ``pd.concat(all_action_plans, ignore_index=True)`` emits the row N times.  Fix mirrors the R75/B2 dedup block exactly: insert ``drop_duplicates(subset=['ID'], keep='first')`` immediately before ``sheets['Action_Plans']`` is assigned, with the explicit ``notna() / isna()`` split so rows without an ID column (rare malformed-export edge case) are passed through unchanged, plus a structured ``logger.info`` line with ``"%d raw rows -> %d unique"`` formatting for operator visibility.  The R75/B2 (AB) and R78/B2 (AP) blocks are co-located and ordered AP-then-AB to match the existing writer flow -- a future refactor that consolidates them into a shared helper picks up both sheets together.  F3 (P3 by-design difference, NOT a bug): 6 Action_Plan IDs appeared in Comprehensive but not in Leader for the same scope.  Investigation confirmed this is by design: Leader scopes APs by per-CSSM email matching (``Owner.Email IN team_emails OR Assignee.Email IN team_emails``) while Comprehensive scopes by per-Account-ID matching (``Account.Id IN team_subscription_accounts``); for accounts whose APs have a blank ``Account Manager`` field AND assignees outside the team's CSSM list (e.g. internal Cisco-on-Cisco accounts), the two reports legitimately diverge.  No code change for F3 -- the design choice is documented as a Critical Rule in CLAUDE.md so future scope-edge anomalies under this category are documented rather than coerced into single-predicate parity.  Net pytest delta +36 (4828 -> 4864): tests/test_round78_b1_comprehensive_stub_bullet_filter.py (28 tests covering source-shape / regex match-no-match / bold-wrap permutations / substantive-bullet preservation / end-to-end paragraph-count / no-hang regression) + tests/test_round78_b2_leader_action_plans_dedup.py (8 tests covering source-shape / log-format / R75-R78 mirror / 2-CSSM shared-AP collapse / canonical-count parity / no-ID frame / partial-ID frame / empty-list guard).  All 4 ``make verify`` gates green: ruff clean, bandit 0 HIGH/MED, pip-audit clean.  No upstream Snowflake query schema changes, no formatter rewrites, no boot-order changes, no corpus or admin-console changes ship in this build.  All Build 53 closed-class invariants (citation injection, HTML leak, MEDIUM, AI fallbacks, score parity, ``unavailable`` token count) STILL GREEN -- the R78 fixes are surgical narrative-quality + dedup wins that compose with the R66/B1 + R67/B6 + R76 contracts.  # Round 77 / Build 53: flips the hardcoded default LLM model from ``gpt-5-nano`` to ``gemini-3.1-flash-lite`` in BOTH ``model_resolver._HARDCODED_DEFAULT`` AND the three ``Config.CIRCUIT_CONFIG`` env-fallback strings (``model_name`` / ``model_name_ask_ai`` / ``model_name_report``).  Both options are CircuIT free-tier (15 RPM, 120K peak tokens/min, 50M monthly input, 5M completion, $0 quarterly).  Operator testing on Build 52 showed flash-lite delivered materially lower per-customer LLM latency on the comprehensive report's per-customer storyboard loop while preserving the R66/B11 + R67/B8 grounding-pass rate.  ``gpt-5-nano`` remains a one-click toggle in every preferences surface (``[data-r69-model-input]`` on /preferences + ``[data-r69-admin-input]`` on the admin dashboard); the R69/Build43 operator-flippable seam and R73/UX-3 two-option dropdown contracts are unchanged.  Both ``<select>`` blocks list ``gemini-3.1-flash-lite`` as the FIRST <option> with ``selected`` so a fresh install with no settings file and no env vars renders the new default consistently across the API, the resolver, and the UI.  This Cursor session also cleared the operator's local ``settings.json`` ``report_model_name`` override (was pinning gpt-5-nano per the Build 52 acceptance run) so the next analysis will resolve to the new default without a UI re-save.  Pinned by tests/test_round77_default_model_flip.py (16 tests covering resolver default / per-resolver fallthrough / settings-empty fallthrough / gpt-5-nano back-toggle / config-resolver drift guard / dropdown first-option order on both /preferences and admin / ``selected`` attribute / descriptive copy positive + negative controls / docstring narrative / build-number floor); 4 existing tests updated to reflect the new default (test_round69_model_preferences.py x3 cases + test_config.py x1 case).  Net pytest delta +16 (4812 -> 4828); ``make verify`` GREEN end-to-end (lint + bandit HIGH/MED + pip-audit + pytest).  No Snowflake query schema changes, no formatter rewrites, no boot-order changes, no corpus or admin-console structural changes ship in this build.  # Round 76 / Build 52: live Build 51 acceptance audit found R76-B still leaked 317 ``unavailable`` tokens in the leader DOCX (down from Build 49's 423 -- a 25% reduction confirming the central filter was working -- but above the <50 acceptance threshold).  Cluster analysis showed 105 customer warning blocks each listing BOTH ``booking`` and ``risk`` as failed sections.  Root cause: ``_PolicyEnforcingCursor.execute`` raises ``TablePolicyViolation("Round 7 / Phase 2.5: enhanced_snowflake_insights refused by table policy")`` -- the wrapped error string contains ``"refused by table policy"`` rather than the upstream ``snowflake_table_policy.guard_sql`` shape ``"not in allowlist policy"`` that Build 51's ``_is_globally_unavailable_error`` matcher knew.  The wrapped error fell through to the generic ``section_errors`` path and reproduced the per-customer warning 105+105=210 times.  Build 52 extends ``_is_globally_unavailable_error`` to recognise BOTH cursor-shape strings (``"refused by table policy"`` raised by ``_PolicyEnforcingCursor``) AND the second ``guard_sql`` shape (``"blocked by policy"`` raised when the SQL references an explicitly-blocked legacy table like ``SUPPORT_CASES``).  All three known ``TablePolicyViolation`` message shapes are now globally classified.  Pinned by tests/test_round76_build52_table_policy_violation_matcher.py (10 tests including a 53-customer reproducer of the live audit collapse from 105+105=210 per-customer warnings to ZERO).  Net pytest delta +10 (4802 -> 4812); ``make verify`` GREEN end-to-end (lint + bandit HIGH/MED + pip-audit + 4812 pytest).  No upstream Snowflake query schema changes, no formatter rewrites, no boot-order changes, no corpus or admin-console changes ship in this build.  # Round 76 / Build 51: live Build 50 acceptance found two residual issues that the synthetic-fixture R76-A/R76-B tests didn't catch.  R76-A residual (embedded paren cluster): Build 50's ``_line_is_paren_kpi_cluster`` only triggered when the WHOLE line was the cluster, but the live leader DOCX produced paragraphs like ``"Bookings stalled (APs: 16, ABs: 3, CPs: 1, TAC: 4) and other context."`` where the cluster sits embedded in narrative; the multi-match loop dropped a citation between every comma so the operator saw ``"(APs: 16, [Source: ...]ABs: 3, [Source: ...]CPs: 1, ...)"``.  Build 51 adds ``_embedded_paren_clusters(line, line_matches)`` that returns ``{cluster_end_match_idx: close_paren_position}`` for ANY ``(KPI: v, KPI: v, ...)`` group nested in a longer paragraph (qualification mirrors the whole-line rules: 2+ matches inside the same ``(...)`` group, no nested ``(``, no bullet glyphs, no joiner words like ``then``/``and`` in the between segments, every comma-separated body segment matches a strict ``KPI: value`` regex).  Mid-cluster matches are skipped; the cluster-end match emits ONE citation flush with the closing ``)``.  Pinned by tests/test_round76_build51_embedded_paren_cluster.py (10 tests including R57 sentence multi-KPI negative control).  R76-B residual (TablePolicyViolation + column_missing not classified as global-config): Build 50's ``_get_booking_insights`` and ``_get_risk_insights`` only matched the strings ``"does not exist"`` / ``"not authorized"`` / ``"invalid identifier"``, but the live errors were ``"Snowflake table not in allowlist policy"`` (table-policy enforcer) and ``"column_missing: AMOUNT"`` (the booking schema variant where the AMOUNT column has been retired upstream).  Both look exactly like the global-config errors R76-B was meant to suppress -- but neither matched any of the three substrings, so every customer's section_errors propagated and the leader DOCX still showed 421 ``unavailable`` tokens (acceptance threshold <50).  Build 51 introduces ``EnhancedSnowflakeInsights._is_globally_unavailable_error(err_str)`` as a static helper that recognises the union ``{"does not exist", "not authorized", "invalid identifier", "column_missing", "missing required column", "not in allowlist policy"}`` and routes the recognition through three call sites: the per-section exception handlers in ``_get_booking_insights`` + ``_get_risk_insights`` (defense layer 1), AND the central ``section_errors`` aggregation loop in ``get_comprehensive_customer_insights`` (defense layer 2 -- catches errors raised by sections that don't tag themselves with ``globally_unavailable_section=True`` directly but emit a ``column_missing:`` error in their payload, like the future hypothetical risk_v2 query).  Pinned by tests/test_round76_build51_global_unavailable_central_filter.py (17 tests including transient-error negative control + a 53-customer reproducer of the Build 50 421-warning collapse).  Net pytest delta +27 (4775 -> 4802); ``make verify`` GREEN end-to-end (lint + bandit HIGH/MED + pip-audit + 4802 pytest).  No upstream Snowflake query schema changes, no formatter rewrites, no boot-order changes, no corpus or admin-console changes ship in this build.  # Round 76 / Build 50: Build 49 acceptance findings -- two pre-existing R76 candidates closed.  R76-A (citation injection): leader DOCX bullet-list KPI summaries (e.g. ``"• Total team activities: 999• Total Action Plans: 366..."``) and paren-grouped clusters (e.g. ``"(APs: 16, ABs: 3, CPs: 1, TAC: 4)"``) were producing mid-string citation jams (``"999• [Source: ...] Total Action Plans"`` and ``"(APs: 16 [Source: ...] , ABs: 3 ...)"``); the R66/B1 unit-deferral fix didn't classify bullet-glyph or paren-cluster boundaries.  Round 76 / R76-A adds two new boundary classifiers in ``report_source_injector._rewrite_paragraph_with_inline_citations``: a bullet-glyph branch (``_BOUNDARY_HAS_BULLET_RE`` matches ``• ``/``* ``/`` - ``) checked BEFORE the unit branch so the bullet stays attached to its label and the citation lands flush with the value (R57 placement); and a paren-cluster branch (``_line_is_paren_kpi_cluster``) that recognises a single ``(KPI: v, KPI: v, ...)`` group and emits ONE citation immediately after the closing ``)``.  Pinned by tests/test_round76_citation_bullet_and_paren_layouts.py (17 tests, including R57/R64/R66 negative controls).  R76-B (Snowflake noise): Build 49 leader DOCX repeated the per-customer "⚠️ Some Snowflake sub-sections were unavailable for this customer" warning ~423 times because (a) the booking query SELECTs ``AMOUNT`` but the source table ``BOOKINGS_TABLE_FOR_ACCOUNT_CHECK`` lacks that column (Snowflake error: ``invalid identifier 'AMOUNT'``) and (b) the ``RISK_ASSESSMENT`` table is not authorized for the current role -- both fire on every customer in the run.  Pre-R76 ``_skip_warned`` correctly logged the INFO message ONCE but ``insights['error']`` was still set per-customer, propagating to ``section_errors`` and the per-customer warning paragraph.  Round 76 / R76-B promotes ``_skip_warned`` with a sibling ``_globally_unavailable_sections: set[str]``, marks per-customer payloads with ``insights['globally_unavailable_section'] = True``, drops marked sections from ``section_errors`` so the per-customer warning never fires, and adds a public ``get_globally_unavailable_sections()`` accessor.  ``LeaderReportGenerator._add_validation_section`` renders ONE banner near the validation summary when the accessor returns non-empty.  Pinned by tests/test_round76_globally_unavailable_section_suppression.py (17 tests, including transient-error negative controls and a 53-customer reproducer of the Build 49 423-warning collapse).  4775 pytest passing (above R75 floor 4741, +34 new R76 tests); ``make verify`` GREEN end-to-end (lint + bandit HIGH/MED + pip-audit + pytest).  No upstream Snowflake query schema changes, no formatter rewrites, no boot-order changes, no corpus or admin-console changes ship in this build.: Build 47 acceptance findings + footer-enforcer first run. B1 (P0): comprehensive Action_Plans returned 0 vs Leader 366 for the same scope -- Build 47 audit caught the divergence; root cause was the post-fetch ``_filter_csconsole_data_by_technology`` call on the Snowflake AP frame collapsing 366 valid rows to 0 because the AP source table (``C360_CS_TASK_C_VW``) lacks the ``SUB_TECHNOLOGY_C`` / ``TECHNOLOGY_C`` columns that the enhanced matcher requires (the matcher then produces an all-False mask with no account-scope fallback for that branch, and the merge step stamps a single ``_adoptiq_provenance_row=True`` marker). Fix removes the over-restrictive post-fetch tech filter so the comprehensive path mirrors Leader's behaviour exactly (the Snowflake query is already ``account_ids``-scoped via team_subs_df, which is upstream tech-scoped). Pinned by tests/test_round75_comp_action_plans_parity_with_leader.py (6 tests). B2 (P1): leader Adoption_Barriers XLSX sheet emitted 4 duplicate barrier rows (73 sheet rows / 69 unique IDs) -- when a single AB is attributed to multiple CSSMs via the R72 ``_ATTRIBUTED_BY_ACCOUNT`` shared-account pathway, the per-CSSM frames each carry the SAME ID and the raw ``pd.concat`` writes the row N times. R72/Layer A fixed the canonical_metrics counts but not the sheet itself. Fix inserts ``drop_duplicates(subset=['ID'], keep='first')`` immediately before the XLSX write; rows without an ID column pass through unchanged. Pinned by tests/test_round75_leader_ab_sheet_dedup.py (5 tests). B3 (P2): comprehensive AB_Detail_All leaked one CISCO SYSTEMS INC CA barrier (`aGte6000000pAi5CAE`) with empty ``ACCOUNT_MANAGER_C`` AND empty ``assignee_cssm_email`` into Brian Frazier's manager-scoped report (Leader correctly excluded it via per-CSSM ``_slice_by_owner_or_account``). Fix adds a defensive scope filter in ``run_comprehensive_analysis`` that drops AB rows where BOTH attribution columns are blank -- but only when ``manager_name != "All Managers"`` (the All-Managers report is intentionally cross-team). Conservative: rows with EITHER column populated survive (defensive: better keep an arguably-in-scope row than silently drop a real barrier). Pinned by tests/test_round75_comp_scope_drops_empty_account_manager.py (9 tests). B4 (cosmetic): Compact ``Sheet_Title:Executive_Dashboard`` value carried a leading space (`' Executive Dashboard - All Managers Portfolio Analysis'`) while every other Sheet_Title row had no leading space. Fix drops the leading space from the f-string template. Pinned by tests/test_round75_compact_executive_dashboard_no_leading_space.py (3 tests). Critical context: Build 47's audit reports were generated by the OPERATOR'S RUNNING PROCESS at App_Build=47, not Build 48 -- the operator never installed the Build 48 DMG that R74 baked. The R74/P0 footer enforcer (``_r74_footer_enforcer.enforce_build_label_footer``) has therefore NEVER executed in production. Build 49 ships the R74 enforcer first-run alongside the four R75 data fixes so the operator gets one install with both. Phase 5 includes a synthetic enforcer self-test (``tests/test_round75_r74_enforcer_smoke.py``) that mints an empty-footer .docx, runs the enforcer on it, and asserts ``word/footer1.xml`` carries the build label -- catches any wiring regression BEFORE the operator installs. Net pytest delta +24 (4715 -> 4739): 6 phase-1 + 5 phase-2 + 9 phase-3 + 3 phase-4 + 1 phase-5 enforcer self-test. All 4 ``make verify`` gates green: ruff clean, bandit 0 HIGH/MED, pip-audit clean. The Round 74 test floor (4715) is preserved as a hard floor; this round only adds, never weakens or skips. Operator-facing changes: zero on the existing UX -- the four R75 data fixes are quiet correctness wins, plus Build 49 finally installs the R74 footer enforcer + Ask AI delight bundle the operator hasn't seen yet.  # Round 74 / Build 48: footer hardening + Ask AI delight bundle.  Phase 1 (P0): defense-in-depth post-save XML enforcer ``_r74_footer_enforcer.enforce_build_label_footer`` that rewrites every footerN.xml part on disk AFTER the writer has saved (the R73 in-memory hardening worked in dev but was bypassed in production by a writer call site that invokes ``self.doc.save(path)`` directly without going through ``formatter.save()``; rather than chase the writer the enforcer operates on the .docx zip bytes so no writer can prevent the build label from landing).  Idempotent on a doc that already carries the label run; preserves all other zip parts byte-for-byte; never raises (returns ``{injected: False, reason: <error>}`` on any failure so the report-completion path is never bricked); a structured WARNING surfaces when injection actually had to happen so a future round can identify the offending writer.  Wired at all 4 report-completion call sites in app_simple.py adjacent to the existing R57 citation injector calls.  Phase 2 (P2): marked@11 + DOMPurify@3 added to ask_ai.html via cdn.jsdelivr.net (with proper SHA-384 SRI hashes + crossorigin + referrerpolicy); new ``_r74RenderMarkdownSafe`` helper converts LLM markdown answers into sanitised HTML (tight tag/attr allow-list -- no img/iframe/style/script/svg/form even after sanitization); transparent fallback to the pre-R74 line-by-line renderer when the CDN libraries are unavailable; ``[Source: ID]`` text nodes are recursively walked + replaced with new ``.r74-source-badge`` spans (XSS-safe via textContent + dataset, idempotent via class-name guard).  Phase 3 (P3): new ``POST /api/ask-ai-portfolio/stream`` SSE endpoint reuses the same retrieval pipeline as the sync endpoint then chunks the answer at word/newline boundaries via ``_r74_chunk_text_for_sse`` for instant first-token feedback; new ``_r74AskStreaming`` client uses fetch + ReadableStream + TextDecoder to consume SSE frames manually (EventSource doesn't support POST + custom CSRF headers); ``askAI`` is now a dispatcher that tries streaming first then falls back to ``_r74AskSync`` (the renamed-but-otherwise-verbatim original sync path) on any hard failure -- the operator never sees a streaming bug as a user-visible failure.  Phase 4 (P4): new ``GET /api/ask-ai/evidence/<query_id>/<source_id>`` lookup endpoint resolves a source ID back to its full evidence record (loopback-only, R71-rate-limited); both sync + streaming paths now persist the full evidence record list on the diag payload (under ``_r74_evidence_records``, capped at 32 KB per record body) and surface ``evidence_records`` in the response; new Bootstrap Offcanvas drawer (``#r74EvidenceDrawer``, slides in from the right with full a11y) wired to ``.r74-source-badge`` clicks via delegated click + keydown handlers on ``#answerContent`` -- in-memory lookup first, lookup-endpoint fallback on miss, structured status messages on every failure mode.  Phase 5 (P5): new conversation toggle (``#r74ConversationToggle``) with capped in-memory history (last 5 turns, 1500-char answer cap per turn) sent server-side via ``conversation_history`` payload field; ``_r74_apply_conversation_history`` helper synthesises a "Conversation context" prompt prefix; reset button clears the history; defensive against malformed entries.  Phase 6 (P6): new ``_r74_generate_follow_up_suggestions`` helper makes a constrained secondary LLM call to produce 2-3 follow-up question chips per answer (capped at 3, tight JSON parse with regex extraction, heuristic fallback that surfaces the active scope when the LLM is unavailable -- chips are bonus UX and MUST never block); new ``#r74FollowUpChipsContainer`` rendered below each answer; click-to-fill the question input (auto-submit when conversation is ON, fill-only when OFF).  Net pytest delta +90 (4625 -> 4715): 23 P1 + 19 P2 + 18 P3 + 16 P4 + 14 P5 + 15 P6 (the original plan estimated 46 tests; final count was 90 because each phase grew more pinning tests during implementation to cover both backend + template + JS shape + behavioural edges).  Two pre-existing pinning tests (``test_round68_ask_ai_client_retry::test_r68_attempt_initialized_at_top_of_askAI``, ``test_round71_query_id_always_recorded::test_round71_record_diag_always_called_after_grounded_ok``) updated to reflect the R74 refactoring (askAI -> _r74AskSync rename + diag wrapper rename to _r74_diag_to_persist); both contracts preserved (retry counter still initialized, diag still always recorded), only the reference identifiers changed.  Two deferrals: (1) the F1 root-cause writer call site is unknown -- the post-save enforcer is defense in depth, the structured WARNING log when it actually injects is the diagnostic seam for a future round; (2) the streaming endpoint chunks the FINAL answer post-hoc rather than streaming LLM tokens -- CircuIT proxy doesn't yet expose stream=True, so when it does this becomes a passthrough; client is already structured to consume true streaming when available.  Ten findings closed across nine fixes (F1-F9) plus three UI/UX changes (UX-1/UX-2/UX-3).  F1 (footer unkillable): 3-layer defense in ``_r68_build_label.apply_word_footer`` (Tier 1 strict + Tier 2 fallback + Tier 3 always-attempt) plus 12 call-site warning promotions so a footer wiring drift surfaces in the operator's stderr, NEVER as a silent missing footer; pinned by tests/test_round73_build_label_unkillable.py (22 tests).  F2 (Comprehensive Action_Plans empty-state provenance preserved): architectural fix in ``report_export_schema.apply_export_schema`` -- provenance marker frames (``_adoptiq_provenance_row=True``) now bypass column projection so the R64/B2 single-row ``Status=EMPTY`` provenance row survives the curated-column filter (was being silently dropped because the marker columns were not in the curated set); pinned by tests/test_round73_comprehensive_action_plans_provenance.py (7 tests).  F3 + F4 (Customer_Action_Plans + All_Support_Cases + Customer_Support_Cases curated columns): three sheets registered in ``report_export_schema.CURATED_COLUMNS`` so they get the same ~30-col customer-facing projection treatment as Critical_Adoption_Barriers / Action_Plans (R67/B7); pre-R73 these were leaking the raw 200+ column Snowflake dump into the operator's XLSX.  F5 (citation injector skips Heading/Title paragraphs): ``report_source_injector._rewrite_paragraph_with_inline_citations`` now early-returns when ``paragraph.style.name`` starts with ``Heading`` or ``Title`` so the chrome is no longer injected into section titles like ``Adoption Barriers (90)``; pinned by tests/test_round73_citation_injector_skips_headings.py (4 tests) plus full R57/R64 regression clean.  F6 (Renewal Report_Info canonical Item/Value 2-col schema): standardised the Renewal XLSX Report_Info sheet to the canonical ``Item`` / ``Value`` 2-col schema (was inconsistently using ``Field`` / ``Value`` while Compact / Comprehensive used ``Item`` / ``Value``); Leader's 4-col legacy schema preserved with parallel Item/Value projection so downstream readers can route either way.  F7 (HTML entity decoding in URL-bearing cells): ``data_normalization.strip_html_from_string`` now calls ``html.unescape(value)`` on entity-only strings (no tags) so ``https://example.com/?a=1&amp;b=2`` decodes to ``https://example.com/?a=1&b=2`` in the operator's XLSX (was leaking raw entities through cells that had no HTML tags but did carry ``&amp;`` in URL query strings); pinned by tests/test_round73_html_entity_decode.py (21 tests).  F8 (Comprehensive Report_Info Sheet_Title backfill): ``adoptiq_backend.write_excel_workbook`` now explicitly emits Sheet_Title rows for ``Summary`` and every populated ``CSConsole_*`` sheet (R66/B5 covered most data sheets but missed these two categories); pinned by tests/test_round73_comprehensive_sheet_title_coverage.py (9 tests).  F9 (Renewal Methodology vocabulary): replaced MEDIUM with MODERATE in the Risk Score Methodology paragraph (``report_utils._render_risk_scoring_explanation``) so the Renewal narrative is consistent with the R67/B6 user-facing vocabulary contract; pinned by tests/test_round73_renewal_methodology_no_medium.py (5 tests).  UX-1 (Preferences page): new ``GET /preferences`` Flask route + ``templates/preferences.html`` consolidates AI model selection (Ask AI + Report) and Intelligence toggle in one place, with informational sections for Appearance + Local Storage paths (settings.json + outputs + corpus); navbar link added in ``templates/base.html``; pinned by tests/test_round73_preferences_page_route.py (13 tests).  UX-2 (inline picker removed): the inline ``r69ReportModelCard`` block in ``templates/analyze.html`` and the ``r69AskAiModelCard`` block in ``templates/ask_ai.html`` are GONE; the corresponding ``static/js/r69_model_preferences.js`` script tags are also removed (the JS module now lives only on /preferences and the admin dashboard).  Existing R69 tests inverted to assert these elements are NOT present.  Pinned by tests/test_round73_analyze_no_inline_picker.py (12 tests).  UX-3 (strict 2-option dropdown): the model pickers on /preferences AND in the admin dashboard are now ``<select>`` dropdowns with EXACTLY two options (``gpt-5-nano`` and ``gemini-3.1-flash-lite``) and NO ``Default (env / config)`` sentinel -- per user direction "ok lets go with 2 option" so the operator's choice is unambiguous.  Server-side defense-in-depth via the new ``_R73_ALLOWED_MODEL_IDS`` frozenset in ``app_simple.py`` (validated in ``_r69_handle_model_setting``); a POST with a value outside the allow-list returns 400 + structured ``error="model_not_in_r73_allowlist"`` payload; empty string still accepted as the "clear override" sentinel.  ``r69_model_preferences.js`` and the inline admin JS bind to BOTH ``input`` and ``change`` events so the disable-Save-until-Test contract works for ``<select>`` elements; the JS gracefully handles a persisted_value that doesn't match any option (does NOT silently reset the dropdown).  Pinned by tests/test_round73_model_dropdown_allowlist.py (20 tests).  Two existing tests updated where they pinned pre-R73 contracts: ``tests/test_round48_renewal_report_info_warning_count.py::test_round48_excel_writer_reads_persisted_pdw`` (pre-F6 ``'Field':`` -> R73/F6 ``'Item':``) and ``tests/test_round69_model_preferences.py::test_r69_post_settings_report_model_persists_valid_value`` + ``test_r69_post_settings_accepts_empty_string_to_clear_override`` (pre-UX-3 ``gpt-4o-mini`` -> R73/UX-3 allow-listed ``gpt-5-nano``).  Net pytest delta: 4625 passed (+10 from Build 46 floor of 4615; the new R73 tests added in this round are partially offset by the small number of pre-R73 fixtures retired during the dropdown allow-list narrowing).  All 4 ``make verify`` gates green: ruff clean, bandit 0 HIGH/MED, pip-audit clean.  No Snowflake query, formatter, boot-order, corpus encryption, or admin-console structural changes ship in this build.  Operator-facing changes: (a) navbar gains "Preferences" link; (b) /preferences page renders 2-option model dropdowns + intelligence toggle; (c) analyze.html and ask_ai.html no longer carry the inline LLM picker card.  # Round 72 / Build 46: post-Round-71 live acceptance sweep + DMG bake. Closes 2 real Round 71 regressions surfaced by the LIVE HTTP iteration loop against real Snowflake/CSOne data on the Brian Frazier / Contact Center 90d baseline. Finding 1 (Renewal Risk_Score rounding parity): ``app_simple.py`` Renewal ``Key_Metrics`` sheet construction routes the ``Risk_Score`` field through a new ``_r72_round_risk_score`` helper (``round(float(value), 1)`` with TypeError/ValueError defensive passthrough) so the XLSX matches the DOCX (which already rounded to 1 decimal place via R71/Phase-4 #23). Pre-R72 the harness parity gate fired ``risk_score docx=9.8 vs xlsx=9.81204188481677`` on every Renewal scenario. Finding 2 (Leader Open Adoption Barriers cross-format parity): two-layer fix. Layer A in ``canonical_metrics.py`` -- the new ``_select_first_populated_status_column`` helper enumerates candidate status columns in their documented priority order (now ``("STATUS_C", "AB_STATUS_C", "Status", "STATUS")``, swapped from the pre-R72 ``("AB_STATUS_C", "STATUS_C", ...)``) and skips any candidate whose ``str.strip().str.len() > 0`` is all-False, so a present-but-100%-NaN ``AB_STATUS_C`` (the leader-pipeline Snowflake AB extract shape, where ``AB_STATUS_C`` joins from a sister table that is empty in this schema while ``STATUS_C`` carries the canonical labels) no longer wins the column-pick contest and silently zeros the count. Both ``count_open_barriers`` and ``count_closed_barriers`` route through the new helper so the open/closed sides of the leader denominator stay symmetric. Layer B in ``leader_report_generator.py`` -- ``_add_overall_individual_summary``'s ``total_open_abs`` is now derived from a single ``cm.count_open_barriers`` call on a ``pd.concat`` of all per-CSSM AB frames (the same input that ``_create_detailed_ab_list`` uses to build the XLSX ``Adoption_Barriers`` sheet), instead of the pre-R72 ``sum(member['open_abs'] for member in team_summary_data)`` that double-counted barriers attributed to multiple CSSMs via the ``_ATTRIBUTED_BY_ACCOUNT`` shared-account pathway. The defensive sum-fallback is retained inside the ``except`` branch so a pandas-version edge case cannot regress the docx to "no number". Layer A also wires per-CSSM ``open_ab_count`` / ``resolved_ab_count`` through ``cm.count_open_barriers`` / ``cm.count_closed_barriers`` for the Individual Team Member Performance table (was inline ``status_series.apply(self._is_status_open).sum()`` -- which counted RAW rows and used a permissive NOT-IN-CLOSED-TOKENS rule; the canonical helpers normalize via ``normalize_status_label`` then dedup by barrier ID). Pre-R72 the harness parity gate fired ``open_adoption_barriers docx=65 vs xlsx=63`` (65 raw "Open" rows -> 63 distinct IDs after canonical dedup) on every Leader scenario. Phase 1 acceptance (4 canonical scenarios x 1 iter against round72-acceptance baseline): comprehensive + compact PASS all gates; renewal + leader PASS all gates after fixes (parity_passed=True, mismatches={}). Phase 2 essentials confirmed: /api/diag/connectivity green (Snowflake + Keeper + CircuIT all ok); /api/grounding-diagnostics/<analysis_id> returns 200 with structured payload (R65/C-3 contract honored); Ask AI portfolio endpoint returned structured 503 for an ungrounded answer (R27 grounding gate working as designed, not a regression). Net pytest delta +28 (4467 -> 4495): tests/test_round72_renewal_key_metrics_risk_score_round.py (5), tests/test_round72_leader_open_ab_canonical_parity.py (7), tests/test_round72_canonical_status_column_skip_empty.py (11), plus 5 already-shipped pin tests pre-fix. All 4 ``make verify`` gates green: ruff clean, bandit 0 HIGH/MED, pip-audit clean.  # Round 71 / Build 45: post-Round-70 deep-audit sweep -- closes ~36 findings across 8 phases identified during a comprehensive code+logic review by six parallel explore agents after Round 70 cut. Phase 0 (3 P0 contract corrections after Round 70 review): #1 reverts the Round 70 over-reach for Compact ``Risk_Band`` (R70/#11 had remapped MEDIUM->MODERATE in the Risk_Band column itself, but the R67/B6 contract pins ``Risk_Band`` as the canonical band key for downstream filters; user-facing remap stays in ``Risk_Level`` only) -- ``Risk_Band`` now retains ``MEDIUM`` while ``Risk_Level`` carries the user-facing ``MODERATE``; #2 applies the MEDIUM->MODERATE remap to single-customer Renewal Key_Metrics.Risk_Category in the XLSX writer for parity with the R70/#11 multi-customer Renewal_Summary remap; #3 adds explicit gold-color branch (RGB 218,165,32) for MEDIUM/MODERATE customers in the Renewal Word dashboard table coloring loop plus a green HEALTHY branch (was falling through to default black). Phase 1 (5 security hardenings): #4 removes hardcoded ``1.0.3``/``1`` defaults from .github/workflows/build.yml so CI artifact names always reflect the SSoT in config.py; #5 fixes IP-spoof bypass in ``_client_ip_from_request`` -- X-Forwarded-For headers are now ONLY honored when the direct ``REMOTE_ADDR`` matches an IP in ``ADOPTIQ_TRUSTED_PROXY_IPS`` (or the legacy ``ADOPTIQ_TRUST_PROXY_HEADERS=1`` loopback escape hatch); #6 extends ``_SENSITIVE_ENDPOINTS`` to cover all sensitive APIs (api_shutdown, all corpus/intel/settings APIs, GET diagnostics) so default loopback-only enforcement reaches every operational seam; #7 strict ``_is_valid_analysis_id`` regex validation on ``/api/grounding-diagnostics/<analysis_id>`` and ``/api/ask-ai/diagnostics/<query_id>`` (return 400 on malformed input instead of probing the in-memory dict); #8 hardens ``/api/shutdown`` so ``ADOPTIQ_TESTING=1`` env var is IGNORED in frozen builds (only the Flask ``app.config['TESTING']`` flag set internally still short-circuits) -- prevents env-injection bypass of the would_shutdown guard in production. Phase 2 (5 corpus correctness fixes): #9 fixes the ``corpus_bootstrap.start_background`` ``completed=True`` gate that was silently blocking daily refresh after the first successful index pass -- adds explicit ``allow_refresh: bool = True`` parameter that callers can pass False for "skip if already done" semantics, daily refreshes always proceed; #10 sets ``_STATE.in_progress = True`` UNDER ``_BOOT_LOCK`` BEFORE the thread spawn (was racy -- two concurrent ``request_refresh`` calls could each see in_progress=False and spawn two indexer threads); #11 raises ``CorpusCryptoError`` on ``commit_to_disk`` WAL checkpoint failure (was swallowed at debug level -- a corrupt WAL would silently produce an encrypted DB containing only the 4096-byte SQLite header); #12 on ``index_folder`` exception, ``configure_connection(None)`` + ``handle.close(persist=False)`` + clear ``_HANDLE`` so a partial corpus state cannot bleed into the next request; #13 ``open_corpus_for_user`` raises ``CorpusCryptoError`` when the salt file is missing but an encrypted DB exists (silently regenerating the salt would brick the existing encrypted corpus -- fail loud instead). Phase 3 (4 LLM grounding-gate fixes): #14 wires ``ai_narrative_validator.validate_narrative`` into the subscription analysis Word writer (was unprotected -- LLM hallucinations went straight into the Word doc); #15 Compact ``validate_narrative`` exception path now substitutes the placeholder (FAIL CLOSED) instead of accepting the raw LLM output as a fallback; #16 wires the R27 grounding gate into the CLI ``adoptiq_backend.main()`` portfolio_summary AND customer_storyboard append paths -- the CLI was the last LLM-narrative seam not covered by the gate; #17 defensive ``sub_data.get('customer_name', subscription_id)`` guard in the subscription analysis flow (was raising bare ``KeyError`` when the upstream payload was missing the canonical key). Phase 4 (6 data-correctness fixes): #18 extends ``snowflake_table_policy._ALLOWED_CANONICAL`` with ``CX_DB.CX_SWSSBST_BR.RENEWAL_DATA`` so renewal opportunity prefetch queries pass the policy guard; #19 changes Action Plans CSConsole+Snowflake merge from ``keep='first'`` (CSConsole wins) to ``keep='last'`` (Snowflake wins) plus ``LastModifiedDate`` tie-break sort so the merged dataframe reflects source-of-truth precedence; #20 ``count_open_action_plans`` returns 0 + warning log when no recognized status column is present (was returning ``len(ap_df)`` which silently inflated the KPI when the schema was unexpected); #21 ``leader_report_generator.fetch_action_plans_snowflake`` wraps ``ctx.cursor()`` with ``enhanced_snowflake_insights._PolicyEnforcingCursor`` so the leader path's Snowflake queries are subject to the same table-policy guard as the rest of the codebase (was bypassing); #22 ``cross_reference_refs`` uses the ``LIKELY_TITLE_COLS`` resolver instead of hardcoded ``row.get('title')``/``row.get('Title')`` (was missing CSOne titles under ``SUBJECT``/``Subject``/etc); #23 pins risk score 0-10 rounding to ``.1f`` in all app_simple.py call sites for consistency with the SSoT in ``risk_scoring.py`` (one Compact dashboard f-string was using ``.2f`` while every other surface used ``.1f`` -- the dashboard table read ``5.40/10`` while the narrative said ``5.4/10`` for the SAME customer). Phase 5 (6 diagnostic / observability hardenings): #24 ``_r64_record_grounding_outcome`` now FIFO-trims rejection_records at ``_R64_MAX_GROUNDING_RECORDS=50`` (was silently stop-appending after 50 -- the tail of long reports was invisible); #25 ``_r65_grounding_excerpt`` redacts customer names from briefing_excerpt + narrative_excerpt before persisting to ``analysis_status.json`` (was leaking PII -- the file is loopback-only but PII-clean diagnostics are the contract); #26 per-IP token-bucket rate limiter on ``/api/ask-ai/diagnostics/<query_id>`` and ``/api/grounding-diagnostics/<analysis_id>`` (30 req/min per IP, 1024-bucket cap with FIFO eviction) -- prevents a misbehaving polling client from starving the analysis worker by holding ``analysis_status_lock``; #27 ``_record_ask_ai_query_diag`` is ALWAYS called after a grounded answer, even when ``retrieval_diag`` is empty (was conditional -- ``query_id`` returned to the UI could 404 on the diagnostics lookup), minimal stub payload created when no diag fields are populated; #28 ``model_resolver._read_env_value`` adds an inline regex (``^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$``) as defense-in-depth fallback when ``adoptiq_settings`` import fails (otherwise a malformed env-var model name would propagate to CircuIT); #29 extends ``_r69_sanitize_llm_error`` regex to redact password/passwd/pwd/api_key/sk-prefixed tokens/Authorization headers (was missing these patterns -- only Bearer/JWT were covered). Phase 6 (4 cosmetic / vocabulary fixes): #30 extends the R70/#10 ``test_round70_no_medium_in_user_facing.py`` lint to catch MEDIUM literals in f-strings AND ``.format()`` insertions inside ``add_run``/``add_paragraph``/``add_heading`` calls (the original lint only caught dict assignments); #31 renames Compact "Immediate Actions" priority label MEDIUM->MODERATE for parity with the R67/B6 user-facing vocabulary contract; #32 ``adoptiq_setup.iss`` ``MyAppVersion`` now sourced from ``ADOPTIQ_VERSION`` env var at compile time (was hardcoded ``1.0`` -- installer name drifted from config.py SSoT for several builds); #33 ``templates/help.html`` extended with R68 build label / restart banner / R69 model preferences / clean-quit documentation (was missing all four post-Round-67 features). Phase 7 (3 build-system parity hardenings): #34 ``adoptiq_pc.spec.hidden_imports`` aligned with ``adoptiq_mac.spec`` for all lazy-imported modules (report_source_injector, report_iteration_loop, knowledge_schema, corpus_crypto/indexer/retriever/bootstrap, ask_ai_corpus, report_corpus_context, adoptiq_settings, ask_ai_embeddings, fastembed/onnxruntime/tokenizers) PLUS adds ``model_resolver`` to BOTH spec files (Round 69 / Build 43 lazy-imported it but never pinned it -- the Windows .exe was silently degrading to platform-default model); #35 ``build_mac_dmg.sh`` PYTHON_BIN venv probe unified with ``build_mac.sh`` (was probing twice with different variable names -- BAKE_PYTHON_BIN vs PYTHON_FOR_VER) so the bake step and version readback always use the same interpreter; #36 new ``tests/test_round71_env_keys_lint.py`` scans the codebase for ``os.environ.get('SECRET_LIKE_*')`` patterns and asserts every match is listed in ``embed_credentials.ENV_KEYS`` (would have caught the R69 ``CIRCUIT_MODEL_NAME_*`` keys being unbundled if introduced before R69 / Build 43 documented them). Phase 8 (verify + ship): #37 adds 24 new test files (~146 tests) covering every Phase 0-7 finding with both source-shape pins (greppable assertions on file content) AND artifact-level integration assertions (exercise the actual writer on synthetic data, then assert on the produced bytes). Net pytest delta +146 (4321 -> 4467 expected). All 4 ``make verify`` gates remain green: ruff clean, bandit 0 HIGH/MED, pip-audit clean. The Round 70 test floor (4321) is preserved as a hard floor; this round only adds, never weakens or skips. Operator-facing changes: zero -- every behavior change is internal correctness/security, not UI/UX -- the ONLY user-visible diffs are (a) the gold MEDIUM band and green HEALTHY band coloring in the Renewal Word dashboard, (b) the MODERATE Immediate Action priority label in Compact, and (c) the R68/R69 advanced features section newly documented in the in-app help. Build 43 acceptance regression sweep -- closes 14 confirmed regressions across the four report formats (Compact / Renewal / Comprehensive / Leader) that pure unit tests on synthetic dataframes failed to catch because the production code paths in ``app_simple.py`` use inline writers that bypass the SSoT helpers the unit tests pin. Phase 1 (P0 -- restore the build label everywhere): adds ``apply_word_footer`` to the previously-unwired Word save sites in ``_create_simple_renewal_report`` (renewal narrative), the compact enhanced-fallback report path, the subscription analysis word writer, ``executive_report_builder.ExecutiveReportBuilder.save`` (Comprehensive CLI smoke harness), AND both branches of the leader-report post-TAC regen (the post-TAC and no-TAC final ``doc.save`` paths -- the post-TAC branch was wiping the footer wired by R68/A1 by rebuilding ``Document()`` from scratch). Phase 1 also adds 12 new artifact-level tests under ``tests/test_round70_build_label_artifacts.py`` that actually open the generated ``.docx`` / ``.xlsx`` artifacts (via ``python-docx`` / ``openpyxl``) and assert the footer + Report_Info build label rows are present in the produced bytes -- pre-R70 unit tests verified the call site existed but never opened the artifact, so the Build 43 stale-binary trap (0 footer paragraphs in 4 docx + 0 build label rows in 4 xlsx) sailed straight through. The new ``test_every_known_word_save_site_has_footer_wiring`` meta-test scans all ``doc.save(...)`` calls in the known production Word writer modules (``app_simple.py``, ``leader_report_generator.py``, ``executive_report_builder.py``, ``executive_intelligence_formatter.py``) and asserts each carries an associated R68/A1 or R70/Phase 1 marker comment so a future writer can never silently ship without the footer. Phase 2 (P0 -- Compact + Renewal + Comprehensive schema): #4 reinforces the Compact ``Risk_Summary`` ``columns=['Customer', 'Overall_Risk_Score', 'Risk_Score', 'Risk_Level', 'Risk_Band', 'Adoption_Barriers', 'Support_Cases']`` constructor with a contract comment naming Round 70 so a future row-shape regression that drops the canonical ``Overall_Risk_Score`` (R67/B6) silently is caught at the column-pin source-shape test; #5 the Renewal ``Renewal_Summary`` writer in ``_create_simple_renewal_report`` previously could drop the ``Risk_Score_0_100`` and ``Risk_Band`` columns when no MEDIUM-band customer was present (the ``_r67_row`` setdefault was inside the remap-conditional branch); the fix hoists the ``Risk_Score_0_100`` and ``Risk_Band`` defaults out of the conditional so EVERY row always carries both columns AND defines a new ``_r70_renewal_canonical_cols`` tuple ``('Customer', 'Overall_Risk_Score', 'Risk_Score_0_100', 'Risk_Level', 'Risk_Band', 'Analysis_Date', 'Next_Review_Date')`` that is pre-populated on every row dict via ``setdefault`` AND passed as the explicit ``columns=list(_r70_renewal_canonical_cols)`` arg on the DataFrame constructor so pandas can never silently drop a column even if the row dicts vary; #6 reinforces the Comprehensive ``Risk_Components`` sheet contract (the sheet was hoisted out of the broad try/except in R67/B2 and remains there in R70 -- Round 70 adds a contract comment naming the round so a future edit can't silently re-wrap it); #7 reinforces the Compact ``Critical_Adoption_Barriers`` + ``Action_Plans`` curated projection (R67/B7 routed both through ``_r15_apply_export_schema`` via ``_CURATED_AB_DETAIL_ALL`` / ``_CURATED_ACTION_PLANS`` -- Round 70 pins the helper invocation source-shape so the raw 243-259 col Snowflake dump regression cannot recur silently); #8 reinforces the Compact ``Report_Info`` canonical Item/Value schema with ``Sheet_Title:<sheet_name>`` keyed rows (R67/B5 fixed the legacy Status/Warning/Generated_At schema; Round 70 pins the schema source-shape); #9 reinforces the Comprehensive ``Report_Info`` ``Sheet_Title:<sheet_name>`` rows for all 7 data sheets (R67/B5 added these; Round 70 pins the source-shape). Phase 3 (P1 -- HTML strip + vocabulary): #10 extends the ``_R66_HTML_STRIP_SHEETS`` allow-list in ``adoptiq_backend.py`` (used by ``write_excel_workbook`` / Comprehensive) with ``All_Support_Cases``, ``Customer_Support_Cases``, ``External_Incidents``, ``TAC_Cases`` AND adds source-side ``strip_html_from_dataframe`` defenses inside the Compact / Renewal / Leader inline writers in ``app_simple.py`` (which bypass the central ``write_excel_workbook`` allow-list); the Compact path covers ``All_Support_Cases``, ``Customer_Support_Cases``, ``External_Incidents``, ``TAC_Cases``, ``Critical_Adoption_Barriers``, ``Action_Plans``, ``Customer_Pulse``, ``Success_Priorities``, ``Adoption_Barriers``, ``All_Adoption_Barriers``; the Renewal path covers ``Customer_Support_Cases``, ``Customer_Adoption_Barriers``, ``Customer_Action_Plans``, ``Customer_Customer_Pulse``, ``Customer_Success_Priorities``; the Leader path covers ``TAC_Cases``, ``External_Incidents``, ``Adoption_Barriers``, ``Customer_Pulse``, ``Success_Priorities``, ``Action_Plans``. Build 43 acceptance found 6 cells with raw HTML markup: 4 in Compact ``All_Support_Cases``, 4 in Renewal ``Customer_Support_Cases``, 1 in Leader ``External_Incidents``, 1 in Leader ``TAC_Cases``, 2 in Comprehensive ``CSOne_Detail_All`` (R67/B4 covered), 1 in Comprehensive ``External_Incidents``. #11 applies the ``{MEDIUM -> MODERATE}`` user-facing label remap to FIVE additional render sites in the Renewal Word builder + the matplotlib gauge labels: the Top-10 Focus Accounts table cell paint (was painting raw ``ana.get('renewal_risk_category', 'N/A')`` -- 8 cells leaked MEDIUM in Build 43), the Risk Score box ``score_run = summary.add_run(f'{risk_score:.1f}/100 ({risk_category})')`` text, the matplotlib donut gauge ``ax.text(0, 0, f'{...}\\n{risk_category}', ...)`` text (embedded PNG), the panel-2 of the 2x2 portfolio chart ``ax2.pie(..., labels=[f'{risk_cat} ({...}/100)', ...])`` wedge label, AND the Compact ``Risk_Band`` column at the row-build site (was previously kept as the canonical band key on the R67/B6 argument that band-based filters expected ``MEDIUM`` -- but the operator's eyes are on the artifact, so MEDIUM in the user-facing cell is a vocabulary regression regardless of the internal lookup contract; downstream color/format lookups in the same writer block use the local ``band`` variable which still carries the canonical key, so internal filters keep working). Phase 4 (P1 -- Leader post-TAC regen): #12 was largely shipped in the previous session -- the post-TAC regen branch in ``leader_report_generator.generate_leader_report`` previously rebuilt a fresh ``Document()`` after ``add_tac_cases_from_csone`` and replayed most of the initial-pass section list but silently dropped ``_add_team_insights_section`` (aging / leaderboard / coverage rollups) AND ``_add_overall_individual_summary`` (per-CSSM individual summaries) AND the R68/A1 ``apply_word_footer`` invocation. Build 43 acceptance confirmed both rollup sections were missing AND the v1.0.4 build 43 footer stamp was unreachable on this branch. The fix mirrors the initial-pass section list around line 867-901 exactly so the post-TAC regen produces a byte-equivalent document modulo the TAC integration. Phase 5 (regression guards): adds 5 new test files (51 new tests) under ``tests/test_round70_*.py`` covering compact_risk_summary_overall_score (8 tests), renewal_summary_scale_and_columns (10 tests), comprehensive_risk_components_present (3 tests), compact_curated_projection (4 tests), html_strip_full_coverage (12 tests), no_medium_in_user_facing (7 tests). The new tests deliberately use both source-shape pins (greppable string assertions on the writer source) AND in-memory synthetic dataframe contract assertions (mirroring the writer's row-dict shape) so the test suite catches regressions at both the source and the artifact layer. Net pytest delta +51 (4270 -> 4321 expected; the 12 ``test_round70_build_label_artifacts.py`` tests shipped in the previous session). All four ``make verify`` gates remain green: ruff clean, bandit 0 HIGH/MED, pip-audit clean. The Leader report richness is verified intact (11 direct reports, 165 tables, 340 headings, 0 stability errors, 0 AI-unavailable placeholders) -- the user's primary concern of "the Leader report stayed detailed" is true and Round 70 explicitly preserves it. Operator-flippable LLM model preferences (test-before-save). Adds a single seam (settings + env + hardcoded-default precedence chain via the new ``model_resolver`` module + a keyword-only ``model_name`` kwarg on ``adoptiq_backend.generate_llm_response`` and ``generate_llm_json_response``) and threads ``model_resolver.get_active_ask_ai_model()`` into the four Ask AI call sites (ask_ai_grounded.run_portfolio_grounded_ask_ai, ask_ai_grounded.run_intel_grounded_ask_ai, app_simple._compose_legacy_ask_ai_answer fallback, app_simple./api/ask-intel) AND ``model_resolver.get_active_report_model()`` into the five report narrative call sites (compact AI insights, per-customer storyboard CLI + web, portfolio summary CLI, subscription analysis). Threading is intentionally asymmetric so Ask AI and Report can be flipped independently without contaminating each other's validation surface (Ask AI eval scorecard vs R27 grounding rejection rate). Three new endpoints on the main app (``GET/POST /api/settings/ask-ai-model``, ``GET/POST /api/settings/report-model``, ``POST /api/llm/ping``) are protected by the same dual-path auth as ``/api/corpus/refresh`` (CSRF token OR ``X-AdoptIQ-Internal``) and surface through three matching admin proxy routes (``/admin_settings/ask_ai_model``, ``/admin_settings/report_model``, ``/admin_settings/llm_ping``). Validation is allow-list ``^[A-Za-z0-9._-]{1,128}$`` (``adoptiq_settings._is_valid_model_name``, empty string accepted as a "clear override" sentinel) so a typo or a malformed model name never reaches CircuIT; upstream CircuIT errors flowing through ``/api/llm/ping`` are sanitised by ``_r69_sanitize_llm_error`` (Bearer/JWT/api_key/Authorization redacted, message capped at 200 chars) before echo to prevent token leakage in the operator's browser. Test-before-save UX is implemented in the shared ``static/js/r69_model_preferences.js`` module: the Save button stays disabled until the operator clicks Test and ``/api/llm/ping`` returns ``ok=true`` for the proposed model name; an empty input always enables Save (it clears the override). The card lives on the analyze page (``templates/analyze.html`` -- "Report narrative model preference"), the Ask AI page (``templates/ask_ai.html`` -- "Ask AI model preference"), AND the admin dashboard (mirrored in ``enhanced_admin_dashboard_v2.py`` inline template). Diagnostic transparency: the active model name is stamped onto every Ask AI diag record (``_ASK_AI_DIAG_BUFFER``) AND surfaced in ``/api/ask-ai-portfolio`` JSON responses + the R68 debug chip footer (new ``#r69DebugChipModel`` span in ``static/js/ask_ai.js``) so the operator can prove which model produced a given answer. Build pipeline: ``CIRCUIT_MODEL_NAME_ASK_AI`` + ``CIRCUIT_MODEL_NAME_REPORT`` added to ``embed_credentials.ENV_KEYS`` and documented in ``secrets.env.template`` with a deliberate warning that flipping the report model is higher-risk than Ask AI (touches R27 grounding-rejection rate). Two existing test fixtures updated to accept the new ``**kwargs`` on ``generate_llm_json_response`` mocks (``test_round4_ask_intel_handles_dict_fetch_errors.py``, ``test_ask_intel_prompt_includes_fetch_errors.py``) -- legacy generic 2-arg ``generate_llm_response`` mocks (``test_llm_json_response.py``, ``test_round4_generate_llm_json_response_validates_claim_shape.py``) keep working because the wrapper conditionally passes ``model_name=`` only when explicitly supplied. ``model_resolver`` deliberately does NOT cache so a UI flip takes effect on the very next call. Critical Rule contract: future endpoints that accept a model name MUST validate via ``adoptiq_settings.is_valid_model_name(...)`` AND require a successful ``/api/llm/ping`` BEFORE persisting -- the test-before-save UX depends on it, and silently writing an unprovisioned model name reproduces the Round 67 stale-binary trap class of bug. Net pytest delta +43 (4227 -> 4270) via tests/test_round69_model_preferences.py covering settings schema + validation, ``CIRCUIT_CONFIG`` shape, ``model_resolver`` precedence (settings > env_specific > env_main > hardcoded default), ``generate_llm_response`` + ``generate_llm_json_response`` keyword-only ``model_name`` threading, the asymmetric "wires-don't-cross" guard (Ask AI sites use the Ask AI resolver, Report sites use the Report resolver), API endpoints (success / validation / sanitisation / dual-auth), admin proxy routes, UI source-shape (templates + JS module), diagnostic surface (``model_name`` stamped on ask_ai diag), and build metadata (env vars + secrets template). All 4 ``make verify`` gates green: ruff clean, bandit 0 HIGH/MED, pip-audit clean. # Round 68 / Build 42: Stale-binary trap, OneDrive gating, Ask AI overhaul. Phase 0 closes the operator-trap that produced Build 40 reports from a still-running pre-Build-41 process: A1 visible build label in every Word + XLSX report (new ``_r68_build_label.py`` helper module captures App_Version / App_Build / Process_Started_At_UTC at app startup + Report_Generated_At_UTC per call; injected into XLSX Report_Info via ``append_build_label_records[_4col]`` and ``append_build_label_rows_pairs`` and into Word section footers via ``apply_word_footer``); A2 ``GET /api/version`` endpoint returns ``{version, build, process_started_at_utc, code_loaded_at_utc, dmg_install_at_utc, restart_required}`` where ``restart_required: true`` when ``Path(sys.executable).stat().st_mtime > process_started_at_utc`` (frozen build only); A2 templates/base.html includes ``static/js/r68_restart_banner.js`` which polls /api/version once on first analyze-page load and renders a non-dismissable yellow banner when restart is needed; A3 28 new tests under tests/test_round68_build_label_in_every_report.py + tests/test_round68_version_endpoint.py pin every Word + XLSX label call site, the endpoint shape, the banner DOM, and the JS polling. Phase 1 closes real report bugs that would still be present on a true Build 41 run: A4 portfolio drift retry-then-escalate (pre-R68 the R25B/R25C validators raised ValueError on numeric/risk-band drift which collapsed straight to the bland ``"Portfolio-level AI summary withheld"`` paragraph 12 times in one Comprehensive run; R68/A4 caps retries at ``_R68_MAX_PORTFOLIO_DRIFT_RETRIES = 2`` with a tightened correction prompt naming the drifted field + canonical value, escalates to a deterministic narrative built from canonical_metrics on exhaustion, persists ``analysis_status['portfolio_llm_diag']['drift_attempts']`` + ``unique_failures`` for operator visibility); A5 per-customer LLM diag (pre-R68 2 customers in the Comprehensive docx fell to ``"AI analysis temporarily unavailable"`` with no per-customer rollup; R68/A5 adds ``_r68_record_per_customer_llm_outcome`` that maintains ``analysis_status['per_customer_llm_diag']`` with per-customer first_error_kind + attempts + portfolio-level fallback_rate, default acceptance <5%); A6 install-and-quit operator checklist appended to QUALITY_AUDIT.md + CURSOR_MAC_BUILD_INSTRUCTIONS.md so a future operator never re-triggers the stale-binary trap (1. ``pkill -f AdoptIQ.app``, 2. drag ``OUTBOX/AdoptIQ.app`` to ``/Applications``, 3. re-launch, 4. hit ``/api/version`` and confirm ``build`` matches). Phase 2 hardens OneDrive auth gating per the user's invariant (``no auth = no data; auth = no false-block``): B1 ``get_latest_csone_from_folder_diag`` returns 3-tuple ``(path, sync_status, real_file_count)`` and skips ``st_size == 0`` files (OneDrive Files-On-Demand placeholders), legacy ``get_latest_csone_from_folder`` is now a thin back-compat wrapper, the leader endpoint records a ``partial_data_warnings`` entry with ``kind='no_onedrive_sync'`` when autopicked is None and sync_status != 'synced'; B2 ``static/js/intel_status.js::classifyCorpusPanel`` adds explicit ``self_healed_baked`` branch (Round 39 valid state) instead of falling through to ``unknown``; B3 templates/analyze.html corpus panel comments updated to reflect Round 53 fail-closed semantics + the new ``self_healed_baked`` state; B4 ``corpus_bootstrap._check_onedrive_sync_status`` docstring corrected to match the single-level iterdir impl (was implying recursive walk); B5 daily refresh worker's ``transition_unblocked`` ALSO checks ``_r68_onedrive_sentinel_present()`` before triggering ``request_refresh`` so we don't burn refresh attempts when the sentinel hasn't landed yet (continues with accelerated 30s tick instead); B6 3 new test files (test_round68_csone_autodiscovery_skip_zero_byte.py, test_round68_corpus_panel_self_healed_branch.py, test_round68_daily_worker_sentinel_gate.py) pin every B1/B2/B5 fix. Phase 3 ships the Ask AI 100% accuracy + 100% stability + UX overhaul (NO multi-agent on CircuIT runtime per user direction): C1 single rank_evidence per request (saves 1 embedding+RRF round-trip per question; ``build_evidence_context_with_ranking`` returns the 4-tuple, ``build_evidence_context`` becomes a thin back-compat wrapper, ``compute_retrieval_diag`` accepts ``precomputed_ranked``); C2 100-entry FIFO diag persisted to a new ``ask_ai_diag`` table inside ``admin_monitoring_v2.db`` (survives process restart so a user can share a query_id across a crash; ``_r68_ensure_ask_ai_diag_table`` + ``_r68_persist_ask_ai_diag`` + ``_r68_load_ask_ai_diag`` keep the FIFO contract on disk); C3 client-side error classification + retry with backoff (``R68_ERROR_KINDS`` map covers rate_limit_429 / vpn_disconnected / server_5xx / llm_timeout / network / invalid_question; ``_r68MaybeRetry`` wraps the fetch with exponential backoff (1s/2s/4s, max 3 attempts) for transient classifications; tailored toast per kind via ``_r68ShowToast``); C4 portfolio-aware risk profile aggregator (raised ``_RISK_PROFILE_CAP`` from 200 to 500 via ``ADOPTIQ_ASK_AI_RISK_PROFILE_CAP``, introduced streaming mode for portfolios > 500 that entirely skips per-customer scoring loop to bound latency, four-state honest CANONICAL_HEADLINE coverage disclosure: FULL / PARTIAL / STREAMING / NONE); C5 query_id + retrieval method debug chip (footer ``#r68DebugChip`` shows ``retrieval: hybrid`` + ``debug: <query_id>`` + Copy debug ID button; both grounded and legacy ungrounded paths now mint a query_id and set retrieval_method); C6 personalized Ask AI suggestion chips (new ``GET /api/ask-ai/suggestions?manager=&technology=&days=`` endpoint returns 4 personalized chips + 2 fallback grouped by category: top_risk / stale_barriers / recent_renewal / last_report; chips refresh on selector change; never blocks on Snowflake / LLM); C7 clickable citation badges (response payload returns ``evidence_index`` from the ranked records actually used, capped at 200 entries with bounded snippets; ``ask_ai.js::_r68BuildCitationBadge`` post-renders ``[Source: ID]`` as clickable badge with popover showing snippet + source_type + customer + timestamp); C8 localStorage chat history (last 20 Q/A in ``r68_ask_ai_history`` key with FIFO trim, dedupes consecutive identical questions, ``Re-ask`` / ``Refine`` / ``Copy`` / ``Delete`` actions per entry, XSS-safe via textContent, scope metadata captured per entry, ``Clear all`` + collapsible toggle); C9 3-step progress indicator (Fetch -> Retrieve -> Synthesize via ``_r68SetStep``, advances via setTimeout at 5s/12s, aria-valuenow updates for screen readers, Cancel button aborts via namespaced ``window._R68_ASK_AI_RUNTIME.activeAbort`` slot, first-time empty-state tip card dismissible via ``r68_ask_ai_tip_dismissed`` localStorage flag); C10 build42 eval scorecard (``tests/ask_ai_eval/scorecards/build42.md`` re-runs 50-question suite at 100% pass under hybrid retrieval mode + all C1-C9 changes; pinned by tests/test_round68_eval_build42.py with both static scorecard checks and live re-run against the offline cassettes). Net pytest delta +176 (4051 -> 4227): ~28 Phase 0 + ~36 Phase 1 + ~30 Phase 2 + ~75 Phase 3 + ~7 C10 eval pin. All 4 ``make verify`` gates green: ruff clean, bandit 0 HIGH/MED, pip-audit clean. Two existing tests updated where they pinned pre-R68 behavior: test_round27_ai_storyboard_validation.py (R25B/R25C no longer raises, A4 retry-then-escalate replaces it) and test_round38_leader_endpoint_provenance.py (now also stubs the new ``get_latest_csone_from_folder_diag`` helper since the leader endpoint calls the diag-aware version directly). 100% accuracy + stability sweep on Build 40 acceptance reports. Closes 8 confirmed bugs across the Comprehensive / Compact / Renewal / Leader pair: B1 (Compact vs Renewal cross-format scoring divergence -- Compact published 0-10 + ext_incidents=None while Renewal published 0-100 + ext_incidents=filtered, so WORLD BANK GROUP US scored 4.9 in Compact and 50.0 in Renewal for the same scope; fix threads ext_incidents into compact_report_formatter.calculate_renewal_risk_scores via _r65_filter_customer_tagged_incidents and aligns Renewal Overall_Risk_Score to the 0-10 scale + Risk_Level=MODERATE vocabulary, preserving Risk_Score_0_100 as a back-compat secondary column), B2 (Comprehensive XLSX missing the Risk_Components sheet entirely -- R66/B6 had added it but a broad outer try/except silently swallowed any row-construction exception, so the sheet never landed in all_sheets; fix hoists the assignment OUT of the broad try/except so the sheet is ALWAYS written, either with constructed rows or a single _adoptiq_provenance_row=True fallback citing the construction error, plus structured logger.warning + logger.debug for operator visibility), B3 (Comprehensive XLSX Report_Info truncated to 2 rows -- Build 40 acceptance showed only Export type + Generated at (UTC) despite the R66/B5 contract requiring Manager / Technology / Days + per-sheet Sheet_Title rows; fix adds explicit logger.info to write_excel_workbook AND to run_comprehensive_analysis immediately before the writer call so production drift is greppable), B4 (CSOne_Detail_All Problem Details cells leaked raw <br /> / <agent name> / &#34; markup -- the sheet was missing from _R66_HTML_STRIP_SHEETS allow-list; fix adds CSOne_Detail_All to the tuple), B5 (Compact Report_Info schema drift -- Compact emitted Status / Warning / Generated_At columns instead of the canonical Item / Value pair used by Renewal / Leader / Comprehensive; fix refactors the writer to align to Item, Value with Sheet_Title:<sheet> keyed rows + Partial_Data_Warning + Excel_Truncation rows), B6 (Compact Risk_Summary.Risk_Score column-name parity -- renamed to Overall_Risk_Score for parity with Renewal Renewal_Summary.Overall_Risk_Score, with Risk_Score retained as a back-compat alias column, plus MEDIUM -> MODERATE remap for Risk_Level vocabulary parity), B7 (Compact Critical_Adoption_Barriers + Action_Plans were raw 243-259 col Snowflake dumps -- fix routes both sheets through _r15_apply_export_schema by adding them to CURATED_COLUMNS in report_export_schema.py, projecting to ~30 customer-facing columns each), B8 (R27 grounding rejection rate 21.1% on Build 40 vs <=10% target -- the dominant failure mode was single-decimal percentages like 28.6% / 41.6% / 99.9% that the LLM derives from briefing pairs the strict R66/B11 ratio check could not cover; fix adds a third-chance auto-grounding rule in validate_grounded_numbers for any value with a % suffix in [0.0, 100.0] expressible as round(value, 1) -- conservative scope, only %-suffixed tokens; ARR-class hallucinations and bare integers without % are still scrutinised). Pinned by 56 new tests under tests/test_round67_*.py: compact_renewal_score_parity (6), compact_xlsx_report_info_schema (7), compact_xlsx_schema_no_raw_snowflake_dump (6), comprehensive_risk_components_always_present (7), csone_detail_html_strip (4), grounding_rejection_below_10_percent (18), renewal_score_scale_and_band (8). Two existing tests updated where they pinned outdated shapes: test_round66_p0_html_strip.py (Action_Plans HTML test now feeds canonical SUBJECT_C / DESCRIPTION_C since R67/B7 routes Action_Plans through curated projection) and test_round66_p1_risk_components_per_customer.py contract preserved (R67/B2 emits BOTH logger.warning + logger.debug). Phase 4 (build-script footgun follow-on): Round 67's first DMG build produced AdoptIQ-v1.0.4-build1.dmg even though config.py said build "41" -- the same Round 61 footgun re-emerging at a layer above update_version_pc.py. Pre-R67 build_mac.sh, build_mac_dmg.sh, and build_pc.bat all wrapped ${ADOPTIQ_BUILD:-1} and explicitly exported the resulting "1" to update_version_pc.py, defeating the R61 SSoT lookup. Fix removes the hard-coded defaults from all three scripts; they now pass empty env vars through (which update_version_pc.py correctly treats as "use the existing config.py value") and read the resolved version back from config.py after update_version_pc.py runs. Pinned by tests/test_round67_build_scripts_no_footgun.py (10 tests). Test floor: 3986 (Build 40) -> 4051 (+65 from R67: 56 phase 1-3 fixes + 10 build-script regression guards). All 4 make verify gates green: ruff clean, bandit 0 HIGH/MED, pip-audit clean.  # Round 66 / Build 40: cuts Pass 4 (Ask AI offline eval framework: 5 portfolio fixtures + 50 golden-set questions across 8 categories + record/replay MockCircuitClient + per-question predicate evaluator + baseline scorecard tracked at tests/ask_ai_eval/scorecards/baseline.md showing 100% pass against the synthetic cassettes; ``make eval-ask-ai`` Makefile target + ``eval`` pytest marker keep the eval out of the default ``pytest -q`` so CI stays fast; the ``compose_grounded_answer`` evaluation seam in ``ask_ai_grounded.py`` is the public contract the runner injects against; one-time ``MOCK_CIRCUIT_MODE=record`` operator step documented in QUALITY_AUDIT.md so the cassettes can be re-recorded against live CircuIT when the prompt body drifts) and Pass 5 (hybrid retrieval -- BM25 + dense + Reciprocal Rank Fusion via the new ``ask_ai_embeddings.py`` module; ``fastembed>=0.8.0`` + ``BAAI/bge-small-en-v1.5`` ONNX INT8 quantized model bundled at ``Resources/embeddings/`` per the mac and pc PyInstaller specs (~+33 MB DMG); dense vectors persisted in a new ``chunk_vectors`` SQLite table inside the existing AES-GCM-encrypted corpus DB so they inherit the at-rest protection + WAL-checkpoint commit contract -- a parallel ``.npy`` would have introduced a new commit-order coupling and a new encryption channel; ``knowledge_schema.SCHEMA_VERSION`` bumped 1->2 so existing on-disk corpus DBs trigger a rebuild and pick up the new table; ``scripts/bake_corpus.py`` extended to compute one 384-dim float32 vector per ``playbook_chunks`` row in 64-batch increments and stamp ``model_id`` per row, with the R39-style decrypt round-trip self-test extended to verify a sample vector decodes to the expected dimension; ``ask_ai_grounded.EvidenceRecord`` extended with ``bm25_rank``/``dense_rank``/``rrf_score`` optional fields (positional-arg backward-compat preserved); ``rank_evidence`` dispatches to ``_hybrid_rank_evidence`` when ``Config.ASK_AI_RETRIEVAL_METHOD == 'hybrid'`` (default) and falls through to lexical when ``ask_ai_embeddings.get_embedder()`` returns None so a degraded environment still produces an answer; ``corpus_bootstrap`` warms the embedder on a daemon thread alongside the corpus thread so the first Ask AI query never pays the 1-3s ONNX cold start; new ``GET /api/ask-ai/diagnostics/<query_id>`` admin endpoint backed by a 100-entry FIFO ring buffer surfaces the per-query method/bm25_top/dense_top/rrf_top/model + top-10 record summaries -- loopback-only, no PII, mirrors the R65/C-3 grounding-diagnostics shape; ``ASK_AI_RETRIEVAL_METHOD`` / ``ASK_AI_EMBEDDING_MODEL`` / ``ASK_AI_EMBEDDING_DIM`` / ``ASK_AI_RRF_K`` env knobs in ``config.py`` give operators a per-process escape hatch without a rebuild). Build 40 scorecard ``tests/ask_ai_eval/scorecards/build40.md`` shows 100% pass under hybrid mode (graceful degradation to lexical confirmed against the synthetic baseline -- the >=15% uplift gate from the plan applies to live CircuIT recordings, not to the synthetic cassettes which are too pure for that signal). 24 new shape tests in ``tests/test_round66_p5_hybrid_retrieval_shape.py`` pin RRF math (Cormack 2009 k=60), vector encoding round-trip (bit-exact), schema bump enforcement, ``chunk_vectors`` ON DELETE CASCADE FK, lexical short-circuit (``_hybrid_rank_evidence`` MUST NOT be called when ``ASK_AI_RETRIEVAL_METHOD=lexical``), graceful-degradation fallback, ``compute_retrieval_diag`` shape, diagnostics endpoint 404/200/FIFO eviction, ``corpus_bootstrap._STATE.embedder_status`` field, and ``_bake_chunk_vectors`` raise-on-missing-embedder + write-on-available. Test floor: 3938 (Build 39) -> 3986 (+48 from Pass 4 + Pass 5 combined). All 4 ``make verify`` gates green: ruff clean, bandit 0 HIGH/MED, pip-audit clean. # Round 66 / Build 39: cuts the Pass 1-3 closing of (a) all five Build 38 accuracy bugs (X-1 already debunked in R65), (b) R27 grounding rejection rate root-cause fix, (c) admin grounding-rate pill, (d) KPI-specific recommendation templates. Pass 1 (B1-B4) closed the P0 report bugs: B1 multi-KPI citation injection ('90 [Source: ...]Days' mid-string split), B2 fetch_action_plans_snowflake gained optional technology_filter + customer_names kwargs as defense-in-depth, B3 count_open_action_plans recognises legacy AdoptIQ_Status='EMPTY' provenance heuristic on old XLSX, B4 _strip_html_safe (BeautifulSoup primary + regex fallback with script/style block decompose) wired through the AB / Action Plan / Pulse / Success Priorities / Customer Pulse XLSX writers and the upstream DataFrame normalization in app_simple.py. Pass 2 (B5-B10) closed the P1 report bugs: B5 Comprehensive XLSX writer dynamically emits Sheet_Title:<sheet> rows in Report_Info for every data sheet (parity with R-1 contract from R65), B6 per-customer Risk_Components sheet in Comprehensive XLSX with seven canonical component scores + Top_Risk_Factor (sorted by score DESC, name ASC; provenance row when no profiles computed), B7 KPI_ALIASES extended with renewal-specific labels (support cases 90 days, renewal risk level, success_priorities, incidents) so the citation injector recognises Renewal narrative KPIs, B8 compact_report_formatter._r66_b8_classify_extra_frames threads per-customer pulse / action_plan / subscription slices into compute_customer_risk_profile so Compact composite no longer dampens ~30% relative to Renewal for the same customer, B9 PSIRT_Vulnerabilities writer drops blank/NaN/Unknown customer rows AND labels portfolio-wide CVE/PSIRT advisories with explicit '(Portfolio-wide)' Customer field instead of empty string, B10 fetch_support_cases_snowflake promotes the empty-result log to logger.warning with structured context and attaches fetch_error_kind='empty_for_scope' diag attrs on the returned DataFrame. Pass 3 (B11-B14) closed the P2 cleanup + R27 grounding root-cause: B11 ai_narrative_validator widened _COMMON_REFERENCE_NUMBERS from 0-31 to 0-100 (plus every multiple of 5 from 100-500, plus calendar years 2024-2030), added _is_derived_ratio_percentage second-chance check (any narrative percentage expressible as a/b*100 for integer pair (a, b) in the briefing within 0.5 ppt is grounded), and widened relative tolerance for ARR-class values (>=$100K) from 1% to 5% so '$1.2M' against briefing '$1,234,567' passes while hallucinated '$5M' against briefing '$1.2M' still fails -- targets post-fix R27 rejection rate <=10%, B12 KPI-specific recommendation templates in report_utils.py (kpi_recommendation_csm_engagement, _training, _engagement_cadence, _high_severity_barriers, _premium_support, _upsell) wired into advanced_renewal_analyzer with lazy-import + legacy-string fallback, B13 audited 7 sites in compact_report_formatter.py and 1 in executive_intelligence_formatter.py for the R38.2 outer-guard / inner-access pattern -- ALL 8 sites are CLEAN; pinned by regression tests + _bu_disp absence assertions, B14 admin /api/status/all projects three new scalars (grounding_rejection_rate, grounding_rejection_count, grounding_total_count) and the admin Currently Running Reports tile gains a Grounding column with three-color pill (green <=10%, yellow <=25%, red >25%) + tooltip with rejected/total counts. Test floor: 3773 (Build 38) -> 3938 (+165). make verify clean. # Round 65 / cut Build38 to close five accuracy / transparency bugs surfaced in the Build 37 manual acceptance audit; full Build 38 narrative preserved in commit history.  # Round 65 / cut Build38 to close five accuracy / transparency bugs surfaced in the Build 37 manual acceptance audit. X-1 (cross-format AB count drift Renewal=161 vs Compact=162) was DEBUNKED during phase-1 investigation -- both Compact and Renewal returned 161 identical ABs; the apparent 162 in Compact was a misread of the title row in row 0; the underlying schema bug is fixed under R-1 below. C-2 (Comprehensive missing Action_Plans for Brian Frazier) was largely CLOSED by Round 64 / B2; Round 65 / phase 1 hardens the writer further by extracting ``LeaderReportGenerator._fetch_action_plans`` into a standalone module-level ``fetch_action_plans_snowflake(account_ids, days, owner_emails)`` helper (so the comprehensive path no longer needs to instantiate the leader generator), merging CSConsole + Snowflake rows with dedup by Action Plan ID, and stamping a single provenance row (``Status=EMPTY``, ``Source=CSConsole+Snowflake``, marker column ``_adoptiq_provenance_row=True``) when both sources return zero so the operator sees honest provenance instead of a missing sheet. ``count_open_action_plans`` skips the marker rows so the Summary KPI reads ``0`` (not ``1``) in the truly-empty case. R-1 (XLSX title row in row 0 broke ``pd.read_excel(...)``): aligned Compact + Renewal + Leader Excel writers to write headers in row 0 (no merged title row above data), moved descriptive titles to a dedicated ``Report_Info`` sheet as ``Sheet_Title:<sheet_name>`` rows, updated all ``startrow``/``autofilter``/``freeze_panes`` anchors, and updated two existing tests (test_round16_apply_excel_polish_offset.py, test_round43_compact_autofilter_no_overlap.py) that had pinned the old buggy schema. R-2 (Renewal Incidents=100 saturation): two-pronged fix -- formula-side cap in ``risk_scoring._score_incidents`` (``min(count, 5) * 6.0`` so a runaway count cannot dominate the score) plus a caller-side filter helper ``_r65_filter_customer_tagged_incidents`` that passes only customer-tagged incidents into per-customer scoring (portfolio-wide Webex Status incidents flow through unchanged when no tagging field is present). C-1 (misleading "builder error" Portfolio Overview fallback): wired structured ``drift_detail`` through both ``report_consistency.validate_word_numeric_drift`` (R25B) and ``validate_word_risk_band_claims`` (R25C) -- the validators now attach ``drift_detail`` to the raised ``ValueError`` AND surface it in the returned ``ConsistencyResultContract``, and ``app_simple.py``'s portfolio-error fallback distinguishes the drift case from a generic builder error and renders an operator-actionable paragraph naming the drifted field, the LLM's claim, and the canonical truth (``Drifted fields: - Total Customers: LLM rendered [27], canonical = 37``). C-3 (34% R27 grounding rejection rate, no per-rejection diagnostic): extended ``_r64_record_grounding_outcome`` with optional ``briefing_excerpt`` + ``narrative_excerpt`` kwargs (bounded ~200 chars each via the new ``_r65_grounding_excerpt`` helper) plus the full sanitised ``sample_offending`` map plus a ``recorded_at`` UTC ISO timestamp, so the persisted ``analysis_status.json`` carries enough context for Round 66 to root-cause the rejection rate without re-running the report; added a read-only ``GET /api/grounding-diagnostics/<analysis_id>`` admin endpoint that returns the structured rejection records (loopback-only, no auth wrapper -- the main app binds to 127.0.0.1 unless ``ADOPTIQ_BIND_PUBLIC=1``). Net pytest delta +55 (3718 -> 3773): 10 phase-1 / C-2 Snowflake-fetch + 7 phase-1 / C-2 fallback + 7 R-1 XLSX clean-schema + 12 R-2 incidents-no-saturation + 6 C-1 portfolio-drift-message + 13 C-3 R27-diagnostic-capture, plus 2 existing tests updated (test_round16_apply_excel_polish_offset.py + test_round43_compact_autofilter_no_overlap.py) where they had pinned the old buggy XLSX schema. All 4 ``make verify`` gates green: ruff clean, bandit 0 HIGH/MED, pip-audit clean. In-locals floor (40) preserved. Phase 1 (Title Page accuracy): risk-bucket denominator coherence -- pre-R64 ``portfolio_metrics['total_customers']`` was the AB ∪ CSOne ∪ Pulse intersection (39 on the Brian Frazier / Contact Center 90d run) while the risk-band buckets were computed from the wider ``risk_profiles`` set (53), so the title page paired ``Customers in portfolio: 39`` with buckets summing to ``1+6+24+22 = 53``; fix scopes the bands to the same narrow universe as the headline number via a new ``_r64_filter_to_narrow_universe`` helper. Mid-string citation injection -- pre-R64 ``report_source_injector._rewrite_paragraph_with_inline_citations`` injected the chrome after every regex match (P005 rendered ``"Analysis Period: 90  [Source: ...]Days"``); fix splits paragraphs on newlines, appends a single citation at end-of-line for single-KPI lines, preserves the R57 multi-KPI interleave contract for multi-match lines. Phase 2 (B2): Action plans (open) always-zero -- pre-R64 the comprehensive XLSX had no ``Action_Plans`` sheet so ``count_open_action_plans`` could only read the ``AB_Detail_All.Action Plan Title`` column (always blank in the comprehensive flow); fix adds a dedicated ``Action_Plans`` sheet to the XLSX, extends ``count_open_action_plans`` with an optional ``ap_df`` parameter that recognises ``_AP_CLOSED_STATUSES`` (case-insensitive, whitespace-collapsed), and threads the new sheet through ``report_export_styling.build_summary_rows``; the R62/B AB_Detail_All fallback is preserved for back-compat. Phase 3: Portfolio Overview LLM resilience -- pre-R64 a single transient empty body / ``ERROR: timeout`` collapsed straight into the bland ``"AI analysis encountered an error.  Data processed successfully."`` paragraph; fix adds a ``_r64_call_llm_with_retry`` helper with 3-attempt exponential backoff on transient classifications (``timeout``/``rate_limited``/``server_error``/``network``/empty/exception), short-circuits on hard failures (``content_filter``/``credentials``/``auth``), and renders an honest fallback paragraph that names the failure mode + last error kind. Grounding-failure diagnostics -- pre-R64 11/28 customer narratives were rejected by the R16/R27 ai_narrative_validator with no per-report rollup; fix adds a ``_r64_record_grounding_outcome`` helper that maintains ``status['grounding_diagnostics']`` (rejection_summary + bounded rejection_records with digested customer names) so the running-reports tile and persisted analysis_status.json carry an honest ``rejected=N total=M rate=R`` rollup, plus structured-logging emits via ``analysis_logger`` for SIEM correlation. Net pytest delta +59 (3663 -> 3718): test_round64_title_page_denominator_coherence.py (5), test_round64_citation_injector_multiline.py (10), test_round64_comprehensive_action_plans_sheet.py (16), test_round64_portfolio_overview_retry_and_fallback.py (14), test_round64_grounding_diagnostics.py (14). All 4 ``make verify`` gates green: ruff clean, bandit 0 HIGH/MED, pip-audit clean. Compact, Renewal, and Leader reports were verified clean during Build 36 acceptance and need no Round 64 changes.

def version_string():
    """e.g. 'v1.0.1 build 1'"""
    return "v" + ADOPTIQ_VERSION + " build " + ADOPTIQ_BUILD

from dotenv import load_dotenv
load_dotenv()

#
# Round 81 / Build 57 ships two scoped fixes that share no code path
# but landed together because they're both small, surgical, and
# user-visible.  (1) SharePoint share-token refresh: the canonical
# ``Config.ADOPTIQ_CORPUS_SHARE_URL`` rotated from ``e=O3a4Ij`` to
# ``e=kpHMgs`` (the share-token Brian and the team now use); the
# path segment is unchanged, only the trailing query-string token
# rotates.  ``ADOPTIQ_SHAREPOINT_FOLDER_URL`` continues to be a
# back-compat alias resolving to the same string per the Round 35
# contract.  (2) Outputs/ reorganized by ``<manager>/<report_type>/``:
# pre-R81 every report writer dropped its DOCX/XLSX directly under
# ``_APP_SUPPORT/outputs/`` so an operator running 90 + 30 + 7-day
# reports for three managers ended up with a single 60+ file flat
# directory and no per-manager grouping.  Build 57 introduces three
# helpers in ``app_simple.py`` -- ``_r81_sanitize_path_segment``
# (strict ``[A-Za-z0-9._-]`` allow-list, spaces -> ``_``, caps at
# 80 chars, defaults to ``_Unknown``), ``_r81_outputs_root`` (the
# canonical writable outputs base), and
# ``_r81_resolve_report_output_dir(manager, report_type, customer=None)``
# (resolves to ``<outputs>/<manager>/<report_type>/`` for multi-
# customer reports and ``<outputs>/<manager>/Customer/<customer>/``
# for per-customer reports, idempotent ``mkdir(parents=True)``).  All
# five report writer call-sites
# (``run_compact_analysis``, ``run_customer_renewal_analysis``,
# ``run_comprehensive_analysis``, ``run_leader_report_generation``,
# ``run_subscription_analysis``) now route through the resolver; the
# Leader writer accepts an ``output_dir`` kwarg so the caller picks
# the directory rather than the generator.  A one-shot, idempotent
# migration helper (``_r81_migrate_flat_outputs_if_needed``) runs at
# startup, parses legacy filenames against the canonical
# ``AdoptIQ_Report_<Type>_<Manager>_<Tech>_<Days>d_<Timestamp>.<ext>``
# pattern (longest-prefix match against the team roster handles
# multi-token managers like ``Mithun_Sakthivel_Subramanian``),
# routes parse-failures to ``_Unknown/_Unknown/`` rather than
# silently re-categorise, and writes a ``.r81_migrated`` sentinel
# so subsequent launches no-op.  The download endpoints
# (``/download-file/<filename>`` + the ``_resolve_safe_path`` helper
# behind ``/download/<id>/<type>``), ``scan_historical_reports``,
# and ``_get_intel_chips`` all switch from ``Path.glob`` to
# ``Path.rglob`` so files in nested subdirs are still findable.
# ``corpus_indexer.enumerate_user_report_files`` gains a
# ``recursive`` kwarg (default ``False`` to preserve the R80
# user-downloads safety contract); ``corpus_bootstrap._resolve_index_sources``
# passes ``recursive=True`` ONLY for the ``local_outputs`` source so
# the AdoptIQ-generated reports under ``_APP_SUPPORT/outputs/<manager>/<type>/``
# are still indexed without enabling the indexer to walk arbitrary
# user dirs.  Pinned by ``tests/test_round81_outputs_per_manager_layout.py``
# (~19 tests) and ``tests/test_round81_sharepoint_url_refresh.py``
# (3 tests + the existing R35 URL pin updated to the new token).
import os
import secrets
from pathlib import Path


def _resolve_secret_key() -> str:
    """Resolve app secret key from env or generate a dev-only ephemeral key."""
    configured = os.environ.get('SECRET_KEY') or os.environ.get('ADOPTIQ_SECRET_KEY')
    if configured:
        return configured
    # Avoid predictable hardcoded defaults in local/dev runs.
    return secrets.token_urlsafe(48)


def _is_production_env() -> bool:
    """Round 9 / Phase 1.4: shared production-readiness probe.

    Centralises the truthy detection used by both ``Config.DEBUG`` and
    ``Config.is_production_ready`` so the DEBUG-hardening guard below
    can re-use the exact same definition.  Production is asserted by
    either ``ADOPTIQ_PRODUCTION_READY=1`` or
    ``FLASK_ENV/APP_ENV/ENVIRONMENT=production``.
    """
    if str(os.environ.get('ADOPTIQ_PRODUCTION_READY', '')).strip().lower() in {'1', 'true', 'yes', 'on'}:
        return True
    env_value = (
        os.environ.get('FLASK_ENV')
        or os.environ.get('APP_ENV')
        or os.environ.get('ENVIRONMENT')
        or ''
    ).strip().lower()
    return env_value in {'production', 'prod'}


def _resolve_debug_flag() -> bool:
    """Round 9 / Phase 1.4: hardened DEBUG resolver.

    The previous form (``os.environ.get('DEBUG', 'false') in truthy``)
    let an operator ship a Flask binary with ``DEBUG=True`` to
    production by accident -- the classic Werkzeug-debugger-RCE
    footgun.  When the production gate is asserted we now force
    ``DEBUG=False`` regardless of env, log a warning to stderr so the
    misconfiguration is visible, and (if the override is *also*
    explicitly truthy) refuse to boot via :func:`enforce_production_safety`
    rather than silently honour it.
    """
    raw = str(os.environ.get('DEBUG', 'false')).strip().lower()
    requested = raw in {'true', '1', 'yes', 'on'}
    if _is_production_env() and requested:
        try:
            import sys as _sys
            print(
                "[config] WARNING: DEBUG=true requested while ADOPTIQ_PRODUCTION_READY=1; "
                "forcing DEBUG=False (Round 9 / Phase 1.4).",
                file=_sys.stderr,
            )
        except Exception:
            pass
        return False
    return requested


# Round 80: corpus-owner identity used to construct the canonical
# OneDrive shared-folder shortcut leaf name. Every non-owner user
# clicks "Add shortcut to OneDrive" against the SharePoint folder
# Jeffrey owns; the OneDrive desktop client materializes that
# shortcut directly under ``OneDrive-Cisco/`` as
# ``<Owner Display Name> (<owner_username>) - <FolderName>``. AdoptIQ
# walks ONLY this leaf -- not the broader OneDrive tree -- so users
# don't have to grant filesystem permission to their personal
# OneDrive contents. The corpus owner is expected to add the same
# shortcut on their own Mac so they exercise the identical code path
# users do (eliminates owner/user drift). If the owner identity ever
# changes (org transfer, AD display-name change), patch these two
# constants and ship a new build. Pinned by
# ``tests/test_round80_onedrive_shared_folder_only.py``.
_R80_CORPUS_OWNER_DISPLAY = 'Jeffrey Story (jestory)'
_R80_CORPUS_FOLDER_NAME = 'AdoptIQ_CSOne_Reports'
_R80_SHARED_FOLDER_LEAF = (
    f'{_R80_CORPUS_OWNER_DISPLAY} - {_R80_CORPUS_FOLDER_NAME}'
)

# Round 83 / Build 59: owner-style fallback path. The corpus owner's
# personal OneDrive sync places the source folder under
# ``AI Projects/AdoptIQ_CSOne_Reports`` because that's their personal
# OneDrive folder -- they can't "Add shortcut to OneDrive" against
# their own folder; the OneDrive desktop client routes back to the
# original. R80's contract still applies for non-owner users (shared
# folder shortcut → canonical leaf), but R83 adds the owner-style
# fallback so the corpus owner's machine resolves correctly without
# a manual env-var workaround. The R80 leaves remain priority 1-2
# (so the 99% non-owner case is unchanged); R83 owner-style entries
# are priority 3-4 -- first-existing-wins.
_R83_OWNER_STYLE_LEAF = os.path.join('AI Projects', _R80_CORPUS_FOLDER_NAME)


def _csone_onedrive_candidates() -> list[str]:
    """Round 80 (extended Round 83): priority-ordered list of paths
    to search for the AdoptIQ corpus folder. R80 narrowed discovery
    to the canonical shared-folder leaf only; R83 adds owner-style
    fallback paths for the corpus owner's machine, where the
    "Add shortcut to OneDrive" workflow doesn't apply (you can't
    shortcut your own folder).

    Priority order, highest first:

      1. R80 modern macOS canonical leaf (shared-folder shortcut)
      2. R80 pre-Big-Sur / Windows canonical leaf
      3. R83 modern macOS owner-style path (``AI Projects/...``)
      4. R83 pre-Big-Sur / Windows owner-style path

    Non-owner users hit candidates 1-2 because they "Add shortcut to
    OneDrive" against the share, materializing the canonical leaf
    directly under ``OneDrive-Cisco/``. The corpus owner falls
    through to 3-4 because their personal OneDrive sync uses the
    owner-style path. AdoptIQ never walks the broader OneDrive-Cisco
    tree -- only these four explicit candidates -- so the R80
    narrowing intent (no arbitrary subdirectory walks) is preserved.

    Power users can still pin any path via the
    ``CSONE_ONEDRIVE_FOLDER`` env override.

    Pinned by ``tests/test_round83_candidate_list_owner_fallback.py``.
    """
    # Round 80 / Round 83
    home = os.path.expanduser('~')
    return [
        # 1. Modern macOS Cloud-Storage canonical leaf (R80).
        os.path.join(home, 'Library', 'CloudStorage', 'OneDrive-Cisco',
                     _R80_SHARED_FOLDER_LEAF),
        # 2. Pre-Big Sur macOS / Windows canonical leaf (R80).
        os.path.join(home, 'OneDrive - Cisco',
                     _R80_SHARED_FOLDER_LEAF),
        # 3. Modern macOS Cloud-Storage owner-style path (R83).
        os.path.join(home, 'Library', 'CloudStorage', 'OneDrive-Cisco',
                     _R83_OWNER_STYLE_LEAF),
        # 4. Pre-Big Sur macOS / Windows owner-style path (R83).
        os.path.join(home, 'OneDrive - Cisco',
                     _R83_OWNER_STYLE_LEAF),
    ]


def _resolve_csone_onedrive_folder() -> str:
    """Round 17.2 (extended Round 88 / F5): return the active CSOne
    OneDrive folder path.

    Resolution order, highest precedence first:

    1. ``settings.json['csone_onedrive_folder']`` -- operator-set via
       the admin Preferences card.  Round 88 / F5 (P1) addition for
       Brian Frazier's Build 63 acceptance comment ("the file location
       for my machine is /Users/<sharee>/.../Jeffrey Story (jestory) -
       AdoptIQ_CSOne_Reports").  Tilde-expanded so ``~/Library/...``
       works.  Re-vetted via
       ``adoptiq_settings.is_valid_csone_folder_path`` so a corrupt
       settings.json cannot inject a malformed path.  Empty string is
       the canonical "unset" sentinel and falls through.
    2. ``CSONE_ONEDRIVE_FOLDER`` env override -- preserved verbatim so
       power users keeping the variable in their shell rc still get
       the same effective path.
    3. ``_csone_onedrive_candidates()`` auto-discovery -- the R80/R83
       candidate list (canonical leaf, owner-style leaf, etc.) walked
       in priority order; first existing wins.
    4. First candidate as fallback so error messages still point at
       the right "expected" location when nothing is synced.

    The settings.json read is wrapped in a broad try/except because
    this resolver runs at ``config.Config`` import time and a partial
    install / missing module MUST NOT break boot.  A corrupt or
    missing settings.json simply falls through to layer 2.
    """
    # Round 88 / F5 (P1): settings.json takes precedence over env.
    try:
        import adoptiq_settings as _settings  # noqa: PLC0415
        try:
            persisted = _settings.get("csone_onedrive_folder", "")
        except Exception:  # noqa: BLE001 - resolver MUST NOT raise
            persisted = ""
        if isinstance(persisted, str) and persisted.strip():
            try:
                if _settings.is_valid_csone_folder_path(persisted):
                    return os.path.expanduser(persisted.strip())
            except Exception:  # noqa: BLE001
                pass  # noqa: PIE790 - fall through to env layer
    except Exception:  # noqa: BLE001 - settings module unavailable in some test fixtures
        pass  # noqa: PIE790

    override = os.environ.get('CSONE_ONEDRIVE_FOLDER')
    if override:
        return override
    for candidate in _csone_onedrive_candidates():
        try:
            if os.path.isdir(candidate):
                return candidate
        except Exception:
            continue
    return _csone_onedrive_candidates()[0]


def enforce_production_safety() -> None:
    """Round 9 / Phase 1.4: hard-fail boot when production+DEBUG/TESTING.

    Defence-in-depth on top of :func:`_resolve_debug_flag`: callers
    (``app_simple.py`` startup) invoke this after ``Config`` is loaded
    so that an operator who *also* mutated ``Config.DEBUG``/``TESTING``
    after import (test fixture leaking into prod, custom subclass,
    etc.) still gets refused at boot rather than running a debug
    interpreter on a production socket.
    """
    if not _is_production_env():
        return
    if bool(getattr(Config, 'DEBUG', False)) or bool(getattr(Config, 'TESTING', False)):
        raise RuntimeError(
            "ADOPTIQ_PRODUCTION_READY=1 requires DEBUG=False and TESTING=False; "
            "refusing to boot (Round 9 / Phase 1.4)."
        )


class Config:
    # Flask Configuration (SECRET_KEY from env; generated ephemeral key otherwise)
    SECRET_KEY = _resolve_secret_key()
    # Round 9 / Phase 1.4: route DEBUG through the hardened resolver so
    # ``ADOPTIQ_PRODUCTION_READY=1`` always wins over a stray
    # ``DEBUG=true`` env entry.
    DEBUG = _resolve_debug_flag()
    # Round 9 / Phase 1.4: explicit TESTING flag participates in the same
    # production safety gate as DEBUG.  Default false; tests opt in.
    TESTING = str(os.environ.get('TESTING', 'false')).strip().lower() in {'true', '1', 'yes', 'on'} and not _is_production_env()
    VERBOSE_DEBUG = os.environ.get('ADOPTIQ_VERBOSE_DEBUG', 'false').lower() in ('true', '1', 'yes', 'on')
    FLASK_ENV = os.environ.get('FLASK_ENV', 'development')

    # Analysis Configuration
    ANALYSIS_TIMEOUT = int(os.environ.get('ANALYSIS_TIMEOUT', '300'))
    STEP_TIMEOUT = int(os.environ.get('STEP_TIMEOUT', '60'))
    OUTPUT_FOLDER = os.environ.get('OUTPUT_FOLDER', './outputs')
    MAX_FILE_SIZE = int(os.environ.get('MAX_FILE_SIZE', '100'))
    UPLOAD_FOLDER = os.environ.get('UPLOAD_FOLDER', './uploads')
    TEMP_FOLDER = os.environ.get('TEMP_FOLDER', './temp')
    LOG_FOLDER = os.environ.get('LOG_FOLDER', './logs')

    # CSOne OneDrive folder: when no file is uploaded, use the most recent .xlsx from this folder.
    # Macro places reports here daily. Override via CSONE_ONEDRIVE_FOLDER env var.
    # Round 17.2: auto-discover the synced OneDrive folder across the
    # paths Microsoft / Apple have used at different points in time.
    # See ``_csone_onedrive_candidates`` / ``_resolve_csone_onedrive_folder``
    # for the priority list; the first existing directory wins.
    CSONE_ONEDRIVE_FOLDER = _resolve_csone_onedrive_folder()

    # Round 102: the deprecated Downloads corpus source is fully retired.
    # Even if an old environment or bundled secret still sets
    # CSONE_INCLUDE_USER_DOWNLOADS=true, the runtime must not touch
    # ~/Downloads because macOS surfaces that as an alarming Files-and-Folders
    # permission prompt. Generated reports are indexed through the
    # permission-clean local_outputs source instead.
    CSONE_INCLUDE_USER_DOWNLOADS = False
    CSONE_USER_DOWNLOADS_DIR = os.environ.get('CSONE_USER_DOWNLOADS_DIR') or ''

    # Round 26 / Phase D: user-uploaded CSOne report drop folder.
    #
    # Reports submitted through the user-facing AdoptIQ Intelligence
    # banner (analyze.html) land here under sanitized random
    # filenames.  The corpus indexer then walks this folder on the
    # next bootstrap pass so user-supplied reports are immediately
    # searchable from Ask AI without forcing an admin re-index.
    #
    # Default location lives under the per-user ``~/.adoptiq``
    # directory so:
    #   * uploads survive Flask restarts (persistent index source);
    #   * the directory is created with 0700 permissions by the
    #     application (see ``_ensure_intel_uploads_folder`` in
    #     ``app_simple.py``) -- never world-readable;
    #   * tests / power users can override via env var without
    #     touching code.
    CSONE_INTEL_UPLOADS_FOLDER = os.environ.get(
        'CSONE_INTEL_UPLOADS_FOLDER'
    ) or str(Path.home() / '.adoptiq' / 'intel_uploads')

    # Round 26 / Phase D: feature flag for the user-facing upload
    # endpoint.  Default OFF so the route + drop-zone are not
    # exposed unless the operator opts in.  When false:
    #   * /api/intel/upload returns 404 (route not registered? no
    #     -- registered but returns 403 with a clear payload so
    #     existing tests can still assert the route exists);
    #   * the analyze.html drop-zone is not rendered;
    #   * the indexer still walks any pre-existing
    #     ``CSONE_INTEL_UPLOADS_FOLDER`` content (so an admin can
    #     pre-seed it from disk even when uploads are disabled).
    ADOPTIQ_INTEL_UPLOAD_ENABLED = (
        str(os.environ.get('ADOPTIQ_INTEL_UPLOAD_ENABLED', 'false')).strip().lower()
        in {'1', 'true', 'yes', 'on'}
    )

    # Round 17: CSOne Knowledge Corpus feature flag.
    #
    # When enabled (env ``CORPUS_KNOWLEDGE_ENABLED`` truthy), AdoptIQ
    # builds an encrypted local cache of the CSOne report corpus from
    # ``CSONE_ONEDRIVE_FOLDER`` on first launch and surfaces it through
    # Ask AI / Customer 360 / Playbook / Admin tile.  The OneDrive
    # client itself enforces the SharePoint ACL -- if the user has no
    # local sync the corpus surfaces as ``CorpusUnavailable`` and every
    # caller falls back to today's behavior.  Default: off, so existing
    # deployments are unchanged until explicitly enabled.
    # Round 32 / Phase 2.F: default flipped to ``true`` so fresh
    # installs surface AdoptIQ Intelligence in the UI immediately
    # after install.  Operators who explicitly set the env var (e.g.
    # ``CORPUS_KNOWLEDGE_ENABLED=false``) keep their override; the
    # in-app toggle (``adoptiq_settings.settings.json``) sits ABOVE
    # the env var in resolution order, so a user who flips the
    # switch off from the UI also wins regardless of env state.
    CORPUS_KNOWLEDGE_ENABLED = (
        str(os.environ.get('CORPUS_KNOWLEDGE_ENABLED', 'true')).strip().lower()
        in {'1', 'true', 'yes', 'on'}
    )

    # Round 66 / Pass 5 - Hybrid retrieval method for Ask AI evidence
    # ranking. Allowed values:
    #   - "hybrid" (default): BM25 + dense (fastembed bge-small-en-v1.5)
    #     fused via Reciprocal Rank Fusion (k=60). Requires the
    #     fastembed model to be installed at the bundled location
    #     ``Resources/embeddings/`` (or HuggingFace fallback).
    #   - "lexical": pre-Round-66 bag-of-words ranking only. The
    #     graceful-degradation path: when fastembed is missing or the
    #     model file cannot be loaded, ``corpus_bootstrap`` flips this
    #     value to "lexical" at runtime and logs a structured warning.
    # Operators can revert via env var without rebuilding the .app:
    # ``ASK_AI_RETRIEVAL_METHOD=lexical``.
    ASK_AI_RETRIEVAL_METHOD = (
        str(os.environ.get('ASK_AI_RETRIEVAL_METHOD', 'hybrid')).strip().lower()
    )

    # Round 66 / Pass 5 - Embedding model identifier. Pinned to the
    # fastembed default; changing this triggers a corpus re-bake (via
    # the corpus_crypto self-heal path) because the chunk_vectors table
    # was computed against the previous model.
    ASK_AI_EMBEDDING_MODEL = str(
        os.environ.get('ASK_AI_EMBEDDING_MODEL', 'BAAI/bge-small-en-v1.5')
    ).strip()
    ASK_AI_EMBEDDING_DIM = 384  # bge-small produces 384-dim float32 vectors

    # Round 66 / Pass 5 - RRF fusion constant. 60 is the literature
    # default (Cormack, Clarke, and Buettcher 2009). Bounded
    # configurable for ablation experiments.
    ASK_AI_RRF_K = int(os.environ.get('ASK_AI_RRF_K', '60') or 60)

    # Round 95 - optional second-stage Ask AI reranker. Runtime failures
    # preserve the RRF order and surface in retrieval diagnostics; release
    # bakes fail loud when the configured reranker cannot load.
    ASK_AI_RERANK_ENABLED = (
        str(os.environ.get('ASK_AI_RERANK_ENABLED', 'true')).strip().lower()
        in {'1', 'true', 'yes', 'on'}
    )
    ASK_AI_RERANK_MODEL = str(
        os.environ.get('ASK_AI_RERANK_MODEL', 'Xenova/ms-marco-MiniLM-L-6-v2')
    ).strip()
    ASK_AI_RERANK_CANDIDATE_K = int(os.environ.get('ASK_AI_RERANK_CANDIDATE_K', '30') or 30)

    # Round 79 / Build 55 - BE-engineering priority barrier analysis.
    # The deterministic scorer (``be_priority_scorer.compute_be_priority_score``)
    # ranks ALL adoption barriers by a weighted formula combining
    # severity, customer composite risk, customer pulse negativity,
    # open age, content signal regex hits, and the explicit
    # ``AB_ESCALATE_C`` flag. The LLM classifier
    # (``be_priority_llm_classifier.classify_top_n_be_barriers``) then
    # tags ONLY the top-N rows (by deterministic score) into one of
    # ``TRUE_BLOCKER`` / ``TRAINING_GAP`` / ``FEATURE_REQUEST`` /
    # ``USER_BEHAVIOR`` / ``DUPLICATE`` / ``OBSERVATION_ONLY``.
    # The portfolio rollup (``be_priority_scorer.compute_be_focus_areas``)
    # groups by ``(sub_technology, ab_category_final)`` to surface the
    # 5-10 most important focus areas per technology for the BE group.
    #
    # Tunables (operator-overridable via env without a rebuild):
    #   - ``BE_PRIORITY_LLM_TOP_N``: how many rows the LLM classifies
    #     per run (default 50). A higher value increases LLM cost
    #     linearly but diminishing returns: barriers below the top 50
    #     by deterministic score are unlikely to drive engineering
    #     priorities for the round.
    #   - ``BE_PRIORITY_FOCUS_AREAS_PER_TECH``: cap on focus-area rows
    #     emitted per technology in the BE_Focus_Areas sheet AND the
    #     Word section (default 10). Aligns with the user-stated
    #     "5-10 most important things" goal.
    #   - ``BE_PRIORITY_MIN_CLUSTER_SCORE``: floor on cluster-focus
    #     score; clusters below this score are dropped from the focus
    #     output. Default 30.0 (a single P3 barrier with no
    #     customer-context boost would land near this floor).
    #   - ``BE_PRIORITY_LLM_ENABLED``: kill-switch for the LLM
    #     classifier. When false, the deterministic scorer still runs
    #     but ``BE_Class`` is empty in the XLSX output. Useful for
    #     air-gapped builds and acceptance dry-runs.
    #   - ``BE_PRIORITY_BRIEFING_ENABLED``: gate on the per-customer
    #     briefing book emitting ``Independent_BE_Priority`` /
    #     ``Independent_BE_Class`` / ``CSConsole_Severity`` /
    #     ``Days_Open``. Default true. Operators can set false to
    #     fall back to the pre-Round-79 briefing shape if a future
    #     issue is traced to the new fields.
    #
    # Pinned by:
    #   - tests/test_round79_b1_be_priority_scorer.py (formula)
    #   - tests/test_round79_b2_be_priority_llm_classifier.py (LLM)
    #   - tests/test_round79_b3_be_focus_areas_rollup.py (rollup)
    #   - tests/test_round79_b4_be_xlsx_sheets.py (XLSX integration)
    #   - tests/test_round79_b5_be_word_section.py (Word section)
    #   - tests/test_round79_b6_briefing_enrichment.py (briefing)
    BE_PRIORITY_LLM_TOP_N = int(
        os.environ.get('BE_PRIORITY_LLM_TOP_N', '50') or 50
    )
    BE_PRIORITY_FOCUS_AREAS_PER_TECH = int(
        os.environ.get('BE_PRIORITY_FOCUS_AREAS_PER_TECH', '10') or 10
    )
    BE_PRIORITY_MIN_CLUSTER_SCORE = float(
        os.environ.get('BE_PRIORITY_MIN_CLUSTER_SCORE', '30.0') or 30.0
    )
    BE_PRIORITY_LLM_ENABLED = (
        str(os.environ.get('BE_PRIORITY_LLM_ENABLED', 'true')).strip().lower()
        in {'1', 'true', 'yes', 'on'}
    )
    BE_PRIORITY_BRIEFING_ENABLED = (
        str(os.environ.get('BE_PRIORITY_BRIEFING_ENABLED', 'true')).strip().lower()
        in {'1', 'true', 'yes', 'on'}
    )

    # Round 17.2 -> Round 36: SharePoint Microsoft Graph corpus
    # source has been retired.  The MSAL/Graph runtime path was
    # blocked by Cisco tenant admin-consent on the default Microsoft
    # Graph PowerShell client ID (14d82eec-204b-4c2f-b7e8-296a70dab67e),
    # so AdoptIQ now relies on the OneDrive desktop client to mirror
    # the canonical AdoptIQ corpus folder under
    # ``CSONE_ONEDRIVE_FOLDER`` and walks that path directly.
    # The ``ADOPTIQ_SHAREPOINT_*`` settings below are kept for back-
    # compat with operator env scripts and tests that reference them;
    # no runtime code path consumes any of them as of Round 36.
    ADOPTIQ_SHAREPOINT_ENABLED = False  # Round 36: hard-disabled.
    # Round 35 / native-corpus: the canonical OneDrive/SharePoint share
    # URL for the AdoptIQ knowledge corpus.  Retained as documentation
    # / for the bake script's logging output -- the runtime indexer
    # never opens this URL (it walks ``CSONE_ONEDRIVE_FOLDER``).
    # Round 81 / Build 57: refreshed share-token from ``e=O3a4Ij`` to
    # ``e=kpHMgs`` -- the previous token was personal to the build
    # operator's session; the new token is the canonical link the
    # rest of the team uses to "Add shortcut to OneDrive".
    # Round 85 / Build 61: rotated to the new ``:f:/p/`` guest-pass
    # share URL (no query string, no ``e=...`` token) -- a different
    # SharePoint share format from the prior render-link shape.
    # Future rotations require a new path entirely (not a token swap)
    # so the legacy shape regression guard in
    # ``tests/test_round81_sharepoint_url_refresh.py`` rejects
    # ``e=O3a4Ij``, ``e=kpHMgs``, ``/personal/jestory_cisco_com``,
    # AND ``csf=1`` to catch silent rollbacks.  Operators rotate the
    # URL via the analyze-page card (R84) OR the new Preferences-hub
    # mirror (R85) -- both bind to ``[data-corpus-share-url-card]``.
    ADOPTIQ_CORPUS_SHARE_URL = (
        os.environ.get('ADOPTIQ_CORPUS_SHARE_URL')
        or 'https://cisco-my.sharepoint.com/:f:/p/jestory/IgBm46pU_P9aTpkxyEQ13ZgYAT4BhGVKNfcUsN7DA7zkRJI'
    )
    # Round 33 / Build8 -> Round 35 -> Round 36: backward-compat alias.
    # No runtime consumer; preserved so existing tests that reference
    # ``ADOPTIQ_SHAREPOINT_FOLDER_URL`` keep importing cleanly.
    ADOPTIQ_SHAREPOINT_FOLDER_URL = (
        os.environ.get('ADOPTIQ_SHAREPOINT_FOLDER_URL')
        or ADOPTIQ_CORPUS_SHARE_URL
    )
    # Round 53 / Phase 53.4.1: clickable "Open OneDrive" link rendered
    # in the analyze-page panel when the corpus is in the
    # ``blocked_no_onedrive`` state.  The default is the same
    # SharePoint web URL used for ``ADOPTIQ_CORPUS_SHARE_URL`` -- when
    # the user clicks it in their browser, SharePoint's UI offers a
    # prominent "Sync" button that wires the folder into the OneDrive
    # desktop client.  An operator who has provisioned a true
    # ``odopen://`` deep link can override via the env var below to
    # skip the browser hop entirely.  Validated by the status-payload
    # serializer in ``app_simple._r17_corpus_status_payload`` -- only
    # ``http://``, ``https://``, ``odopen:``, and ``ms-onedrive:``
    # schemes are surfaced to the UI; anything else is replaced with
    # ``None`` to defang an env-driven open-redirect / XSS attempt.
    ADOPTIQ_CORPUS_ONEDRIVE_DEEP_LINK = (
        os.environ.get('ADOPTIQ_CORPUS_ONEDRIVE_DEEP_LINK')
        or ADOPTIQ_CORPUS_SHARE_URL
    )
    # Round 36: dead settings retained as ``None`` so any operator env
    # script that exports these vars does not crash on missing
    # attribute access.  Removing them entirely is deferred to a
    # later round once the operator runbooks have been refreshed.
    ADOPTIQ_SHAREPOINT_CLIENT_ID = None
    ADOPTIQ_SHAREPOINT_AUTHORITY = None
    ADOPTIQ_SHAREPOINT_CACHE_DIR = None
    ADOPTIQ_SHAREPOINT_MAX_FILE_BYTES = 50 * 1024 * 1024

    # CSOne shared folder URL: opens in browser so users can download and
    # upload when the OneDrive folder isn't synced.  Configure via
    # ``CSONE_SHARED_FOLDER_URL``.  Round 7 / Phase 3.17: previously
    # defaulted to a personal SharePoint URL belonging to a single user
    # (``jestory_cisco_com``) which leaked operator identity into every
    # deployment and broke for anyone else.  No baked-in default now --
    # callers should treat ``None`` as "no shared folder configured".
    CSONE_SHARED_FOLDER_URL = os.environ.get('CSONE_SHARED_FOLDER_URL') or None

    # Round 7 / Phase 3.18: production-readiness gate is derived from the
    # environment instead of hard-coded ``True``.  Operators opt in
    # explicitly via ``ADOPTIQ_PRODUCTION_READY`` (truthy) or
    # ``FLASK_ENV=production`` / ``ENVIRONMENT=production``.  The previous
    # hard-coded ``True`` made it impossible to surface "not yet ready"
    # in dev/test environments and silently bypassed downstream guards.
    is_production_ready = (
        str(os.environ.get('ADOPTIQ_PRODUCTION_READY', '')).strip().lower()
        in {'1', 'true', 'yes', 'on'}
    ) or (
        (
            os.environ.get('FLASK_ENV')
            or os.environ.get('APP_ENV')
            or os.environ.get('ENVIRONMENT')
            or ''
        ).strip().lower()
        in {'production', 'prod'}
    )

    # Keeper Configuration - from environment only (no defaults for secrets)
    KEEPER_CONFIG = {
        "url": os.environ.get('KEEPER_URL', 'https://keeper.cisco.com'),
        "namespace": os.environ.get('KEEPER_NAMESPACE', 'cloudDB'),
        "role_id": os.environ.get('KEEPER_ROLE_ID') or '',
        "secret_id": os.environ.get('KEEPER_SECRET_ID') or '',
        "secret_path": os.environ.get('KEEPER_SECRET_PATH', 'secret/snowflake/prd/cx_swssbst_etl_svc/key')
    }

    # Snowflake Configuration - from environment only (password = direct auth; no Keeper needed when set)
    SNOWFLAKE_CONFIG = {
        "user": os.environ.get('SNOWFLAKE_USER') or '',
        "account": os.environ.get('SNOWFLAKE_ACCOUNT') or '',
        "role": os.environ.get('SNOWFLAKE_ROLE') or '',
        "warehouse": os.environ.get('SNOWFLAKE_WAREHOUSE') or '',
        "password": os.environ.get('SNOWFLAKE_PASSWORD') or '',
    }

    # CircuIT Configuration - from environment only
    #
    # Round 69 / Build 43: split ``model_name`` into per-call-site keys
    # ``model_name_ask_ai`` and ``model_name_report`` so the operator
    # can flip the Ask AI model independently of the report-narrative
    # model (different validation surfaces, different tuning targets).
    # Both normally default through the legacy ``CIRCUIT_MODEL_NAME``.
    # Round 103 maps a stale ``gpt-5-nano`` env/bundled default back to
    # Gemini so demo installs do not start on the old model unless the
    # operator deliberately picks it in settings.json after migration.
    # Round 77 / Build 53: hardcoded fallback flipped from ``gpt-5-nano``
    # to ``gemini-3.1-flash-lite``.  MUST track ``model_resolver._HARDCODED_DEFAULT``
    # so the env-fallback path and the resolver-level fallback agree
    # byte-for-byte (a config/resolver split would let the
    # ``CircuitChatClient`` constructor instantiate with one model while
    # ``model_resolver.get_active_*_model()`` returned another, producing
    # silent inconsistencies in per-call diagnostic records).  See R77
    # tests for the parity guard.
    @staticmethod
    def _r103_model_default_from_env(*names, default='gemini-3.1-flash-lite'):
        for name in names:
            value = (os.environ.get(name) or '').strip()
            if not value:
                continue
            if value == 'gpt-5-nano':
                return default
            return value
        return default

    CIRCUIT_CONFIG = {
        "client_id": os.environ.get('CIRCUIT_CLIENT_ID') or '',
        "client_secret": os.environ.get('CIRCUIT_CLIENT_SECRET') or '',
        "app_key": os.environ.get('CIRCUIT_APP_KEY') or '',
        "model_name": _r103_model_default_from_env.__func__('CIRCUIT_MODEL_NAME'),  # Round 103
        "model_name_ask_ai": _r103_model_default_from_env.__func__(
            'CIRCUIT_MODEL_NAME_ASK_AI', 'CIRCUIT_MODEL_NAME'
        ),  # Round 69 / Build 43, stale default mapped Round 103
        "model_name_report": _r103_model_default_from_env.__func__(
            'CIRCUIT_MODEL_NAME_REPORT', 'CIRCUIT_MODEL_NAME'
        ),  # Round 69 / Build 43, stale default mapped Round 103
    }

    # Database Table Names
    DSM_TABLE = "CX_DB.CX_SWSSBST_BR.dsm_assignment_data"
    AB_TABLE = "EDW_SALES_ETL_DB.SS.C360_CS_TASK_C_VW"

    # Round 7 / Phase 3.17: removed the hard-coded ``TEAM_ROSTER`` and
    # ``MANAGERS`` defaults that previously embedded ~30 personal email
    # addresses (Cisco internal) directly into source control.  The
    # runtime source of truth is ``team_config.json`` loaded via
    # :func:`adoptiq_backend._load_team_config`; nothing in the app
    # actually imported ``Config.TEAM_ROSTER`` / ``Config.MANAGERS`` so
    # the only effect of these defaults was to leak PII through the
    # repo and packaged binary.  Tests should populate
    # ``team_config.json`` (or monkey-patch ``adoptiq_backend.TEAM_ROSTER``)
    # rather than relying on a baked-in roster.

    # Technology Choices
    TECH_CHOICES = [
        "Webex Meetings & Messaging",
        "Webex Calling",
        "Webex Contact Center",
        "Webex Contact Center Enterprise",
        "Cisco UCCE",
        "Cisco UCCX",
    ]

    # Technology Filters
    TECH_FILTERS = {
        "Webex Meetings & Messaging": [
            r'webex\s*meetings?',
            r'webex\s*messag(ing|e)',
            r'webex\s*app',
            r'webex\s*suite',
            r'collaboration',
            r'room\s*devices',
            r'desk\s*series',
            r'joining\s*a\s*meeting',
            r'scheduling',
            r'productivity\s*tools',
            r'recording',
            r'vidcast',
            r'video',
            r'hybrid\s*calendar',
            r'site\s*management',
            r'user\s*management',
            r'org\s*management',
            r's\s*s\s*p\s*t',
            r'c\s*v\s*i',
            r'edge\s*audio',
            r'video\s*mesh',
            r'edge\s*connect',
            r'webex\s*share',
            r'webex\s*events',
            r'socio',
            r'proactive\s*cases'
        ],
        "Webex Calling": [r'webex\s*calling', r'(dedicated\s*instance|\bdi\b)'],
        "Webex Contact Center": [
            r'(webex\s*contact\s*center|wxcc)',
            r'cloud\s*and\s*hybrid\s*products',
            r'contact\s*center\s*cloud',
            r'contact\s*center\s*hybrid'
        ],
        "Webex Contact Center Enterprise": [
            r'(webex\s*contact\s*center\s*enterprise|wxcc\s*enterprise)',
            r'contact\s*center\s*software',  # Only when NOT UCCX/UCCE
            r'enterprise\s*contact\s*center'
        ],
        "Cisco UCCE": [
            r'\bucce\b',
            r'unified\s*contact\s*center\s*enterprise',
            r'contact\s*center\s*enterprise'
        ],
        "Cisco UCCX": [
            r'\buccx\b',
            r'unified\s*contact\s*center\s*express',
            r'contact\s*center\s*express'
        ],
    }

    # Sub-technology normalization assists to reduce "Other/Unknown" leakage.
    SUB_TECHNOLOGY_MAPPINGS = {
        r"\bwebex\s*contact\s*center\s*enterprise\b|\bwxcc\s*enterprise\b|\bwebex\s*cce\b": "Webex Contact Center Enterprise",
        r"\bwebex\s*contact\s*center\b|\bwxcc\b": "Webex Contact Center",
        r"\bunified\s*contact\s*center\s*enterprise\b|\bucce\b": "Cisco UCCE",
        r"\bunified\s*contact\s*center\s*express\b|\buccx\b": "Cisco UCCX",
        r"\bwebex\s*calling\b|\bdedicated\s*instance\b|\bdi\b": "Webex Calling",
        r"\bwebex\s*meetings?\b|\bwebex\s*messag(ing|e)\b|\bwebex\s*app\b|\bcollaboration\b": "Webex Meetings & Messaging",
        r"\bcontact\s*center\s*software\b|\bcontact\s*center\b": "All Contact Center",
    }

    # Official Categories
    OFFICIAL_CATEGORIES = {
        "Cisco External": [
            "Not a Customer Priority", "Customer Considering Competitor",
            "Customer Internal Strategy Mis-Alignment", "Future Intent to Adopt",
            "Intent to Opt Out or No Value Fit (Future Follow Up)", "No Budget Funding"
        ],
        "Customer Enablement/Technical Gap": [
            "Additional Customer Training Required", "Cisco or 3rd Party Compatibility",
            "Configuration Assistance Needed", "Diagnostic or Monitoring Assistance Needed",
            "High-Level/Low-Level Design Assistance Needed", "Installation Assistance Needed",
            "Licensing or Smart Account Support Needed", "Limited/Lack of Use Case Understanding",
            "Migration/Upgrade Assistance Needed", "Product Licensing Operations Issue",
            "Professional Services Needed (Cisco AS or Partner)", "TAC Support Needed or Pending TAC Cases"
        ],
        "Customer Environment Not Ready": [
            "Additional HW/SW needed", "Customer's Infrastructure Not Ready"
        ],
        "Customer Perceives Product Not Ready": [
            "Feature to Complete Deploy", "Feature Request",
            "Pending Known Product Bug", "Product or Solution Not Fed-Ramped (or lacks certifications)"
        ],
        "Customer Needs Partner Support": [
            "Partner Experience (includes Technical and/or Resource Challenge)",
            "Partner Needs Enablement", "Partner Unresponsive to Customer"
        ],
        "Cisco Internal": [
            "Unable to Engage", "Customer is Unresponsive", "Invalid Contact", "Missing Contact"
        ],
        "Adoption On Hold": [
            "Deprioritized by Theater Leadership", "Hold Request from Account Team",
            "Internal CS Resource Limitations", "Product/Service Seeded",
            "Supply Chain Delay/Issue with HW"
        ],
        "Data/Documentation Issue": [
            "Documentation Not Found or Insufficient", "Inaccurate Eligibility",
            "Internal Use Case Exit Criteria Issue", "Internal Use Case Telemetry Issue"
        ],
    }

    # Help URLs
    HELP_URLS = [
        "https://help.webex.com/en-us/article/mqkve8/Webex-App-%7C-Release-notes",
        "https://help.webex.com/en-us/article/8dmbcr/What's-New-in-Webex-Suite",
        "https://help.webex.com/en-us/article/n8z6v5c/Webex-App-%7C-Known-issues",
    ]

    # Likely Column Names for CSOne Excel Files
    LIKELY_DATE_COLS = {"Date/Time Opened", "Created", "Created Date", "OPEN_DATE", "OPEN_DATE_C", "CREATED_DATE", "CREATED_DATE_C"}
    LIKELY_TITLE_COLS = {"Title", "TITLE", "SUBJECT", "SUBJECT_C", "NAME", "ACTION_PLAN_TITLE_C"}
    LIKELY_DESC_COLS = {"Problem Description", "DESCRIPTION", "DESCRIPTION_C", "COMMENTS", "COMMENTS__C"}
    LIKELY_OWNER_COLS = {"Owner Email", "OWNER_EMAIL", "ASSIGNEE_EMAIL", "ASSIGNEE", "ASSIGNEE_C", "OWNER"}
    LIKELY_CUST_COLS = {"Customer Name", "BU_NAME", "CUSTOMER", "ACCOUNT", "ACCOUNT_NAME"}
    LIKELY_CASE_COLS = {"SR Number", "Case Number", "CASE_NUMBER", "SR_NUMBER"}
    LIKELY_TECH_COLS = {"Product", "Technology", "PRODUCT", "PRODUCT_C", "PRODUCT_NAME_C", "CSS_PRE_UNLINK_TECHNOLOGY_NAME_C", "SUCCESS_TRACK_C", "Sub Technology"}
    LIKELY_SUB_COLS = {"Subscription ID", "SUBSCRIPTION_ID", "SUB_ID", "Subscription Number", "Subscription Reference Id"}
