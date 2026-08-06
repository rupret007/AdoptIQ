"""Round 145: packaged loopback sessions must survive CSRF validation."""
from source_shape_utils import assert_in_source

from pathlib import Path

from session_cookie_policy import resolve_session_cookie_secure


def test_default_packaged_loopback_http_cookie_is_not_secure() -> None:
    assert resolve_session_cookie_secure("", "http://localhost:5151") is False
    assert resolve_session_cookie_secure("", "http://127.0.0.1:5151") is False


def test_https_url_enables_secure_cookie_without_an_override() -> None:
    assert resolve_session_cookie_secure("", "https://adoptiq.example.invalid") is True


def test_explicit_cookie_transport_override_always_wins() -> None:
    assert resolve_session_cookie_secure("1", "http://localhost:5151") is True
    assert resolve_session_cookie_secure("off", "https://adoptiq.example.invalid") is False


def test_app_uses_transport_policy_instead_of_frozen_status() -> None:
    source = Path("app_simple.py").read_text(encoding="utf-8")
    assert_in_source(source, "resolve_session_cookie_secure(", label='source')
    assert "SESSION_COOKIE_SECURE'] = bool(_frozen)" not in source
