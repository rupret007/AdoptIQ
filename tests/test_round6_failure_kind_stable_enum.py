"""Round 6 / Phase 7.4 regression test.

Subsection ``failure_kind`` values must come from a small stable
enum (``snowflake`` / ``value`` / ``unknown``) via
``_stable_failure_kind`` rather than raw ``type(e).__name__`` --
which would otherwise leak third-party driver class names through
the public payload.
"""
from __future__ import annotations
from source_shape_utils import assert_in_source
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_failure_kind_stable_enum() -> None:
    src = (REPO_ROOT / "adoptiq_backend.py").read_text(encoding="utf-8")
    assert_in_source(src, "Round 6 / Phase 7.4", label='src')
    assert_in_source(src, "_stable_failure_kind", label='src')
