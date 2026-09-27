"""Corpus links must identify a path, not just a query or fragment."""

import pytest

import adoptiq_settings as settings
import corpus_share_url_resolver as resolver


@pytest.mark.parametrize("suffix", ["?web=1", "#folder", "?web=1#folder"])
def test_share_url_requires_path_before_query_or_fragment(suffix, monkeypatch):
    invalid_url = "https://contoso.sharepoint.com/" + suffix
    fallback_url = "https://contoso.sharepoint.com/sites/team"

    assert not settings.is_valid_sharepoint_url(invalid_url)
    settings.save_settings({"corpus_share_url": invalid_url})
    assert not settings.load_settings().get("corpus_share_url")

    monkeypatch.setattr(settings, "load_settings", lambda: {"corpus_share_url": invalid_url})
    monkeypatch.setenv("ADOPTIQ_CORPUS_SHARE_URL", fallback_url)
    assert resolver.get_active_corpus_share_url() == (fallback_url, "env")


def test_share_url_rejects_trailing_newline():
    assert not settings.is_valid_sharepoint_url("https://contoso.sharepoint.com/sites/team\n")


@pytest.mark.parametrize("suffix", ["sites/team?web=1", "sites/team#folder", ":f:/s/team/abc?web=1"])
def test_share_url_preserves_real_paths(suffix):
    url = "https://contoso.sharepoint.com/" + suffix
    assert settings.is_valid_sharepoint_url(url)
    settings.save_settings({"corpus_share_url": url})
    assert settings.load_settings()["corpus_share_url"] == url
