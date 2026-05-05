"""Round 81 / Build 57 -> Round 85 / Build 61: SharePoint share-URL refresh.

Filename retained on disk so ``git blame`` / ``git log -- <path>``
preserves the full rotation history (R35 default -> R81 token refresh
-> R85 URL-shape rotation).  The test functions are renamed to
``round85_*`` to reflect the round responsible for the current
contract; the legacy ``round81_*`` names are gone (no silent
deprecation -- pytest's collector would still find them).

Round 85 contract assertions:

1.  The new URL is the canonical default when no env override is set.
2.  A regression guard rejects every legacy URL fragment we have ever
    shipped: ``e=O3a4Ij`` (R35 default), ``e=kpHMgs`` (R81 refresh),
    ``/personal/jestory_cisco_com`` (legacy render-link path), and
    ``csf=1`` (legacy render-link query parameter).  This catches any
    cherry-pick / merge-conflict resolution that silently rolls back
    to either of the older URL shapes.
3.  The ``ADOPTIQ_SHAREPOINT_FOLDER_URL`` back-compat alias still
    mirrors the canonical ``ADOPTIQ_CORPUS_SHARE_URL`` byte-for-byte
    (Round 35 alias contract preserved).
4.  The new URL has the structural properties of a ``:f:/p/`` guest-
    pass: it contains ``:/p/``, does NOT contain ``:/r/personal``,
    and does NOT carry a query string (no ``?``).

Note: future rotations require a new path entirely (the path itself
is the share token in a guest-pass URL); a token-only refresh helper
is not applicable to this URL shape.  When the next rotation lands,
update the ``_EXPECTED_FULL_URL`` constant AND the matching constant
in ``tests/test_round35_corpus_url_hardcoded.py`` together.
"""

from __future__ import annotations

import importlib


# Legacy fragments that MUST NOT reappear in the canonical default.
# Each entry is a substring; the test fails if any of them is found
# inside ``Config.ADOPTIQ_CORPUS_SHARE_URL``.
_LEGACY_FRAGMENTS = (
    "e=O3a4Ij",                       # R35 original token
    "e=kpHMgs",                       # R81 refreshed token
    "/personal/jestory_cisco_com",    # legacy render-link path
    "csf=1",                          # legacy render-link query param
    ":/r/",                           # legacy render-link route prefix
)

_EXPECTED_FULL_URL = (
    "https://cisco-my.sharepoint.com/:f:/p/jestory/"
    "IgBm46pU_P9aTpkxyEQ13ZgYAT4BhGVKNfcUsN7DA7zkRJI"
)


def _reload_config():
    import config as _cfg
    return importlib.reload(_cfg).Config


def test_round85_new_share_url_is_pinned_in_canonical_url(monkeypatch):
    """The post-R85 canonical default MUST be the new ``:f:/p/``
    guest-pass URL byte-for-byte.  If this fails, the URL constant
    has drifted from the round-85 contract (e.g. someone tried to
    update only the test or only the config without the other)."""
    monkeypatch.delenv("ADOPTIQ_CORPUS_SHARE_URL", raising=False)
    monkeypatch.delenv("ADOPTIQ_SHAREPOINT_FOLDER_URL", raising=False)
    cfg = _reload_config()
    assert cfg.ADOPTIQ_CORPUS_SHARE_URL == _EXPECTED_FULL_URL, (
        f"Round 85 canonical URL drifted: expected\n  "
        f"{_EXPECTED_FULL_URL!r}\ngot\n  {cfg.ADOPTIQ_CORPUS_SHARE_URL!r}"
    )


def test_round85_regression_guard_against_legacy_url_shapes(monkeypatch):
    """Round 85 regression guard: NONE of the legacy URL fragments
    we have ever shipped (R35 token, R81 token, legacy path, legacy
    query string, legacy route prefix) may appear in the canonical
    default.  This is the structural defense against silent rollback
    from a future merge / cherry-pick."""
    monkeypatch.delenv("ADOPTIQ_CORPUS_SHARE_URL", raising=False)
    monkeypatch.delenv("ADOPTIQ_SHAREPOINT_FOLDER_URL", raising=False)
    cfg = _reload_config()
    for fragment in _LEGACY_FRAGMENTS:
        assert fragment not in cfg.ADOPTIQ_CORPUS_SHARE_URL, (
            f"Round 85 regression: legacy URL fragment {fragment!r} is "
            f"back in {cfg.ADOPTIQ_CORPUS_SHARE_URL!r}"
        )


def test_round85_back_compat_alias_resolves_to_canonical_url(monkeypatch):
    """Round 35 alias contract preserved: when no env override is set,
    ``ADOPTIQ_SHAREPOINT_FOLDER_URL`` MUST mirror the canonical
    ``ADOPTIQ_CORPUS_SHARE_URL`` byte-for-byte (so legacy consumers
    that still read the old name keep working)."""
    monkeypatch.delenv("ADOPTIQ_CORPUS_SHARE_URL", raising=False)
    monkeypatch.delenv("ADOPTIQ_SHAREPOINT_FOLDER_URL", raising=False)
    cfg = _reload_config()
    assert cfg.ADOPTIQ_SHAREPOINT_FOLDER_URL == cfg.ADOPTIQ_CORPUS_SHARE_URL
    assert cfg.ADOPTIQ_SHAREPOINT_FOLDER_URL == _EXPECTED_FULL_URL


def test_round85_new_url_is_guest_pass_shape(monkeypatch):
    """Structural assertion: the new URL is a ``:f:/p/`` guest-pass
    (path-segment-as-token), NOT a ``:/r/personal/...`` render link
    with a rotating query-string token.  Pinning the SHAPE here
    means a future rotation that drops back to the render-link shape
    will fail this test loudly instead of silently changing the
    threat-model surface (guest-pass URLs are bearer-shareable; the
    render-link shape requires recipient SSO via the SharePoint
    site)."""
    monkeypatch.delenv("ADOPTIQ_CORPUS_SHARE_URL", raising=False)
    monkeypatch.delenv("ADOPTIQ_SHAREPOINT_FOLDER_URL", raising=False)
    cfg = _reload_config()
    url = cfg.ADOPTIQ_CORPUS_SHARE_URL
    assert ":/p/" in url, (
        f"Round 85: URL must be a :f:/p/ guest-pass shape; got {url!r}"
    )
    assert ":/r/personal" not in url, (
        f"Round 85: URL must NOT carry the legacy render-link path; "
        f"got {url!r}"
    )
    assert "?" not in url, (
        f"Round 85: guest-pass URLs carry no query string; got {url!r}"
    )
