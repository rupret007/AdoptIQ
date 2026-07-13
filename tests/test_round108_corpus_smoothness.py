"""Round 108 / Corpus Smoothness focused regressions."""

from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def test_round108_refresh_helper_skips_under_tests_without_override(monkeypatch):
    import app_simple as appm
    import corpus_bootstrap

    calls: list[dict[str, object]] = []

    def fake_refresh(*, rebuild=False):
        calls.append({"rebuild": rebuild})
        return True

    monkeypatch.setattr(corpus_bootstrap, "request_refresh", fake_refresh)
    monkeypatch.delenv("ADOPTIQ_ALLOW_TEST_CORPUS_REFRESH", raising=False)
    monkeypatch.setitem(appm.app.config, "TESTING", True)

    assert appm._r108_request_corpus_refresh_after_source_update("unit-test") is False
    assert calls == []


def test_round108_refresh_helper_requests_incremental_refresh_with_override(monkeypatch):
    import app_simple as appm
    import corpus_bootstrap

    calls: list[dict[str, object]] = []

    def fake_refresh(*, rebuild=False):
        calls.append({"rebuild": rebuild})
        return True

    monkeypatch.setattr(corpus_bootstrap, "request_refresh", fake_refresh)
    monkeypatch.setenv("ADOPTIQ_ALLOW_TEST_CORPUS_REFRESH", "1")
    monkeypatch.setitem(appm.app.config, "TESTING", True)

    assert appm._r108_request_corpus_refresh_after_source_update("unit-test") is True
    assert calls == [{"rebuild": False}]


def test_round108_event_refresh_call_sites_are_wired():
    src = (PROJECT_ROOT / "app_simple.py").read_text(encoding="utf-8")

    for marker in (
        '"generated_report_sidecar"',
        '"corpus_share_url_saved"',
        '"csone_onedrive_folder_saved"',
        '"report_outputs_folder_saved"',
    ):
        assert marker in src
    assert src.count("_r108_request_corpus_refresh_after_source_update(") >= 5


def test_round108_runtime_vector_upsert_is_skipped_under_pytest_by_default(monkeypatch):
    import corpus_bootstrap

    monkeypatch.setenv("PYTEST_CURRENT_TEST", "unit::test")
    monkeypatch.delenv("ADOPTIQ_ALLOW_TEST_RUNTIME_VECTORS", raising=False)
    assert corpus_bootstrap._r108_skip_runtime_vectors_under_pytest() is True

    monkeypatch.setenv("ADOPTIQ_ALLOW_TEST_RUNTIME_VECTORS", "1")
    assert corpus_bootstrap._r108_skip_runtime_vectors_under_pytest() is False
