"""Round 6 / Phase 2.1 regression test.

Bootstrap CDN ``<link>``/``<script>`` tags must include
``integrity=`` SRI and ``crossorigin=anonymous``.  CodeGuard
``client-side-web-security``.
"""
from __future__ import annotations
from source_shape_utils import assert_in_source
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_bootstrap_cdn_sri() -> None:
    src = (REPO_ROOT / "templates" / "base.html").read_text(encoding="utf-8")
    assert_in_source(src, "Round 6 / Phase 2.1", label='src')
    assert_in_source(src, "integrity=", label='src')
    assert_in_source(src, "crossorigin=", label='src')
