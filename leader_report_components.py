"""Role-based facades for :class:`LeaderReportGenerator`.

The :class:`LeaderReportGenerator` in :mod:`leader_report_generator` is a
"god class" that mixes three distinct responsibilities:

1. **Repository** -- fetching and collating Snowflake data
   (``_fetch_*``, ``_get_subscriptions_for_cssm``, ``_collect_team_data``).
2. **Validator** -- integrity / consistency checks on the collected data
   (``_validate_*``).
3. **Writer** -- Word document rendering (``_add_*`` / ``_create_*``).

A full physical split of the 5,000+ line class into three modules would be
a large refactor with non-trivial regression risk, so for now we provide
thin *facade* classes that expose each role as a focused API surface. The
facades delegate to the underlying generator so behavior is unchanged,
but new call sites (and tests) can target only the responsibility they
care about.

Example:
    >>> from leader_report_generator import LeaderReportGenerator
    >>> from leader_report_components import (
    ...     TeamDataRepository, TeamDataValidator, LeaderReportWriter,
    ... )
    >>> gen = LeaderReportGenerator(...)
    >>> repo = TeamDataRepository(gen)
    >>> team_data = repo.collect_team_data(direct_reports, days=90)
    >>> problems = TeamDataValidator(gen).validate(team_data, days=90)
    >>> LeaderReportWriter(gen).write_summary_table(team_data, days=90)

The facades are intentionally minimal: they document and group the
existing methods rather than reimplementing them. When the class is
finally split into dedicated modules, these facades give us a stable
import path to preserve.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List, Optional

import pandas as pd

if TYPE_CHECKING:  # pragma: no cover - import cycle guard
    from leader_report_generator import LeaderReportGenerator


class _GeneratorFacadeBase:
    """Shared state for the three role facades."""

    __slots__ = ("_generator",)

    def __init__(self, generator: "LeaderReportGenerator") -> None:
        if generator is None:
            raise ValueError("generator must not be None")
        self._generator = generator

    @property
    def generator(self) -> "LeaderReportGenerator":
        """Return the underlying god-class instance.

        Exposed so callers can bail out to the full API until the split is
        complete.
        """
        return self._generator


class TeamDataRepository(_GeneratorFacadeBase):
    """Snowflake-facing read model for the manager report.

    Wraps the ``_fetch_*`` and data-collection methods on
    :class:`LeaderReportGenerator`. All methods are thin pass-throughs so
    they share caching, retry, and error-handling semantics with the
    parent class.
    """

    def fetch_action_plans(
        self,
        account_ids: List[str],
        days: int,
        owner_emails: Optional[List[str]] = None,
    ) -> pd.DataFrame:
        return self._generator._fetch_action_plans(
            account_ids, days, owner_emails=owner_emails
        )

    def fetch_adoption_barriers(
        self,
        account_ids: List[str],
        days: int,
        owner_emails: Optional[List[str]] = None,
    ) -> pd.DataFrame:
        return self._generator._fetch_adoption_barriers(
            account_ids, days, owner_emails=owner_emails
        )

    def fetch_customer_pulse(
        self,
        account_ids: List[str],
        days: int,
        owner_emails: Optional[List[str]] = None,
    ) -> pd.DataFrame:
        return self._generator._fetch_customer_pulse(
            account_ids, days, owner_emails=owner_emails
        )

    def fetch_success_priorities(
        self, customer_names: List[str], days: int
    ) -> pd.DataFrame:
        return self._generator._fetch_success_priorities(customer_names, days)

    def get_subscriptions_for_cssm(self, emails: List[str]) -> pd.DataFrame:
        return self._generator._get_subscriptions_for_cssm(emails)

    def collect_team_data(
        self,
        direct_reports: List[Dict[str, Any]],
        days: int,
    ) -> Dict[str, Dict[str, Any]]:
        return self._generator._collect_team_data(
            direct_reports=direct_reports, days=days
        )


class TeamDataValidator(_GeneratorFacadeBase):
    """Integrity / consistency checks on the collected team data.

    Wraps the ``_validate_*`` methods on :class:`LeaderReportGenerator`.
    The top-level :meth:`validate` runs the same pipeline as
    ``_validate_and_verify_data``.
    """

    def validate(
        self, team_data: Dict[str, Dict[str, Any]], days: int
    ) -> Dict[str, Any]:
        return self._generator._validate_and_verify_data(team_data, days)

    def validate_date_ranges(
        self, team_data: Dict[str, Dict[str, Any]], days: int
    ) -> Dict[str, Any]:
        return self._generator._validate_date_ranges(team_data, days)

    def validate_customer_data_consistency(
        self, team_data: Dict[str, Dict[str, Any]]
    ) -> Dict[str, Any]:
        return self._generator._validate_customer_data_consistency(team_data)

    def validate_tac_cases(
        self, team_data: Dict[str, Dict[str, Any]], days: int
    ) -> Dict[str, Any]:
        return self._generator._validate_tac_cases(team_data, days)


class LeaderReportWriter(_GeneratorFacadeBase):
    """Word-rendering role for :class:`LeaderReportGenerator`.

    Provides a narrow API over the ``_add_*`` / ``_create_*`` rendering
    methods. Useful for tests that want to exercise one section in
    isolation without spinning up the full report pipeline.
    """

    def write_title_page(
        self,
        manager_name: str,
        days: int,
        direct_reports: List[Dict[str, Any]],
    ) -> None:
        self._generator._create_title_page(manager_name, days, direct_reports)

    def write_summary_table(
        self, team_data: Dict[str, Dict[str, Any]], days: int
    ) -> None:
        self._generator._create_summary_table(team_data, days)

    def write_team_insights(
        self, team_data: Dict[str, Dict[str, Any]], days: int
    ) -> None:
        self._generator._add_team_insights_section(team_data, days)

    def write_adoptiq_summaries_per_person(
        self, team_data: Dict[str, Dict[str, Any]], days: int
    ) -> None:
        self._generator._create_adoptiq_summaries_per_person(team_data, days)

    def write_detailed_ab_list(
        self, team_data: Dict[str, Dict[str, Any]]
    ) -> None:
        self._generator._create_detailed_ab_list(team_data)

    def write_bems_section(
        self, team_data: Dict[str, Dict[str, Any]]
    ) -> None:
        self._generator._add_bems_escalation_section(team_data)

    def write_validation_section(self, validation_results: Dict[str, Any]) -> None:
        self._generator._add_validation_section(validation_results)


__all__ = [
    "TeamDataRepository",
    "TeamDataValidator",
    "LeaderReportWriter",
]
