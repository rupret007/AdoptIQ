"""Round 45 / Phase 3 regression: ``run_compact_analysis`` MUST tolerate
an empty CSOne DataFrame in PORTFOLIO mode (no explicit upload) by
treating CSOne as optional and proceeding with a partial-data warning
-- mirroring the comprehensive-without-CSOne contract at
``run_comprehensive_analysis`` L12819-L12823.

Pre-Round-45, the compact validator at L7591 fell through to the
default ``required_sources`` for compact (``[..., 'csone']``) which
hard-failed every portfolio run without an uploaded CSOne file.  This
broke the documented happy-path demo workflow where the operator
relies on OneDrive autodiscovery to surface the latest CSOne export.

This test pins the source-shape contract:

* portfolio mode (no single-customer filter) MUST list
  ``['snowflake', 'team_subscriptions', 'adoption_barriers']`` --
  matching comprehensive's L12819-L12823.
* explicit-upload uploads MUST still fail loud (Round 2 / Phase 4.3
  contract preserved via ``csone_file_provided=`` argument).
* the worker MUST log a clear "no explicit upload + autodiscovery
  produced 0 rows" warning so the operator understands why TAC sections
  look thin.
"""

from __future__ import annotations
from source_shape_utils import assert_in_source

import re
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parent.parent
_APP_SIMPLE = _REPO_ROOT / "app_simple.py"


def _run_compact_body() -> str:
    """Return the body of ``run_compact_analysis`` as a string slice
    so individual asserts can grep it."""
    src = _APP_SIMPLE.read_text(encoding="utf-8")
    m = re.search(r"def run_compact_analysis\(.*?\):", src)
    assert m is not None, "run_compact_analysis function not found"
    next_def = re.search(
        r"^def [a-zA-Z_]+\(",
        src[m.end():],
        flags=re.MULTILINE,
    )
    end = (m.end() + next_def.start()) if next_def else len(src)
    return src[m.start():end]


def test_portfolio_mode_uses_comprehensive_source_list() -> None:
    """Portfolio branch MUST pass the same source list as
    comprehensive's L12819-L12823 to the validator.

    Accept either inline-list or multi-line-list forms (both are
    semantically identical) so a future code-style refactor doesn't
    silently break the test."""
    body = _run_compact_body()
    flat = re.sub(r"\s+", " ", body)
    assert re.search(
        r"""["']snowflake["']\s*,\s*["']team_subscriptions["']\s*,\s*["']adoption_barriers["']""",
        flat,
    ), (
        "Round 45 / Phase 3 regression: run_compact_analysis portfolio "
        "branch must pass "
        "``['snowflake','team_subscriptions','adoption_barriers']`` to "
        "the validator (CSOne optional)."
    )


def test_validator_call_passes_csone_file_provided_kwarg() -> None:
    """The validator call MUST forward ``csone_file_provided=`` so the
    Round 2 / Phase 4.3 fail-loud-on-explicit-upload contract still
    fires when the operator actually uploaded a file."""
    body = _run_compact_body()
    assert_in_source(body, "csone_file_provided=", label='body')


def test_partial_warning_appended_when_csone_empty_and_not_uploaded() -> None:
    """When CSOne is NOT uploaded and the scoped frame is empty, the
    worker MUST append a partial-data warning so the writer banner
    explains why TAC sections look thin."""
    body = _run_compact_body()
    assert_in_source(body, "autodiscovered_empty_after_scope", label="body")
    # The warning should also include a remediation cue so the
    # banner explains what to do.
    assert_in_source(body, "Upload a CSOne export from CSOne Reports", label='body')


def test_worker_reads_explicit_upload_flag_from_status() -> None:
    """The worker MUST read ``csone_file_was_uploaded`` from the status
    dict (NOT from a recomputed flag) before the validator call."""
    body = _run_compact_body()
    needle = "csone_file_was_uploaded"
    assert_in_source(body, needle, label='body')


def test_no_dead_required_sources_none_fallback() -> None:
    """The pre-Round-45 fallthrough where portfolio mode passed
    ``required_sources=None`` (collapsing to the default that includes
    csone) MUST be gone.  Regex pins that we no longer have a
    ``validation_required_sources = None`` fallback in the portfolio
    branch."""
    body = _run_compact_body()
    # The pre-Round-45 line was:
    #   validation_required_sources = ['snowflake', 'team_subscriptions'] if single_customer_mode else None
    pre_round45_pattern = re.compile(
        r"validation_required_sources\s*=\s*\[.*?\]\s+if\s+single_customer_mode\s+else\s+None"
    )
    assert pre_round45_pattern.search(body) is None, (
        "Round 45 / Phase 3 regression: the pre-Round-45 fallthrough "
        "where portfolio mode passed required_sources=None is back. "
        "Portfolio mode must explicitly list "
        "['snowflake','team_subscriptions','adoption_barriers']."
    )
