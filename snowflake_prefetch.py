import logging
from dataclasses import dataclass, field
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


@dataclass
class AnalysisRunContext:
    ctx: Any
    account_ids: Tuple[str, ...]
    days: int
    cache: Dict[str, pd.DataFrame] = field(default_factory=dict)
    metrics: Dict[str, int] = field(default_factory=dict)

    @classmethod
    def build(cls, ctx: Any, account_ids: Iterable[Any], days: int) -> "AnalysisRunContext":
        return cls(ctx=ctx, account_ids=_normalize_account_ids(account_ids), days=int(days))

    def get_or_fetch(self, dataset_name: str) -> pd.DataFrame:
        if dataset_name in self.cache:
            self.metrics[f"{dataset_name}_cache_hits"] = self.metrics.get(f"{dataset_name}_cache_hits", 0) + 1
            return self.cache[dataset_name]

        fetcher = _FETCHERS[dataset_name]
        df = fetcher(self.ctx, list(self.account_ids), self.days)
        if df is None:
            df = pd.DataFrame()
        self.cache[dataset_name] = df
        self.metrics[f"{dataset_name}_queries"] = self.metrics.get(f"{dataset_name}_queries", 0) + 1
        return df


def prefetch_datasets(run_ctx: AnalysisRunContext, dataset_names: Iterable[str]) -> Dict[str, pd.DataFrame]:
    results: Dict[str, pd.DataFrame] = {}
    for name in dataset_names:
        if name not in _FETCHERS:
            continue
        try:
            results[name] = run_ctx.get_or_fetch(name)
        except Exception as e:
            logger.debug("Snowflake prefetch skipped for %s: %s", name, e)
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
