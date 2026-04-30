#!/usr/bin/env python3
"""Update ADOPTIQ_VERSION and ADOPTIQ_BUILD in config.py and version_info.txt.

Called by build_pc.bat (Windows) and build_mac.sh (macOS).

Round 61 / Phase 2.B: default-resolution order is now
    env var -> existing value in config.py -> hard-coded floor.

Pre-Round-61 the script would unconditionally reset ADOPTIQ_BUILD to "1"
(and ADOPTIQ_VERSION to "1.0.3") whenever the env var was unset, which
silently downgraded the shipped build label every time `bash build_mac_dmg.sh`
was invoked without explicit `ADOPTIQ_VERSION=... ADOPTIQ_BUILD=... ...`
exports.  This caused the Round 59 mis-labeling incident (build_mac_dmg.sh
produced AdoptIQ-v1.0.4-build1.dmg even though config.py said build "32").
The footgun is now closed: when an env var is unset we preserve whatever
the source-of-truth config.py already carries, which is what the operator
intended in 99% of cases.

The hard-coded floor only fires when config.py is missing the line entirely
(e.g. corrupted file).  Operators who explicitly want to bump the version
still set the env var on the command line; that path is unchanged.
"""
import os
import re

# Round 61: hard-coded fallbacks ONLY apply when config.py is missing
# the line (corrupted / brand-new file).  Real default lives in config.py.
_VERSION_FLOOR = "1.0.3"
_BUILD_FLOOR = "1"

_VERSION_LINE_RE = re.compile(r'ADOPTIQ_VERSION\s*=\s*"([^"]*)"')
_BUILD_LINE_RE = re.compile(r'ADOPTIQ_BUILD\s*=\s*"([^"]*)"')


def _read_existing(path: str) -> tuple[str | None, str | None]:
    """Read the current ADOPTIQ_VERSION / ADOPTIQ_BUILD values from
    a config.py file.  Returns (version, build) tuple where each entry
    is the captured value, or None if the file is missing or the line
    cannot be parsed.

    Round 61 / Phase 2.B: this is the SSoT lookup that closes the
    update_version_pc.py env-var footgun.  Pinned by
    tests/test_round61_update_version_no_footgun.py.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return None, None
    v_match = _VERSION_LINE_RE.search(text)
    b_match = _BUILD_LINE_RE.search(text)
    return (
        v_match.group(1) if v_match else None,
        b_match.group(1) if b_match else None,
    )


def _resolve_target(env_key: str, existing: str | None, floor: str) -> str:
    """Round 61 / Phase 2.B: env var -> existing config.py value ->
    hard-coded floor.
    """
    env_val = os.environ.get(env_key)
    if env_val is not None and env_val.strip() != "":
        return env_val
    if existing is not None:
        return existing
    return floor


def main(config_path: str = "config.py", version_info_path: str = "version_info.txt") -> tuple[str, str]:
    existing_v, existing_b = _read_existing(config_path)
    v = _resolve_target("ADOPTIQ_VERSION", existing_v, _VERSION_FLOOR)
    b = _resolve_target("ADOPTIQ_BUILD", existing_b, _BUILD_FLOOR)

    with open(config_path, "r", encoding="utf-8") as f:
        c = f.read()
    c = _VERSION_LINE_RE.sub(f'ADOPTIQ_VERSION = "{v}"', c)
    c = _BUILD_LINE_RE.sub(f'ADOPTIQ_BUILD = "{b}"', c)
    with open(config_path, "w", encoding="utf-8") as f:
        f.write(c)

    # Write version info for installer (Programs and Features, display)
    with open(version_info_path, "w", encoding="utf-8") as f:
        f.write(f"{v}\n{b}\n")
    print(f"Updated {config_path} and {version_info_path}: v{v} build {b}")
    return v, b


if __name__ == "__main__":
    main()
