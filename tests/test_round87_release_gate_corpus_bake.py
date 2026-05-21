"""Round 96 / Build 69: release gate forbids bundled corpus data.

Round 87 made the release gate require a baked corpus. Round 96 inverts
that contract: release builds must skip the corpus data bake, scrub any
stale local bake artifacts, and fail if corpus DB/salt files are present
when ``ADOPTIQ_RELEASE_GATE=1`` is active.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BUILD_SCRIPT = REPO_ROOT / "build_mac_dmg.sh"
BAKE_SCRIPT = REPO_ROOT / "scripts" / "bake_corpus.py"


def _read_build_script() -> str:
    assert BUILD_SCRIPT.exists(), f"missing build script: {BUILD_SCRIPT}"
    return BUILD_SCRIPT.read_text(encoding="utf-8")


def _read_bake_script() -> str:
    assert BAKE_SCRIPT.exists(), f"missing bake script: {BAKE_SCRIPT}"
    return BAKE_SCRIPT.read_text(encoding="utf-8")


def test_release_gate_block_present_and_inverted() -> None:
    body = _read_build_script()
    assert 'ADOPTIQ_RELEASE_GATE:-0' in body
    assert 'Round 96: ADOPTIQ_RELEASE_GATE=1 active' in body
    assert 'bake/.bake-skipped' in body
    assert '[[ ! -f "bake/.bake-skipped" ]]' in body, (
        "release gate must require the skip marker because shipping "
        "builds must not bake corpus data"
    )
    assert '[[ -f "bake/corpus.db.enc" || -f "bake/corpus.db.salt" ]]' in body, (
        "release gate must fail if stale corpus DB/salt artifacts are present"
    )
    assert "No corpus data artifacts present, gate satisfied." in body


def test_build_script_defaults_to_skip_corpus_bake() -> None:
    body = _read_build_script()
    assert 'BAKE_FLAG="${ADOPTIQ_BAKE_CORPUS:-0}"' in body
    assert "runtime-only shipping" in body


def test_bake_corpus_script_still_emits_and_scrubs_skip_marker() -> None:
    body = _read_bake_script()
    assert '_emit_skip_marker' in body
    assert '.bake-skipped' in body
    for stale in (
        '"corpus.db.enc"',
        '"corpus.db.salt"',
        '"sentinel.json"',
        '"corpus.sentinel.lock.json"',
    ):
        assert stale in body, (
            f"skip path must scrub stale {stale} so packaging cannot "
            "pick up old corpus artifacts"
        )


def test_bake_script_documents_developer_validation_only() -> None:
    body = _read_bake_script()
    assert "developer validation tool" in body
    assert "Shipping builds no longer embed" in body
