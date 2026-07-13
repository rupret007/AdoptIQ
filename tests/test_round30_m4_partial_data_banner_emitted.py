"""Round 30 / M4 — Partial Data banner must be emitted when an
optional source fetch fails.

Optional CSConsole sources (action_plans, customer_pulse,
success_priorities, adoption_barriers) and the optional ARR feed
were stored in ``error_details`` but the strict ``is_valid`` gate
ignored them.  Callers that only checked ``is_valid`` treated empty
optional frames as "no data" rather than "fetch failed".

Round 30 / M4 introduces ``has_optional_fetch_errors`` and
``get_optional_fetch_errors`` helpers, then wires each report
writer (leader, executive, compact) to promote optional fetch
errors into ``partial_data_warnings`` so the banner renders.
"""

from __future__ import annotations

import inspect

import data_source_validator


def test_round30_m4_helper_exists() -> None:
    """Source pin: ``has_optional_fetch_errors`` and
    ``get_optional_fetch_errors`` must be public-name symbols."""
    assert hasattr(data_source_validator, 'has_optional_fetch_errors'), (
        "Round 30 / M4: data_source_validator must export "
        "has_optional_fetch_errors."
    )
    assert hasattr(data_source_validator, 'get_optional_fetch_errors'), (
        "Round 30 / M4: data_source_validator must export "
        "get_optional_fetch_errors."
    )


def test_round30_m4_has_optional_fetch_errors_none_returns_false() -> None:
    """Empty / None / wrong-type inputs return False (don't crash the
    caller)."""
    f = data_source_validator.has_optional_fetch_errors
    assert f(None) is False
    assert f({}) is False
    assert f("not-a-dict") is False  # type: ignore[arg-type]
    assert f([]) is False  # type: ignore[arg-type]


def test_round30_m4_has_optional_fetch_errors_detects_csconsole() -> None:
    """An optional CSConsole error key triggers True."""
    f = data_source_validator.has_optional_fetch_errors
    assert f({'csconsole_action_plans': 'connection refused'}) is True
    assert f({'csconsole_customer_pulse': 'timeout'}) is True
    assert f({'csconsole_success_priorities': 'auth error'}) is True
    assert f({'csconsole_adoption_barriers': 'rate limited'}) is True


def test_round30_m4_has_optional_fetch_errors_detects_arr() -> None:
    """An optional ARR error key triggers True."""
    f = data_source_validator.has_optional_fetch_errors
    assert f({'arr_data': 'fetch failed'}) is True


def test_round30_m4_has_optional_fetch_errors_ignores_required() -> None:
    """Errors against REQUIRED-source keys (csone, ab_data, team_subs)
    do NOT trigger the optional helper -- those are caught by
    ``is_valid``."""
    f = data_source_validator.has_optional_fetch_errors
    # Hypothetical required-source keys that shouldn't trigger the
    # optional helper.
    assert f({'csone_data': 'fail'}) is False
    assert f({'team_subs_df': 'fail'}) is False
    assert f({'ab_data': 'fail'}) is False


def test_round30_m4_get_optional_fetch_errors_returns_only_optional() -> None:
    """``get_optional_fetch_errors`` MUST filter to optional keys
    only (never leak required-source error strings into the banner)."""
    g = data_source_validator.get_optional_fetch_errors
    out = g({
        'csconsole_action_plans': 'timeout',
        'csone_data': 'should-not-appear',
        'arr_data': 'fetch failed',
    })
    assert set(out) == {'csconsole_action_plans', 'arr_data'}, (
        "Round 30 / M4: get_optional_fetch_errors must filter to "
        "optional keys only and never leak required-source errors."
    )


def test_round30_m4_app_simple_promotes_errors_to_warnings() -> None:
    """Source pin: ``app_simple.py`` MUST call
    ``get_optional_fetch_errors`` and promote the entries into
    ``partial_data_warnings`` for each report writer."""
    import app_simple
    src = inspect.getsource(app_simple)
    assert "get_optional_fetch_errors" in src, (
        "Round 30 / M4: app_simple must call get_optional_fetch_errors "
        "to promote optional errors into partial_data_warnings."
    )
    assert "partial_data_warnings" in src, (
        "Round 30 / M4: app_simple must thread partial_data_warnings "
        "through the report writers."
    )


def test_round30_m4_leader_renders_partial_data_warning_banner() -> None:
    """Source pin: leader must render the canonical 'Partial Data
    Warning' heading on the title page (and after the post-TAC
    regeneration) when the warning list is non-empty."""
    import leader_report_generator
    src = inspect.getsource(leader_report_generator)
    assert "Partial Data Warning" in src, (
        "Round 30 / M4: leader must render the 'Partial Data Warning' "
        "heading when partial_data_warnings is non-empty."
    )
    # The leader must accept partial_data_warnings as a kwarg.
    assert "partial_data_warnings" in src, (
        "Round 30 / M4: leader must accept partial_data_warnings."
    )


def test_round30_m4_compact_renders_partial_data_warning_banner() -> None:
    """Source pin: compact must render the partial-data banner."""
    import compact_report_formatter
    src = inspect.getsource(compact_report_formatter)
    assert "partial_data_warnings" in src, (
        "Round 30 / M4: compact must accept and render "
        "partial_data_warnings."
    )
