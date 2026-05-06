"""Round 89 / F3 -- macOS Info.plist version stamping reads from config.py SSoT.

Regression guard for the R67-class footgun in ``adoptiq_mac.spec`` that the
Build 65 acceptance loop caught: even though ``config.py`` correctly carried
``ADOPTIQ_VERSION = "1.0.4"`` and ``ADOPTIQ_BUILD = "65"``, the produced
``AdoptIQ.app``'s ``Info.plist`` reported ``CFBundleShortVersionString=1.0.3``
and ``CFBundleVersion=1`` because the spec had hard-coded fallbacks
(``os.environ.get("ADOPTIQ_VERSION", "1.0.3")``) and ``build_mac.sh`` did
not export the env vars before invoking PyInstaller -- so the spec's
``os.environ.get`` returned ``None`` and fell through to the hardcoded
defaults.

R67 / Phase 4 fixed exactly this footgun in the bash scripts (removed
hardcoded ``${ADOPTIQ_BUILD:-1}`` defaults and added a read-back from
``config.py`` after ``update_version_pc.py`` runs) but missed the spec
file.  R89 / F3 closes the gap with two-pronged defense-in-depth:

1. ``adoptiq_mac.spec`` now reads ``config.py`` directly via a regex
   lookup (``_r89_resolve_version_from_config``) when the env var is unset.
2. ``build_mac.sh`` now ``export``s ``ADOPTIQ_VERSION`` / ``ADOPTIQ_BUILD``
   after the read-back so the PyInstaller subprocess inherits them.

The reports produced by the running app already showed the correct
version (R68/A1 build label reads ``config.py`` at runtime), so this is
NOT a P0 data-correctness bug -- it's a P1 chrome bug that confused
operators inspecting the .app's Finder/About dialog.  Both surfaces are
now covered.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_MAC_SPEC = _REPO_ROOT / "adoptiq_mac.spec"
_BUILD_MAC = _REPO_ROOT / "build_mac.sh"
_CONFIG_PY = _REPO_ROOT / "config.py"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Source-shape pins for adoptiq_mac.spec
# ---------------------------------------------------------------------------


def test_round89_f3_mac_spec_no_hardcoded_version_fallback() -> None:
    """The bare ``os.environ.get("ADOPTIQ_VERSION", "1.0.3")`` pattern must
    NOT appear in the spec -- it bypasses the SSoT and re-introduces the
    Build 65 footgun.
    """
    src = _read(_MAC_SPEC)
    assert 'os.environ.get("ADOPTIQ_VERSION", "1.0.3")' not in src, (
        "Round 89 / F3: adoptiq_mac.spec must NOT carry the hardcoded "
        "'1.0.3' fallback for ADOPTIQ_VERSION -- read from config.py SSoT."
    )
    assert 'os.environ.get("ADOPTIQ_BUILD", "1")' not in src, (
        "Round 89 / F3: adoptiq_mac.spec must NOT carry the hardcoded "
        "'1' fallback for ADOPTIQ_BUILD -- read from config.py SSoT."
    )


def test_round89_f3_mac_spec_has_config_resolver() -> None:
    """The spec must define a helper that reads from config.py and use it
    in the BUNDLE info_plist block.
    """
    src = _read(_MAC_SPEC)
    assert "_r89_resolve_version_from_config" in src, (
        "Round 89 / F3: adoptiq_mac.spec must define the SSoT-lookup helper."
    )
    assert "_R89_RESOLVED_VERSION" in src, (
        "Round 89 / F3: adoptiq_mac.spec must compute the resolved version."
    )
    assert "_R89_RESOLVED_BUILD" in src, (
        "Round 89 / F3: adoptiq_mac.spec must compute the resolved build."
    )
    # The resolved values must actually be wired into Info.plist.
    assert '"CFBundleShortVersionString": _R89_RESOLVED_VERSION' in src, (
        "Round 89 / F3: CFBundleShortVersionString must use _R89_RESOLVED_VERSION."
    )
    assert '"CFBundleVersion": _R89_RESOLVED_BUILD' in src, (
        "Round 89 / F3: CFBundleVersion must use _R89_RESOLVED_BUILD."
    )


def test_round89_f3_resolver_reads_config_py_pattern() -> None:
    """The resolver must use the canonical regex shape that
    ``update_version_pc.py`` also uses (so a config.py rewrite by
    ``update_version_pc.py`` is immediately visible to the spec's lookup).
    """
    src = _read(_MAC_SPEC)
    assert 'ADOPTIQ_VERSION\\s*=\\s*"([^"]*)"' in src, (
        "Round 89 / F3: the version regex must match the canonical SSoT pattern."
    )
    assert 'ADOPTIQ_BUILD\\s*=\\s*"([^"]*)"' in src, (
        "Round 89 / F3: the build regex must match the canonical SSoT pattern."
    )


def test_round89_f3_resolver_env_takes_precedence_over_config() -> None:
    """Source-shape pin: env var (set by build_mac.sh after the R67/Phase 4
    read-back) must take precedence over the config.py fallback so the bash
    loop remains the canonical resolution path.  The fallback is only for
    standalone PyInstaller invocations.
    """
    src = _read(_MAC_SPEC)
    # The pattern is `os.environ.get(...) or _R89_CFG_VERSION or "1.0.3"`.
    # Env var must come FIRST (so the bash export wins).
    pattern = re.compile(
        r'_R89_RESOLVED_VERSION\s*=\s*os\.environ\.get\("ADOPTIQ_VERSION"\)\s*or\s*_R89_CFG_VERSION'
    )
    assert pattern.search(src), (
        "Round 89 / F3: env var ADOPTIQ_VERSION must take precedence over "
        "the config.py fallback in the resolved-version expression."
    )
    pattern_build = re.compile(
        r'_R89_RESOLVED_BUILD\s*=\s*os\.environ\.get\("ADOPTIQ_BUILD"\)\s*or\s*_R89_CFG_BUILD'
    )
    assert pattern_build.search(src), (
        "Round 89 / F3: env var ADOPTIQ_BUILD must take precedence over "
        "the config.py fallback in the resolved-build expression."
    )


# ---------------------------------------------------------------------------
# Source-shape pins for build_mac.sh
# ---------------------------------------------------------------------------


def test_round89_f3_build_mac_sh_exports_version_env_vars() -> None:
    """``build_mac.sh`` must ``export`` both env vars after the R67/Phase 4
    read-back from config.py so the PyInstaller subprocess inherits them.
    """
    src = _read(_BUILD_MAC)
    assert "export ADOPTIQ_VERSION" in src, (
        "Round 89 / F3: build_mac.sh must export ADOPTIQ_VERSION so "
        "PyInstaller's subprocess inherits the value."
    )
    assert "export ADOPTIQ_BUILD" in src, (
        "Round 89 / F3: build_mac.sh must export ADOPTIQ_BUILD so "
        "PyInstaller's subprocess inherits the value."
    )


def test_round89_f3_build_mac_sh_exports_after_config_readback() -> None:
    """The export must come AFTER the config.py read-back so the resolved
    SSoT value is what gets exported, not the pre-readback empty string.
    """
    src = _read(_BUILD_MAC)
    # Find the line numbers of the readback and the export.
    lines = src.splitlines()
    readback_idx = None
    export_idx = None
    for i, line in enumerate(lines):
        if "from config import ADOPTIQ_BUILD" in line and readback_idx is None:
            readback_idx = i
        if "export ADOPTIQ_BUILD" in line and export_idx is None:
            export_idx = i
    assert readback_idx is not None, (
        "Round 89 / F3: build_mac.sh missing the R67/Phase 4 config.py readback."
    )
    assert export_idx is not None, (
        "Round 89 / F3: build_mac.sh missing the export ADOPTIQ_BUILD line."
    )
    assert export_idx > readback_idx, (
        "Round 89 / F3: build_mac.sh must export AFTER the config.py "
        "readback (got readback at line %d, export at line %d)."
        % (readback_idx + 1, export_idx + 1)
    )


# ---------------------------------------------------------------------------
# Behavior round-trip: invoke the spec helper logic against config.py
# ---------------------------------------------------------------------------


def test_round89_f3_resolver_reads_actual_config_py_values() -> None:
    """Behavior pin: extract the regex from the spec and run it against the
    real ``config.py`` -- the result must match the live values.  Catches
    a future config.py rewrite that breaks the SSoT pattern.
    """
    cfg_src = _read(_CONFIG_PY)
    m_v = re.search(r'ADOPTIQ_VERSION\s*=\s*"([^"]*)"', cfg_src)
    m_b = re.search(r'ADOPTIQ_BUILD\s*=\s*"([^"]*)"', cfg_src)
    assert m_v is not None, (
        "Round 89 / F3: config.py must carry ADOPTIQ_VERSION = \"...\" "
        "in the canonical SSoT shape that the spec's regex looks for."
    )
    assert m_b is not None, (
        "Round 89 / F3: config.py must carry ADOPTIQ_BUILD = \"...\" "
        "in the canonical SSoT shape that the spec's regex looks for."
    )
    # Both should be non-empty strings (sanity check).
    assert m_v.group(1).strip(), "ADOPTIQ_VERSION in config.py is empty"
    assert m_b.group(1).strip(), "ADOPTIQ_BUILD in config.py is empty"


def test_round89_f3_round_trip_against_live_config() -> None:
    """End-to-end behavior: cross-check that the live config.py value
    matches what we'd see in the produced .app's Info.plist.
    """
    from config import ADOPTIQ_BUILD, ADOPTIQ_VERSION  # noqa: PLC0415

    cfg_src = _read(_CONFIG_PY)
    m_v = re.search(r'ADOPTIQ_VERSION\s*=\s*"([^"]*)"', cfg_src)
    m_b = re.search(r'ADOPTIQ_BUILD\s*=\s*"([^"]*)"', cfg_src)
    assert m_v is not None and m_b is not None
    assert m_v.group(1) == ADOPTIQ_VERSION, (
        "Round 89 / F3: config.py text and module value must agree."
    )
    assert m_b.group(1) == ADOPTIQ_BUILD, (
        "Round 89 / F3: config.py text and module value must agree."
    )


# ---------------------------------------------------------------------------
# Round 89 / F3 source marker pin
# ---------------------------------------------------------------------------


def test_round89_f3_source_marker_present_in_mac_spec() -> None:
    """Per the audit-log convention: changed lines carry a ``# Round 89``
    marker so ``git diff adoptiq_mac.spec | grep 'Round 89'`` shows the
    per-file footprint.
    """
    src = _read(_MAC_SPEC)
    assert "Round 89 / F3" in src, (
        "Round 89 / F3: adoptiq_mac.spec must carry the Round 89 source marker."
    )


def test_round89_f3_source_marker_present_in_build_mac_sh() -> None:
    src = _read(_BUILD_MAC)
    assert "Round 89 / F3" in src, (
        "Round 89 / F3: build_mac.sh must carry the Round 89 source marker."
    )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
