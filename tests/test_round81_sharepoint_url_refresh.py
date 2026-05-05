"""Round 81 / Build 57: SharePoint share-URL refresh.

This file pins the canonical ``Config.ADOPTIQ_CORPUS_SHARE_URL``
to the post-R81 token ``e=kpHMgs`` (the share-token Brian and the
team now use).  Three contract assertions:

1.  The new token is the canonical default (no env override).
2.  A regression guard rejects any silent rollback to the pre-R81
    token ``e=O3a4Ij``.
3.  The ``ADOPTIQ_SHAREPOINT_FOLDER_URL`` back-compat alias still
    resolves to the same string (Round 35 contract preserved).

The path segment of the URL (``/personal/jestory_cisco_com/
Documents/AI%20Projects/AdoptIQ_CSOne_Reports``) is intentionally
NOT in scope for this round -- only the query-string share-token
rotates.  If a future round changes the path, that round MUST
update both this test and ``tests/test_round35_corpus_url_hardcoded.py``
together so the SSoT pin stays consistent.
"""

from __future__ import annotations

import importlib


_R81_NEW_TOKEN = "kpHMgs"
_PRE_R81_TOKEN = "O3a4Ij"

_EXPECTED_FULL_URL = (
    "https://cisco-my.sharepoint.com/:f:/r/personal/jestory_cisco_com/"
    "Documents/AI%20Projects/AdoptIQ_CSOne_Reports?csf=1&web=1&e=kpHMgs"
)


def _reload_config():
    import config as _cfg
    return importlib.reload(_cfg).Config


def test_round81_new_share_token_is_pinned_in_canonical_url(monkeypatch):
    """The post-R81 canonical default MUST carry the new
    ``e=kpHMgs`` share-token; if this fails, the URL constant has
    drifted from the round-81 contract."""
    monkeypatch.delenv("ADOPTIQ_CORPUS_SHARE_URL", raising=False)
    monkeypatch.delenv("ADOPTIQ_SHAREPOINT_FOLDER_URL", raising=False)
    cfg = _reload_config()
    assert cfg.ADOPTIQ_CORPUS_SHARE_URL.endswith(f"e={_R81_NEW_TOKEN}"), (
        f"Round 81 canonical URL MUST end with e={_R81_NEW_TOKEN}; got "
        f"{cfg.ADOPTIQ_CORPUS_SHARE_URL!r}"
    )
    assert cfg.ADOPTIQ_CORPUS_SHARE_URL == _EXPECTED_FULL_URL


def test_round81_regression_guard_against_old_token(monkeypatch):
    """Regression guard: the pre-R81 token ``e=O3a4Ij`` MUST NOT
    appear in the canonical default.  This catches any accidental
    cherry-pick / merge-conflict resolution that silently rolls
    the share-token back."""
    monkeypatch.delenv("ADOPTIQ_CORPUS_SHARE_URL", raising=False)
    monkeypatch.delenv("ADOPTIQ_SHAREPOINT_FOLDER_URL", raising=False)
    cfg = _reload_config()
    assert _PRE_R81_TOKEN not in cfg.ADOPTIQ_CORPUS_SHARE_URL, (
        f"Round 81 regression: pre-R81 share-token e={_PRE_R81_TOKEN} "
        f"is back in {cfg.ADOPTIQ_CORPUS_SHARE_URL!r}"
    )


def test_round81_back_compat_alias_resolves_to_canonical_url(monkeypatch):
    """Round 35 contract preserved: when no env override is set,
    ``ADOPTIQ_SHAREPOINT_FOLDER_URL`` MUST mirror the canonical
    ``ADOPTIQ_CORPUS_SHARE_URL`` byte-for-byte (so legacy
    consumers that still read the old name keep working)."""
    monkeypatch.delenv("ADOPTIQ_CORPUS_SHARE_URL", raising=False)
    monkeypatch.delenv("ADOPTIQ_SHAREPOINT_FOLDER_URL", raising=False)
    cfg = _reload_config()
    assert cfg.ADOPTIQ_SHAREPOINT_FOLDER_URL == cfg.ADOPTIQ_CORPUS_SHARE_URL
    assert cfg.ADOPTIQ_SHAREPOINT_FOLDER_URL.endswith(f"e={_R81_NEW_TOKEN}")
