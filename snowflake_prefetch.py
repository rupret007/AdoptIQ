import logging
from dataclasses import dataclass, field
from threading import Lock
from typing import Any, Dict, Iterable, List, Tuple

import pandas as pd

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
    _lock: Lock = field(default_factory=Lock)

    @classmethod
    def build(
        cls,
        ctx: Any,
        account_ids: Iterable[Any],
        days: int,
        customer_names: Iterable[Any] = (),
        owner_emails: Iterable[Any] = (),
    ) -> "AnalysisRunContext":
        return cls(
            ctx=ctx,
            account_ids=_normalize_account_ids(account_ids),
            days=int(days),
            customer_names=_normalize_customer_names(customer_names),
            owner_emails=_normalize_owner_emails(owner_emails),
        )

    def get_or_fetch(self, dataset_name: str) -> Any:
        with self._lock:
            if dataset_name in self.cache:
                self.metrics[f"{dataset_name}_cache_hits"] = self.metrics.get(f"{dataset_name}_cache_hits", 0) + 1
                return self.cache[dataset_name]

            if dataset_name not in _FETCHERS:
                raise ValueError(f"Unknown dataset: {dataset_name}")
            fetcher = _FETCHERS[dataset_name]
            if dataset_name == "csconsole_success_priorities":
                # Success priorities are keyed by customer identifiers, not account IDs.
                identifiers = list(self.customer_names)
            else:
                identifiers = list(self.account_ids)
            if dataset_name in _OWNER_AWARE_DATASETS and self.owner_emails:
                df = fetcher(self.ctx, identifiers, self.days, owner_emails=list(self.owner_emails))
            else:
                df = fetcher(self.ctx, identifiers, self.days)
            if df is None:
                df = pd.DataFrame()
            self.cache[dataset_name] = df
            self.metrics[f"{dataset_name}_queries"] = self.metrics.get(f"{dataset_name}_queries", 0) + 1
            return df


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
                logger.debug("Could not set fetch_error attr on %s: %s", name, _attr_err)
            with run_ctx._lock:
                run_ctx.cache[name] = empty
                run_ctx.metrics[f"{name}_errors"] = run_ctx.metrics.get(f"{name}_errors", 0) + 1
            results[name] = empty
    return results


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
