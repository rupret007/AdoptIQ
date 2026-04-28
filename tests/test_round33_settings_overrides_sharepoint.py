"""Round 33 / Build8: ``settings.json`` ``sharepoint_folder_url`` must
override ``Config.ADOPTIQ_SHAREPOINT_FOLDER_URL`` at app boot.

Build7 left users without a way to set the SharePoint URL outside of
env vars or hand-editing config.py.  Build8 adds a startup hook in
``app_simple.py`` that mirrors the existing
``corpus_knowledge_enabled`` bridge.  This test asserts the bridge
exists in source -- a regression that deletes the ``.startswith``-
guarded ``isinstance(_r33_sp_url, str)`` block would silently strand
the per-user override.
"""
from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent


def test_app_simple_bridges_sharepoint_url_at_startup():
    body = (REPO_ROOT / "app_simple.py").read_text(encoding="utf-8")
    # The bridge lives inside the existing _r32_persisted try/except.
    assert "_r33_sp_url" in body, (
        "Round 33 startup bridge missing -- settings.json "
        "sharepoint_folder_url must override "
        "Config.ADOPTIQ_SHAREPOINT_FOLDER_URL at boot."
    )
    assert 'Config.ADOPTIQ_SHAREPOINT_FOLDER_URL = _r33_sp_url' in body
    # The bridge must guard against non-string and empty values to
    # preserve env-var fallthrough semantics.
    assert "isinstance(_r33_sp_url, str)" in body


def test_config_default_sharepoint_url_is_empty_string():
    """The hardcoded personal Cisco URL was removed in build8 -- the
    config default must now be empty so installs without a configured
    URL show the analyze-page "Not configured" prompt instead of
    failing to index a folder they can't access."""
    from config import Config
    import os

    if not os.environ.get("ADOPTIQ_SHAREPOINT_FOLDER_URL"):
        assert Config.ADOPTIQ_SHAREPOINT_FOLDER_URL == "", (
            "Config.ADOPTIQ_SHAREPOINT_FOLDER_URL must default to '' "
            "when the env var is unset.  Hardcoding a tenant-specific "
            "URL leaks operator identity and breaks indexing for every "
            "other tenant."
        )


def test_config_no_personal_cisco_default_in_source():
    """Belt-and-suspenders: even if ``Config`` is mutated at runtime,
    the source must not contain the legacy hardcoded personal URL as
    a live default value.  A documentation reference inside a comment
    ("we used to ship ``jestory_cisco_com``...") is acceptable; what
    we forbid is an executable string literal that becomes a default.
    """
    import re
    body = (REPO_ROOT / "config.py").read_text(encoding="utf-8")
    # Match an `https://...jestory_cisco_com...` URL in either '...'
    # or "..." quotes anywhere outside a comment-only line.  The
    # simpler version: scan line-by-line, ignore lines that begin
    # with optional whitespace + ``#``.
    offenders: list[str] = []
    for raw in body.splitlines():
        stripped = raw.strip()
        if stripped.startswith("#"):
            continue
        if "jestory_cisco_com" not in stripped:
            continue
        # Reject only when it appears inside a quoted string literal.
        if re.search(r"['\"][^'\"]*jestory_cisco_com[^'\"]*['\"]", stripped):
            offenders.append(raw)
    assert not offenders, (
        "Personal Cisco SharePoint URL must not appear as a quoted "
        "string literal in config.py -- use settings.json or the "
        "ADOPTIQ_SHAREPOINT_FOLDER_URL env var instead. Offending "
        "lines:\n" + "\n".join(offenders)
    )
