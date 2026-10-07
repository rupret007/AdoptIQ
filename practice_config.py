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

import contextvars
import logging
import os
from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Callable, Final, Iterator

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


class LivePracticeMapping(Mapping):
    """Round 188: dict view that re-reads practice filters on every access.

    Import-time snapshots stayed empty after a Security boot even when
    Preferences later switched to Collaboration. Callers keep using
    ``TECH_FILTERS[tech]`` / ``.get()`` / ``.keys()``.

    Round 189: getters honor a worker-bound ``PracticeFilterSnapshot`` so
    an in-flight job keeps the pack captured at admission.
    """

    def __init__(self, getter: Callable[[], dict]) -> None:
        self._getter = getter

    def _data(self) -> dict:
        try:
            return self._getter()
        except PracticeTechnologyUnavailableError:
            return {}

    def __getitem__(self, key):
        return self._data()[key]

    def __iter__(self):
        return iter(self._data())

    def __len__(self) -> int:
        return len(self._data())

    def get(self, key, default=None):
        return self._data().get(key, default)

    def keys(self):
        return self._data().keys()

    def items(self):
        return self._data().items()

    def values(self):
        return self._data().values()

    def __eq__(self, other: object) -> bool:
        data = dict(self._data())
        if isinstance(other, Mapping):
            return data == dict(other)
        return data == other

    def __repr__(self) -> str:
        return repr(self._data())


class LivePracticeSequence(Sequence):
    """Round 188: list view that re-reads practice choices on every access."""

    def __init__(self, getter: Callable[[], list]) -> None:
        self._getter = getter

    def _data(self) -> list:
        try:
            return self._getter()
        except PracticeTechnologyUnavailableError:
            return []

    def __getitem__(self, index):
        return self._data()[index]

    def __len__(self) -> int:
        return len(self._data())

    def __iter__(self):
        return iter(self._data())

    def __contains__(self, item: object) -> bool:
        return item in self._data()

    def __eq__(self, other: object) -> bool:
        return list(self._data()) == list(other)

    def __repr__(self) -> str:
        return repr(self._data())


# Round 189: admission-time practice/filter snapshot for in-flight jobs.
# LivePracticeMapping still re-reads the global preference for new requests;
# bound workers must not observe a Preferences switch mid-run.
_JOB_PRACTICE_SNAPSHOT: contextvars.ContextVar["PracticeFilterSnapshot | None"] = (
    contextvars.ContextVar("adoptiq_job_practice_snapshot", default=None)
)


def _freeze_str_tuple(values: Any) -> tuple[str, ...]:
    if not isinstance(values, (list, tuple)):
        return ()
    return tuple(str(item) for item in values)


def _freeze_filter_map(raw: Any) -> Mapping[str, tuple[str, ...]]:
    if not isinstance(raw, Mapping):
        return MappingProxyType({})
    frozen: dict[str, tuple[str, ...]] = {}
    for key, value in raw.items():
        if not isinstance(key, str):
            continue
        frozen[key] = _freeze_str_tuple(value)
    return MappingProxyType(frozen)


def _freeze_str_map(raw: Any) -> Mapping[str, str]:
    if not isinstance(raw, Mapping):
        return MappingProxyType({})
    frozen: dict[str, str] = {}
    for key, value in raw.items():
        if not isinstance(key, str):
            continue
        frozen[key] = str(value)
    return MappingProxyType(frozen)


@dataclass(frozen=True)
class PracticeFilterSnapshot:
    """Immutable practice + technology pack captured when a report is admitted."""

    practice: str
    pack_available: bool
    backend_tech_filters: Mapping[str, tuple[str, ...]]
    backend_tech_choices: tuple[str, ...]
    config_tech_filters: Mapping[str, tuple[str, ...]]
    config_tech_choices: tuple[str, ...]
    sub_technology_mappings: Mapping[str, str]
    matrix_technology_choices: tuple[str, ...]
    all_family_scope_name: str

    def as_status_dict(self) -> dict[str, Any]:
        """JSON-safe payload persisted on ``analysis_status`` at admission."""
        return {
            "practice": self.practice,
            "pack_available": self.pack_available,
            "backend_tech_filters": {
                key: list(patterns) for key, patterns in self.backend_tech_filters.items()
            },
            "backend_tech_choices": list(self.backend_tech_choices),
            "config_tech_filters": {
                key: list(patterns) for key, patterns in self.config_tech_filters.items()
            },
            "config_tech_choices": list(self.config_tech_choices),
            "sub_technology_mappings": dict(self.sub_technology_mappings),
            "matrix_technology_choices": list(self.matrix_technology_choices),
            "all_family_scope_name": self.all_family_scope_name,
        }


def _empty_practice_snapshot(practice: str) -> PracticeFilterSnapshot:
    return PracticeFilterSnapshot(
        practice=practice,
        pack_available=False,
        backend_tech_filters=MappingProxyType({}),
        backend_tech_choices=(),
        config_tech_filters=MappingProxyType({}),
        config_tech_choices=(),
        sub_technology_mappings=MappingProxyType({}),
        matrix_technology_choices=(),
        all_family_scope_name="",
    )


def _collaboration_practice_snapshot() -> PracticeFilterSnapshot:
    pack = _collaboration_pack()
    return PracticeFilterSnapshot(
        practice=PRACTICE_COLLABORATION,
        pack_available=True,
        backend_tech_filters=_freeze_filter_map(pack.BACKEND_TECH_FILTERS),
        backend_tech_choices=_freeze_str_tuple(pack.BACKEND_TECH_CHOICES),
        config_tech_filters=_freeze_filter_map(pack.CONFIG_TECH_FILTERS),
        config_tech_choices=_freeze_str_tuple(pack.CONFIG_TECH_CHOICES),
        sub_technology_mappings=_freeze_str_map(pack.SUB_TECHNOLOGY_MAPPINGS),
        matrix_technology_choices=_freeze_str_tuple(pack.MATRIX_TECHNOLOGY_CHOICES),
        all_family_scope_name=str(pack.ALL_FAMILY_SCOPE_NAME),
    )


def capture_practice_filter_snapshot() -> PracticeFilterSnapshot:
    """Freeze the live practice pack for the job that is being admitted.

    Round 189: call this at the shared start gate *after* admission
    succeeds. Workers bind the returned snapshot; new HTTP requests
    keep reading the live preference.
    """
    practice = get_active_practice()
    if practice != PRACTICE_COLLABORATION:
        return _empty_practice_snapshot(practice)
    return _collaboration_practice_snapshot()


def practice_filter_snapshot_from_mapping(raw: Any) -> PracticeFilterSnapshot | None:
    """Rehydrate a snapshot from ``analysis_status`` JSON."""
    if not isinstance(raw, Mapping):
        return None
    practice_raw = raw.get("practice")
    if not isinstance(practice_raw, str):
        return None
    practice = _normalize_practice(practice_raw)
    if practice not in _VALID_PRACTICES:
        return None
    pack_available = bool(raw.get("pack_available"))
    if not pack_available:
        return _empty_practice_snapshot(practice)
    if practice != PRACTICE_COLLABORATION:
        return _empty_practice_snapshot(practice)
    snapshot = PracticeFilterSnapshot(
        practice=practice,
        pack_available=True,
        backend_tech_filters=_freeze_filter_map(raw.get("backend_tech_filters")),
        backend_tech_choices=_freeze_str_tuple(raw.get("backend_tech_choices")),
        config_tech_filters=_freeze_filter_map(raw.get("config_tech_filters")),
        config_tech_choices=_freeze_str_tuple(raw.get("config_tech_choices")),
        sub_technology_mappings=_freeze_str_map(raw.get("sub_technology_mappings")),
        matrix_technology_choices=_freeze_str_tuple(raw.get("matrix_technology_choices")),
        all_family_scope_name=str(raw.get("all_family_scope_name") or ""),
    )
    if not snapshot.backend_tech_filters:
        # Defense: a truncated status row still keeps the admitted Collaboration pack.
        return _collaboration_practice_snapshot()
    return snapshot


def get_bound_job_practice_snapshot() -> PracticeFilterSnapshot | None:
    """Return the worker-bound snapshot, or None for live request-time lookups."""
    return _JOB_PRACTICE_SNAPSHOT.get()


def bind_job_practice_snapshot(snapshot: PracticeFilterSnapshot | None) -> contextvars.Token:
    """Bind an admitted snapshot for the current worker context."""
    return _JOB_PRACTICE_SNAPSHOT.set(snapshot)


def reset_job_practice_snapshot(token: contextvars.Token) -> None:
    """Clear the worker-bound snapshot (always from a ``finally``)."""
    _JOB_PRACTICE_SNAPSHOT.reset(token)


@contextmanager
def bound_job_practice_snapshot(snapshot: PracticeFilterSnapshot | None) -> Iterator[None]:
    """Round 189: bind snapshot for a worker (or a regression test) and reset."""
    token = bind_job_practice_snapshot(snapshot)
    try:
        yield
    finally:
        reset_job_practice_snapshot(token)


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


def _job_pack_snapshot_or_live_require() -> PracticeFilterSnapshot | None:
    """Round 189: bound job snapshot wins; None means use the live pack.

    HTTP gates keep using ``collaboration_technology_pack_available()``
    (live preference). Filter getters used by workers go through here.
    """
    snapshot = get_bound_job_practice_snapshot()
    if snapshot is None:
        _require_collaboration_technology_pack()  # Round Slice1.1
        return None
    if not snapshot.pack_available:
        raise PracticeTechnologyUnavailableError(practice=snapshot.practice)
    return snapshot


def get_config_tech_choices() -> list[str]:
    snapshot = _job_pack_snapshot_or_live_require()  # Round 189
    if snapshot is not None:
        return list(snapshot.config_tech_choices)
    return list(_collaboration_pack().CONFIG_TECH_CHOICES)


def get_config_tech_filters() -> dict[str, list[str]]:
    snapshot = _job_pack_snapshot_or_live_require()  # Round 189
    if snapshot is not None:
        return {key: list(patterns) for key, patterns in snapshot.config_tech_filters.items()}
    return dict(_collaboration_pack().CONFIG_TECH_FILTERS)


def get_sub_technology_mappings() -> dict[str, str]:
    snapshot = _job_pack_snapshot_or_live_require()  # Round 189
    if snapshot is not None:
        return dict(snapshot.sub_technology_mappings)
    return dict(_collaboration_pack().SUB_TECHNOLOGY_MAPPINGS)


def get_backend_tech_choices() -> list[str]:
    snapshot = _job_pack_snapshot_or_live_require()  # Round 189
    if snapshot is not None:
        return list(snapshot.backend_tech_choices)
    return list(_collaboration_pack().BACKEND_TECH_CHOICES)


def get_backend_tech_filters() -> dict[str, list[str]]:
    snapshot = _job_pack_snapshot_or_live_require()  # Round 189
    if snapshot is not None:
        return {key: list(patterns) for key, patterns in snapshot.backend_tech_filters.items()}
    return dict(_collaboration_pack().BACKEND_TECH_FILTERS)


def get_matrix_technology_choices() -> tuple[str, ...]:
    snapshot = _job_pack_snapshot_or_live_require()  # Round 189
    if snapshot is not None:
        return snapshot.matrix_technology_choices
    return _collaboration_pack().MATRIX_TECHNOLOGY_CHOICES


def get_analysis_form_technology_choices() -> list[tuple[str, str]]:
    snapshot = _job_pack_snapshot_or_live_require()  # Round 189
    if snapshot is not None:
        return [(label, label) for label in snapshot.matrix_technology_choices]
    return _collaboration_pack().analysis_form_technology_choices()


def get_report_defaults_technology_choices() -> list[str]:
    """Technologies for preferences / report-defaults (backend roster)."""
    return get_backend_tech_choices()


def get_all_family_scope_name() -> str:
    snapshot = _job_pack_snapshot_or_live_require()  # Round 189
    if snapshot is not None:
        return snapshot.all_family_scope_name
    return _collaboration_pack().ALL_FAMILY_SCOPE_NAME
