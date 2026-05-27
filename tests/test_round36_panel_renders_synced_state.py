"""Round 96 / Build 69: runtime-only corpus panel source-shape pins."""

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
        "Sign in to OneDrive",
        "OneDrive sync is optional",
    ],
)
def test_panel_label_and_detail_copy(fragment: str) -> None:
    assert fragment in JS_SRC


def test_baked_snapshot_copy_is_removed_from_panel() -> None:
    forbidden = (
        "baked_synced",
        "baked_not_synced",
        "baked snapshot",
        "bundled snapshot",
        "Last bake",
    )
    for needle in forbidden:
        assert needle not in JS_SRC


def test_legacy_baked_sources_map_without_baked_ui_copy() -> None:
    classifier_start = JS_SRC.find("function classifyCorpusPanel")
    classifier_end = JS_SRC.find("function corpusPanelLabel", classifier_start)
    assert classifier_start != -1 and classifier_end != -1
    classifier = JS_SRC[classifier_start:classifier_end]
    assert "source === 'baked'" in classifier
    assert "source === 'self_healed_baked'" in classifier
    assert "return 'runtime_synced'" in classifier
    assert "return 'fresh_not_synced'" in classifier
