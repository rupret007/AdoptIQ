"""Round 2 / Phase 2.1 regression test.

When a DataFrame written to an Excel sheet carries
``df.attrs['fetch_error']``, the workbook builder MUST render a
``Data_Unavailable`` row containing the reason / detail rather than a
silently empty sheet.  This is a source-level pin that the tristate
handling block in ``app_simple.py`` is still wired in.
"""
from __future__ import annotations

import pathlib
import re

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def _read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


def test_workbook_builder_inspects_fetch_error_attr() -> None:
    src = _read(REPO_ROOT / "app_simple.py")
    assert re.search(
        r"\.attrs\.get\(\s*['\"]fetch_error['\"]",
        src,
    ), (
        "Round 2 Phase 2.1: the Excel workbook builder must inspect "
        "df.attrs['fetch_error'] so a failed fetch surfaces as a "
        "Data_Unavailable row instead of a blank sheet."
    )


def test_workbook_builder_emits_data_unavailable_row() -> None:
    src = _read(REPO_ROOT / "app_simple.py")
    assert "Data_Unavailable" in src, (
        "Round 2 Phase 2.1: the Excel workbook builder must emit a "
        "Data_Unavailable row when df.attrs['fetch_error'] is set."
    )


def test_workbook_builder_uses_fetch_error_kind() -> None:
    """Phase 2.1 also propagates ``fetch_error_kind`` so the
    Data_Unavailable row distinguishes a load failure from a policy
    block from a transient timeout.  Lock that distinction in.
    """
    src = _read(REPO_ROOT / "app_simple.py")
    assert "fetch_error_kind" in src, (
        "Round 2 Phase 2.1: the Excel writer must propagate "
        "fetch_error_kind into the Data_Unavailable row so failure "
        "modes are distinguishable."
    )
