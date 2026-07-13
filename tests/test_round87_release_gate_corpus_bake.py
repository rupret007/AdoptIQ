"""Round 107 / Build 76: release gate requires bundled corpus data."""

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


def test_release_gate_block_requires_prebaked_corpus() -> None:
    body = _read_build_script()
    assert 'ADOPTIQ_RELEASE_GATE:-0' in body
    assert 'Round 107: ADOPTIQ_RELEASE_GATE=1 active' in body
    assert 'bake/.bake-skipped' in body
    assert '[[ -f "bake/.bake-skipped" ]]' in body, (
        "release gate must reject skip mode for shipping builds"
    )
    assert '[[ ! -f "bake/corpus.db.enc" || ! -f "bake/corpus.db.salt" || ! -f "bake/sentinel.json" ]]' in body, (
        "release gate must fail if prebaked corpus DB/salt/sentinel artifacts are missing"
    )
    assert "Prebaked corpus artifacts present, gate satisfied." in body


def test_build_script_defaults_to_corpus_bake() -> None:
    body = _read_build_script()
    assert 'BAKE_FLAG="${ADOPTIQ_BAKE_CORPUS:-1}"' in body
    assert "prebaked corpus" in body.lower()


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


def test_bake_script_documents_shipping_bake() -> None:
    body = _read_bake_script()
    assert "Round 107 / Build 76 restores this script as a production DMG input" in body
    assert "shipping builds pre-bake a corpus snapshot" in body
