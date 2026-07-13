"""Round 4 / Phase 2.1 regression test.

The client-side redirect regex in ``analyze.html`` must accept the
same character set / length as the server's ``_ANALYSIS_ID_RE``
(``[A-Za-z0-9._-]{1,200}``).  Pre-Round-4 the JS regex was
``^[a-zA-Z0-9_]+$`` and rejected legitimate IDs like
``sub_ACME-123_2026-04-24`` with hyphens / dots.
"""
from __future__ import annotations

import pathlib
import re

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_analyze_redirect_regex_allows_dot_dash_underscore() -> None:
    html = (REPO_ROOT / "templates" / "analyze.html").read_text(encoding="utf-8")
    # The server regex is _ANALYSIS_ID_RE = ^[A-Za-z0-9._-]{1,200}$.
    # The client regex must accept dots ('.') and dashes ('-') in the
    # ID portion of /progress/<id>.
    # We accept either bracket-class form: [A-Za-z0-9._-] or
    # [a-zA-Z0-9._-] (case is irrelevant for the bracket).
    assert re.search(
        r"\[A-Za-z0-9\._\-\]|\[a-zA-Z0-9\._\-\]",
        html,
    ) or re.search(
        r"\[A-Za-z0-9\.\-_\]|\[a-zA-Z0-9\.\-_\]",
        html,
    ), (
        "Round 4 Phase 2.1: analyze.html final-redirect regex must "
        "accept '.', '-', '_', and alphanumerics (matching the server "
        "_ANALYSIS_ID_RE = [A-Za-z0-9._-]{1,200})."
    )
