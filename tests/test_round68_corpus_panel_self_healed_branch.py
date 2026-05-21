"""Round 96 / Build 69: old self-healed-bake UI copy is retired."""

from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
INTEL_STATUS_JS = REPO_ROOT / "static" / "js" / "intel_status.js"
CORPUS_BOOTSTRAP_PY = REPO_ROOT / "corpus_bootstrap.py"


def test_classifier_maps_legacy_self_healed_source_without_ui_state() -> None:
    src = INTEL_STATUS_JS.read_text(encoding="utf-8")
    assert "source === 'self_healed_baked'" in src
    assert "return 'runtime_synced'" in src
    assert "case 'self_healed_baked'" not in src
    assert "self-healed bake" not in src


def test_python_side_no_longer_emits_self_healed_baked() -> None:
    src = CORPUS_BOOTSTRAP_PY.read_text(encoding="utf-8")
    assert '_STATE.source = "self_healed_baked"' not in src
    assert "legacy no-op" in src


def test_template_documents_runtime_only_panel() -> None:
    src = (REPO_ROOT / "templates" / "analyze.html").read_text(encoding="utf-8")
    assert "Round 96 / runtime-only corpus" in src
    assert "the app ships no" in src and "corpus data" in src
    assert "self_healed_baked" not in src
