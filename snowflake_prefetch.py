import logging
from dataclasses import dataclass, field
from threading import Lock
from typing import Any, Dict, Iterable, List, Tuple

import pandas as pd

from adoptiq_backend import (
    fetch_csconsole_action_plans,
    fetch_csconsole_customer_pulse,
    fetch_csconsole_success_priorities,
    fetch_csconsole_adoption_barriers,
    fetch_support_cases_snowflake,
)


_FETCHERS = {
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
        cleaned = str(v).strip()
        if not cleaned or cleaned.lower() == "none":
            continue
        values.append(cleaned)
    return tuple(sorted(set(values)))


@dataclass
class AnalysisRunContext:
    ctx: Any
    account_ids: Tuple[str, ...]
    days: int
    customer_names: Tuple[str, ...] = field(default_factory=tuple)
    cache: Dict[str, pd.DataFrame] = field(default_factory=dict)
    metrics: Dict[str, int] = field(default_factory=dict)
    _lock: Lock = field(default_factory=Lock)

    @classmethod
    def build(
        cls,
        ctx: Any,
        account_ids: Iterable[Any],
        days: int,
        customer_names: Iterable[Any] = (),
    ) -> "AnalysisRunContext":
        return cls(
            ctx=ctx,
            account_ids=_normalize_account_ids(account_ids),
            days=int(days),
            customer_names=_normalize_customer_names(customer_names),
        )

    def get_or_fetch(self, dataset_name: str) -> pd.DataFrame:
        with self._lock:
            if dataset_name in self.cache:
                self.metrics[f"{dataset_name}_cache_hits"] = self.metrics.get(f"{dataset_name}_cache_hits", 0) + 1
                return self.cache[dataset_name]

            if dataset_name not in _FETCHERS:
                raise ValueError(f"Unknown dataset: {dataset_name}")
            fetcher = _FETCHERS[dataset_name]
            if dataset_name == "csconsole_success_priorities":
                identifiers = list(self.customer_names) if self.customer_names else list(self.account_ids)
            else:
                identifiers = list(self.account_ids)
            df = fetcher(self.ctx, identifiers, self.days)
            if df is None:
                df = pd.DataFrame()
            self.cache[dataset_name] = df
            self.metrics[f"{dataset_name}_queries"] = self.metrics.get(f"{dataset_name}_queries", 0) + 1
            return df


def prefetch_datasets(run_ctx: AnalysisRunContext, dataset_names: Iterable[str]) -> Dict[str, pd.DataFrame]:
    results: Dict[str, pd.DataFrame] = {}
    for name in dataset_names:
        if name not in _FETCHERS:
            logger.warning("Unknown dataset name '%s' in prefetch request", name)
            continue
        try:
            results[name] = run_ctx.get_or_fetch(name)
        except Exception as e:
            logger.warning("Snowflake prefetch failed for %s: %s", name, e)
            results[name] = pd.DataFrame()
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
