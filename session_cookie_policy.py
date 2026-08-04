"""Transport-aware Flask session-cookie policy for AdoptIQ."""

from __future__ import annotations

from urllib.parse import urlsplit


_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES = frozenset({"0", "false", "no", "off"})


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
