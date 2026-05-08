"""Round 92 report-output path resolution.

AdoptIQ's writers, download routes, Preferences UI, and corpus indexer
must agree on the same report-output root.  Packaged builds default to a
user-visible Documents folder, while dev mode keeps the historical local
``./outputs`` path so tests and repo runs do not litter Documents.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Optional, Tuple

REPORTS_FOLDER_NAME = "AdoptIQ Reports"
REPORT_OUTPUTS_SETTING_KEY = "report_outputs_folder"
REPORT_OUTPUTS_ENV_VAR = "ADOPTIQ_OUTPUTS_DIR"


def _legacy_app_support_root() -> Path:
    """Return the pre-R92 App Support outputs root."""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "AdoptIQ" / "outputs"
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", str(Path.home()))) / "AdoptIQ" / "outputs"
    return Path.home() / ".adoptiq" / "outputs"


def documents_default_reports_root() -> Path:
    """Return the user-visible packaged default under Documents."""
    return Path.home() / "Documents" / REPORTS_FOLDER_NAME


def _expand_folder(value: Any) -> Optional[Path]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return Path(os.path.expanduser(text))


def _is_syntactically_valid(value: Any) -> bool:
    try:
        import adoptiq_settings  # noqa: PLC0415

        return bool(adoptiq_settings.is_valid_report_outputs_folder(value))
    except Exception:
        return False


def _mkdir_if_requested(path: Path, *, create: bool) -> bool:
    if not create:
        return path.is_dir()
    try:
        path.mkdir(parents=True, exist_ok=True)
        return path.is_dir()
    except OSError:
        return False


def get_report_outputs_root(
    *,
    create: bool = True,
    frozen: Optional[bool] = None,
) -> Path:
    """Return the effective report-output root.

    Precedence:
    1. ``settings.json['report_outputs_folder']`` when valid.
    2. ``ADOPTIQ_OUTPUTS_DIR`` when valid.
    3. Frozen packaged default: ``~/Documents/AdoptIQ Reports``.
    4. Frozen fallback: legacy App Support ``outputs``.
    5. Dev default: ``./outputs``.
    """
    root, _source = get_report_outputs_root_with_source(create=create, frozen=frozen)
    return root


def get_report_outputs_root_with_source(
    *,
    create: bool = True,
    frozen: Optional[bool] = None,
) -> Tuple[Path, str]:
    """Return ``(root, source_label)`` for UI diagnostics."""
    is_frozen = bool(getattr(sys, "frozen", False)) if frozen is None else bool(frozen)

    try:
        import adoptiq_settings  # noqa: PLC0415

        persisted = adoptiq_settings.get(REPORT_OUTPUTS_SETTING_KEY, "") or ""
    except Exception:
        persisted = ""
    if persisted and _is_syntactically_valid(persisted):
        path = _expand_folder(persisted)
        if path is not None and _mkdir_if_requested(path, create=create):
            return path, "settings.json"

    env_value = os.environ.get(REPORT_OUTPUTS_ENV_VAR, "") or ""
    if env_value and _is_syntactically_valid(env_value):
        path = _expand_folder(env_value)
        if path is not None and _mkdir_if_requested(path, create=create):
            return path, "env"

    if is_frozen:
        docs = documents_default_reports_root()
        if _mkdir_if_requested(docs, create=create):
            return docs, "default"
        legacy = _legacy_app_support_root()
        _mkdir_if_requested(legacy, create=create)
        return legacy, "legacy"

    dev = Path("outputs")
    _mkdir_if_requested(dev, create=create)
    return dev, "dev"


def legacy_outputs_roots() -> tuple[Path, ...]:
    """Roots that may contain pre-R92 files and should remain readable."""
    roots = [_legacy_app_support_root()]
    try:
        roots.append(Path("outputs").resolve())
    except Exception:
        roots.append(Path("outputs"))
    return tuple(roots)


def candidate_output_roots(*, create_current: bool = True) -> tuple[Path, ...]:
    """Return current + legacy roots, preserving order and de-duping."""
    candidates = [get_report_outputs_root(create=create_current)]
    candidates.extend(legacy_outputs_roots())
    out: list[Path] = []
    seen: set[str] = set()
    for path in candidates:
        try:
            key = str(path.resolve())
        except Exception:
            key = str(path)
        if key in seen:
            continue
        seen.add(key)
        out.append(path)
    return tuple(out)


__all__ = [
    "REPORTS_FOLDER_NAME",
    "REPORT_OUTPUTS_SETTING_KEY",
    "REPORT_OUTPUTS_ENV_VAR",
    "documents_default_reports_root",
    "get_report_outputs_root",
    "get_report_outputs_root_with_source",
    "legacy_outputs_roots",
    "candidate_output_roots",
]
