"""Round 107 / Build 76: baked corpus labels map to active local corpus."""

from __future__ import annotations
from source_shape_utils import assert_in_source

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
INTEL_STATUS_JS = REPO_ROOT / "static" / "js" / "intel_status.js"
CORPUS_BOOTSTRAP_PY = REPO_ROOT / "corpus_bootstrap.py"


def test_classifier_maps_baked_sources_to_active_local_state() -> None:
    src = INTEL_STATUS_JS.read_text(encoding="utf-8")
    assert_in_source(src, "source === 'self_healed_baked'", label='src')
    assert_in_source(src, "return 'runtime_synced'", label='src')
    assert "case 'self_healed_baked'" not in src
    assert_in_source(src, "prebaked corpus", label='src')


def test_python_side_emits_self_healed_baked_again() -> None:
    src = CORPUS_BOOTSTRAP_PY.read_text(encoding="utf-8")
    assert_in_source(src, "self_healed_baked", label='src')
    assert_in_source(src, "prebaked corpus", label='src')


def test_template_documents_prebaked_panel() -> None:
    src = (REPO_ROOT / "templates" / "analyze.html").read_text(encoding="utf-8")
    assert_in_source(src, "Round 107 / Build 76", label='src')
    assert_in_source(src, "prebaked" in src and "first", label='src')
    assert "self_healed_baked" not in src
