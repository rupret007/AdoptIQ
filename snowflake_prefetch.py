import hashlib
import logging
import os
import random
import time
import weakref
from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Lock
from typing import Any, Dict, Iterable, List, Optional, Tuple

import pandas as pd


# Round 12 / Phase 11.2: process-wide registry of active
# ``AnalysisRunContext`` instances so the verbose debug API in
# ``app_simple.verbose_debug_api`` can surface live cache key counts and
# per-dataset metrics (``*_cache_hits``, ``*_queries``, ``*_errors``,
# ``*_retries``).  Previously these counters were locked inside each
# context dataclass and never crossed the request boundary, so support
# had no way to tell whether a slow report was hammering Snowflake or
# happily replaying cached frames.  We use a ``WeakSet`` so contexts are
# evicted automatically when their owning analysis finishes -- the
# admin endpoint never extends their lifetime.
_ACTIVE_CONTEXTS: "weakref.WeakSet[AnalysisRunContext]" = weakref.WeakSet()
_ACTIVE_CONTEXTS_LOCK = Lock()


# Round 7 / Phase 2.10: bounded retry with jittered exponential
# backoff for transient Snowflake fetch failures.  The previous code
# made exactly one fetch attempt per dataset, so a single
# ``OperationalError: 503 / connection reset`` permanently stamped
# ``fetch_error`` on the cache and caused the report to render the
# partial-data banner instead of recovering.  Snowflake's recommended
# client behaviour for the most common transient errors (network
# blips, brief 5xx, rate-limit) is exactly this pattern.

_PREFETCH_RETRY_MAX_ATTEMPTS = max(
    1, int(os.environ.get("ADOPTIQ_PREFETCH_RETRY_MAX_ATTEMPTS", "3"))
)
_PREFETCH_RETRY_BASE_SECONDS = max(
    0.1, float(os.environ.get("ADOPTIQ_PREFETCH_RETRY_BASE_SECONDS", "0.5"))
)
_PREFETCH_RETRY_MAX_SECONDS = max(
    _PREFETCH_RETRY_BASE_SECONDS,
    float(os.environ.get("ADOPTIQ_PREFETCH_RETRY_MAX_SECONDS", "5.0")),
)

_TRANSIENT_FETCH_ERROR_TOKENS = (
    "operationalerror",
    "timeout",
    "timed out",
    "connection reset",
    "connection aborted",
    "temporarily unavailable",
    "service unavailable",
    "gateway timeout",
    "throttle",
    "rate limit",
    "rate-limit",
    "too many requests",
    "503",
    "504",
)


def _is_transient_fetch_error(exc: BaseException) -> bool:
    """Heuristic: does this fetch failure look retryable?"""
    if exc is None:
        return False
    msg = (str(exc) or "").lower()
    name = type(exc).__name__.lower()
    if any(tok in name for tok in ("operationalerror", "interfaceerror", "connectionerror", "timeout")):
        return True
    return any(tok in msg for tok in _TRANSIENT_FETCH_ERROR_TOKENS)


def _retry_sleep_seconds(attempt: int) -> float:
    """Full-jitter exponential backoff (AWS / Snowflake-style).

    Round 12 / Phase 11.5: previously this used ``random.uniform(0,
    backoff)`` unconditionally.  Tests that captured retry traces (or
    asserted "no sleep when ``ADOPTIQ_PREFETCH_RETRY_MAX_ATTEMPTS=1``"
    timing budgets) saw flaky outputs because the random jitter was
    seeded by the wall clock.  When ``ADOPTIQ_TEST_MODE`` is set we
    return a deterministic *half* of the cap so golden traces are
    reproducible across runs without losing the magnitude of the
    backoff curve.  Production behaviour is unchanged.
    """
    backoff = min(
        _PREFETCH_RETRY_MAX_SECONDS,
        _PREFETCH_RETRY_BASE_SECONDS * (2 ** max(0, attempt - 1)),
    )
    if os.environ.get("ADOPTIQ_TEST_MODE", "").strip().lower() in {"1", "true", "yes"}:
        return float(backoff) / 2.0
    return random.uniform(0.0, backoff)

from adoptiq_backend import (
    fetch_barrier_velocity,
    fetch_enhanced_account_insights,
    fetch_period_comparison,
    fetch_adoption_barriers,
    fetch_csconsole_action_plans,
    fetch_csconsole_customer_pulse,
    fetch_csconsole_success_priorities,
    fetch_csconsole_adoption_barriers,
    fetch_support_cases_snowflake,
)
from data_normalization import normalize_customer_name
from data_contracts import annotate_with_contract


# Phase 4.1: map prefetch dataset names to row-contract names so we
# can validate the schema as soon as a fetch lands. Datasets without
# a row contract (period_comparison / barrier_velocity / etc. — these
# are dict aggregates, not row tables) are intentionally absent.
_DATASET_TO_ROW_CONTRACT: Dict[str, str] = {
    "adoption_barriers": "adoption_barriers",
    "csconsole_adoption_barriers": "adoption_barriers",
    "csconsole_customer_pulse": "customer_pulse",
    "support_cases_snowflake": "tac_cases",
}

# Round 7 / Phase 2.12: period_comparison, barrier_velocity, and
# enhanced_account_insights are dict aggregates rather than row tables,
# but they still benefit from a documented "expected key" contract so
# downstream consumers can detect partial / malformed results.  We
# annotate them on a best-effort basis: any payload that is a dict gets
# a ``payload_contract`` attribute via ``df.attrs`` /
# ``aggregate_meta`` so consistency checks know which top-level keys
# they must see.
# Round 11 / Phase 11.6: previous contract advertised
# ``current_period`` / ``previous_period`` / ``delta`` for
# ``period_comparison``, ``by_customer`` / ``summary`` for
# ``barrier_velocity`` and ``accounts`` / ``summary`` for
# ``enhanced_account_insights`` -- none of which match what
# ``adoptiq_backend.fetch_*`` actually returns.  Downstream
# consistency checks were therefore comparing payloads against
# fictitious keys, and any payload would either always trip a
# warning or always pass depending on which side was treated as
# authoritative.  Align the contract with the real return shape
# so a missing top-level key signals a true partial / malformed
# result instead of a documentation drift.  Keys here MUST stay
# in sync with the corresponding ``fetch_*`` implementations in
# ``adoptiq_backend.py``.
_DATASET_TO_AGGREGATE_CONTRACT: Dict[str, Tuple[str, ...]] = {
    # adoptiq_backend.fetch_period_comparison sets one entry per
    # rolled-up table (adoption_barriers / customer_pulse /
    # action_plans) with each containing ``current``/``previous``
    # counts.  We treat all three as required for a healthy run;
    # ``fetch_error`` may also be present but is optional.
    "period_comparison": (
        "adoption_barriers",
        "customer_pulse",
        "action_plans",
    ),
    # adoptiq_backend.fetch_barrier_velocity returns the weekly
    # aggregate in ``weeks`` plus headline scalars.
    "barrier_velocity": (
        "weeks",
        "weeks_total",
        "avg_new_per_week",
        "avg_closed_per_week",
        "net_velocity_per_week",
        "resolution_rate_pct",
    ),
    # adoptiq_backend.fetch_enhanced_account_insights always
    # stamps ``_meta`` first, then attaches per-subsection
    # results that may individually be missing if their
    # subquery failed.  Only ``_meta`` is mandatory; the rest
    # are tracked through ``_meta['subsection_errors']`` (Round
    # 11 / Phase 10.7).
    "enhanced_account_insights": (
        "_meta",
    ),
}


def _fetch_adoption_barriers(ctx: Any, account_ids: List[str], days: int) -> pd.DataFrame:
    return fetch_adoption_barriers(ctx, account_ids, days)


def _fetch_period_comparison(ctx: Any, account_ids: List[str], days: int) -> Dict[str, Any]:
    return fetch_period_comparison(ctx, account_ids, days) or {}


def _fetch_barrier_velocity(ctx: Any, account_ids: List[str], days: int) -> Dict[str, Any]:
    return fetch_barrier_velocity(ctx, account_ids, days) or {}


def _fetch_enhanced_account_insights(ctx: Any, account_ids: List[str], days: int) -> Dict[str, Any]:
    return fetch_enhanced_account_insights(ctx, account_ids, days) or {}


_FETCHERS = {
    "adoption_barriers": _fetch_adoption_barriers,
    "period_comparison": _fetch_period_comparison,
    "barrier_velocity": _fetch_barrier_velocity,
    "enhanced_account_insights": _fetch_enhanced_account_insights,
    "csconsole_action_plans": fetch_csconsole_action_plans,
    "csconsole_customer_pulse": fetch_csconsole_customer_pulse,
    "csconsole_success_priorities": fetch_csconsole_success_priorities,
    "csconsole_adoption_barriers": fetch_csconsole_adoption_barriers,
    "support_cases_snowflake": fetch_support_cases_snowflake,
}

logger = logging.getLogger(__name__)


# Round 30 / L2: defensive contract check that every datetime-typed
# column on a prefetched Snowflake frame is tz-aware UTC.  The
# ``ALTER SESSION SET TIMEZONE='UTC'`` we issue at connect time
# (adoptiq_backend._instrument_snowflake_connection) ensures Snowflake
# returns rows in UTC, but the connector's ``fetch_pandas_*`` API can
# still produce tz-naive ``datetime64[ns]`` columns when the source
# column type is ``TIMESTAMP_NTZ`` (no explicit zone).  The compact /
# executive 30-day window math now compares against
# ``datetime.now(timezone.utc)`` (Round 30 / H3), so a tz-naive
# ``date_opened`` column would raise ``TypeError: Invalid comparison
# between dtype=datetime64[ns] and Timestamp``.  This helper warns
# (or raises in strict mode) so the contract violation surfaces at
# prefetch time rather than midway through report rendering.
_DATETIME_COLUMN_HINTS: Tuple[str, ...] = (
    "DATE", "TIME", "AT", "OPENED", "CLOSED", "CREATED",
    "MODIFIED", "UPDATED", "_TS", "_DT",
)


def _assert_datetime_columns_tz_aware(
    df: Optional[pd.DataFrame],
    *,
    dataset_name: str = "<unknown>",
    strict: Optional[bool] = None,
) -> None:
    """Round 30 / L2: assert that every datetime column is tz-aware.

    Iterates over ``df.columns`` and inspects every column whose
    dtype is ``datetime64[ns, ...]`` or ``datetime64[ns]``.  When a
    column is tz-naive AND its name looks like a timestamp/date
    column (matched against :data:`_DATETIME_COLUMN_HINTS`), emit a
    structured WARN so operators see the drift at fetch time.  When
    ``strict`` is ``True`` (or ``ADOPTIQ_STRICT_MODE`` env var is
    enabled), raise ``ValueError`` instead so CI and dev runs catch
    the violation immediately.

    Tolerated:

    - Empty / ``None`` frames (no-op).
    - Columns whose dtype is not ``datetime64`` (e.g. plain ``object``
      columns containing ISO strings -- those are normalized later
      by ``pd.to_datetime(..., utc=True)`` at the call site).
    - Columns whose name does not look like a date/time column
      (e.g. ``ACCOUNT_ID_C`` is ``object`` but happens to be parsed
      as ``datetime64`` by an upstream test fixture).
    """

    if df is None:
        return
    try:
        if not isinstance(df, pd.DataFrame) or df.empty:
            return
    except Exception:  # noqa: BLE001
        return

    # Resolve strict mode the same way ``_assert_arr_attrs`` does.
    if strict is None:
        try:
            _env = str(os.environ.get("ADOPTIQ_STRICT_MODE", "0")).strip().lower()
            strict = _env in ("1", "true", "yes", "on")
        except Exception:  # noqa: BLE001
            strict = False

    naive_columns: List[str] = []
    for col in df.columns:
        try:
            dtype = df[col].dtype
        except Exception:  # noqa: BLE001
            continue
        # Only inspect datetime-typed columns.
        if not str(dtype).startswith("datetime64"):
            continue
        # tz-aware dtypes look like ``datetime64[ns, UTC]`` -- the
        # presence of a comma is the canonical signal.  pandas also
        # exposes ``DatetimeTZDtype`` which carries a ``.tz``
        # attribute; check both for back-compat.
        is_tz_aware = "," in str(dtype) or getattr(dtype, "tz", None) is not None
        if is_tz_aware:
            continue
        # Only flag columns whose NAME looks like a timestamp.
        col_upper = str(col).upper()
        if not any(hint in col_upper for hint in _DATETIME_COLUMN_HINTS):
            continue
        naive_columns.append(str(col))

    if not naive_columns:
        return

    extra = {
        "event": "snowflake.tz_naive_datetime_columns",
        "dataset_name": dataset_name,
        "naive_columns": naive_columns,
        "note": (
            "Snowflake returned tz-naive datetime columns despite "
            "ALTER SESSION SET TIMEZONE='UTC'.  Source columns may be "
            "TIMESTAMP_NTZ; coerce with pd.to_datetime(..., utc=True) "
            "before comparing against tz-aware now()."
        ),
    }
    if strict:
        logger.error("snowflake.tz_naive_datetime_columns", extra=extra)
        raise ValueError(
            f"Snowflake dataset {dataset_name!r} returned tz-naive datetime "
            f"columns: {naive_columns}.  Pin the session to UTC and coerce "
            f"with pd.to_datetime(..., utc=True)."
        )
    logger.warning("snowflake.tz_naive_datetime_columns", extra=extra)


def _normalize_account_ids(account_ids: Iterable[Any]) -> Tuple[str, ...]:
    values: List[str] = []
    for v in (account_ids or []):
        if v is None:
            continue
        cleaned = str(v).strip()
        if not cleaned or cleaned.lower() == "none":
            continue
        values.append(cleaned)
    return tuple(sorted(set(values)))


def _normalize_customer_names(customer_names: Iterable[Any]) -> Tuple[str, ...]:
    values: List[str] = []
    for v in (customer_names or []):
        if v is None:
            continue
        cleaned = normalize_customer_name(v)
        if not cleaned or cleaned in {"Unknown", "none", "None"}:
            continue
        values.append(cleaned)
    return tuple(sorted(set(values)))


def _normalize_owner_emails(owner_emails: Iterable[Any]) -> Tuple[str, ...]:
    """Return a sorted, de-duplicated tuple of email addresses.

    Delegates normalization (lowercase, strip, ``@`` required) to the canonical
    implementation in ``adoptiq_backend._normalize_owner_emails`` so the backend
    and the run-context stay byte-identical.
    """
    try:
        from adoptiq_backend import _normalize_owner_emails as _canonical_normalize_owner_emails
    except Exception:
        _canonical_normalize_owner_emails = None

    if _canonical_normalize_owner_emails is not None:
        cleaned = _canonical_normalize_owner_emails(owner_emails)
    else:
        # Fallback mirrors the backend contract so behavior stays consistent
        # even if the import cycle breaks at startup.
        cleaned = []
        for v in (owner_emails or []):
            if v is None:
                continue
            try:
                text = str(v).strip().lower()
            except Exception:
                continue
            if not text or "@" not in text:
                continue
            cleaned.append(text)
    return tuple(sorted(set(cleaned)))


# Datasets that accept an optional ``owner_emails`` kwarg to capture records
# created by team members on accounts they do not primarily own.
_OWNER_AWARE_DATASETS: Tuple[str, ...] = (
    "csconsole_action_plans",
    "csconsole_customer_pulse",
    "csconsole_adoption_barriers",
)


@dataclass
class AnalysisRunContext:
    ctx: Any
    account_ids: Tuple[str, ...]
    days: int
    customer_names: Tuple[str, ...] = field(default_factory=tuple)
    owner_emails: Tuple[str, ...] = field(default_factory=tuple)
    cache: Dict[str, Any] = field(default_factory=dict)
    metrics: Dict[str, int] = field(default_factory=dict)
    # Phase 3.1: timestamp marking when the *data* was first retrieved
    # for this analysis run, separate from the *render* time formatters
    # use today. Defaults to context construction time and is exposed to
    # formatters so reports can render both "Generated" and "Data as of".
    data_retrieved_at: Optional[datetime] = None
    _lock: Lock = field(default_factory=Lock)

    @classmethod
    def build(
        cls,
        ctx: Any,
        account_ids: Iterable[Any],
        days: int,
        customer_names: Iterable[Any] = (),
        owner_emails: Iterable[Any] = (),
        data_retrieved_at: Optional[datetime] = None,
    ) -> "AnalysisRunContext":
        instance = cls(
            ctx=ctx,
            account_ids=_normalize_account_ids(account_ids),
            days=int(days),
            customer_names=_normalize_customer_names(customer_names),
            owner_emails=_normalize_owner_emails(owner_emails),
            # Round 5 / Phase 6.10: ``datetime.utcnow()`` is deprecated in
            # Python 3.12+ (returns naive UTC, ambiguous when serialized).
            # Use ``datetime.now(timezone.utc)`` so the prefetch stamp is
            # explicitly tz-aware UTC and serializes with a 'Z' suffix.
            data_retrieved_at=data_retrieved_at or datetime.now(timezone.utc),
        )
        # Round 12 / Phase 11.2: register the freshly built context so
        # ``get_active_prefetch_metrics`` (called from the verbose debug
        # API) can introspect cache hits / errors / queue depth without
        # needing each request to thread the context through.  The
        # registry is a ``WeakSet`` so this introduces no extra
        # lifetime; the entry vanishes once the analysis releases its
        # last strong reference.
        try:
            with _ACTIVE_CONTEXTS_LOCK:
                _ACTIVE_CONTEXTS.add(instance)
        except Exception:  # pragma: no cover - defensive only
            # Round 12 / Phase 11.2: never let the registry side-channel
            # crash a real analysis -- the registry is purely
            # diagnostic.
            pass
        return instance

    def _cache_key(self, dataset_name: str) -> str:
        """Round 11 / Phase 11.7: derive a per-fetch cache key that
        captures *what* was actually queried in addition to the
        dataset name, so a shared/long-lived ``AnalysisRunContext``
        cannot return stale data when the underlying scope changes
        mid-run.

        Previously ``self.cache`` was keyed only on ``dataset_name``.
        That was safe in the common case where the context is built
        once per analysis and discarded, but it silently returned
        stale values when:

          * ``account_ids`` was widened (e.g. portfolio mode upgraded
            from a single CSSM to the full team after the first
            dataset landed in cache),
          * ``customer_names`` rotated for ``csconsole_success_priorities``
            (the only dataset keyed off customer names rather than
            account IDs), or
          * ``days`` was bumped from 90 to 180 to widen the
            historical window.

        The new key fingerprints (dataset, days, scope_kind,
        identifiers_hash) so any of those rotations forces a fresh
        fetch instead of returning a stale answer that disagrees
        with the displayed window.
        """
        if dataset_name == "csconsole_success_priorities":
            scope_kind = "customer_names"
            scope_values = self.customer_names
        else:
            scope_kind = "account_ids"
            scope_values = self.account_ids
        # ``account_ids`` / ``customer_names`` are already sorted
        # tuples produced by the ``_normalize_*`` helpers above, so
        # the SHA-1 fingerprint is deterministic across runs that
        # share the same scope regardless of original input order.
        _id_payload = "\n".join(scope_values).encode("utf-8")
        # NOTE: SHA-256 (not SHA-1) per the canonical crypto rule -
        # this is a non-security cache fingerprint, but we still
        # use the project-approved digest so a future re-purposing
        # of the value (e.g. logging, on-disk persistence) does not
        # accidentally rely on a banned algorithm.
        _id_hash = hashlib.sha256(_id_payload).hexdigest()[:16]
        # Owner emails matter for ``_OWNER_AWARE_DATASETS`` because
        # widening the team membership widens the row set (records
        # created by a teammate on accounts they do not primarily
        # own).  Fold the email fingerprint in so a mid-run team
        # roster change does not surface stale rows.
        _owner_hash = ""
        if dataset_name in _OWNER_AWARE_DATASETS and self.owner_emails:
            _owner_payload = "\n".join(self.owner_emails).encode("utf-8")
            _owner_hash = hashlib.sha256(_owner_payload).hexdigest()[:8]
        return f"{dataset_name}::days={int(self.days)}::scope={scope_kind}::ids={_id_hash}::owners={_owner_hash}"

    def get_or_fetch(self, dataset_name: str) -> Any:
        with self._lock:
            cache_key = self._cache_key(dataset_name)
            if cache_key in self.cache:
                self.metrics[f"{dataset_name}_cache_hits"] = self.metrics.get(f"{dataset_name}_cache_hits", 0) + 1
                return self.cache[cache_key]
            # Round 11 / Phase 11.7: also honour the legacy
            # ``dataset_name`` key so external callers that pre-seeded
            # the cache (e.g. tests, or a code path that warmed the
            # cache before this commit landed) do not silently re-fetch
            # on first call.  Migration is one-way: we copy the legacy
            # value into the canonical key so subsequent lookups are
            # served from the new keyspace.
            if dataset_name in self.cache:
                _legacy = self.cache.pop(dataset_name)
                self.cache[cache_key] = _legacy
                self.metrics[f"{dataset_name}_cache_hits"] = self.metrics.get(f"{dataset_name}_cache_hits", 0) + 1
                return _legacy

            if dataset_name not in _FETCHERS:
                raise ValueError(f"Unknown dataset: {dataset_name}")
            fetcher = _FETCHERS[dataset_name]
            if dataset_name == "csconsole_success_priorities":
                # Success priorities are keyed by customer identifiers, not account IDs.
                identifiers = list(self.customer_names)
            else:
                identifiers = list(self.account_ids)
            # Round 5 / Phase 4.5: capture both the None-return path AND
            # the exception path with an explicit ``fetch_error`` attr
            # on the resulting empty DataFrame.  Previously a fetcher
            # exception bubbled up (forcing the whole prefetch to
            # abort) while a fetcher that returned ``None`` was
            # silently swapped for an empty frame indistinguishable
            # from a clean "no rows in window".  Both failure modes
            # now produce an empty DataFrame whose ``attrs`` carry the
            # diagnostic, so the data-source validator (Phase 4.2)
            # can blame the fetch instead of pretending the source is
            # healthy and just empty.
            # Round 7 / Phase 2.10: jittered exponential-backoff
            # retry for transient Snowflake errors.  Non-transient
            # exceptions (compilation errors, table policy violations,
            # auth failures) short-circuit immediately so the report
            # surfaces them without artificial latency.
            df = None
            _fetch_err: Optional[BaseException] = None
            for _attempt in range(1, _PREFETCH_RETRY_MAX_ATTEMPTS + 1):
                try:
                    if dataset_name in _OWNER_AWARE_DATASETS and self.owner_emails:
                        df = fetcher(self.ctx, identifiers, self.days, owner_emails=list(self.owner_emails))
                    else:
                        df = fetcher(self.ctx, identifiers, self.days)
                    _fetch_err = None
                    break
                except Exception as _err:
                    _fetch_err = _err
                    if (
                        _attempt < _PREFETCH_RETRY_MAX_ATTEMPTS
                        and _is_transient_fetch_error(_err)
                    ):
                        _wait = _retry_sleep_seconds(_attempt)
                        logger.warning(
                            "snowflake_prefetch: fetcher %s attempt %d/%d failed "
                            "with transient error (%s); retrying in %.2fs.",
                            dataset_name, _attempt, _PREFETCH_RETRY_MAX_ATTEMPTS,
                            type(_err).__name__, _wait,
                        )
                        try:
                            time.sleep(_wait)
                        except Exception:
                            pass  # noqa: PIE790
                        self.metrics[f"{dataset_name}_retries"] = (
                            self.metrics.get(f"{dataset_name}_retries", 0) + 1
                        )
                        continue
                    break

            if _fetch_err is not None:
                logger.warning(
                    "snowflake_prefetch: fetcher %s raised after %d attempt(s); "
                    "returning empty frame with fetch_error attr: %s",
                    dataset_name, _PREFETCH_RETRY_MAX_ATTEMPTS, _fetch_err,
                )
                df = pd.DataFrame()
                try:
                    df.attrs['fetch_error'] = f"{type(_fetch_err).__name__}: {_fetch_err}"
                    df.attrs['fetch_failure_kind'] = type(_fetch_err).__name__
                except Exception as _attr_err:
                    # Round 7 / Phase 2.11: surface attrs-stamp failures
                    # at WARNING (not silent) so the partial-data banner
                    # is not skipped because the marker never landed.
                    logger.warning(
                        "Round 7 / Phase 2.11: failed to stamp fetch_error attrs "
                        "on %s placeholder: %s",
                        dataset_name, _attr_err,
                    )
                # Round 5 / Phase 4.5: increment a per-dataset error
                # counter on the run context so the prefetch retry-cache
                # contract test can verify that a failed fetch is
                # remembered exactly once and not silently retried.
                self.metrics[f"{dataset_name}_errors"] = (
                    self.metrics.get(f"{dataset_name}_errors", 0) + 1
                )
            if df is None:
                df = pd.DataFrame()
                try:
                    df.attrs['fetch_error'] = (
                        f"fetcher for {dataset_name} returned None "
                        "(treat as fetch failure, not 'no rows')"
                    )
                    df.attrs['fetch_failure_kind'] = 'NoneReturned'
                except Exception as _attr_err:
                    # Round 7 / Phase 2.11: surface attrs-stamp failures.
                    logger.warning(
                        "Round 7 / Phase 2.11: failed to stamp fetch_error attrs "
                        "on %s None-result placeholder: %s",
                        dataset_name, _attr_err,
                    )
            # Round 30 / L2: assert the contract that every datetime
            # column on a Snowflake-sourced frame is tz-aware UTC.
            # Combined with ``ALTER SESSION SET TIMEZONE='UTC'`` at
            # connect time, this gives belt-and-suspenders coverage
            # for the 30-day window math in the compact + executive
            # reports.  Non-strict mode logs a WARN; strict mode
            # (``ADOPTIQ_STRICT_MODE=1``) raises so CI catches the
            # regression immediately.
            try:
                _assert_datetime_columns_tz_aware(df, dataset_name=dataset_name)
            except ValueError:
                # Re-raise so the strict-mode contract violation surfaces.
                raise
            except Exception as _tz_err:  # noqa: BLE001
                logger.debug(
                    "Round 30 / L2: tz-aware datetime assertion skipped for %s: %s",
                    dataset_name, _tz_err,
                )
            # Phase 4.1: validate row-level schema for datasets that
            # have a contract. Annotation stashes the result on
            # ``df.attrs['row_contract']`` so downstream consumers can
            # see the missing slot list without re-running the check.
            contract = _DATASET_TO_ROW_CONTRACT.get(dataset_name)
            if contract is not None and isinstance(df, pd.DataFrame):
                try:
                    annotate_with_contract(df, dataset=contract)
                except Exception as _ce:
                    logger.debug("Row contract annotation failed for %s: %s", dataset_name, _ce)
            # Round 7 / Phase 2.12: annotate dict-aggregate datasets
            # with their expected top-level keys so downstream
            # consistency checks can detect partial / malformed
            # responses without re-issuing the fetch.
            agg_contract = _DATASET_TO_AGGREGATE_CONTRACT.get(dataset_name)
            if agg_contract is not None and isinstance(df, dict):
                missing_keys = [k for k in agg_contract if k not in df]
                df.setdefault("_aggregate_contract", {
                    "dataset": dataset_name,
                    "expected_keys": list(agg_contract),
                    "missing_keys": missing_keys,
                })
                if missing_keys:
                    logger.warning(
                        "Round 7 / Phase 2.12: aggregate dataset %s missing "
                        "expected key(s): %s",
                        dataset_name, ", ".join(missing_keys),
                    )
            # Round 12 / Phase 10.2: previously cached DataFrames
            # only carried the *context-level* ``data_retrieved_at``
            # (set once for the whole prefetch batch) so the report
            # could not tell, per dataset, when its rows were
            # actually pulled from Snowflake.  A long-running batch
            # could finish hours after its first dataset was
            # retrieved -- and downstream "data as-of" disclosures
            # would then claim a single batch-end timestamp for
            # every panel, even though the early datasets were much
            # older.  Stamp each cache entry with its own
            # ``fetched_at_utc`` (UTC ISO-Z) so reports can disclose
            # per-dataset freshness honestly.  Use ``setdefault`` /
            # ``getattr`` defensively because ``df`` may be a dict
            # or a DataFrame.
            try:
                _r12_fetched = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
                if isinstance(df, pd.DataFrame):
                    try:
                        df.attrs['fetched_at_utc'] = _r12_fetched
                    except Exception as _attr_err:
                        logger.debug(
                            "Round 12 / Phase 10.2: failed to stamp fetched_at_utc on %s frame: %s",
                            dataset_name, _attr_err,
                        )
                elif isinstance(df, dict):
                    df.setdefault('fetched_at_utc', _r12_fetched)
            except Exception as _r12_ts_err:
                logger.debug(
                    "Round 12 / Phase 10.2: per-dataset fetched_at_utc stamp skipped (%s)",
                    _r12_ts_err,
                )
            # Round 11 / Phase 11.7: store under the scope-aware key
            # produced by ``_cache_key`` so a subsequent fetch for the
            # same dataset under a different (days, scope) tuple does
            # not collide with this entry.
            self.cache[cache_key] = df
            self.metrics[f"{dataset_name}_queries"] = self.metrics.get(f"{dataset_name}_queries", 0) + 1
            return df


def get_active_prefetch_metrics() -> Dict[str, Any]:
    """Round 12 / Phase 11.2: return aggregated prefetch metrics across
    every live ``AnalysisRunContext`` so the verbose debug API can
    surface ``cache_hits`` / ``queries`` / ``errors`` counts without
    threading a context through every request.

    The aggregate is intentionally lossy: per-dataset counters are
    summed across all live contexts, and ``cache_key_count`` reports
    the *total* number of populated cache slots (one slot per
    ``(dataset, scope)`` fingerprint).  Operators chasing why a single
    report is slow should still consult per-context metrics inline; the
    aggregate is for "is the prefetch layer healthy right now?" -- the
    same admin-visibility question Round 11 / Phase 10.3 raised about
    ``total_managers`` failures.

    Returns ``{ 'context_count': int, 'cache_key_count': int,
    'metrics': { '<dataset>_<counter>': int }, ... }``.
    """
    aggregate: Dict[str, int] = {}
    cache_key_count = 0
    context_count = 0
    try:
        with _ACTIVE_CONTEXTS_LOCK:
            # Snapshot under the registry lock to avoid concurrent
            # mutation while we fan out.
            contexts = list(_ACTIVE_CONTEXTS)
    except Exception:  # pragma: no cover - defensive
        contexts = []
    for ctx in contexts:
        if ctx is None:
            continue
        context_count += 1
        try:
            with ctx._lock:  # noqa: SLF001 - intentional metrics introspection
                cache_key_count += len(ctx.cache)
                for k, v in ctx.metrics.items():
                    try:
                        aggregate[k] = aggregate.get(k, 0) + int(v)
                    except (TypeError, ValueError):
                        # Round 12 / Phase 11.2: tolerate non-int
                        # counter values rather than blowing up the
                        # debug surface.
                        continue
        except Exception:  # pragma: no cover - defensive
            continue
    return {
        "context_count": context_count,
        "cache_key_count": cache_key_count,
        "metrics": aggregate,
    }


def prefetch_datasets(run_ctx: AnalysisRunContext, dataset_names: Iterable[str]) -> Dict[str, Any]:
    results: Dict[str, Any] = {}
    for name in dataset_names:
        if name not in _FETCHERS:
            logger.warning("Unknown dataset name '%s' in prefetch request", name)
            continue
        try:
            results[name] = run_ctx.get_or_fetch(name)
        except Exception as e:
            logger.warning("Snowflake prefetch failed for %s: %s", name, e)
            # Round 4: tag the empty placeholder with ``fetch_error`` so
            # downstream truncation-aware consumers can distinguish a
            # legitimate zero-row result from a query failure.  Pandas
            # ``.attrs`` survives most concat/copy paths (we add the
            # attribute to the cache copy *and* the returned copy).
            empty = pd.DataFrame()
            try:
                empty.attrs['fetch_error'] = str(e).strip() or e.__class__.__name__
                empty.attrs['fetch_error_dataset'] = name
            except Exception as _attr_err:
                # Round 7 / Phase 2.11: promoted from DEBUG to WARNING --
                # if the attrs marker fails to land, the partial-data
                # banner cannot recover the failure context.
                logger.warning(
                    "Round 7 / Phase 2.11: could not set fetch_error attr on %s: %s",
                    name, _attr_err,
                )
            with run_ctx._lock:
                run_ctx.cache[name] = empty
                run_ctx.metrics[f"{name}_errors"] = run_ctx.metrics.get(f"{name}_errors", 0) + 1
            results[name] = empty
    return results


def collect_fetch_warnings(bundle: Dict[str, Any]) -> list:
    """
    Phase 1.3b: scan a prefetch bundle (or any dict of DataFrames / dicts)
    for objects carrying a ``fetch_error`` marker and return a normalized
    list of ``{'dataset': str, 'error': str, 'kind': str}`` dicts. The
    returned list is what report formatters render as a "partial data"
    banner so the user sees that something failed instead of believing
    "0 rows" means "no events".

    Supports two input shapes (both used in the codebase):

    1. ``pandas.DataFrame`` whose ``.attrs`` carry ``fetch_error`` /
       ``fetch_error_dataset`` / optional ``fetch_error_kind`` (the canonical
       Phase 1.3a contract).
    2. plain ``dict`` results from helpers like ``fetch_period_comparison``
       and ``fetch_barrier_velocity`` that surface ``fetch_error`` /
       ``fetch_error_dataset`` keys directly.
    """
    warnings: list = []
    if not isinstance(bundle, dict):
        return warnings
    # Phase 4.2: also surface column-introspection failures so callers
    # can flag that owner-aware filters may have been silently
    # stripped from queries during the run.
    try:
        from adoptiq_backend import get_recent_column_introspection_failures
        for table, msg in (get_recent_column_introspection_failures() or {}).items():
            warnings.append({
                "dataset": f"schema:{table}",
                "error": msg,
                "kind": "column_introspection_failure",
            })
    except Exception as _ie:
        logger.debug("collect_fetch_warnings: introspection probe failed: %s", _ie)
    for key, value in bundle.items():
        try:
            if isinstance(value, pd.DataFrame):
                attrs = getattr(value, "attrs", {}) or {}
                err = attrs.get("fetch_error")
                if err:
                    warnings.append({
                        "dataset": attrs.get("fetch_error_dataset") or key,
                        "error": str(err),
                        "kind": attrs.get("fetch_error_kind") or "runtime",
                    })
                # Round 3 / Phase 2.8: also promote ``was_truncated`` to
                # a warning so the partial-data banner names the
                # dataset that hit its LIMIT and totals derived from
                # it (counts, sums) cannot be silently misread as
                # complete. Truncated frames always have a
                # ``fetch_limit`` / ``rows_returned`` pair we can quote.
                if attrs.get("was_truncated"):
                    _limit = attrs.get("fetch_limit")
                    _rows = attrs.get("rows_returned")
                    _detail_parts = []
                    if _rows is not None:
                        _detail_parts.append(f"{_rows} rows returned")
                    if _limit is not None:
                        _detail_parts.append(f"limit={_limit}")
                    detail = ", ".join(_detail_parts) or "limit hit"
                    warnings.append({
                        "dataset": attrs.get("fetch_error_dataset") or key,
                        "error": (
                            f"Result truncated by per-query LIMIT ({detail}); "
                            "totals derived from this source may underrepresent reality."
                        ),
                        "kind": "truncation",
                    })
            elif isinstance(value, dict):
                err = value.get("fetch_error")
                if err:
                    # Round 8 / Phase 2.2: ``fetch_error`` may now be a
                    # structured ``{error_kind, user_message}`` dict rather
                    # than a raw exception string.  Normalize before
                    # surfacing so downstream renderers don't print
                    # Python-dict repr strings.
                    if isinstance(err, dict):
                        _err_text = (
                            err.get("user_message")
                            or err.get("error_kind")
                            or "See logs for details"
                        )
                        _kind = (
                            err.get("error_kind")
                            or value.get("fetch_error_kind")
                            or "runtime"
                        )
                    else:
                        _err_text = str(err)
                        _kind = value.get("fetch_error_kind") or "runtime"
                    warnings.append({
                        "dataset": value.get("fetch_error_dataset") or key,
                        "error": _err_text,
                        "kind": _kind,
                    })
                # Phase 2.8: same truncation surfacing for dict-shaped
                # results (e.g. fetch_period_comparison).
                if value.get("was_truncated"):
                    _limit = value.get("fetch_limit")
                    _rows = value.get("rows_returned")
                    _detail_parts = []
                    if _rows is not None:
                        _detail_parts.append(f"{_rows} rows returned")
                    if _limit is not None:
                        _detail_parts.append(f"limit={_limit}")
                    detail = ", ".join(_detail_parts) or "limit hit"
                    warnings.append({
                        "dataset": value.get("fetch_error_dataset") or key,
                        "error": (
                            f"Result truncated by per-query LIMIT ({detail}); "
                            "totals derived from this source may underrepresent reality."
                        ),
                        "kind": "truncation",
                    })
        except Exception as scan_err:
            logger.debug("collect_fetch_warnings: skipped %s due to %s", key, scan_err)
    return warnings


def raise_if_required_dataset_failed(
    bundle: Dict[str, Any],
    required_datasets: Iterable[str],
) -> None:
    """
    Phase 1.3b: hard-stop the report if any *required* dataset is in a
    fetch-error state. This is the loud counterpart to
    ``collect_fetch_warnings``: warnings can be rendered, but errors on
    required sources should kill the report so we don't ship a confident
    zero. Raises ``RuntimeError`` with a per-dataset summary.
    """
    required = {str(name) for name in required_datasets if name}
    if not required:
        return
    failed = [w for w in collect_fetch_warnings(bundle) if w.get("dataset") in required]
    if not failed:
        return
    summary = "; ".join(f"{w['dataset']}: {w['error']}" for w in failed)
    raise RuntimeError(
        f"Required data source(s) failed to fetch: {summary}. "
        "Refusing to render report with missing required data."
    )


def prefetch_comprehensive(run_ctx: AnalysisRunContext) -> Dict[str, pd.DataFrame]:
    return prefetch_datasets(
        run_ctx,
        (
            "csconsole_action_plans",
            "csconsole_customer_pulse",
            "csconsole_success_priorities",
            "csconsole_adoption_barriers",
        ),
    )


def prefetch_ask_ai(run_ctx: AnalysisRunContext) -> Dict[str, pd.DataFrame]:
    return prefetch_datasets(
        run_ctx,
        (
            "support_cases_snowflake",
            "csconsole_customer_pulse",
            "csconsole_success_priorities",
            "csconsole_action_plans",
        ),
    )


def prefetch_ask_ai_grounded(
    run_ctx: AnalysisRunContext,
    include_datasets: Iterable[str] = (),
) -> Dict[str, Any]:
    """
    Grounded Ask AI bundle with optional dataset gating.
    The optional include list enables intent-based retrieval planning to reduce query volume.
    """
    # NOTE: we intentionally use ``csconsole_adoption_barriers`` (owner-aware)
    # rather than the legacy ``adoption_barriers`` (account-only) so that
    # collaborator-authored ABs on accounts outside a CSSM's primary portfolio
    # are captured here too (mirrors the manager-report Brandon/Mario fix).
    base = (
        "support_cases_snowflake",
        "csconsole_customer_pulse",
        "csconsole_success_priorities",
        "csconsole_action_plans",
        "csconsole_adoption_barriers",
        "period_comparison",
        "barrier_velocity",
        "enhanced_account_insights",
    )
    if include_datasets:
        selected = tuple(name for name in base if name in set(include_datasets))
    else:
        selected = base
    return prefetch_datasets(run_ctx, selected)
