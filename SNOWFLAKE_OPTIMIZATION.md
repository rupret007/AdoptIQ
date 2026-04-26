# Snowflake Query Optimization

This document captures the current query-reduction model, baseline instrumentation, and runtime fallback controls.

> Companion documents: `SNOWFLAKE_USAGE.md` (table inventory, schemas, and per-report data flow) and `QUALITY_AUDIT.md` (Round 14+ audit findings, including any optimization-related work).

## What Changed

- Added run-scoped prefetch layer in `snowflake_prefetch.py`:
  - `AnalysisRunContext` stores account scope, day window, dataset cache, and cache-hit metrics.
  - `prefetch_comprehensive(...)` fetches the CSConsole bundle once per run context.
- Added Snowflake query instrumentation in `adoptiq_backend.py`:
  - Every `cursor.execute(...)`/`executemany(...)` call is counted.
  - Query previews (truncated SQL text) are sampled for diagnostics.
- Added runtime debug API in `app_simple.py`:
  - `GET /api/debug/verbose` returns:
    - `verbose_debug`
    - `snowflake_query_count`
    - `snowflake_query_samples`
  - `POST /api/debug/verbose` can:
    - Toggle verbose debug (`enabled: true|false`)
    - Reset baseline counter (`reset_query_metrics: true`)
- Added admin dashboard controls in `enhanced_admin_dashboard_v2.py`:
  - Verbose debug ON/OFF toggle
  - Snowflake query counter display
  - Query counter reset button
- Batched leader report data retrieval in `leader_report_generator.py`:
  - Before: per CSSM repeated fetch loops.
  - Now: one shared fetch per dataset, then in-memory split by CSSM.
- Integrated prefetch usage in `app_simple.py` for:
  - compact flow CSConsole bundle
  - comprehensive flow CSConsole bundle
  - renewal flow CSConsole bundle (including reuse for adoption barrier merge)
  - ask-ai legacy flow via `prefetch_ask_ai(...)`
  - ask-ai grounded flow via `prefetch_ask_ai_grounded(...)` with intent-based dataset include list

## Query Savings Model

For leader reports with `N` direct reports:

- Previous pattern: approximately `N * 5` Snowflake queries
  - subscriptions + action plans + adoption barriers + customer pulse + success priorities per CSSM
- Current pattern: approximately `5` Snowflake queries total
  - one query per dataset, then local filtering
- Estimated reduction: `((N*5 - 5) / (N*5)) * 100%`
  - Example (`N=10`): ~50 queries to ~5 queries (~90% reduction)

## Baseline Procedure

1. Open admin dashboard at `http://localhost:5152`.
2. In **Debug Controls**, click **Reset Query Counter**.
3. Run a target report flow.
4. Refresh admin dashboard and record `Snowflake Queries (since reset)`.
5. Repeat for each flow:
   - comprehensive
   - compact
   - renewal
   - leader
   - ask-ai

## Runtime Fallback and Safety

- Verbose mode defaults OFF unless `ADOPTIQ_VERBOSE_DEBUG=1`.
- Toggle is local-only through protected admin/main app routes.
- Query instrumentation is read-only and does not alter SQL payloads.
- If instrumentation or debug endpoint fails, report generation continues.

## Remaining Roadmap

- Consolidate any remaining duplicated fetch paths to use shared prefetched datasets.
- Add more per-flow parity assertions tied to query-count baselines.
- Add a compact runtime summary for prefetch cache hits/misses by report flow.
