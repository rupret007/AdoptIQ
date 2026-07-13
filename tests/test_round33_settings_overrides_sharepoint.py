"""Round 33 / Build8 → Round 35 / native-corpus.

Build8 introduced a startup bridge in ``app_simple.py`` that mirrored
``settings.json``'s ``sharepoint_folder_url`` into
``Config.ADOPTIQ_SHAREPOINT_FOLDER_URL`` so per-user URLs survived
restarts without an env-var dance.  Round 35 retired that bridge
along with the user-facing URL paste UI -- the corpus URL is now
hardcoded in ``Config.ADOPTIQ_CORPUS_SHARE_URL`` (env-overridable
for ops only) and ``Config.ADOPTIQ_SHAREPOINT_FOLDER_URL`` is a
backward-compat alias of the same value.

These tests now pin the *removal* of the bridge:

* The R33 ``_r33_sp_url`` block is gone from ``app_simple.py``.
* ``Config.ADOPTIQ_CORPUS_SHARE_URL`` defaults to the canonical
  Cisco-internal share when no env override is set (the hardcoded
  URL the user explicitly accepted for the bake-time corpus
  source).  The legacy "must be empty" check from R33/Build8 no
  longer applies because R35 is intentionally hardcoded.
* ``Config.ADOPTIQ_SHAREPOINT_FOLDER_URL`` mirrors
  ``ADOPTIQ_CORPUS_SHARE_URL`` so callers in
  ``corpus_bootstrap.py`` (which still read the legacy attribute)
  keep working without code churn.
"""
from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent


def test_app_simple_no_longer_bridges_sharepoint_url_at_startup():
    """Round 35 retired the per-user URL paste UI.  The Build8 startup
    bridge is gone -- a regression that re-adds it would silently
    contradict the new "URL is hardcoded in Config" contract.
    """
    body = (REPO_ROOT / "app_simple.py").read_text(encoding="utf-8")
    assert "_r33_sp_url" not in body, (
        "Round 35 removed the Build8 startup bridge for "
        "settings.json sharepoint_folder_url; resurrecting "
        "_r33_sp_url would re-introduce the per-user URL surface."
    )
    assert "Config.ADOPTIQ_SHAREPOINT_FOLDER_URL = _r33_sp_url" not in body
    assert "isinstance(_r33_sp_url, str)" not in body


def test_config_corpus_share_url_default_is_canonical_cisco_share():
    """Round 35 contract: ``ADOPTIQ_CORPUS_SHARE_URL`` defaults to the
    Cisco-internal AdoptIQ_CSOne_Reports share when no env override
    is set.  This URL is intentional (the user accepted the operator-
    identity tradeoff in exchange for a turnkey native corpus).

    Round 81 / Build 57 refreshed the trailing share-token from
    ``e=O3a4Ij`` to ``e=kpHMgs``.

    Round 85 / Build 61 rotated to a new SharePoint share format:
    a ``:f:/p/`` guest-pass URL (no query string, no rotating ``e=...``
    token; the path segment IS the share token).  The canonical pin
    moves here in lockstep with ``tests/test_round35_corpus_url_hardcoded.py``,
    ``tests/test_round81_sharepoint_url_refresh.py``, and
    ``tests/test_round85_url_refresh_and_preferences_card.py``.
    """
    import os
    import importlib

    # Force a clean import so any prior monkeypatching does not
    # mask the default.
    if not os.environ.get("ADOPTIQ_CORPUS_SHARE_URL"):
        import config as _cfg
        cfg = importlib.reload(_cfg).Config
        expected = (
            "https://cisco-my.sharepoint.com/:f:/p/jestory/"
            "IgBm46pU_P9aTpkxyEQ13ZgYAT4BhGVKNfcUsN7DA7zkRJI"
        )
        assert cfg.ADOPTIQ_CORPUS_SHARE_URL == expected, (
            "Config.ADOPTIQ_CORPUS_SHARE_URL must default to the "
            "Round 85 guest-pass share URL when the env var is unset."
        )
        # And the legacy attribute mirrors the canonical value so
        # corpus_bootstrap consumers keep working without churn.
        assert cfg.ADOPTIQ_SHAREPOINT_FOLDER_URL == cfg.ADOPTIQ_CORPUS_SHARE_URL


def test_config_hardcoded_share_url_is_documented():
    """Defense-in-depth: when the canonical share URL appears as a
    string literal in ``config.py``, it MUST sit inside the
    ``ADOPTIQ_CORPUS_SHARE_URL`` definition (not in any other
    config field).  This catches a future engineer who copies the
    URL into a different field without realizing the operator-
    identity tradeoff applies only to the bake source.

    Round 85 / Build 61: marker rotated from the legacy
    ``jestory_cisco_com`` substring (no longer in the canonical URL,
    which is now a ``:f:/p/`` guest-pass) to the new URL's unique
    path-segment token ``IgBm46pU_P9aTpkxyEQ13ZgYAT4BhGVKNfcUsN7DA7zkRJI``.
    The token is base64-ish and has no semantic meaning outside the
    SharePoint share, so any future occurrence in the source tree
    would itself be a regression worth flagging.
    """
    import re

    # Round 85 marker -- unique base64-ish path token from the new
    # guest-pass URL.  When the next rotation lands, update this AND
    # the URL pins in tests/test_round35_corpus_url_hardcoded.py +
    # tests/test_round81_sharepoint_url_refresh.py +
    # tests/test_round85_url_refresh_and_preferences_card.py together.
    _R85_TOKEN = "IgBm46pU_P9aTpkxyEQ13ZgYAT4BhGVKNfcUsN7DA7zkRJI"

    body_lines = (REPO_ROOT / "config.py").read_text(encoding="utf-8").splitlines()
    hits: list[int] = []
    for idx, line in enumerate(body_lines):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if _R85_TOKEN not in stripped:
            continue
        # Only count quoted string literals (single OR double).
        # The pattern allows the token to appear adjacent to either
        # quote style, with no quote chars between the surrounding
        # quotes and the token itself.
        if re.search(r"['\"][^'\"]*" + re.escape(_R85_TOKEN) + r"[^'\"]*['\"]", stripped):
            hits.append(idx)
    assert hits, (
        "Round 85 expects the canonical share URL token to appear "
        "as a string literal in config.py (it is the hardcoded "
        "source of the AdoptIQ Knowledge Corpus)."
    )
    assert len(hits) == 1, (
        "Round 85 share URL token appeared in multiple non-comment "
        "string literals in config.py; only the "
        "ADOPTIQ_CORPUS_SHARE_URL default should carry it. Hits: "
        f"{[body_lines[i] for i in hits]}"
    )
    target_idx = hits[0]
    # ``ADOPTIQ_CORPUS_SHARE_URL`` must appear within 5 lines above.
    upper = max(0, target_idx - 5)
    upper_window = "\n".join(body_lines[upper:target_idx + 1])
    assert "ADOPTIQ_CORPUS_SHARE_URL" in upper_window, (
        "Hardcoded share URL must live inside the "
        "ADOPTIQ_CORPUS_SHARE_URL definition; found at line "
        f"{target_idx + 1} but no surrounding constant declaration."
    )
