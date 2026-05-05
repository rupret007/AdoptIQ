"""Round 84 / Build 60: settings.json schema + validator coverage for
the operator-flippable corpus share URL.

Coverage matrix (C1 in the R84 plan):

* Allow-list: ``"corpus_share_url" in _SCHEMA`` and exposed by
  ``schema_keys()``.
* Validator wiring: ``_VALIDATORS["corpus_share_url"]`` is the
  canonical ``_is_valid_sharepoint_url`` -- the same validator that
  vetted the retired Round 33 / Build8 ``sharepoint_folder_url`` key.
* Validator behavior (parametrised): accepts canonical Cisco
  SharePoint URLs and the empty-string sentinel; rejects ``http://``,
  ``javascript:``, ``data:``, oversize values, and non-sharepoint
  hosts.
* Round-trip: ``save_settings`` -> ``load_settings`` preserves a valid
  URL and the empty-string sentinel; a hand-edited file with an
  invalid value is silently dropped on save.

The R83 OneDrive-auth-as-access-gate contract is preserved: this key
ONLY governs which SharePoint share opens in the user's browser when
they click the bootstrap-shortcut button. The encryption / sentinel /
decrypt path is NOT touched -- a stolen DMG without OneDrive auth is
still useless ciphertext (audited by ``corpus_bootstrap._run_index_pass``
calling ``open_corpus_for_user(..., allow_local_sentinel=False)``).
"""

# Round 84 / Build 60

from __future__ import annotations

import json
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Section 1: schema allow-list + validator wiring
# ---------------------------------------------------------------------------


def test_r84_corpus_share_url_key_in_schema():
    """The new operator-flippable key MUST be allow-listed.

    Pre-R84 the analyze-page panel had no operator-set URL: the
    bootstrap-shortcut endpoint read directly from
    ``Config.ADOPTIQ_CORPUS_SHARE_URL`` so rotating the share token
    required a DMG rebuild. R84 introduces this allow-listed key as
    the highest-precedence tier in
    ``corpus_share_url_resolver.get_active_corpus_share_url``.
    """
    import adoptiq_settings as _s
    assert "corpus_share_url" in _s._SCHEMA
    assert "corpus_share_url" in _s.schema_keys()


def test_r84_corpus_share_url_schema_default_is_empty_string():
    """Empty string is the canonical "unset" sentinel and means "fall
    through to env then config default" in the resolver."""
    import adoptiq_settings as _s
    coercer, default = _s._SCHEMA["corpus_share_url"]
    assert coercer is str
    assert default == ""


def test_r84_corpus_share_url_validator_wired_in_validators_dict():
    """``_VALIDATORS["corpus_share_url"]`` MUST point at the canonical
    SharePoint URL allow-list (already used pre-R84 by the retired
    Round 33 / Build8 ``sharepoint_folder_url`` key + the bake script's
    env-override sanity check)."""
    import adoptiq_settings as _s
    assert "corpus_share_url" in _s._VALIDATORS
    assert _s._VALIDATORS["corpus_share_url"] is _s._is_valid_sharepoint_url


def test_r84_is_valid_sharepoint_url_public_alias_returns_same_truth():
    """Defense-in-depth: external callers (the bake script, future
    admin UIs) use the public alias; it MUST agree with the private
    validator on every input."""
    import adoptiq_settings as _s
    for v in (
        "",
        None,
        "https://cisco-my.sharepoint.com/foo",
        "http://cisco-my.sharepoint.com/foo",
        "ftp://x.sharepoint.com/y",
        "x" * 5000,
    ):
        assert _s.is_valid_sharepoint_url(v) == _s._is_valid_sharepoint_url(v), (
            f"public alias and private validator disagree on {v!r}"
        )


# ---------------------------------------------------------------------------
# Section 2: validator behaviour (allow-list contract)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("good", [
    # Canonical Cisco share URL with R83 share-token shape
    "https://cisco-my.sharepoint.com/:f:/r/personal/jestory_cisco_com/Documents/AI%20Projects/AdoptIQ_CSOne_Reports?csf=1&web=1&e=kpHMgs",
    # Other sharepoint tenant + minimal path
    "https://contoso.sharepoint.com/sites/team",
    # Mixed-case tenant label (DNS labels are case-insensitive)
    "https://CISCO-MY.sharepoint.com/personal/abc",
])
def test_r84_corpus_share_url_validator_accepts_canonical_urls(good):
    """Real-world Cisco SharePoint share URLs must pass the allow-list
    so the operator can paste any tenant variant into the analyze-page
    card."""
    import adoptiq_settings as _s
    assert _s._is_valid_sharepoint_url(good) is True, (
        f"validator rejected canonical URL {good!r}"
    )


def test_r84_corpus_share_url_validator_accepts_empty_sentinel():
    """Empty string == "no operator override -- fall through to env then
    config default"."""
    import adoptiq_settings as _s
    assert _s._is_valid_sharepoint_url("") is True
    assert _s._is_valid_sharepoint_url(None) is True


@pytest.mark.parametrize("bad", [
    # Non-https schemes - the validator gates the URL against MITM
    # and against open-redirect / XSS / file:// risks.
    "http://cisco-my.sharepoint.com/foo",
    "ftp://cisco-my.sharepoint.com/foo",
    "javascript:alert(1)",
    "data:text/html,<script>alert(1)</script>",
    "file:///etc/passwd",
    "ms-onedrive://cisco-my.sharepoint.com/foo",
])
def test_r84_corpus_share_url_validator_rejects_non_https_schemes(bad):
    """Defense-in-depth: anything other than ``https://`` must be
    rejected so the bootstrap-shortcut endpoint cannot be tricked into
    handing a non-https URL to ``window.open``."""
    import adoptiq_settings as _s
    assert _s._is_valid_sharepoint_url(bad) is False, (
        f"validator accepted non-https URL {bad!r}"
    )


@pytest.mark.parametrize("bad", [
    # Non-sharepoint hosts - the URL MUST point at a Cisco-tenant
    # SharePoint share so the share ACL governs access.
    "https://evil.example.com/foo",
    "https://attacker.com/sharepoint.com/foo",
    "https://sharepoint.com.evil/foo",
    # Bare host without path (validator requires a path component
    # because callers always need a folder reference).
    "https://cisco-my.sharepoint.com",
    "https://cisco-my.sharepoint.com/",  # path is just slash w/o body chars after slash
])
def test_r84_corpus_share_url_validator_rejects_non_sharepoint_hosts(bad):
    """The host MUST be ``<tenant>.sharepoint.com`` so a typo (or a
    malicious paste) cannot land an arbitrary URL on disk."""
    import adoptiq_settings as _s
    assert _s._is_valid_sharepoint_url(bad) is False, (
        f"validator accepted non-sharepoint URL {bad!r}"
    )


def test_r84_corpus_share_url_validator_rejects_oversize_values():
    """The 2048-byte cap mirrors ``_R83_SHARE_URL_MAX_BYTES`` so a
    runaway query string cannot bloat ``settings.json``."""
    import adoptiq_settings as _s
    base = "https://cisco-my.sharepoint.com/personal/abc?csf=1&"
    payload = base + ("k=v&" * 1000)
    assert len(payload) > 2048
    assert _s._is_valid_sharepoint_url(payload) is False


def test_r84_corpus_share_url_validator_rejects_non_string_input():
    """Non-string types (``int`` / ``list`` / ``dict``) must be rejected
    so a malformed JSON payload never lands on disk."""
    import adoptiq_settings as _s
    for bad in (123, 1.5, [], {}, object()):
        assert _s._is_valid_sharepoint_url(bad) is False


# ---------------------------------------------------------------------------
# Section 3: round-trip (save -> load) preserves valid + empty values
# ---------------------------------------------------------------------------


def test_r84_settings_save_roundtrip_preserves_valid_url(tmp_path, monkeypatch):
    """A canonical SharePoint URL written via ``save_settings`` MUST
    round-trip through ``load_settings`` byte-identically."""
    import adoptiq_settings as _s
    monkeypatch.setattr(_s, "_app_support_dir", lambda: tmp_path)
    canonical = "https://cisco-my.sharepoint.com/:f:/r/personal/jestory_cisco_com/Documents/AI%20Projects/AdoptIQ_CSOne_Reports?csf=1&web=1&e=kpHMgs"
    _s.save_settings({"corpus_share_url": canonical})
    loaded = _s.load_settings()
    assert loaded.get("corpus_share_url") == canonical


def test_r84_settings_save_drops_invalid_url_silently(tmp_path, monkeypatch):
    """A hand-edited settings.json (or a future schema bug) that
    surfaces a malformed URL on save MUST be silently dropped --
    the defense-in-depth allow-list at the validator layer is the
    last gate before disk."""
    import adoptiq_settings as _s
    monkeypatch.setattr(_s, "_app_support_dir", lambda: tmp_path)
    out_path = _s.save_settings({
        "corpus_share_url": "javascript:alert(1)",
        "ask_ai_model_name": "gpt-5-nano",
    })
    on_disk = json.loads(Path(out_path).read_text())
    assert "corpus_share_url" not in on_disk, (
        "malformed URL must NOT land on disk"
    )
    # Other valid keys must round-trip.
    assert on_disk.get("ask_ai_model_name") == "gpt-5-nano"


def test_r84_settings_save_preserves_empty_string_as_unset_sentinel(tmp_path, monkeypatch):
    """Empty string is the canonical "unset" sentinel. The schema
    validator accepts it, ``save_settings`` writes it (so a future
    explicit "no override" state is recoverable), and
    ``load_settings`` returns it. The resolver treats this same as a
    missing key (both fall through to env)."""
    import adoptiq_settings as _s
    monkeypatch.setattr(_s, "_app_support_dir", lambda: tmp_path)
    out_path = _s.save_settings({"corpus_share_url": ""})
    on_disk = json.loads(Path(out_path).read_text())
    assert on_disk.get("corpus_share_url") == ""
    loaded = _s.load_settings()
    assert loaded.get("corpus_share_url") == ""


def test_r84_settings_load_skips_corpus_share_url_when_absent(tmp_path, monkeypatch):
    """A settings.json that pre-dates R84 (no ``corpus_share_url`` key)
    must continue to load cleanly -- the resolver simply falls through
    to the env / config layers."""
    import adoptiq_settings as _s
    monkeypatch.setattr(_s, "_app_support_dir", lambda: tmp_path)
    _s.save_settings({"ask_ai_model_name": "gpt-5-nano"})
    loaded = _s.load_settings()
    assert "corpus_share_url" not in loaded
    assert loaded.get("ask_ai_model_name") == "gpt-5-nano"


def test_r84_settings_load_drops_corpus_share_url_with_invalid_value(tmp_path, monkeypatch):
    """If a hand-edited settings.json already has a bad URL on disk,
    ``load_settings`` MUST drop it on read so the resolver sees the
    same "absent key" state as a clean install (and falls through to
    env / config). Mirrors the R69 ``_is_valid_model_name`` behaviour
    for malformed model_name on disk."""
    import adoptiq_settings as _s
    monkeypatch.setattr(_s, "_app_support_dir", lambda: tmp_path)
    target = tmp_path / _s.SETTINGS_FILENAME
    tmp_path.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({"corpus_share_url": "javascript:alert(1)"}))
    loaded = _s.load_settings()
    assert "corpus_share_url" not in loaded
