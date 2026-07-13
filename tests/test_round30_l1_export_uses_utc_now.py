"""Round 30 / L1 — export filename timestamp must use UTC.

``export_adrian_snowflake_records.py`` previously stamped the output
filename with naive ``datetime.now()``, which silently picked up the
host's local timezone.  That made the filename timestamp drift from
the ``cutoff_date`` (computed with ``datetime.now(UTC)``) any time
the host clock was not on UTC.

Round 30 / L1 aligns the filename stamp with the rest of the
codebase (every other AdoptIQ surface uses tz-aware UTC).
"""

from __future__ import annotations

import inspect

import export_adrian_snowflake_records


def test_round30_l1_export_filename_uses_utc_now() -> None:
    """Source pin: the filename stamp inside ``_build_output_path``
    MUST call ``datetime.now(UTC)`` (or equivalent), NOT the naive
    ``datetime.now()`` form."""
    src = inspect.getsource(export_adrian_snowflake_records)
    # Locate the build_output_path function body.
    idx = src.find("def _build_output_path")
    assert idx != -1, (
        "Round 30 / L1: _build_output_path helper must exist."
    )
    body_end = src.find("\ndef run_export", idx)
    assert body_end != -1
    body = src[idx:body_end]

    # Negative pin: the body must NOT contain a naive
    # ``datetime.now()`` call (no tz argument) on any non-comment line.
    # The literal ``datetime.now()`` (with no argument) is the
    # regression we are guarding against.  We strip comment lines so
    # the explanatory comment ("``datetime.now()`` without a tz...")
    # does not false-positive against the regex.
    code_lines = [
        ln for ln in body.splitlines()
        if not ln.lstrip().startswith("#")
    ]
    code_body = "\n".join(code_lines)
    assert "datetime.now()" not in code_body, (
        "Round 30 / L1: _build_output_path must not call naive "
        "datetime.now(); use datetime.now(UTC) or "
        "datetime.now(timezone.utc) so the filename timestamp aligns "
        "with the cutoff_date / data_retrieved_at contract."
    )
    # Positive pin: a tz-aware now() is present.
    assert (
        "datetime.now(UTC)" in body
        or "datetime.now(timezone.utc)" in body
    ), (
        "Round 30 / L1: _build_output_path must stamp the filename "
        "with a tz-aware UTC clock."
    )


def test_round30_l1_filename_stamp_aligns_with_cutoff_date() -> None:
    """Source pin: ``run_export`` already uses ``datetime.now(UTC)``
    for the cutoff_date.  The filename stamp must use the same clock
    so cross-export comparisons are reliable."""
    src = inspect.getsource(export_adrian_snowflake_records)
    # Locate run_export
    idx = src.find("def run_export")
    assert idx != -1
    # Pin that run_export uses datetime.now(UTC) for cutoff_date.
    snippet = src[idx:idx + 2000]
    assert "datetime.now(UTC)" in snippet, (
        "Round 30 / L1: run_export must continue to use "
        "datetime.now(UTC) for cutoff_date so the filename stamp "
        "is at parity."
    )
