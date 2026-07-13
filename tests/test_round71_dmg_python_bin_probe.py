"""Round 71 / Phase 7 (#35) -- build_mac_dmg.sh PYTHON_BIN venv parity.

Pre-R71 ``build_mac_dmg.sh`` had THREE different probe names for the
Python interpreter:

* ``BAKE_PYTHON_BIN`` (used by the corpus bake step)
* ``PYTHON_FOR_VER`` (used by the post-build version-readback)
* implicit ``python3`` elsewhere

Each probe had its own ``.venv/bin/python`` check, with subtly
divergent fallbacks if anyone added env-var overrides later.

Round 71 / Phase 7 (#35) consolidates everything onto a single
``PYTHON_BIN`` probe, mirroring the contract in ``build_mac.sh``.
"""

from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent


def _read_dmg_script() -> str:
    return (REPO_ROOT / "build_mac_dmg.sh").read_text(encoding="utf-8", errors="replace")


def _read_mac_script() -> str:
    return (REPO_ROOT / "build_mac.sh").read_text(encoding="utf-8", errors="replace")


def test_round71_dmg_script_defines_python_bin_at_top() -> None:
    """The DMG script MUST define ``PYTHON_BIN`` once at the top
    (just like ``build_mac.sh``)."""
    src = _read_dmg_script()
    assert 'PYTHON_BIN="${PYTHON_BIN:-python3}"' in src, (
        "Round 71 / Phase 7 (#35): build_mac_dmg.sh must define "
        '``PYTHON_BIN="${PYTHON_BIN:-python3}"`` once at the top, '
        "matching build_mac.sh's pattern."
    )


def test_round71_dmg_script_probes_venv_python_once() -> None:
    """The venv probe MUST appear exactly once."""
    src = _read_dmg_script()
    venv_probes = src.count('PYTHON_BIN=".venv/bin/python"')
    assert venv_probes == 1, (
        f"Round 71 / Phase 7 (#35): build_mac_dmg.sh must probe "
        f'``.venv/bin/python`` ONCE; found {venv_probes} occurrences.'
    )


def test_round71_dmg_script_no_python_for_ver_local() -> None:
    """The pre-R71 ``PYTHON_FOR_VER`` local MUST be removed."""
    src = _read_dmg_script()
    # Comments referencing the old name are fine; assignments are not.
    forbidden_assign = 'PYTHON_FOR_VER="'
    assert forbidden_assign not in src, (
        "Round 71 / Phase 7 (#35): the pre-R71 ``PYTHON_FOR_VER=`` "
        "assignment must be removed (consolidated under PYTHON_BIN)."
    )


def test_round71_dmg_script_bake_python_bin_aliases_python_bin() -> None:
    """The legacy ``BAKE_PYTHON_BIN`` name (kept for diff readability)
    MUST be assigned from ``PYTHON_BIN`` (not from a separate probe)."""
    src = _read_dmg_script()
    assert 'BAKE_PYTHON_BIN="$PYTHON_BIN"' in src, (
        "Round 71 / Phase 7 (#35): BAKE_PYTHON_BIN must be aliased "
        "to $PYTHON_BIN so both reuse the same venv probe result."
    )


def test_round71_dmg_script_uses_python_bin_for_version_readback() -> None:
    """The post-build version readback MUST use ``$PYTHON_BIN``."""
    src = _read_dmg_script()
    assert "VERSION=\"${ADOPTIQ_VERSION:-$(\"$PYTHON_BIN\"" in src, (
        "Round 71 / Phase 7 (#35): version readback must use "
        "$PYTHON_BIN (consolidated probe), not a separate ``PYTHON_FOR_VER``."
    )
    assert "BUILD=\"${ADOPTIQ_BUILD:-$(\"$PYTHON_BIN\"" in src, (
        "Round 71 / Phase 7 (#35): build-number readback must use "
        "$PYTHON_BIN (consolidated probe)."
    )
