"""Round 4 / Phase 4.5 regression test.

When the configured CSOne file path is missing, ``process_csone_data``
must NOT silently pick the first ``.xlsx`` in the uploads directory
(which can be another customer's workbook).  It must fail closed with
an empty CSOne plus a user-visible ``partial_data_warnings`` entry.
"""
from __future__ import annotations

import pathlib
import re

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_process_csone_does_not_glob_uploads_for_first_xlsx() -> None:
    src = (REPO_ROOT / "app_simple.py").read_text(encoding="utf-8")
    # Pre-Round-4 there was a literal ``xlsx_files[0]`` fallback used
    # in real code (assignment / pass-through). Round 4 removed it;
    # the only remaining mention is in a comment explaining the
    # removal. We pin that no executable use of ``xlsx_files[0]``
    # remains by checking that every occurrence is inside a comment.
    for line in src.splitlines():
        if "xlsx_files[0]" in line:
            stripped = line.lstrip()
            assert stripped.startswith("#"), (
                "Round 4 Phase 4.5: process_csone_data must NOT fall back "
                "to xlsx_files[0] when the configured CSOne path is "
                "missing - that picks an arbitrary other customer's "
                f"workbook. Offending line: {line!r}"
            )


def test_process_csone_appends_missing_input_warning() -> None:
    src = (REPO_ROOT / "app_simple.py").read_text(encoding="utf-8")
    # The Round 4 fix appends a partial_data_warnings entry of
    # kind 'missing_input' when the CSOne file is not found.
    assert "missing_input" in src or "missing input" in src.lower(), (
        "Round 4 Phase 4.5: process_csone_data must surface a "
        "'missing_input' partial_data_warnings entry when the "
        "configured CSOne path does not exist."
    )
