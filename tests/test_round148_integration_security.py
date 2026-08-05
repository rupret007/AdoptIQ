"""Round 148 integration security regressions."""

from pathlib import Path

import pytest

from session_cookie_policy import (
    PublicBindSecurityError,
    require_secure_public_bind,
)


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1", "[::1]"])
def test_loopback_http_remains_usable(host: str) -> None:
    require_secure_public_bind(host, "http://localhost:5151", False)


def test_public_http_without_secure_cookie_fails_closed() -> None:
    with pytest.raises(PublicBindSecurityError, match="requires an HTTPS"):
        require_secure_public_bind("0.0.0.0", "http://localhost:5151", False)


def test_public_https_is_allowed() -> None:
    require_secure_public_bind(
        "0.0.0.0",
        "https://adoptiq.example.invalid",
        False,
    )


def test_public_http_requires_explicit_secure_cookie_override() -> None:
    require_secure_public_bind(
        "192.0.2.10",
        "http://adoptiq.example.invalid",
        True,
    )


def test_main_startup_enforces_public_bind_transport() -> None:
    source = Path("app_simple.py").read_text(encoding="utf-8")
    assert "# Round 148:" in source
    assert "require_secure_public_bind(" in source
    assert "except PublicBindSecurityError" in source
    assert "sys.exit(2)" in source


def test_packaged_llm_schema_validator_is_a_required_dependency() -> None:
    requirements = Path("requirements.txt").read_text(encoding="utf-8")
    mac_spec = Path("adoptiq_mac.spec").read_text(encoding="utf-8")

    assert "jsonschema>=4.26.0" in requirements
    assert "'jsonschema'" in mac_spec
