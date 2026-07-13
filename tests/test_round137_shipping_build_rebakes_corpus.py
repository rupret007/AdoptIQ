"""Round 137: shipping Mac builds must rebake corpus via build_mac_dmg.sh."""

from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_MAC_BUILD_INSTRUCTIONS = _REPO_ROOT / "CURSOR_MAC_BUILD_INSTRUCTIONS.md"
_BUILD_MAC_DMG = _REPO_ROOT / "build_mac_dmg.sh"
_BRANCH_WORKFLOW = _REPO_ROOT / "BRANCH_WORKFLOW.md"


def test_mac_build_instructions_require_dmg_rebake_for_shipping() -> None:
    text = _MAC_BUILD_INSTRUCTIONS.read_text(encoding="utf-8")
    assert "every release rebakes the corpus" in text.lower() or "Round 137" in text
    assert "Do **not** ship from `./build_mac.sh` alone" in text
    assert "ADOPTIQ_RELEASE_GATE=1" in text
    assert "ADOPTIQ_BAKE_CORPUS=0" in text
    assert "incompatible" in text.lower()


def test_branch_workflow_mac_shipping_uses_build_mac_dmg() -> None:
    text = _BRANCH_WORKFLOW.read_text(encoding="utf-8")
    assert "build_mac_dmg.sh" in text
    assert "rebake corpus" in text.lower() or "rebake" in text.lower()
    assert "build_mac.sh` alone" in text or "build_mac.sh alone" in text


def test_build_mac_dmg_runs_bake_before_build_mac_sh() -> None:
    text = _BUILD_MAC_DMG.read_text(encoding="utf-8")
    bake_pos = text.find("bake_corpus.py")
    build_mac_pos = text.find("./build_mac.sh")
    assert bake_pos != -1, "build_mac_dmg.sh must invoke bake_corpus.py"
    assert build_mac_pos != -1, "build_mac_dmg.sh must invoke build_mac.sh"
    assert bake_pos < build_mac_pos, "corpus bake must run before PyInstaller build"
