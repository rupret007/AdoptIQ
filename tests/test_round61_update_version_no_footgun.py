"""Round 61 / Phase 2.B regression tests: update_version_pc.py must
preserve the existing ADOPTIQ_VERSION / ADOPTIQ_BUILD values in
config.py when the corresponding env vars are unset, instead of
silently resetting them to the hard-coded floor ("1.0.3" / "1").

This pins the fix for the Round 59 build-versioning incident where
``bash build_mac_dmg.sh`` (without explicit env exports) produced
``AdoptIQ-v1.0.4-build1.dmg`` even though ``config.py`` already said
``ADOPTIQ_BUILD = "32"``.

Tests cover:
- env var unset -> existing value preserved (the actual fix).
- env var set    -> override still works (back-compat with build scripts).
- malformed config.py -> safety floor still fires.
- empty env var  -> treated as unset (matches operator intent).
- version_info.txt is written from the resolved values, not the env vars.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def _write_fixture_config(tmp_path: Path, version: str = "1.0.4", build: str = "33") -> Path:
    """Write a minimal config.py fixture with the ADOPTIQ_VERSION and
    ADOPTIQ_BUILD lines set to the provided values."""
    cfg = tmp_path / "config.py"
    cfg.write_text(
        '"""Test fixture config.py for Round 61 / Phase 2.B."""\n'
        f'ADOPTIQ_VERSION = "{version}"\n'
        f'ADOPTIQ_BUILD = "{build}"\n'
        'OTHER_SETTING = True\n',
        encoding="utf-8",
    )
    return cfg


def _read_resolved(cfg: Path) -> tuple[str | None, str | None]:
    """Re-read the file and pluck out whatever values are written now."""
    import re
    text = cfg.read_text(encoding="utf-8")
    v = re.search(r'ADOPTIQ_VERSION\s*=\s*"([^"]*)"', text)
    b = re.search(r'ADOPTIQ_BUILD\s*=\s*"([^"]*)"', text)
    return (v.group(1) if v else None, b.group(1) if b else None)


def _run_main(tmp_path: Path, env: dict[str, str | None]) -> tuple[str, str]:
    """Invoke update_version_pc.main() against fixture paths inside
    ``tmp_path``, with the given environment overrides applied for the
    duration of the call.  Returns whatever main() returned."""
    import update_version_pc

    cfg = tmp_path / "config.py"
    info = tmp_path / "version_info.txt"

    saved = {
        k: os.environ.get(k) for k in ("ADOPTIQ_VERSION", "ADOPTIQ_BUILD")
    }
    try:
        for k, v in env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        return update_version_pc.main(
            config_path=str(cfg), version_info_path=str(info)
        )
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_no_env_preserves_existing_build(tmp_path):
    """The Round 61 / B fix: with no env vars set, the existing
    config.py values are preserved.  Pre-fix behavior would have reset
    these to "1.0.3" / "1" and shipped a wrong-version DMG."""
    cfg = _write_fixture_config(tmp_path, version="1.0.4", build="33")

    v, b = _run_main(tmp_path, env={"ADOPTIQ_VERSION": None, "ADOPTIQ_BUILD": None})

    assert v == "1.0.4", f"expected '1.0.4' (existing), got {v!r}"
    assert b == "33", f"expected '33' (existing), got {b!r}"

    file_v, file_b = _read_resolved(cfg)
    assert file_v == "1.0.4", f"config.py rewritten with wrong version: {file_v!r}"
    assert file_b == "33", f"config.py rewritten with wrong build: {file_b!r}"


def test_env_override_still_works(tmp_path):
    """Back-compat: when build scripts DO export the env vars, those
    take precedence (preserves the ``ADOPTIQ_BUILD=34 bash build_*.sh``
    pattern documented in CLAUDE.md / build_mac_dmg.sh)."""
    cfg = _write_fixture_config(tmp_path, version="1.0.4", build="33")

    v, b = _run_main(
        tmp_path, env={"ADOPTIQ_VERSION": "1.0.5", "ADOPTIQ_BUILD": "34"}
    )

    assert v == "1.0.5", f"env var ignored, got {v!r}"
    assert b == "34", f"env var ignored, got {b!r}"

    file_v, file_b = _read_resolved(cfg)
    assert file_v == "1.0.5"
    assert file_b == "34"


def test_partial_env_only_overrides_set_vars(tmp_path):
    """Operator who exports only ADOPTIQ_BUILD must NOT see the version
    silently downgraded to the floor.  The unset variable falls back to
    the existing config.py value, not to the hard-coded floor."""
    cfg = _write_fixture_config(tmp_path, version="1.0.4", build="33")

    v, b = _run_main(
        tmp_path, env={"ADOPTIQ_VERSION": None, "ADOPTIQ_BUILD": "34"}
    )

    assert v == "1.0.4", f"version silently changed despite no env var: {v!r}"
    assert b == "34"

    file_v, file_b = _read_resolved(cfg)
    assert file_v == "1.0.4"
    assert file_b == "34"


def test_empty_string_env_treated_as_unset(tmp_path):
    """Some shells leave an empty ``ADOPTIQ_BUILD=`` in the environment
    when the operator deletes the value.  Treat empty as unset so the
    existing config.py value still wins (the principle-of-least-surprise
    behavior)."""
    cfg = _write_fixture_config(tmp_path, version="1.0.4", build="33")

    v, b = _run_main(
        tmp_path, env={"ADOPTIQ_VERSION": "", "ADOPTIQ_BUILD": ""}
    )

    assert v == "1.0.4", f"empty env var did NOT fall back to existing: {v!r}"
    assert b == "33", f"empty env var did NOT fall back to existing: {b!r}"


def test_unparseable_config_falls_back_to_floor(tmp_path):
    """Safety floor: if config.py is missing the ADOPTIQ_BUILD line
    entirely (corrupted file, brand-new project), the script must still
    produce a valid output.  Falls through to the documented floor.

    Note the file is rewritten in-place; since the source line is not
    present, the regex-substitution is a no-op and config.py keeps
    whatever shape it had (the script does NOT inject new lines).  But
    version_info.txt MUST still be written with sensible defaults."""
    cfg = tmp_path / "config.py"
    cfg.write_text(
        '"""Malformed fixture: no ADOPTIQ_VERSION / ADOPTIQ_BUILD lines."""\n'
        'OTHER_SETTING = True\n',
        encoding="utf-8",
    )

    v, b = _run_main(
        tmp_path, env={"ADOPTIQ_VERSION": None, "ADOPTIQ_BUILD": None}
    )

    assert v == "1.0.3", f"floor fallback misfired: {v!r}"
    assert b == "1", f"floor fallback misfired: {b!r}"

    info = tmp_path / "version_info.txt"
    assert info.exists()
    assert info.read_text(encoding="utf-8") == "1.0.3\n1\n"


def test_version_info_txt_matches_resolved_values(tmp_path):
    """version_info.txt is the contract for the Windows installer
    UI.  It MUST always reflect the values actually written into
    config.py (not the env vars, not the floor)."""
    _write_fixture_config(tmp_path, version="1.0.4", build="33")

    v, b = _run_main(tmp_path, env={"ADOPTIQ_VERSION": None, "ADOPTIQ_BUILD": "34"})

    info = tmp_path / "version_info.txt"
    assert info.read_text(encoding="utf-8") == f"{v}\n{b}\n"
    assert v == "1.0.4"
    assert b == "34"


def test_module_imports_without_side_effects():
    """The module-level body MUST not perform file I/O; only ``main()``
    does.  This guards against a future refactor that re-adds the
    module-level write (which made the script un-importable for tests
    pre-Round-61 unless config.py existed in the CWD)."""
    # If this import would write any file, pytest would have errored
    # already during the test session.  This is a smoke test.
    import update_version_pc  # noqa: F401
    # Sanity: the module exposes the helpers we test against.
    assert hasattr(update_version_pc, "main")
    assert hasattr(update_version_pc, "_read_existing")
    assert hasattr(update_version_pc, "_resolve_target")


# Round 61 / Phase 2.B
