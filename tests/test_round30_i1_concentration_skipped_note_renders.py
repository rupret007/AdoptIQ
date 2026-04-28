"""Round 30 / I1 — concentration "skipped" note must surface in
leader, executive, and compact reports when the upstream portfolio
mixes currencies.

The backend already populated ``insights['concentration']`` with
``not_comparable_across_currencies: True`` and a ``note`` describing
why percent / HHI / top-N analysis was skipped, but no renderer was
reading the flag.  Round 30 / I1 wires the helper
``concentration_note_text`` into the leader title-page advisory and
the executive ARR Exposure section so the skipped note is visible.
"""

from __future__ import annotations

import inspect

import adoptiq_backend


def test_round30_i1_concentration_note_text_helper_exists() -> None:
    """Source pin: ``concentration_note_text`` must be a public-name
    helper in ``adoptiq_backend`` so renderers across modules can
    call it without re-implementing the multi-currency branch."""
    assert hasattr(adoptiq_backend, 'concentration_note_text'), (
        "Round 30 / I1: adoptiq_backend must expose "
        "concentration_note_text(concentration_dict) so leader / "
        "executive / compact renderers can surface the multi-currency "
        "skipped note from a single source of truth."
    )
    assert hasattr(adoptiq_backend, 'CONCENTRATION_MULTICURRENCY_NOTE'), (
        "Round 30 / I1: the canonical fallback note text must live in "
        "adoptiq_backend.CONCENTRATION_MULTICURRENCY_NOTE so a future "
        "phrasing change flows through to every renderer."
    )


def test_round30_i1_helper_returns_none_for_single_currency() -> None:
    """Helper contract: single-currency or empty concentration => None."""
    assert adoptiq_backend.concentration_note_text(None) is None
    assert adoptiq_backend.concentration_note_text({}) is None
    assert adoptiq_backend.concentration_note_text(
        {'top5_pct': 60, 'hhi': 1500},
    ) is None


def test_round30_i1_helper_returns_backend_note_for_multicurrency() -> None:
    """Helper contract: multi-currency concentration with an explicit
    note => return the backend-supplied note verbatim."""
    backend_note = (
        "Skipped percent/HHI/top-N: portfolio mixes USD/EUR/GBP."
    )
    out = adoptiq_backend.concentration_note_text({
        'not_comparable_across_currencies': True,
        'note': backend_note,
    })
    assert out == backend_note, (
        "Round 30 / I1: when the backend supplies a note, the helper "
        "must return it verbatim so a phrasing change in "
        "derive_portfolio_intelligence flows through to every "
        "renderer with no further code change."
    )


def test_round30_i1_helper_falls_back_to_canonical_note() -> None:
    """Helper contract: when the flag is set but the note is missing
    or empty, fall back to the canonical note constant."""
    out = adoptiq_backend.concentration_note_text({
        'not_comparable_across_currencies': True,
    })
    assert out == adoptiq_backend.CONCENTRATION_MULTICURRENCY_NOTE
    out2 = adoptiq_backend.concentration_note_text({
        'not_comparable_across_currencies': True,
        'note': '',
    })
    assert out2 == adoptiq_backend.CONCENTRATION_MULTICURRENCY_NOTE


def test_round30_i1_executive_renders_concentration_note() -> None:
    """Source pin: executive ARR Exposure section must read
    ``arr_impact['concentration_note']`` and surface it adjacent to
    the multi-currency disclosure."""
    import executive_intelligence_formatter
    src = inspect.getsource(executive_intelligence_formatter)
    assert "concentration_note" in src, (
        "Round 30 / I1: executive ARR Exposure must surface the "
        "concentration_note alongside the multi-currency disclosure."
    )


def test_round30_i1_leader_renders_concentration_note() -> None:
    """Source pin: leader title-page advisory must surface the
    concentration_note as a second italic paragraph."""
    import leader_report_generator
    src = inspect.getsource(leader_report_generator)
    assert "concentration_note" in src, (
        "Round 30 / I1: leader title-page advisory must surface the "
        "concentration_note as a second italic paragraph."
    )
