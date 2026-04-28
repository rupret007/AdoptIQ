"""Round 35 / native-corpus: pin the SharePoint share-link encoder.

Microsoft Graph documents that a SharePoint sharing link must be
turned into a share id via:

  1. base64-url-encode the full URL bytes (incl. query string)
  2. drop the trailing ``=`` padding
  3. prepend ``u!``

The encoder is the doorway between the hardcoded
``Config.ADOPTIQ_CORPUS_SHARE_URL`` and every Graph round-trip the
bake script and runtime refresh perform, so we lock down both the
positive (well-known input → expected output) and negative
(non-https / non-sharepoint hosts must raise) behavior here.
"""

from __future__ import annotations

import base64

import pytest

from sharepoint_corpus_source import (
    _encode_share_url_for_graph,
    encode_share_url,
)


# ---------------------------------------------------------------------------
# Positive cases -- pin the encoding bit-for-bit so a future refactor
# (e.g. swapping base64 modules) cannot silently break Graph fetches.
# ---------------------------------------------------------------------------


def _hand_encode(url: str) -> str:
    """Mirror Microsoft's documented algorithm independently of the
    module under test, so the assertion compares two implementations
    rather than asserting against a magic string."""
    raw = base64.urlsafe_b64encode(url.encode("utf-8")).decode("ascii")
    return "u!" + raw.rstrip("=")


def test_encoder_matches_documented_algorithm_simple():
    url = "https://contoso.sharepoint.com/sites/Marketing/Shared%20Documents"
    assert _encode_share_url_for_graph(url) == _hand_encode(url)


def test_encoder_matches_documented_algorithm_user_share_with_query():
    # The exact share URL that ships in Config.ADOPTIQ_CORPUS_SHARE_URL.
    url = (
        "https://cisco-my.sharepoint.com/:f:/r/personal/jestory_cisco_com/"
        "Documents/AI%20Projects/AdoptIQ_CSOne_Reports?csf=1&web=1&e=O3a4Ij"
    )
    assert _encode_share_url_for_graph(url) == _hand_encode(url)


def test_encoder_includes_the_e_query_param():
    # The ``e=`` param is the share token; encoder must NOT strip it
    # or Graph will return 404 on anonymous-link shares.
    url = (
        "https://cisco-my.sharepoint.com/:f:/r/personal/x_cisco_com/Documents/"
        "Folder?csf=1&web=1&e=AbCd1234"
    )
    encoded = _encode_share_url_for_graph(url)
    decoded = base64.urlsafe_b64decode(
        encoded[len("u!"):] + "=" * (-len(encoded[len("u!"):]) % 4)
    ).decode("utf-8")
    assert "e=AbCd1234" in decoded


def test_encoder_starts_with_u_bang():
    url = "https://contoso.sharepoint.com/Shared/Foo"
    assert _encode_share_url_for_graph(url).startswith("u!")


def test_encoder_strips_padding():
    # Choose a URL whose base64 encoding has trailing ``=``.
    url = "https://contoso.sharepoint.com/a"
    encoded = _encode_share_url_for_graph(url)
    assert "=" not in encoded


def test_encoder_accepts_apex_sharepoint_com():
    url = "https://sharepoint.com/legacy/share"
    # Must not raise; must produce a u!-prefixed token.
    out = _encode_share_url_for_graph(url)
    assert out.startswith("u!")


def test_encoder_passthrough_matches_legacy_helper():
    # The hardened wrapper and the legacy ``encode_share_url`` should
    # return identical tokens for a valid SharePoint URL -- callers
    # that migrated to the new name MUST observe no behavior change.
    url = "https://contoso.sharepoint.com/sites/X/Documents"
    assert _encode_share_url_for_graph(url) == encode_share_url(url)


# ---------------------------------------------------------------------------
# Negative cases -- the hardened wrapper exists specifically to refuse
# non-SharePoint and non-https inputs before they hit Graph.  Each
# raise here closes a redirection vector that an
# ``ADOPTIQ_CORPUS_SHARE_URL`` env-override could otherwise open.
# ---------------------------------------------------------------------------


def test_encoder_rejects_http_scheme():
    with pytest.raises(ValueError, match="https"):
        _encode_share_url_for_graph(
            "http://contoso.sharepoint.com/Shared/Foo"
        )


def test_encoder_rejects_file_scheme():
    with pytest.raises(ValueError, match="https"):
        _encode_share_url_for_graph(
            "file:///etc/passwd"
        )


def test_encoder_rejects_gopher_scheme():
    with pytest.raises(ValueError, match="https"):
        _encode_share_url_for_graph("gopher://example.com/")


def test_encoder_rejects_non_sharepoint_host():
    with pytest.raises(ValueError, match="sharepoint"):
        _encode_share_url_for_graph(
            "https://attacker.example.com/path"
        )


def test_encoder_rejects_subdomain_spoof():
    # ``foo.sharepoint.com.attacker.tld`` ends in ``.tld``, not in
    # ``.sharepoint.com``, so the suffix check must reject it.
    with pytest.raises(ValueError, match="sharepoint"):
        _encode_share_url_for_graph(
            "https://contoso.sharepoint.com.attacker.tld/Shared/Foo"
        )


def test_encoder_rejects_bare_host():
    # Allow-list requires a path component -- a bare host has no
    # folder context and Graph would refuse anyway.
    with pytest.raises(ValueError, match="path"):
        _encode_share_url_for_graph(
            "https://contoso.sharepoint.com"
        )


def test_encoder_rejects_root_only_path():
    with pytest.raises(ValueError, match="path"):
        _encode_share_url_for_graph(
            "https://contoso.sharepoint.com/"
        )


def test_encoder_rejects_empty_string():
    with pytest.raises(ValueError, match="empty"):
        _encode_share_url_for_graph("")


def test_encoder_rejects_none():
    with pytest.raises(ValueError, match="None"):
        _encode_share_url_for_graph(None)  # type: ignore[arg-type]


def test_encoder_rejects_whitespace_only():
    with pytest.raises(ValueError, match="empty"):
        _encode_share_url_for_graph("   ")


def test_encoder_case_insensitive_host_match():
    # Mixed-case host should still match the suffix allow-list.
    url = "https://Contoso.SharePoint.COM/Shared/Foo"
    out = _encode_share_url_for_graph(url)
    assert out.startswith("u!")
