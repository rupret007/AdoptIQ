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
ADOPTIQ_BUILD = "37"  # Round 64 / cut Build37 to close five comprehensive-report accuracy bugs surfaced in the Build 36 manual acceptance pass. Phase 1 (Title Page accuracy): risk-bucket denominator coherence -- pre-R64 ``portfolio_metrics['total_customers']`` was the AB ∪ CSOne ∪ Pulse intersection (39 on the Brian Frazier / Contact Center 90d run) while the risk-band buckets were computed from the wider ``risk_profiles`` set (53), so the title page paired ``Customers in portfolio: 39`` with buckets summing to ``1+6+24+22 = 53``; fix scopes the bands to the same narrow universe as the headline number via a new ``_r64_filter_to_narrow_universe`` helper. Mid-string citation injection -- pre-R64 ``report_source_injector._rewrite_paragraph_with_inline_citations`` injected the chrome after every regex match (P005 rendered ``"Analysis Period: 90  [Source: ...]Days"``); fix splits paragraphs on newlines, appends a single citation at end-of-line for single-KPI lines, preserves the R57 multi-KPI interleave contract for multi-match lines. Phase 2 (B2): Action plans (open) always-zero -- pre-R64 the comprehensive XLSX had no ``Action_Plans`` sheet so ``count_open_action_plans`` could only read the ``AB_Detail_All.Action Plan Title`` column (always blank in the comprehensive flow); fix adds a dedicated ``Action_Plans`` sheet to the XLSX, extends ``count_open_action_plans`` with an optional ``ap_df`` parameter that recognises ``_AP_CLOSED_STATUSES`` (case-insensitive, whitespace-collapsed), and threads the new sheet through ``report_export_styling.build_summary_rows``; the R62/B AB_Detail_All fallback is preserved for back-compat. Phase 3: Portfolio Overview LLM resilience -- pre-R64 a single transient empty body / ``ERROR: timeout`` collapsed straight into the bland ``"AI analysis encountered an error.  Data processed successfully."`` paragraph; fix adds a ``_r64_call_llm_with_retry`` helper with 3-attempt exponential backoff on transient classifications (``timeout``/``rate_limited``/``server_error``/``network``/empty/exception), short-circuits on hard failures (``content_filter``/``credentials``/``auth``), and renders an honest fallback paragraph that names the failure mode + last error kind. Grounding-failure diagnostics -- pre-R64 11/28 customer narratives were rejected by the R16/R27 ai_narrative_validator with no per-report rollup; fix adds a ``_r64_record_grounding_outcome`` helper that maintains ``status['grounding_diagnostics']`` (rejection_summary + bounded rejection_records with digested customer names) so the running-reports tile and persisted analysis_status.json carry an honest ``rejected=N total=M rate=R`` rollup, plus structured-logging emits via ``analysis_logger`` for SIEM correlation. Net pytest delta +59 (3663 -> 3718): test_round64_title_page_denominator_coherence.py (5), test_round64_citation_injector_multiline.py (10), test_round64_comprehensive_action_plans_sheet.py (16), test_round64_portfolio_overview_retry_and_fallback.py (14), test_round64_grounding_diagnostics.py (14). All 4 ``make verify`` gates green: ruff clean, bandit 0 HIGH/MED, pip-audit clean. Compact, Renewal, and Leader reports were verified clean during Build 36 acceptance and need no Round 64 changes.

def version_string():
    """e.g. 'v1.0.1 build 1'"""
    return "v" + ADOPTIQ_VERSION + " build " + ADOPTIQ_BUILD

from dotenv import load_dotenv
load_dotenv()

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


def _csone_onedrive_candidates() -> list[str]:
    """Round 17.2: priority-ordered list of paths to search for a
    synced OneDrive copy of ``AdoptIQ_CSOne_Reports``.  Modern macOS
    (Big Sur+) puts the OneDrive sync under
    ``~/Library/CloudStorage/OneDrive-Cisco/...``; older installations
    kept the human-readable ``OneDrive - Cisco`` directory directly
    under ``~``; some users nested everything under a ``Documents/``
    subfolder.  We try each candidate in this order and pick the
    first one that exists.
    """
    home = os.path.expanduser('~')
    return [
        # Modern macOS Cloud-Storage location (most users today).
        os.path.join(home, 'Library', 'CloudStorage', 'OneDrive-Cisco',
                     'AI Projects', 'AdoptIQ_CSOne_Reports'),
        # Modern macOS Cloud-Storage with the legacy ``Documents/`` nesting.
        os.path.join(home, 'Library', 'CloudStorage', 'OneDrive-Cisco',
                     'Documents', 'AI Projects', 'AdoptIQ_CSOne_Reports'),
        # Pre-Big Sur macOS / Windows symlink shape.
        os.path.join(home, 'OneDrive - Cisco',
                     'AI Projects', 'AdoptIQ_CSOne_Reports'),
        # Legacy default with the ``Documents/`` segment.
        os.path.join(home, 'OneDrive - Cisco', 'Documents',
                     'AI Projects', 'AdoptIQ_CSOne_Reports'),
    ]


def _resolve_csone_onedrive_folder() -> str:
    """Round 17.2: return the first existing OneDrive candidate, or
    fall back to the modern Cloud-Storage path so error messages
    still point at the right "expected" location when nothing is
    synced.  Honors ``CSONE_ONEDRIVE_FOLDER`` env override
    unconditionally so power users can pin any path."""
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

    # Round 17.1: corpus also indexes the runtime user's Downloads
    # folder, filtered to ``AdoptIQ_*`` / ``AdoptIQ Enhanced ...``
    # filenames (see ``corpus_indexer._USER_REPORT_NAME_RE``).  The
    # walker is non-recursive so unrelated user files in nested
    # directories (e.g. ``~/Downloads/Photos/``) are never opened.
    # ``CSONE_INCLUDE_USER_DOWNLOADS=false`` skips the Downloads
    # source entirely; ``CSONE_USER_DOWNLOADS_DIR`` overrides the
    # default ``~/Downloads`` location for the rare site-specific
    # case where reports land elsewhere.
    CSONE_INCLUDE_USER_DOWNLOADS = (
        str(os.environ.get('CSONE_INCLUDE_USER_DOWNLOADS', 'true')).strip().lower()
        in {'1', 'true', 'yes', 'on'}
    )
    CSONE_USER_DOWNLOADS_DIR = os.environ.get('CSONE_USER_DOWNLOADS_DIR') or str(
        Path.home() / 'Downloads'
    )

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
    ADOPTIQ_CORPUS_SHARE_URL = (
        os.environ.get('ADOPTIQ_CORPUS_SHARE_URL')
        or 'https://cisco-my.sharepoint.com/:f:/r/personal/jestory_cisco_com/Documents/AI%20Projects/AdoptIQ_CSOne_Reports?csf=1&web=1&e=O3a4Ij'
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
    CIRCUIT_CONFIG = {
        "client_id": os.environ.get('CIRCUIT_CLIENT_ID') or '',
        "client_secret": os.environ.get('CIRCUIT_CLIENT_SECRET') or '',
        "app_key": os.environ.get('CIRCUIT_APP_KEY') or '',
        "model_name": os.environ.get('CIRCUIT_MODEL_NAME', 'gpt-5-nano')
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
