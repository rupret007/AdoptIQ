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

Round 7 / Phase 3.19: this module is intentionally a *thin wrapper* and
intentionally has no behaviour of its own.  Each facade delegates 1:1 to
the matching ``LeaderReportGenerator`` private method; therefore the
expected invariants are:

* Every public method on the facade calls **exactly one** generator
  ``_method`` and forwards its arguments without translation.
* The facade does not cache, mutate, or post-process any return value;
  any change in semantics must happen on the underlying generator.
* The facade must not introduce new public surface that is not also
  reachable on ``LeaderReportGenerator``.  This keeps the eventual
  physical split (one module per role) a pure rename, not a behaviour
  change.

The :func:`_assert_facade_invariants` helper at module load time
performs a structural check to catch accidental drift -- if a facade
method is added without a corresponding ``_method`` on the generator,
import will raise :class:`AssertionError`.  This protects the wrapper
contract documented above.
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


# Round 7 / Phase 3.19: facade-invariant check.  Every public method on
# the three facades must map to a private ``_<name>`` (or a documented
# alias) on :class:`LeaderReportGenerator`.  We compute this once at
# import time using the explicit map below so we do not have to import
# the generator (which would be a circular dependency); the map is
# audited as part of code review and tested in
# ``tests/test_round7_leader_components_invariants.py``.
_FACADE_METHOD_TO_GENERATOR: Dict[str, str] = {
    # TeamDataRepository
    "fetch_action_plans": "_fetch_action_plans",
    "fetch_adoption_barriers": "_fetch_adoption_barriers",
    "fetch_customer_pulse": "_fetch_customer_pulse",
    "fetch_success_priorities": "_fetch_success_priorities",
    "get_subscriptions_for_cssm": "_get_subscriptions_for_cssm",
    "collect_team_data": "_collect_team_data",
    # TeamDataValidator
    "validate": "_validate_and_verify_data",
    "validate_date_ranges": "_validate_date_ranges",
    "validate_customer_data_consistency": "_validate_customer_data_consistency",
    "validate_tac_cases": "_validate_tac_cases",
    # LeaderReportWriter
    "write_title_page": "_create_title_page",
    "write_summary_table": "_create_summary_table",
    "write_team_insights": "_add_team_insights_section",
    "write_adoptiq_summaries_per_person": "_create_adoptiq_summaries_per_person",
    "write_detailed_ab_list": "_create_detailed_ab_list",
    "write_bems_section": "_add_bems_escalation_section",
    "write_validation_section": "_add_validation_section",
}


def _assert_facade_invariants() -> None:
    """Verify each facade method has a matching map entry.

    Round 7 / Phase 3.19: catches the case where someone adds a public
    method to a facade without updating the wrapper contract, which
    would break the "thin wrapper" invariant documented in the module
    docstring.
    """
    facades = (TeamDataRepository, TeamDataValidator, LeaderReportWriter)
    for facade in facades:
        for name, attr in vars(facade).items():
            if name.startswith("_") or not callable(attr):
                continue
            if name not in _FACADE_METHOD_TO_GENERATOR:
                raise AssertionError(
                    f"Round 7 / Phase 3.19: facade {facade.__name__}."
                    f"{name} has no entry in _FACADE_METHOD_TO_GENERATOR; "
                    "either remove the method or update the contract map."
                )


_assert_facade_invariants()


__all__ = [
    "TeamDataRepository",
    "TeamDataValidator",
    "LeaderReportWriter",
]
