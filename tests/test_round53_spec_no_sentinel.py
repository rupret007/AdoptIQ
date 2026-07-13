"""Round 107 / Build 76: macOS spec ships a prebaked corpus.

The app bundle intentionally includes the encrypted corpus database,
salt, and local sentinel so Ask AI can use corpus data on first launch
without OneDrive or startup indexing.
"""

from __future__ import annotations

from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parent.parent
_SPEC_PATH = _REPO_ROOT / "adoptiq_mac.spec"


def _spec_source() -> str:
    assert _SPEC_PATH.exists(), (
        f"adoptiq_mac.spec missing at {_SPEC_PATH}; the macOS build "
        "pipeline depends on it."
    )
    return _SPEC_PATH.read_text(encoding="utf-8")


def test_spec_file_exists() -> None:
    assert _SPEC_PATH.exists()


def test_spec_bundles_required_corpus_artifacts() -> None:
    src = _spec_source()
    required = (
        "corpus.db.enc",
        "corpus.db.salt",
        "sentinel.json",
        "Resources/baked_corpus",
    )
    for needle in required:
        assert needle in src, (
            f"adoptiq_mac.spec is missing {needle!r}; Build 76 requires "
            "the app bundle to ship the prebaked corpus."
        )
    assert "corpus.sentinel.lock.json" not in src


def test_spec_documents_prebaked_corpus_posture() -> None:
    src = _spec_source()
    assert "Round 107 / Build 76" in src
    assert "first" in src and "launch has corpus data immediately" in src
