"""Round 6 / Phase 6.9 regression test.

A Flask ``@app.after_request`` hook must apply defensive HTTP
security headers (CSP/HSTS/X-Frame-Options/Referrer-Policy) to
sensitive endpoints.
"""
from __future__ import annotations
from source_shape_utils import assert_in_source
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_after_request_security_headers() -> None:
    src = (REPO_ROOT / "app_simple.py").read_text(encoding="utf-8")
    assert_in_source(src, "Round 6 / Phase 6.9", label='src')
    assert_in_source(src, "after_request", label='src')
