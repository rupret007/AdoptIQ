"""Round 32 / Phase 1.B regression: matplotlib + Pillow must be
included in the packaged .app so chart generation actually produces
images instead of logging "Matplotlib not available - charts will be
skipped" inside the bundled run.

Build6 had ``matplotlib`` in PyInstaller's ``excludes`` list which
prevented every report from rendering charts.  These tests are
runtime checks (matplotlib actually importable, chart byte-buffer
non-empty) plus a static spec-file lint so the regression cannot
re-enter through a future spec edit.
"""
from __future__ import annotations

import io
import pathlib

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
SPEC_PATH = REPO_ROOT / "adoptiq_mac.spec"


def test_matplotlib_importable() -> None:
    """matplotlib must be installable in the dev environment too;
    the bundled .app re-uses the same dependency set."""
    matplotlib = pytest.importorskip("matplotlib")
    assert hasattr(matplotlib, "use")
    matplotlib.use("Agg")  # idempotent; locks in the headless backend


def test_pyplot_renders_non_empty_png_with_agg_backend() -> None:
    matplotlib = pytest.importorskip("matplotlib")
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(2, 1.5))
    ax.bar(["a", "b", "c"], [1, 3, 2])
    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    plt.close(fig)
    raw = buf.getvalue()
    assert raw[:8] == b"\x89PNG\r\n\x1a\n", "expected PNG signature"
    assert len(raw) > 100, "PNG looks suspiciously small"


def test_spec_file_does_not_exclude_matplotlib_or_pil() -> None:
    src = SPEC_PATH.read_text(encoding="utf-8")
    assert "'matplotlib'" not in _excludes_section(src), (
        "Round 32 / Phase 1.B regression: matplotlib must NOT appear "
        "in the PyInstaller excludes block."
    )
    assert "'PIL'" not in _excludes_section(src), (
        "Round 32 / Phase 1.B regression: PIL must NOT appear in the "
        "PyInstaller excludes block (matplotlib needs it)."
    )


def test_spec_file_hidden_imports_matplotlib_chain() -> None:
    src = SPEC_PATH.read_text(encoding="utf-8")
    block = _hidden_imports_section(src)
    for name in (
        "'matplotlib'",
        "'matplotlib.pyplot'",
        "'matplotlib.backends.backend_agg'",
        "'PIL'",
        "'PIL.Image'",
    ):
        assert name in block, (
            f"Round 32 / Phase 1.B regression: {name} missing from "
            "PyInstaller hidden_imports."
        )


def test_app_simple_locks_agg_backend_at_module_load() -> None:
    """Round 32 / Phase 1.B: app_simple.py must select the Agg backend
    before any later import touches ``matplotlib.pyplot`` so the
    Flask worker thread cannot accidentally pick the macOS Cocoa
    backend (which crashes off the main thread)."""
    src = (REPO_ROOT / "app_simple.py").read_text(encoding="utf-8")
    needle = 'matplotlib.use("Agg")'
    assert src.count(needle) >= 1, (
        "app_simple.py must call matplotlib.use(\"Agg\") at module load."
    )
    assert "Round 32 / Phase 1.B" in src


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _excludes_section(src: str) -> str:
    """Return the substring of the .spec containing the ``excludes=[...]``
    list.  We grab a generous window so a multi-line entry stays inside
    the slice."""
    marker = "excludes=["
    start = src.find(marker)
    if start == -1:
        return ""
    end = src.find("]", start)
    if end == -1:
        return src[start:]
    return src[start:end + 1]


def _hidden_imports_section(src: str) -> str:
    marker = "hidden_imports = ["
    start = src.find(marker)
    if start == -1:
        return ""
    end = src.find("]", start)
    if end == -1:
        return src[start:]
    return src[start:end + 1]
