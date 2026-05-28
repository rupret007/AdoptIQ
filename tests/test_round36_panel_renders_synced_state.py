"""Round 107 / Build 76: prebaked corpus panel source-shape pins."""

from __future__ import annotations

from pathlib import Path

import pytest


JS_PATH = Path(__file__).parent.parent / "static" / "js" / "intel_status.js"
JS_SRC = JS_PATH.read_text(encoding="utf-8")


def test_intel_status_js_exports_panel_classifier() -> None:
    assert "window.__adoptiqCorpusPanelState" in JS_SRC
    assert "classify: classifyCorpusPanel" in JS_SRC
    assert "label: corpusPanelLabel" in JS_SRC
    assert "pillClass: corpusPanelPillClass" in JS_SRC
    assert "detail: corpusPanelDetail" in JS_SRC


def test_intel_status_js_uses_onedrive_status_field_not_msal() -> None:
    assert "boot.onedrive_status" in JS_SRC
    forbidden = (
        "boot.signed_in",
        "boot.token_expires",
        "boot.sharepoint_account",
        "sharepoint.signed_in",
    )
    for needle in forbidden:
        assert needle not in JS_SRC


@pytest.mark.parametrize(
    "state",
    [
        "runtime_synced",
        "fresh_indexing",
        "fresh_not_synced",
        "refreshing",
        "refresh_failed",
        "signed_in_no_corpus",
        "blocked_no_onedrive",
        "unknown",
    ],
)
def test_panel_state_has_label_branch(state: str) -> None:
    if state == "unknown":
        assert "return 'unknown'" in JS_SRC
        assert "checking" in JS_SRC
        return
    assert f"return '{state}'" in JS_SRC
    assert f"case '{state}':" in JS_SRC


@pytest.mark.parametrize(
    "fragment",
    [
        "Active \\u2022 local corpus",
        "Indexing local corpus",
        "Local corpus pending",
        "Building local corpus",
        "Last refresh failed",
        "Add corpus share to OneDrive",
        "Optional OneDrive refresh",
        "OneDrive sync is optional",
    ],
)
def test_panel_label_and_detail_copy(fragment: str) -> None:
    assert fragment in JS_SRC


def test_old_snapshot_terms_are_removed_from_panel() -> None:
    forbidden = (
        "baked_synced",
        "baked_not_synced",
        "baked snapshot",
        "bundled snapshot",
        "Last bake",
    )
    for needle in forbidden:
        assert needle not in JS_SRC


def test_baked_sources_map_to_active_local_corpus() -> None:
    classifier_start = JS_SRC.find("function classifyCorpusPanel")
    classifier_end = JS_SRC.find("function corpusPanelLabel", classifier_start)
    assert classifier_start != -1 and classifier_end != -1
    classifier = JS_SRC[classifier_start:classifier_end]
    assert "source === 'baked'" in classifier
    assert "source === 'self_healed_baked'" in classifier
    assert "return 'runtime_synced'" in classifier
    baked_branch = classifier[classifier.find("source === 'self_healed_baked'"):]
    assert "return 'fresh_not_synced'" not in baked_branch.split("// Round 106", 1)[0]


def test_round108_no_onedrive_hard_blocker_copy_on_active_paths() -> None:
    """Round 108: OneDrive absence is optional refresh context."""
    forbidden = (
        "cannot create or open the local corpus",
        "no authorized local index to serve",
        "Sign in to OneDrive required",
        "OneDrive sync required.",
        "unlock requires the canonical",
    )
    for needle in forbidden:
        assert needle not in JS_SRC
    assert "OneDrive sync is optional" in JS_SRC
    assert "Dense retrieval is degraded; lexical fallback is active" in JS_SRC
