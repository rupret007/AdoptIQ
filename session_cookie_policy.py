"""Transport-aware Flask session-cookie policy for AdoptIQ."""

from __future__ import annotations

from urllib.parse import urlsplit


_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES = frozenset({"0", "false", "no", "off"})
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "[::1]", "localhost"})


class PublicBindSecurityError(ValueError):
    """Raised when a public bind would use an insecure session transport."""


def resolve_session_cookie_secure(
    explicit_value: object,
    main_url: object,
) -> bool:
    """Return whether Flask should add ``Secure`` to its session cookie.

    AdoptIQ's packaged desktop app serves its UI over loopback HTTP, so being
    frozen does not imply HTTPS. An explicit operator setting always wins;
    otherwise the URL scheme is authoritative and malformed/blank URLs fail
    to the usable HTTP-local default.
    """

    normalized = str(explicit_value or "").strip().casefold()
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        return False
    try:
        return urlsplit(str(main_url or "").strip()).scheme.casefold() == "https"
    except (TypeError, ValueError):
        return False


def require_secure_public_bind(
    bind_host: object,
    main_url: object,
    session_cookie_secure: bool,
) -> None:
    """Round 148: reject public HTTP unless secure cookies were explicit.

    AdoptIQ remains usable on its default loopback HTTP transport. A public
    bind, however, must either advertise an HTTPS main URL or opt into Secure
    session cookies. This turns the old warning-only startup path into a
    fail-closed boundary without changing the desktop default.
    """

    normalized_host = str(bind_host or "").strip().casefold()
    if normalized_host in _LOOPBACK_HOSTS:
        return

    try:
        uses_https = urlsplit(str(main_url or "").strip()).scheme.casefold() == "https"
    except (TypeError, ValueError):
        uses_https = False
    if uses_https or bool(session_cookie_secure):
        return

    raise PublicBindSecurityError(
        "Public AdoptIQ binding requires an HTTPS ADOPTIQ_MAIN_URL or "
        "ADOPTIQ_SECURE_COOKIES=1."
    )
