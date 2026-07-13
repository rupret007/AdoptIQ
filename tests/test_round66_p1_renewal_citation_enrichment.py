"""Round 66 / Pass 2 (B7) — Renewal citation enrichment tests.

Pre-R66 the Renewal Word document had fewer ``[Source: ...]`` chips
than Compact for equivalent KPI density because several renewal-only
labels (``Total Support Cases (90 days)``, ``Renewal Risk Level``,
``Total Success Priorities``, ``Service Incidents``) were not in
``KPI_ALIASES``. The canonical-KPI gate inside
``report_source_injector._paragraph_match_is_canonical_kpi`` then
treated these matches as non-canonical, so the inline injection path
silently fell through.

These tests pin the new aliases and verify the canonical resolver
picks them up.
"""
from __future__ import annotations

import pytest

from report_iteration_loop import KPI_ALIASES


def test_renewal_risk_level_is_aliased_to_risk_category() -> None:
    """Renewal subscription summary uses ``Renewal Risk Level: HIGH``."""
    assert "renewal risk level" in KPI_ALIASES["risk_category"], (
        "renewal risk level alias missing -- renewal subscription "
        "narrative will not be source-backed"
    )


def test_risk_level_is_aliased_to_risk_category() -> None:
    """Generic ``Risk Level: ...`` paragraph in renewal subscription path."""
    assert "risk level" in KPI_ALIASES["risk_category"], (
        "risk level alias missing"
    )


def test_total_support_cases_90_days_is_aliased() -> None:
    """Renewal Word ab_para emits ``Total Support Cases (90 days): 293``."""
    assert "total support cases 90 days" in KPI_ALIASES["support_cases"], (
        "Renewal-formatted support cases label not in KPI_ALIASES"
    )


def test_success_priorities_canonical_exists() -> None:
    """New canonical ``success_priorities`` MUST exist with renewal-relevant aliases."""
    assert "success_priorities" in KPI_ALIASES, (
        "success_priorities canonical missing from KPI_ALIASES"
    )
    aliases = KPI_ALIASES["success_priorities"]
    assert "success priorities" in aliases
    assert "total success priorities" in aliases


def test_incidents_canonical_exists() -> None:
    """New canonical ``incidents`` MUST exist with renewal narrative aliases."""
    assert "incidents" in KPI_ALIASES, (
        "incidents canonical missing from KPI_ALIASES"
    )
    aliases = KPI_ALIASES["incidents"]
    assert "service incidents" in aliases
    assert "total incidents" in aliases
    assert "high impact incidents" in aliases


def test_gate_canonical_kpi_label_resolves_renewal_labels() -> None:
    """report_source_injector._gate_canonical_kpi_label MUST resolve new renewal labels.

    This is the integration path: the injector imports KPI_ALIASES via
    ``_gate_kpi_aliases`` and resolves a label string through
    ``_gate_canonical_kpi_label``. The new aliases must canonicalize
    to a known canonical key so the inline-injection path fires.
    """
    # Reset the lazy-cache so the test sees the freshly-edited aliases
    import report_source_injector

    report_source_injector._GATE_KPI_ALIASES = None

    from report_source_injector import _gate_canonical_kpi_label

    assert _gate_canonical_kpi_label("Renewal Risk Level") == "risk_category"
    assert _gate_canonical_kpi_label("Total Support Cases (90 days)") == "support_cases"
    assert _gate_canonical_kpi_label("Total Success Priorities") == "success_priorities"
    assert _gate_canonical_kpi_label("Service Incidents") == "incidents"


def test_paragraph_kpi_regex_recognizes_renewal_labels() -> None:
    """The injector's canonical-KPI gate fires on renewal narrative paragraphs.

    End-to-end check: feed a paragraph that mirrors what
    ``_create_simple_renewal_report`` actually emits and confirm the
    canonical-match list is non-empty.
    """
    import report_source_injector

    report_source_injector._GATE_KPI_ALIASES = None

    from report_source_injector import (
        _PARAGRAPH_KPI_NUMERIC_RE,
        _paragraph_match_is_canonical_kpi,
    )

    # These are renewal narrative lines whose label/value pair matches
    # the colon-suffix regex AND whose label canonicalizes to one of
    # the new R66/B7 aliases. ``Renewal Risk Level: HIGH (7.3/10)``
    # is intentionally NOT in this list because its value (``HIGH``)
    # is non-numeric -- the regex skips it and the narrative-token
    # gate (which doesn't require canonical labels) carries that
    # paragraph instead. The new ``renewal risk level`` alias still
    # earns its keep on the consistency-gate side.
    renewal_lines = [
        "Total Support Cases (90 days): 293",
        "Total Success Priorities: 12",
        "Service Incidents: 4",
        "Total Incidents: 33",
        "High Impact Incidents: 5",
    ]
    for line in renewal_lines:
        matches = list(_PARAGRAPH_KPI_NUMERIC_RE.finditer(line))
        assert matches, f"regex did not match renewal narrative line: {line!r}"
        canonical = [m for m in matches if _paragraph_match_is_canonical_kpi(m)]
        assert canonical, (
            f"no canonical KPI match for renewal narrative line: {line!r} "
            "-- inline citation injection would silently fall through"
        )


def test_existing_compact_aliases_remain_intact() -> None:
    """Negative regression: B7 enrichment MUST NOT remove any pre-R66 aliases."""
    pre_r66_required = {
        ("total_customers", "total customers"),
        ("support_cases", "support cases"),
        ("support_cases", "total support cases"),
        ("adoption_barriers", "adoption barriers"),
        ("action_plans", "action plans"),
        ("action_plans", "total action plans"),
        ("customer_pulse", "customer pulse"),
        ("customer_pulse", "total customer pulse"),
        ("risk_score", "renewal risk score"),
        ("risk_score", "overall risk score"),
        ("risk_category", "risk category"),
        ("risk_category", "renewal risk category"),
    }
    for canonical, alias in pre_r66_required:
        assert alias in KPI_ALIASES.get(canonical, set()), (
            f"R66 enrichment regressed pre-R66 alias: ({canonical!r}, {alias!r})"
        )


def test_new_canonicals_do_not_conflict_with_existing() -> None:
    """B7 new canonicals MUST be disjoint from existing aliases (no double-mapping)."""
    new_aliases = (
        KPI_ALIASES["success_priorities"]
        | KPI_ALIASES["incidents"]
    )
    existing_pre_r66 = set()
    for canonical, alias_set in KPI_ALIASES.items():
        if canonical not in {"success_priorities", "incidents"}:
            existing_pre_r66.update(alias_set)
    overlap = new_aliases & existing_pre_r66
    assert not overlap, (
        f"R66 enrichment created ambiguous double-mapping: {overlap}"
    )


def test_total_support_cases_alias_does_not_clobber_short_form() -> None:
    """Adding ``total support cases 90 days`` MUST NOT remove the short forms."""
    assert "total support cases" in KPI_ALIASES["support_cases"]
    assert "support cases" in KPI_ALIASES["support_cases"]
    assert "tac cases" in KPI_ALIASES["support_cases"]


def test_renewal_risk_level_does_not_collide_with_risk_score() -> None:
    """Renewal report has BOTH 'Renewal Risk Score: 73/100' AND 'Renewal Risk Level: HIGH'.

    The two MUST canonicalize to DIFFERENT canonical keys so the
    cross-format gate does not collapse a numeric risk score onto a
    text risk band (or vice versa).
    """
    assert "renewal risk level" not in KPI_ALIASES["risk_score"]
    assert "renewal risk score" not in KPI_ALIASES["risk_category"]
    assert "renewal risk score" in KPI_ALIASES["risk_score"]
    assert "renewal risk level" in KPI_ALIASES["risk_category"]
