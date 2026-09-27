"""Practice resolution and technology SSoT accessors (Slice 1).

Precedence for ``get_active_practice()`` (highest first):

1. ``settings.json`` key ``practice`` when valid.
2. ``ADOPTIQ_PRACTICE`` environment variable when valid.
3. Hard default ``collaboration``.

Slice 1 ships only the Collaboration technology pack. When
``practice=security``, technology accessors fail closed (they do not
return Collaboration Webex rows). External Intelligence and other
Security-aware surfaces may still operate; report technology scope
requires ``practice=collaboration`` until Slice 2 adds a Security pack.
"""

from __future__ import annotations

import os
import logging
from typing import Any, Final

from practices import collaboration as _collab

logger = logging.getLogger(__name__)

PRACTICE_COLLABORATION: Final[str] = "collaboration"
PRACTICE_SECURITY: Final[str] = "security"
DEFAULT_PRACTICE: Final[str] = PRACTICE_COLLABORATION

_VALID_PRACTICES: frozenset[str] = frozenset({PRACTICE_COLLABORATION, PRACTICE_SECURITY})

# Round Slice1.1 — fail-closed when Security practice has no tech pack yet
ERROR_COLLAB_TECH_UNAVAILABLE: Final[str] = "collaboration_technology_pack_unavailable"


class PracticeTechnologyUnavailableError(Exception):
    """Raised when Collaboration-wired technology data must not be served."""

    error_kind: str = ERROR_COLLAB_TECH_UNAVAILABLE

    def __init__(self, practice: str | None = None) -> None:
        self.practice = practice if practice is not None else get_active_practice()
        self.detail = (
            "Collaboration technology choices and filters are unavailable while "
            f"practice is '{self.practice}'. Switch to collaboration or wait for "
            "the Security technology pack (Slice 2)."
        )
        super().__init__(self.detail)


def is_valid_practice(value: Any) -> bool:
    """Return True when ``value`` is a known practice id."""
    try:
        import adoptiq_settings as _settings  # noqa: PLC0415

        return _settings.is_valid_practice(value)
    except Exception:  # noqa: BLE001
        if not isinstance(value, str):
            return False
        return value.strip().lower() in _VALID_PRACTICES


def _normalize_practice(value: str) -> str:
    return value.strip().lower()


def get_active_practice() -> str:
    """Resolve the active practice without caching (UI flips apply next call)."""
    try:
        import adoptiq_settings as _settings  # noqa: PLC0415

        raw = (_settings.load_settings() or {}).get("practice", "")
        if isinstance(raw, str) and raw.strip():
            candidate = _normalize_practice(raw)
            if candidate in _VALID_PRACTICES:
                return candidate
    except Exception as exc:  # noqa: BLE001
        logger.debug("practice_config: settings read failed: %s", exc)

    env_raw = os.environ.get("ADOPTIQ_PRACTICE", "").strip()
    if env_raw:
        candidate = _normalize_practice(env_raw)
        if candidate in _VALID_PRACTICES:
            return candidate

    return DEFAULT_PRACTICE


def collaboration_technology_pack_available() -> bool:
    """True when Collaboration technology accessors may return data."""
    return get_active_practice() == PRACTICE_COLLABORATION


def _require_collaboration_technology_pack() -> None:
    # Round Slice1.1
    if not collaboration_technology_pack_available():
        raise PracticeTechnologyUnavailableError()


def technology_unavailable_payload(*, http_status: int = 409) -> dict[str, Any]:
    """JSON-serialisable body for fail-closed API responses."""
    practice = get_active_practice()
    return {
        "ok": False,
        "error": ERROR_COLLAB_TECH_UNAVAILABLE,
        "practice": practice,
        "detail": (
            "Collaboration technology choices and filters are unavailable while "
            f"practice is '{practice}'. Switch practice to collaboration or wait "
            "for the Security technology pack."
        ),
        "collaboration_technology_available": False,
        "http_status": http_status,
    }


def _collaboration_pack():
    """Return the frozen Collaboration constants module (Slice 1 only pack)."""
    return _collab


def get_config_tech_choices() -> list[str]:
    _require_collaboration_technology_pack()  # Round Slice1.1
    return list(_collaboration_pack().CONFIG_TECH_CHOICES)


def get_config_tech_filters() -> dict[str, list[str]]:
    _require_collaboration_technology_pack()  # Round Slice1.1
    return dict(_collaboration_pack().CONFIG_TECH_FILTERS)


def get_sub_technology_mappings() -> dict[str, str]:
    _require_collaboration_technology_pack()  # Round Slice1.1
    return dict(_collaboration_pack().SUB_TECHNOLOGY_MAPPINGS)


def get_backend_tech_choices() -> list[str]:
    _require_collaboration_technology_pack()  # Round Slice1.1
    return list(_collaboration_pack().BACKEND_TECH_CHOICES)


def get_backend_tech_filters() -> dict[str, list[str]]:
    _require_collaboration_technology_pack()  # Round Slice1.1
    return dict(_collaboration_pack().BACKEND_TECH_FILTERS)


def get_matrix_technology_choices() -> tuple[str, ...]:
    _require_collaboration_technology_pack()  # Round Slice1.1
    return _collaboration_pack().MATRIX_TECHNOLOGY_CHOICES


def get_analysis_form_technology_choices() -> list[tuple[str, str]]:
    _require_collaboration_technology_pack()  # Round Slice1.1
    return _collaboration_pack().analysis_form_technology_choices()


def get_report_defaults_technology_choices() -> list[str]:
    """Technologies for preferences / report-defaults (backend roster)."""
    return get_backend_tech_choices()


def get_all_family_scope_name() -> str:
    _require_collaboration_technology_pack()  # Round Slice1.1
    return _collaboration_pack().ALL_FAMILY_SCOPE_NAME
