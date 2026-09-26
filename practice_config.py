"""Practice resolution and technology SSoT accessors (Slice 1).

Precedence for ``get_active_practice()`` (highest first):

1. ``settings.json`` key ``practice`` when valid.
2. ``ADOPTIQ_PRACTICE`` environment variable when valid.
3. Hard default ``collaboration``.

Slice 1 ships only the Collaboration pack; ``security`` is accepted in
settings for forward compatibility but still loads Collaboration
technology data until Slice 2 adds a Security pack.
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


def _collaboration_pack():
    """Return the frozen Collaboration constants module (Slice 1 only pack)."""
    return _collab


def get_config_tech_choices() -> list[str]:
    return list(_collaboration_pack().CONFIG_TECH_CHOICES)


def get_config_tech_filters() -> dict[str, list[str]]:
    return dict(_collaboration_pack().CONFIG_TECH_FILTERS)


def get_sub_technology_mappings() -> dict[str, str]:
    return dict(_collaboration_pack().SUB_TECHNOLOGY_MAPPINGS)


def get_backend_tech_choices() -> list[str]:
    return list(_collaboration_pack().BACKEND_TECH_CHOICES)


def get_backend_tech_filters() -> dict[str, list[str]]:
    return dict(_collaboration_pack().BACKEND_TECH_FILTERS)


def get_matrix_technology_choices() -> tuple[str, ...]:
    return _collaboration_pack().MATRIX_TECHNOLOGY_CHOICES


def get_analysis_form_technology_choices() -> list[tuple[str, str]]:
    return _collaboration_pack().analysis_form_technology_choices()


def get_report_defaults_technology_choices() -> list[str]:
    """Technologies for preferences / report-defaults (backend roster)."""
    return get_backend_tech_choices()


def get_all_family_scope_name() -> str:
    return _collaboration_pack().ALL_FAMILY_SCOPE_NAME
