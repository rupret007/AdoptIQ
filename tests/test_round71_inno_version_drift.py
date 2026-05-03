"""Round 71 / Phase 6 (#32) -- Inno Setup MyAppVersion sourced from env.

Pre-R71 ``adoptiq_setup.iss`` hardcoded ``#define MyAppVersion "1.0"``.
The Inno Setup compiler baked that literal into ``AdoptIQ-Setup.exe``,
so the installer's version metadata cosmetically advertised "1.0"
while every other surface (``config.py``, ``version_info.txt``, the
Mac DMG label) showed the real version (e.g. 1.0.4).

Round 71 / Phase 6 (#32) sources ``MyAppVersion`` from the
``ADOPTIQ_VERSION`` environment variable (set by ``build_pc.bat``
from ``config.py``) with a sensible fallback for ad-hoc ``iscc``
runs.
"""

from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent


def _read_iss() -> str:
    return (REPO_ROOT / "adoptiq_setup.iss").read_text(encoding="utf-8", errors="replace")


def test_round71_inno_uses_env_var_for_version() -> None:
    """The .iss script MUST source ``MyAppVersion`` from the
    ``ADOPTIQ_VERSION`` env var via ``GetEnv``."""
    src = _read_iss()
    assert "#define MyAppVersion GetEnv('ADOPTIQ_VERSION')" in src, (
        "Round 71 / Phase 6 (#32): adoptiq_setup.iss must source "
        "MyAppVersion from the ADOPTIQ_VERSION env var via GetEnv()."
    )


def test_round71_inno_provides_fallback_for_missing_env() -> None:
    """If the env var is empty, the .iss MUST fall back to a
    sensible default (currently ``"1.0.4"`` matching ``config.py``).
    The fallback prevents an empty version metadata field in the
    installer when an operator runs ``iscc`` ad-hoc."""
    src = _read_iss()
    assert '#if MyAppVersion == ""' in src, (
        "Round 71 / Phase 6 (#32): .iss must include a fallback "
        "branch for empty ADOPTIQ_VERSION env."
    )
    assert '#define MyAppVersion "1.0.4"' in src, (
        "Round 71 / Phase 6 (#32): the fallback default version must "
        "match the current config.py default (1.0.4)."
    )


def test_round71_inno_no_hardcoded_old_version() -> None:
    """The pre-R71 ``"1.0"`` literal MUST NOT appear as the active
    MyAppVersion definition."""
    src = _read_iss()
    forbidden = '#define MyAppVersion "1.0"\n'
    assert forbidden not in src, (
        f"Round 71 / Phase 6 (#32): the pre-R71 literal {forbidden!r} "
        f"must be removed from adoptiq_setup.iss."
    )


def test_round71_inno_marker_present() -> None:
    """The .iss MUST carry a Round 71 marker comment."""
    src = _read_iss()
    assert "Round 71 / Phase 6 (#32)" in src, (
        "adoptiq_setup.iss must carry a ``Round 71 / Phase 6 (#32)`` "
        "marker so the audit grep finds it."
    )
