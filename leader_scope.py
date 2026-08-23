"""Server-side scope validation for Leader reports.

Round 142 keeps the scope contract independent from Flask and Snowflake so the
authorization boundary can be exercised with small, deterministic tests.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Dict, Iterable, List, Sequence, Tuple

import pandas as pd

from data_normalization import normalize_customer_name


VALID_LEADER_SCOPE_TYPES = frozenset({"team", "member", "customer"})
_EMAIL_COLUMN_CANDIDATES = (
    "CSSM_EMAIL",
    "PRIMARY_DSM_EMAIL",
    "ASSIGNEE_EMAIL",
    "OWNER_EMAIL",
)
_CUSTOMER_COLUMN_CANDIDATES = (
    "BU_NAME",
    "CUSTOMER_NAME",
    "CUSTOMER_NAME_C",
    "Customer Name",
    "customer_name",
)
_ACCOUNT_COLUMN_CANDIDATES = (
    "ACCOUNT_ID_C",
    "ACCOUNT__C",
    "ACCOUNT_ID",
    "Account ID",
)


class LeaderScopeValidationError(ValueError):
    """Raised when a requested Leader scope is not authorized or resolvable."""


@dataclass(frozen=True)
class LeaderScopeSelection:
    """Canonical, server-validated Leader scope."""

    manager_name: str
    scope_type: str
    scope_value: str = ""
    member_email: str = ""
    member_name: str = ""
    customer_name: str = ""

    @property
    def display_value(self) -> str:
        if self.scope_type == "member":
            if self.member_name and self.member_email:
                return f"{self.member_name} ({self.member_email})"
            return self.member_name or self.member_email
        if self.scope_type == "customer":
            if self.member_name:
                return f"{self.customer_name} ({self.member_name})"
            return self.customer_name
        return "Entire team"

    @property
    def fact_value(self) -> str:
        """Return the undecorated value used by canonical facts and scope gates.

        ``display_value`` may append a member name to help a person understand
        the selected customer.  That presentation label must never become the
        authorization key written to ``Scope_Value`` because exact customer
        reconciliation would then reject otherwise valid source records.
        """

        if self.scope_type == "customer":
            return self.customer_name or self.scope_value
        if self.scope_type == "member":
            return self.member_email or self.scope_value
        return self.scope_value or f"{self.manager_name} team"


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _fold(value: Any) -> str:
    return _clean(value).casefold()


def _customer_key(value: Any) -> str:
    """Exact customer key with display normalization plus case folding.

    Corporate suffixes remain significant here. A broader fuzzy key could
    collapse two legally distinct customers and is therefore inappropriate at
    this authorization boundary.
    """

    if not _clean(value):
        return ""
    return _fold(normalize_customer_name(value))


def _usable_roster_rows(
    team_roster: Iterable[Tuple[str, str, str]],
) -> List[Tuple[str, str, str]]:
    rows: List[Tuple[str, str, str]] = []
    for row in team_roster or ():
        try:
            manager, member_name, member_email = row[:3]
        except (TypeError, ValueError):
            continue
        manager = _clean(manager)
        member_name = _clean(member_name)
        member_email = _clean(member_email).lower()
        if manager and member_email:
            rows.append((manager, member_name, member_email))
    return rows


def manager_roster_members(
    team_roster: Iterable[Tuple[str, str, str]], manager_name: str
) -> List[Dict[str, str]]:
    """Return one deterministic member option per email for ``manager_name``."""

    manager_key = _fold(manager_name)
    members_by_email: Dict[str, Dict[str, str]] = {}
    for manager, member_name, member_email in _usable_roster_rows(team_roster):
        if _fold(manager) != manager_key:
            continue
        members_by_email.setdefault(
            member_email,
            {
                "name": member_name or member_email,
                "email": member_email,
                "manager": manager,
            },
        )
    return sorted(
        members_by_email.values(),
        key=lambda member: (member["name"].casefold(), member["email"]),
    )


def validate_leader_scope_request(
    manager_name: str,
    scope_type: str,
    scope_value: str,
    team_roster: Iterable[Tuple[str, str, str]],
    *,
    member_email: str = "",
) -> LeaderScopeSelection:
    """Validate and canonicalize a Leader scope using the trusted roster.

    A member selection is authorized here, before any background worker starts.
    A customer name is only syntactically required here; ownership is verified
    against the fetched subscription rows by :func:`filter_leader_subscriptions`.
    """

    requested_manager = _clean(manager_name)
    requested_type = _clean(scope_type).lower() or "team"
    requested_value = _clean(scope_value)

    if requested_type not in VALID_LEADER_SCOPE_TYPES:
        raise LeaderScopeValidationError(
            "Report scope must be team, member, or customer."
        )

    members = manager_roster_members(team_roster, requested_manager)
    if not members:
        # Backward compatibility: pre-R142 callers may queue a team report
        # with a test/custom manager and rely on the worker's existing
        # no-subscriptions validation. Member/customer scopes cannot use that
        # escape hatch because they carry an authorization boundary.
        if requested_type == "team" and requested_manager:
            return LeaderScopeSelection(
                manager_name=requested_manager,
                scope_type="team",
            )
        raise LeaderScopeValidationError(
            "The selected manager does not have any members in the Leader roster."
        )
    canonical_manager = members[0]["manager"]
    member_by_email = {member["email"].casefold(): member for member in members}

    if requested_type == "team":
        return LeaderScopeSelection(
            manager_name=canonical_manager,
            scope_type="team",
        )

    requested_member = requested_value if requested_type == "member" else _clean(member_email)
    canonical_member: Dict[str, str] | None = None
    if requested_member:
        canonical_member = member_by_email.get(requested_member.casefold())
        if canonical_member is None:
            raise LeaderScopeValidationError(
                "The selected team member does not report to the selected manager."
            )

    if requested_type == "member":
        if canonical_member is None:
            raise LeaderScopeValidationError("Select a team member for this report.")
        return LeaderScopeSelection(
            manager_name=canonical_manager,
            scope_type="member",
            scope_value=canonical_member["email"],
            member_email=canonical_member["email"],
            member_name=canonical_member["name"],
        )

    if not requested_value:
        raise LeaderScopeValidationError("Select a customer for this report.")
    return LeaderScopeSelection(
        manager_name=canonical_manager,
        scope_type="customer",
        scope_value=requested_value,
        member_email=canonical_member["email"] if canonical_member else "",
        member_name=canonical_member["name"] if canonical_member else "",
        customer_name=requested_value,
    )


def _find_column(df: pd.DataFrame, candidates: Sequence[str]) -> str:
    by_folded_name = {_fold(column): column for column in df.columns}
    for candidate in candidates:
        actual = by_folded_name.get(_fold(candidate))
        if actual is not None:
            return actual
    return ""


def subscription_member_emails(subscriptions_df: pd.DataFrame) -> List[str]:
    """Return normalized member emails represented by subscription rows."""

    if not isinstance(subscriptions_df, pd.DataFrame) or subscriptions_df.empty:
        return []
    email_column = _find_column(subscriptions_df, _EMAIL_COLUMN_CANDIDATES)
    if not email_column:
        return []
    values = {
        _clean(value).lower()
        for value in subscriptions_df[email_column].dropna().tolist()
        if _clean(value)
    }
    return sorted(values)


def filter_leader_subscriptions(
    subscriptions_df: pd.DataFrame,
    selection: LeaderScopeSelection,
) -> pd.DataFrame:
    """Fail-closed filter for a validated Leader scope.

    Customer ownership is deliberately checked only against subscription rows
    fetched for the selected manager (and optional member). A customer absent
    from that trusted frame is rejected instead of generating an empty or
    cross-portfolio report.
    """

    if not isinstance(subscriptions_df, pd.DataFrame):
        subscriptions_df = pd.DataFrame()
    scoped = subscriptions_df.copy()
    # Keep the manager-wide frame for ambiguity checks even when the requested
    # customer is narrowed to one member below.
    manager_authorized = scoped.copy()

    if selection.scope_type == "team":
        return scoped

    if selection.member_email:
        if scoped.empty:
            return scoped
        email_column = _find_column(scoped, _EMAIL_COLUMN_CANDIDATES)
        if not email_column:
            raise LeaderScopeValidationError(
                "Subscription data cannot verify the selected team member."
            )
        member_key = selection.member_email.casefold()
        member_mask = scoped[email_column].map(_fold).eq(member_key)
        scoped = scoped.loc[member_mask].copy()

    if selection.scope_type == "member":
        return scoped

    if scoped.empty:
        raise LeaderScopeValidationError(
            "The selected customer is not assigned to the selected manager or team member."
        )
    customer_column = _find_column(scoped, _CUSTOMER_COLUMN_CANDIDATES)
    if not customer_column:
        raise LeaderScopeValidationError(
            "Subscription data cannot verify the selected customer."
        )
    customer_key = _customer_key(selection.customer_name)
    if not customer_key:
        raise LeaderScopeValidationError("Select a valid customer for this report.")
    customer_mask = scoped[customer_column].map(_customer_key).eq(customer_key)
    matching_customer = scoped.loc[customer_mask].copy()
    if matching_customer.empty:
        raise LeaderScopeValidationError(
            "The selected customer is not assigned to the selected manager or team member."
        )

    # Round 142: activities are fetched by ACCOUNT_ID_C, not by subscription
    # row. If the same account id spans another BU/customer, an account-only
    # fetch cannot isolate the requested customer and could relabel another
    # customer's activity. Reject that ambiguous scope instead of emitting an
    # inaccurate individual report.
    account_column = _find_column(matching_customer, _ACCOUNT_COLUMN_CANDIDATES)
    manager_account_column = _find_column(manager_authorized, _ACCOUNT_COLUMN_CANDIDATES)
    manager_customer_column = _find_column(manager_authorized, _CUSTOMER_COLUMN_CANDIDATES)
    if account_column and manager_account_column and manager_customer_column:
        selected_accounts = {
            _fold(value)
            for value in matching_customer[account_column].dropna().tolist()
            if _fold(value) not in {"", "nan", "none", "null"}
        }
        if selected_accounts:
            manager_account_keys = manager_authorized[manager_account_column].map(_fold)
            manager_customer_keys = manager_authorized[manager_customer_column].map(_customer_key)
            ambiguous_mask = (
                manager_account_keys.isin(selected_accounts)
                & manager_customer_keys.ne("")
                & manager_customer_keys.ne(customer_key)
            )
            if bool(ambiguous_mask.any()):
                raise LeaderScopeValidationError(
                    "The selected customer shares an account identifier with "
                    "another customer, so an accurate isolated report cannot be generated."
                )

    return matching_customer


def canonicalize_leader_customer_selection(
    selection: LeaderScopeSelection,
    authorized_subscriptions: pd.DataFrame,
) -> LeaderScopeSelection:
    """Bind an accepted customer request to its authoritative source label.

    Authorization intentionally matches normalized customer keys, so harmless
    case/spacing variants may be accepted.  Canonical facts must nevertheless
    use the deterministic BU/customer label from the trusted subscription
    rows, not caller spelling, or identical scopes acquire different
    ``Scope_Value`` values across report families.
    """

    if selection.scope_type != "customer":
        return selection
    customer_column = _find_column(authorized_subscriptions, _CUSTOMER_COLUMN_CANDIDATES)
    if not customer_column or authorized_subscriptions.empty:
        raise LeaderScopeValidationError(
            "Subscription data cannot resolve the authoritative customer label."
        )
    requested_key = _customer_key(selection.customer_name or selection.scope_value)
    labels = sorted(
        {
            _clean(value)
            for value in authorized_subscriptions[customer_column].dropna().tolist()
            if _clean(value) and _customer_key(value) == requested_key
        },
        key=lambda value: (value.casefold(), value),
    )
    if not labels:
        raise LeaderScopeValidationError(
            "Subscription data cannot resolve the authoritative customer label."
        )
    authoritative_label = labels[0]
    return replace(
        selection,
        scope_value=authoritative_label,
        customer_name=authoritative_label,
    )


def leader_customer_options(subscriptions_df: pd.DataFrame) -> List[Dict[str, Any]]:
    """Build deterministic customer options from already-authorized rows."""

    if not isinstance(subscriptions_df, pd.DataFrame) or subscriptions_df.empty:
        return []
    customer_column = _find_column(subscriptions_df, _CUSTOMER_COLUMN_CANDIDATES)
    if not customer_column:
        return []

    options: Dict[str, Dict[str, Any]] = {}
    for value in subscriptions_df[customer_column].dropna().tolist():
        label = _clean(value)
        key = _customer_key(label)
        if not label or not key:
            continue
        if key not in options:
            options[key] = {"value": label, "label": label, "subscription_count": 0}
        options[key]["subscription_count"] += 1
        # Stable display when Snowflake returns spelling/case variants.
        if (label.casefold(), label) < (
            options[key]["label"].casefold(),
            options[key]["label"],
        ):
            options[key]["value"] = label
            options[key]["label"] = label

    return sorted(
        options.values(), key=lambda option: (option["label"].casefold(), option["label"])
    )
