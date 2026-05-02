"""Round 67 / Build 41 follow-on regression tests: build_mac.sh,
build_mac_dmg.sh, and build_pc.bat must NOT default the env vars to
"1.0.3" / "1.0.4" / "1" — that defeats the Round 61 SSoT fix in
update_version_pc.py.

Pre-Round-67 the build scripts wrapped ``${ADOPTIQ_BUILD:-1}`` and
explicitly exported the resulting "1" to update_version_pc.py, which
overrode whatever value config.py was carrying.  Round 67 follow-on
fix: leave the env vars empty when the operator does not explicitly
set them, so update_version_pc.py reads from config.py (the SSoT).

Tests cover:
- build_mac.sh does NOT carry a ``"${ADOPTIQ_VERSION:-1.0.4}"`` or
  ``"${ADOPTIQ_BUILD:-1}"`` literal (the pre-R67 footgun).
- build_mac.sh DOES read the resolved values back from config.py
  after update_version_pc.py runs.
- build_mac_dmg.sh DOES read the resolved values from config.py
  when the env vars are unset.
- build_pc.bat does NOT carry the ``set ADOPTIQ_VERSION=1.0.3`` /
  ``set ADOPTIQ_BUILD=1`` hard-coded defaults.
- build_pc.bat DOES read back the resolved values from config.py.

Round 67 / Phase 4 (build41_dmg).
"""
from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _read(name: str) -> str:
    return (PROJECT_ROOT / name).read_text(encoding="utf-8")


def test_build_mac_sh_does_not_default_version_to_1_0_4_literal() -> None:
    """The pre-R67 footgun was ``ADOPTIQ_VERSION="${ADOPTIQ_VERSION:-1.0.4}"``
    — the literal default would override config.py's actual value."""
    body = _read("build_mac.sh")
    assert 'ADOPTIQ_VERSION:-1.0.4' not in body, (
        "build_mac.sh still carries the pre-R67 hard-coded version default; "
        "this defeats the Round 61 SSoT fix in update_version_pc.py."
    )


def test_build_mac_sh_does_not_default_build_to_1_literal() -> None:
    """The pre-R67 footgun was ``ADOPTIQ_BUILD="${ADOPTIQ_BUILD:-1}"`` —
    the literal "1" default produced ``AdoptIQ-v1.0.4-build1.dmg`` even
    when config.py said build "41"."""
    body = _read("build_mac.sh")
    assert 'ADOPTIQ_BUILD:-1' not in body, (
        "build_mac.sh still carries the pre-R67 hard-coded build default; "
        "this is the exact Round 61 footgun that produced the mis-labeled "
        "build1 DMG even when config.py said build 41."
    )


def test_build_mac_sh_reads_resolved_version_from_config_py() -> None:
    """After update_version_pc.py runs, the script MUST re-read the
    SSoT values from config.py so the DMG name carries the correct
    label regardless of whether the operator passed env vars."""
    body = _read("build_mac.sh")
    assert "from config import ADOPTIQ_VERSION" in body, (
        "build_mac.sh must read the resolved version from config.py "
        "after update_version_pc.py runs."
    )
    assert "from config import ADOPTIQ_BUILD" in body, (
        "build_mac.sh must read the resolved build from config.py "
        "after update_version_pc.py runs."
    )


def test_build_mac_dmg_sh_does_not_default_version_to_1_0_4_literal() -> None:
    body = _read("build_mac_dmg.sh")
    assert 'ADOPTIQ_VERSION:-1.0.4' not in body, (
        "build_mac_dmg.sh still carries the pre-R67 hard-coded version default."
    )


def test_build_mac_dmg_sh_does_not_default_build_to_1_literal() -> None:
    body = _read("build_mac_dmg.sh")
    assert 'ADOPTIQ_BUILD:-1' not in body, (
        "build_mac_dmg.sh still carries the pre-R67 hard-coded build default."
    )


def test_build_mac_dmg_sh_falls_back_to_config_py_when_env_unset() -> None:
    """build_mac_dmg.sh must use a parameter-expansion fallback that
    invokes ``python -c 'from config import ...; print(...)'`` when
    the env vars are unset, so the DMG name carries the SSoT value."""
    body = _read("build_mac_dmg.sh")
    assert "from config import ADOPTIQ_VERSION" in body, (
        "build_mac_dmg.sh must fall back to reading the SSoT version from "
        "config.py when ADOPTIQ_VERSION is not exported by the operator."
    )
    assert "from config import ADOPTIQ_BUILD" in body, (
        "build_mac_dmg.sh must fall back to reading the SSoT build from "
        "config.py when ADOPTIQ_BUILD is not exported by the operator."
    )


def test_build_pc_bat_does_not_hardcode_version_default() -> None:
    """The pre-R67 build_pc.bat had ``set ADOPTIQ_VERSION=1.0.3`` /
    ``set ADOPTIQ_BUILD=1`` near the top, which clobbered any value
    config.py was carrying."""
    body = _read("build_pc.bat")
    # The bad line was literally `set ADOPTIQ_VERSION=1.0.3` (with the
    # version baked in).  Negative-match that exact pattern.
    assert "set ADOPTIQ_VERSION=1.0.3" not in body, (
        "build_pc.bat still hard-codes ADOPTIQ_VERSION=1.0.3 — defeats Round 61 SSoT."
    )
    assert "set ADOPTIQ_VERSION=1.0.4" not in body, (
        "build_pc.bat still hard-codes ADOPTIQ_VERSION=1.0.4 — defeats Round 61 SSoT."
    )


def test_build_pc_bat_does_not_hardcode_build_default() -> None:
    body = _read("build_pc.bat")
    # The bad line was literally `set ADOPTIQ_BUILD=1`.  We need to be
    # precise here because legit `set ADOPTIQ_BUILD=%ADOPTIQ_BUILD%`
    # passthroughs are fine.
    lines = body.splitlines()
    bad_lines = [
        ln for ln in lines
        if ln.strip().startswith("set ADOPTIQ_BUILD=")
        and "%" not in ln  # %VAR% passthroughs are allowed
        and ln.strip() != "set ADOPTIQ_BUILD="  # the new R67 empty assignment is fine
    ]
    assert not bad_lines, (
        f"build_pc.bat still carries hard-coded build default lines: {bad_lines}; "
        "this is the Round 61 footgun."
    )


def test_build_pc_bat_reads_resolved_values_from_config_py() -> None:
    """After update_version_pc.py runs, build_pc.bat must read back the
    resolved version from config.py so subsequent steps (DMG name,
    OUTBOX copy, build_info.txt) see the SSoT values."""
    body = _read("build_pc.bat")
    assert "from config import ADOPTIQ_VERSION" in body, (
        "build_pc.bat must read the resolved version from config.py."
    )
    assert "from config import ADOPTIQ_BUILD" in body, (
        "build_pc.bat must read the resolved build from config.py."
    )


def test_config_py_carries_build_41() -> None:
    """Sanity check: config.py is on Build 41 at this commit; if a future
    Cursor session resets it (the symptom of the Round 67 footgun
    re-appearing), this test fires the alarm immediately."""
    cfg = _read("config.py")
    import re
    m = re.search(r'ADOPTIQ_BUILD\s*=\s*"([^"]*)"', cfg)
    assert m is not None, "config.py is missing ADOPTIQ_BUILD line entirely."
    # Allow any value >= 41 so future builds (Build 42, 43, ...) don't
    # break this test, but pre-R67 regressions to "1" / "32" / etc. fire.
    build_int = int(m.group(1))
    assert build_int >= 41, (
        f"config.py says ADOPTIQ_BUILD={m.group(1)} but Round 67 / Build 41 "
        "established the floor at 41.  A Cursor edit may have silently "
        "downgraded the build label."
    )
