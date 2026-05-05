"""Round 77 / Build 53: hardcoded default LLM flipped from
``gpt-5-nano`` to ``gemini-3.1-flash-lite``.

Both options are CircuIT free-tier (15 RPM, 120K peak tokens/min,
50M monthly input, 5M completion, $0 quarterly).  Operator testing
showed flash-lite delivered materially lower per-customer LLM latency
on the comprehensive report's per-customer storyboard loop while
preserving the R66/B11 + R67/B8 grounding-pass rate.

The R69 (Build 43) operator-flippable seam and R73 (UX-3) two-option
dropdown contracts are unchanged -- this round only flips which option
is the implicit default when no override is set.  ``gpt-5-nano`` remains
a one-click toggle in every preferences surface.

Pinned source-shape contracts:

* ``model_resolver._HARDCODED_DEFAULT == "gemini-3.1-flash-lite"``
* ``Config.CIRCUIT_CONFIG`` env-fallbacks all default to the same string.
* Both ``<select data-r69-model-input>`` dropdowns on /preferences list
  ``gemini-3.1-flash-lite`` as the FIRST <option> (so it becomes the
  selected default when the persisted value is empty).
* Admin dashboard inline pickers list flash-lite first too.
* ``gpt-5-nano`` still passes ``adoptiq_settings.is_valid_model_name``
  AND still round-trips through ``adoptiq_settings.set`` so the
  back-toggle is preserved.
* Empty-string in settings.json still falls through to the new default.
"""

# Round 77 / Build 53

from __future__ import annotations

import importlib
import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent

R77_DEFAULT = "gemini-3.1-flash-lite"
R77_LEGACY_DEFAULT = "gpt-5-nano"  # still allow-listed; just no longer the default


# ---------------------------------------------------------------------------
# Section 1: model_resolver hardcoded default
# ---------------------------------------------------------------------------


def test_round77_resolver_hardcoded_default_is_flash_lite():
    """Source-shape pin: ``model_resolver._HARDCODED_DEFAULT`` MUST be
    ``"gemini-3.1-flash-lite"`` so a fresh install with no settings
    file and no env vars resolves to flash-lite."""
    import model_resolver as _mr
    importlib.reload(_mr)
    assert _mr._HARDCODED_DEFAULT == R77_DEFAULT, (
        f"Round 77 / Build 53: _HARDCODED_DEFAULT must be {R77_DEFAULT!r}, "
        f"got {_mr._HARDCODED_DEFAULT!r}.  This is the canonical fallback for "
        f"both get_active_ask_ai_model() and get_active_report_model() and "
        f"MUST track Config.CIRCUIT_CONFIG's env-fallback string byte-for-byte."
    )


def test_round77_default_is_gemini_flash_lite_for_ask_ai(monkeypatch, tmp_path):
    """``get_active_ask_ai_model()`` returns flash-lite when no
    settings.json override AND no env vars are set."""
    monkeypatch.delenv("CIRCUIT_MODEL_NAME", raising=False)
    monkeypatch.delenv("CIRCUIT_MODEL_NAME_ASK_AI", raising=False)
    monkeypatch.delenv("CIRCUIT_MODEL_NAME_REPORT", raising=False)
    import adoptiq_settings as _s
    monkeypatch.setattr(_s, "_app_support_dir", lambda: tmp_path)
    import model_resolver as _mr
    importlib.reload(_mr)
    assert _mr.get_active_ask_ai_model() == R77_DEFAULT


def test_round77_default_is_gemini_flash_lite_for_reports(monkeypatch, tmp_path):
    """Mirror test for the report resolver -- same fallback chain,
    same R77 default."""
    monkeypatch.delenv("CIRCUIT_MODEL_NAME", raising=False)
    monkeypatch.delenv("CIRCUIT_MODEL_NAME_ASK_AI", raising=False)
    monkeypatch.delenv("CIRCUIT_MODEL_NAME_REPORT", raising=False)
    import adoptiq_settings as _s
    monkeypatch.setattr(_s, "_app_support_dir", lambda: tmp_path)
    import model_resolver as _mr
    importlib.reload(_mr)
    assert _mr.get_active_report_model() == R77_DEFAULT


def test_round77_settings_json_empty_falls_through_to_new_default(monkeypatch, tmp_path):
    """An explicit empty-string override in settings.json is the
    canonical "unset / clear override" sentinel and MUST fall through
    to the env layer and finally the R77 hardcoded default."""
    monkeypatch.delenv("CIRCUIT_MODEL_NAME", raising=False)
    monkeypatch.delenv("CIRCUIT_MODEL_NAME_ASK_AI", raising=False)
    monkeypatch.delenv("CIRCUIT_MODEL_NAME_REPORT", raising=False)
    import adoptiq_settings as _s
    monkeypatch.setattr(_s, "_app_support_dir", lambda: tmp_path)
    _s.save_settings({"ask_ai_model_name": "", "report_model_name": ""})
    import model_resolver as _mr
    importlib.reload(_mr)
    assert _mr.get_active_ask_ai_model() == R77_DEFAULT
    assert _mr.get_active_report_model() == R77_DEFAULT


# ---------------------------------------------------------------------------
# Section 2: gpt-5-nano back-toggle still works
# ---------------------------------------------------------------------------


def test_round77_gpt_5_nano_still_passes_validation():
    """The legacy default ``gpt-5-nano`` MUST remain allow-list-clean
    so the operator can toggle back via the UI dropdown without a
    rebuild."""
    import adoptiq_settings as _s
    assert _s.is_valid_model_name(R77_LEGACY_DEFAULT) is True


def test_round77_gpt_5_nano_round_trips_through_settings(monkeypatch, tmp_path):
    """Save+load of ``gpt-5-nano`` MUST be byte-identical so the
    back-toggle preserves the operator's selection across restarts."""
    import adoptiq_settings as _s
    monkeypatch.setattr(_s, "_app_support_dir", lambda: tmp_path)
    _s.save_settings({"report_model_name": R77_LEGACY_DEFAULT})
    on_disk = _s.load_settings()
    assert on_disk.get("report_model_name") == R77_LEGACY_DEFAULT


def test_round77_gpt_5_nano_resolver_returns_legacy_when_persisted(monkeypatch, tmp_path):
    """When the operator persists ``gpt-5-nano`` via the UI, the
    resolver MUST return that value (not the new R77 hardcoded
    default) -- the override is what makes the UI flip honest."""
    monkeypatch.delenv("CIRCUIT_MODEL_NAME", raising=False)
    monkeypatch.delenv("CIRCUIT_MODEL_NAME_REPORT", raising=False)
    import adoptiq_settings as _s
    monkeypatch.setattr(_s, "_app_support_dir", lambda: tmp_path)
    _s.save_settings({"report_model_name": R77_LEGACY_DEFAULT})
    import model_resolver as _mr
    importlib.reload(_mr)
    assert _mr.get_active_report_model() == R77_LEGACY_DEFAULT


# ---------------------------------------------------------------------------
# Section 3: config.py CIRCUIT_CONFIG drift guard
# ---------------------------------------------------------------------------


def test_round77_config_circuit_config_default_matches_resolver(monkeypatch):
    """Drift guard: ``Config.CIRCUIT_CONFIG`` env-fallback strings MUST
    track ``model_resolver._HARDCODED_DEFAULT``.  A config/resolver
    split would let ``CircuitChatClient`` instantiate with one model
    while ``model_resolver.get_active_*_model()`` returned another,
    producing silent inconsistencies in per-call diagnostic records."""
    monkeypatch.delenv("CIRCUIT_MODEL_NAME", raising=False)
    monkeypatch.delenv("CIRCUIT_MODEL_NAME_ASK_AI", raising=False)
    monkeypatch.delenv("CIRCUIT_MODEL_NAME_REPORT", raising=False)
    import config as _c
    importlib.reload(_c)
    cfg = _c.Config.CIRCUIT_CONFIG
    import model_resolver as _mr
    importlib.reload(_mr)
    assert cfg["model_name"] == _mr._HARDCODED_DEFAULT == R77_DEFAULT
    assert cfg["model_name_ask_ai"] == R77_DEFAULT
    assert cfg["model_name_report"] == R77_DEFAULT


# ---------------------------------------------------------------------------
# Section 4: UI dropdown order
# ---------------------------------------------------------------------------


def test_round77_dropdown_lists_flash_lite_first_on_preferences():
    """Both <select data-r69-model-input> blocks on /preferences MUST
    list ``gemini-3.1-flash-lite`` as the FIRST <option> so it becomes
    the implicit selected default when the persisted value is empty."""
    src = (PROJECT_ROOT / "templates" / "preferences.html").read_text(encoding="utf-8")
    # Find each <select data-r69-model-input> block and inspect its
    # first <option value="..."> child.
    select_iter = re.finditer(
        r"<select[^>]*data-r69-model-input[^>]*>(.*?)</select>",
        src,
        flags=re.DOTALL,
    )
    matches = list(select_iter)
    assert len(matches) == 2, (
        f"Round 77: expected 2 <select data-r69-model-input> blocks on "
        f"/preferences (Ask AI + Report cards); found {len(matches)}"
    )
    for i, m in enumerate(matches):
        body = m.group(1)
        first_option = re.search(r'<option[^>]*value="([^"]+)"', body)
        assert first_option is not None, (
            f"Round 77: <select> block {i} on /preferences carries no "
            f"<option value=...>; cannot verify first-option contract"
        )
        assert first_option.group(1) == R77_DEFAULT, (
            f"Round 77: <select> block {i} on /preferences lists "
            f"{first_option.group(1)!r} as the first <option>; "
            f"expected {R77_DEFAULT!r} so it becomes the implicit "
            f"default when the persisted value is empty"
        )


def test_round77_dropdown_lists_flash_lite_first_on_admin():
    """Same first-option contract for the admin dashboard inline
    pickers (R69 / Build 43 + R73 / UX-3 mirror surface)."""
    src = (PROJECT_ROOT / "enhanced_admin_dashboard_v2.py").read_text(encoding="utf-8")
    select_iter = re.finditer(
        r"<select[^>]*data-r69-admin-input[^>]*>(.*?)</select>",
        src,
        flags=re.DOTALL,
    )
    matches = list(select_iter)
    assert len(matches) == 2, (
        f"Round 77: expected 2 <select data-r69-admin-input> blocks on "
        f"the admin dashboard; found {len(matches)}"
    )
    for i, m in enumerate(matches):
        body = m.group(1)
        first_option = re.search(r'<option[^>]*value="([^"]+)"', body)
        assert first_option is not None
        assert first_option.group(1) == R77_DEFAULT, (
            f"Round 77: admin <select> block {i} lists "
            f"{first_option.group(1)!r} as the first <option>; "
            f"expected {R77_DEFAULT!r}"
        )


def test_round77_dropdown_first_option_carries_selected_attribute():
    """Defense-in-depth: the first <option> on each /preferences card
    MUST carry the ``selected`` attribute so that, for a fresh install
    where the R69 JS overlay has not yet run (or has failed to load),
    the browser still defaults the visible state to flash-lite."""
    src = (PROJECT_ROOT / "templates" / "preferences.html").read_text(encoding="utf-8")
    # Pattern: <option value="gemini-3.1-flash-lite" selected>...
    pattern = re.compile(
        r'<option\s+value="gemini-3\.1-flash-lite"\s+selected\s*>',
    )
    matches = pattern.findall(src)
    assert len(matches) >= 2, (
        f"Round 77: expected >=2 <option value='gemini-3.1-flash-lite' "
        f"selected> entries on /preferences (one per card); "
        f"found {len(matches)}"
    )


# ---------------------------------------------------------------------------
# Section 5: descriptive copy on /preferences
# ---------------------------------------------------------------------------


def test_round77_preferences_descriptive_copy_names_new_default():
    """The "Defaults to ..." paragraph on each card MUST name
    ``gemini-3.1-flash-lite`` so the operator's mental model matches
    the actual fallback.

    Tolerant of whitespace (including newlines) between "Defaults to"
    and the ``<code>`` tag so a Jinja template line-break doesn't
    break the assertion."""
    src = (PROJECT_ROOT / "templates" / "preferences.html").read_text(encoding="utf-8")
    pattern = re.compile(
        r"Defaults\s+to\s*<code>gemini-3\.1-flash-lite</code>",
    )
    occurrences = len(pattern.findall(src))
    assert occurrences >= 2, (
        f"Round 77: descriptive 'Defaults to gemini-3.1-flash-lite' copy "
        f"appears {occurrences} time(s) on /preferences; expected at "
        f"least 2 (Ask AI + Report cards)"
    )


def test_round77_preferences_descriptive_copy_does_not_claim_nano_is_default():
    """A stale 'Defaults to gpt-5-nano' string would mislead the
    operator about which model their fresh install is using.

    Tolerant of whitespace between 'Defaults to' and the <code> tag
    to mirror the positive-control test's pattern."""
    src = (PROJECT_ROOT / "templates" / "preferences.html").read_text(encoding="utf-8")
    pattern = re.compile(r"Defaults\s+to\s*<code>gpt-5-nano</code>")
    assert pattern.search(src) is None, (
        "Round 77: stale 'Defaults to gpt-5-nano' copy on /preferences "
        "-- the descriptive paragraph MUST name the new R77 default"
    )


def test_round77_admin_descriptive_copy_names_new_default():
    """Mirror test for the admin dashboard.  The ``CIRCUIT_MODEL_NAME``
    fallback citation MUST name flash-lite so a power user inspecting
    the admin tile sees an honest default."""
    src = (PROJECT_ROOT / "enhanced_admin_dashboard_v2.py").read_text(encoding="utf-8")
    # The admin tile's "default <code>...</code>" descriptor is the
    # canonical surface; assert it carries the R77 default.
    assert "<code>gemini-3.1-flash-lite</code>" in src, (
        "Round 77: admin descriptive copy does not name "
        "gemini-3.1-flash-lite as the default"
    )


# ---------------------------------------------------------------------------
# Section 6: docstring narrative on model_resolver
# ---------------------------------------------------------------------------


def test_round77_resolver_docstring_names_new_default():
    """The module docstring's "Hardcoded ..." reference MUST name the
    R77 default so a future maintainer reading the file sees a
    consistent story."""
    src = (PROJECT_ROOT / "model_resolver.py").read_text(encoding="utf-8")
    assert "gemini-3.1-flash-lite" in src.split('"""')[1], (
        "Round 77: model_resolver.py module docstring does not name "
        "gemini-3.1-flash-lite -- the docstring narrative is stale"
    )


# ---------------------------------------------------------------------------
# Section 7: build metadata
# ---------------------------------------------------------------------------


def test_round77_build_number_at_least_53():
    """Round 77 ships as Build 53.  Pin the floor so a future round
    that forgets to bump the build cannot regress past R77."""
    import config as _c
    importlib.reload(_c)
    build_str = _c.ADOPTIQ_BUILD
    try:
        build_int = int(build_str)
    except (TypeError, ValueError):
        pytest.fail(
            f"Round 77: ADOPTIQ_BUILD is non-numeric {build_str!r}; "
            "cannot enforce floor"
        )
    assert build_int >= 53, (
        f"Round 77: ADOPTIQ_BUILD={build_int} is below the R77 floor of 53"
    )
