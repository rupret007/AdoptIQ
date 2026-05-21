"""Round 96 / Build 69: macOS spec ships no corpus data.

Round 53 originally shrank the app bundle from four baked-corpus
artifacts to two by excluding the sentinel material. Round 96 completes
the externalization: the app bundle must not include the corpus
database, salt, sentinel, lock file, or a ``baked_corpus`` resource
directory at all. Each user builds the encrypted local corpus from
authorized OneDrive data at runtime.
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


def test_spec_does_not_bundle_any_corpus_artifacts() -> None:
    src = _spec_source()
    forbidden = (
        "corpus.db.enc",
        "corpus.db.salt",
        "sentinel.json",
        "corpus.sentinel.lock.json",
        "baked_corpus",
    )
    for needle in forbidden:
        assert needle not in src, (
            f"adoptiq_mac.spec contains {needle!r}; Round 96 requires "
            "the app bundle to ship no corpus data artifacts."
        )


def test_spec_documents_runtime_only_corpus_posture() -> None:
    src = _spec_source()
    assert "Round 96 / runtime-only corpus" in src
    assert "do NOT bundle corpus data" in src
    assert "OneDrive" in src
