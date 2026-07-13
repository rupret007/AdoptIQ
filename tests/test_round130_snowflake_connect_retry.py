"""Round 130 — Snowflake connect retry on transient failures."""

from __future__ import annotations

from unittest.mock import patch

import pytest

import adoptiq_backend as ab


def test_connect_with_keeper_retries_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    def _fake_impl():
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("Failed to connect to Snowflake")
        return "conn-ok"

    monkeypatch.setattr(ab, "_connect_with_keeper_impl", _fake_impl)
    monkeypatch.setattr(ab.time, "sleep", lambda _s: None)

    assert ab._connect_with_keeper() == "conn-ok"
    assert calls["n"] == 3


def test_connect_with_keeper_does_not_retry_config_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    def _fake_impl():
        calls["n"] += 1
        raise RuntimeError("Snowflake credentials not set.")

    monkeypatch.setattr(ab, "_connect_with_keeper_impl", _fake_impl)
    monkeypatch.setattr(ab.time, "sleep", lambda _s: None)

    with pytest.raises(RuntimeError, match="credentials not set"):
        ab._connect_with_keeper()
    assert calls["n"] == 1


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
