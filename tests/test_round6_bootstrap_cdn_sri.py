"""Round 6 / Phase 2.1 regression test.

Bootstrap CDN ``<link>``/``<script>`` tags must include
``integrity=`` SRI and ``crossorigin=anonymous``.  CodeGuard
``client-side-web-security``.
"""
from __future__ import annotations
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_bootstrap_cdn_sri() -> None:
    src = (REPO_ROOT / "templates" / "base.html").read_text(encoding="utf-8")
    assert "Round 6 / Phase 2.1" in src
    assert "integrity=" in src
    assert "crossorigin=" in src
