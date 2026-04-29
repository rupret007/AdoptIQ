"""Round 45 / Phase 3 regression: ``start_compact_analysis`` MUST track
explicit-upload vs autodiscovery provenance separately so the worker
can branch the Round 2 / Phase 4.3 fail-loud-on-explicit-upload
contract correctly.

The 2026-04-28 Build-20 audit found that the compact endpoint silently
folded autodiscovery hits and explicit uploads into a single
``csone_file`` slot, then handed the worker no way to distinguish the
two.  Combined with the validator's default
``required_sources=['snowflake','team_subscriptions','adoption_barriers','csone']``
for compact, that meant every compact run without an uploaded CSOne
file aborted at validation -- including all OneDrive-autodiscovery-only
runs, which is the documented happy path for the demo workflow.

This test pins the SOURCE-SHAPE contract so the regression cannot
silently recur:

1. ``start_compact_analysis`` writes ``csone_file_was_uploaded``,
   ``csone_file_path``, ``csone_file_autopicked``, and
   ``csone_autodiscovery_path`` keys into the analysis_status entry it
   creates.
2. The status keys are populated based on Round 38 leader provenance
   semantics (explicit upload -> ``csone_file_was_uploaded=True``;
   autodiscovery only -> ``csone_file_was_uploaded=False`` with
   ``csone_file_autopicked`` carrying the resolved path).
3. ``run_compact_analysis`` reads ``csone_file_was_uploaded`` from
   status (NOT from a recomputed flag) before passing
   ``csone_file_provided=`` to ``raise_validation_error_if_invalid``.

Source-shape pin -- the test never starts a real analysis; it asserts
the literal handler shape so the asymmetry vs leader cannot recur.
"""

from __future__ import annotations

import re
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parent.parent
_APP_SIMPLE = _REPO_ROOT / "app_simple.py"


def _read_app_simple() -> str:
    return _APP_SIMPLE.read_text(encoding="utf-8")


def test_start_compact_analysis_tracks_explicit_vs_autopicked_provenance() -> None:
    """``start_compact_analysis`` MUST distinguish explicit uploads
    from autodiscovery hits via separate variables before storing the
    effective ``csone_file`` in analysis_status."""
    src = _read_app_simple()

    # Locate the start_compact_analysis function body.
    m = re.search(
        r"def start_compact_analysis\(\).*?(?=^@app\.route|\Z)",
        src,
        flags=re.DOTALL | re.MULTILINE,
    )
    assert m is not None, (
        "start_compact_analysis route handler not found in app_simple.py"
    )
    body = m.group(0)

    # The Round 45 / Phase 3 contract requires distinct variables for
    # explicit upload vs autodiscovery.
    assert "csone_file_explicit" in body, (
        "Round 45 / Phase 3 regression: start_compact_analysis must "
        "track ``csone_file_explicit`` separately from the autodiscovery "
        "path (mirrors leader endpoint Round 38 pattern)."
    )
    assert "csone_file_autopicked" in body, (
        "Round 45 / Phase 3 regression: start_compact_analysis must "
        "track ``csone_file_autopicked`` separately so the worker can "
        "log the autodiscovery-empty-after-scope warning correctly."
    )


def test_start_compact_analysis_writes_provenance_into_status() -> None:
    """The status dict MUST carry the four Round 45 provenance keys so
    the worker can read them without recomputing."""
    src = _read_app_simple()

    m = re.search(
        r"def start_compact_analysis\(\).*?(?=^@app\.route|\Z)",
        src,
        flags=re.DOTALL | re.MULTILINE,
    )
    assert m is not None
    body = m.group(0)

    for required_key in (
        "'csone_file_was_uploaded'",
        "'csone_file_path'",
        "'csone_file_autopicked'",
        "'csone_autodiscovery_path'",
    ):
        assert required_key in body, (
            f"Round 45 / Phase 3 regression: status dict in "
            f"start_compact_analysis must include {required_key}; "
            f"the worker reads it for the validator + Phase 4 logging."
        )


def test_run_compact_analysis_passes_csone_file_provided_from_status() -> None:
    """The compact worker MUST honor ``csone_file_was_uploaded`` from
    the analysis_status when calling
    ``raise_validation_error_if_invalid``.  Pre-Round-45 the call
    omitted ``csone_file_provided=`` entirely, so explicit uploads were
    treated identically to autodiscovery (the wrong direction)."""
    src = _read_app_simple()

    # The validator call inside run_compact_analysis must read
    # csone_file_was_uploaded from the status dict and pass it through
    # as csone_file_provided=.
    needles = (
        "csone_file_was_uploaded",
        "csone_file_provided=_csone_was_uploaded",
    )
    for needle in needles:
        assert needle in src, (
            f"Round 45 / Phase 3 regression: run_compact_analysis must "
            f"read ``{needle}`` so the explicit-upload fail-loud "
            f"contract from Round 2 / Phase 4.3 still fires."
        )


def test_run_compact_analysis_treats_csone_optional_in_portfolio() -> None:
    """The compact validator call MUST use the same
    ``['snowflake','team_subscriptions','adoption_barriers']`` source
    list as comprehensive in portfolio mode -- CSOne is optional unless
    explicitly uploaded.  Pre-Round-45 it passed
    ``required_sources=None`` which fell back to the validator default
    that includes ``csone`` and hard-failed every autodiscovery-only
    run."""
    src = _read_app_simple()

    # Locate run_compact_analysis (we use the function-def anchor
    # because there's no ``@app.route`` decorator on it; it's a plain
    # function called from the start_compact_analysis route).
    m = re.search(
        r"def run_compact_analysis\(.*?\):",
        src,
    )
    assert m is not None
    # Take a wide window after the def; the validator call site is in
    # the first ~3000 lines after.
    start_idx = m.start()
    # Cut off at the next top-level ``def`` so we don't pick up
    # neighboring run_*_analysis bodies.
    next_def = re.search(
        r"^def [a-zA-Z_]+\(",
        src[m.end():],
        flags=re.MULTILINE,
    )
    end_idx = (m.end() + next_def.start()) if next_def else len(src)
    body = src[start_idx:end_idx]

    # Phase 3 must explicitly list adoption_barriers in the portfolio
    # branch (i.e. the no-single-customer branch).  Accept either the
    # inline list form or the multi-line list form.
    flat = re.sub(r"\s+", " ", body)
    assert "'snowflake', 'team_subscriptions', 'adoption_barriers'" in flat, (
        "Round 45 / Phase 3 regression: run_compact_analysis portfolio "
        "branch must explicitly pass "
        "``['snowflake','team_subscriptions','adoption_barriers']`` to "
        "the validator (CSOne optional, mirrors comprehensive at L12819-L12823)."
    )


def test_run_compact_analysis_logs_autodiscovery_warning_when_empty() -> None:
    """When CSOne is NOT uploaded and the scoped frame is empty, the
    worker MUST append a ``partial_data_warnings`` entry tagged
    ``kind='autodiscovered_empty_after_scope'`` so the writer banner
    explains why TAC sections look thin."""
    src = _read_app_simple()
    assert "'kind': 'autodiscovered_empty_after_scope'" in src, (
        "Round 45 / Phase 3 regression: run_compact_analysis must "
        "append a partial-data warning tagged "
        "``kind='autodiscovered_empty_after_scope'`` when CSOne was "
        "not uploaded and the scoped frame is empty."
    )
