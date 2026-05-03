"""Round 71 / Phase 5 (#29) -- _r69_sanitize_llm_error covers more credentials.

Pre-R71 ``_r69_sanitize_llm_error`` redacted Bearer tokens, JWTs, and
``client_secret`` patterns but missed:

* ``password`` / ``passwd`` / ``pwd`` in URL query strings or form data
* ``api_key`` / ``api-key`` / ``apikey`` patterns
* ``sk-XXXX``-style provider keys (Anthropic, OpenAI, Stripe,
  SendGrid, etc.)
* generic ``Authorization: <scheme> <token>`` headers without the
  literal ``bearer`` prefix

Round 71 / Phase 5 (#29) extends the regex set so leaked credentials
never round-trip into the JSON response we echo back to the browser.
"""

from __future__ import annotations

import pytest

import app_simple


def test_round71_sanitizer_redacts_password() -> None:
    """``password=...`` in any case MUST be redacted."""
    fn = app_simple._r69_sanitize_llm_error
    assert "password=secret123" not in fn("Error: password=secret123 invalid")
    assert "<redacted>" in fn("Error: password=secret123 invalid")


def test_round71_sanitizer_redacts_passwd_alias() -> None:
    """``passwd=`` and ``pwd=`` aliases MUST also be redacted."""
    fn = app_simple._r69_sanitize_llm_error
    assert "secret456" not in fn("Login failed: passwd=secret456")
    assert "secret789" not in fn("Login failed: pwd=secret789")


def test_round71_sanitizer_redacts_api_key() -> None:
    """``api_key=...`` / ``api-key=...`` / ``apikey=...`` MUST be redacted."""
    fn = app_simple._r69_sanitize_llm_error
    assert "abcdef" not in fn("api_key=abcdef")
    assert "abcdef" not in fn("API-KEY=abcdef")
    assert "abcdef" not in fn("apikey: abcdef")
    assert "<redacted>" in fn("api_key=abcdef") or "api_key=<redacted>" in fn("api_key=abcdef")


def test_round71_sanitizer_redacts_sk_prefix_keys() -> None:
    """``sk-XXXX``-style provider keys (Anthropic, OpenAI, Stripe,
    etc.) MUST be redacted."""
    fn = app_simple._r69_sanitize_llm_error
    cases = [
        "sk-ant-api03-abcdefghijklmnopqr",
        "sk-proj-abcdefghijklmnopqr",
        "sk-test-abcdefghijklmnopqr",
        "sk-live-abcdefghijklmnopqr",
    ]
    for key in cases:
        out = fn(f"Auth failed: {key}")
        assert key not in out, (
            f"Round 71 / Phase 5 (#29): provider key {key!r} must be "
            f"redacted; got {out!r}"
        )
        assert "<api-key-redacted>" in out


def test_round71_sanitizer_redacts_generic_authorization_header() -> None:
    """Generic ``Authorization: <scheme> <token>`` headers MUST be redacted."""
    fn = app_simple._r69_sanitize_llm_error
    out = fn("Authorization: Basic dGVzdDpwYXNzd29yZA==")
    assert "dGVzdDpwYXNzd29yZA==" not in out
    assert "Authorization: <redacted>" in out


def test_round71_sanitizer_preserves_non_credential_text() -> None:
    """Negative control: regular error text must be preserved."""
    fn = app_simple._r69_sanitize_llm_error
    out = fn("Connection timeout after 30 seconds")
    assert "Connection timeout" in out
    assert "30 seconds" in out


def test_round71_sanitizer_still_redacts_pre_r71_patterns() -> None:
    """Negative control: the pre-R71 patterns (Bearer, JWT,
    client_secret) must continue to redact."""
    fn = app_simple._r69_sanitize_llm_error
    assert "abc123" not in fn("Bearer abc123")
    assert "<redacted>" in fn("Bearer abc123")
    out_jwt = fn("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9")
    assert "eyJ" not in out_jwt
    assert "<jwt-redacted>" in out_jwt
    out_cs = fn("client_secret=mysecret123")
    assert "mysecret123" not in out_cs
    assert "client_secret=<redacted>" in out_cs


def test_round71_sanitizer_caps_length() -> None:
    """The sanitizer's max_len cap MUST still apply post-redaction."""
    fn = app_simple._r69_sanitize_llm_error
    long_msg = "Error: " + "x" * 1000
    out = fn(long_msg, max_len=50)
    assert len(out) <= 50
