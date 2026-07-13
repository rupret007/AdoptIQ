"""Round 84 / Build 60: GET / POST endpoint coverage for the
operator-flippable corpus share URL.

Coverage matrix (C2 in the R84 plan):

* GET ``/api/settings/corpus-share-url`` returns ``{ok, share_url,
  source, persisted_value, env_var, env_value_set}``.
* GET source label correctness (settings.json / env / config.py).
* POST ``/api/settings/corpus-share-url`` happy path (atomic write
  with mode ``0o600``, refreshes the active resolver result).
* POST validation rejection (bad scheme, oversize, non-sharepoint host)
  returns 400 + ``error: "invalid_share_url"``.
* POST empty string clears the override (settings.json key persisted
  as ``""`` -- resolver falls through to env / config).
* POST CSRF dual-auth (two paths: token in JSON header AND
  ``X-AdoptIQ-Internal``).
* POST non-string payload returns 400.

R83 contract preserved: this endpoint ONLY governs which SharePoint
share opens in the user's browser. The encryption / sentinel /
decrypt path is NOT touched -- a stolen DMG without OneDrive auth is
still useless ciphertext.
"""

# Round 84 / Build 60

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Section 1: GET endpoint
# ---------------------------------------------------------------------------


def test_r84_get_returns_resolver_tuple_and_keys(client, monkeypatch, tmp_path):
    """GET response shape: ``ok=True``, ``share_url``, ``source``,
    ``persisted_value``, ``env_var``, ``env_value_set``."""
    import adoptiq_settings as _s
    monkeypatch.setattr(_s, "_app_support_dir", lambda: tmp_path)
    monkeypatch.delenv("ADOPTIQ_CORPUS_SHARE_URL", raising=False)
    rv = client.get("/api/settings/corpus-share-url")
    assert rv.status_code == 200
    data = rv.get_json()
    assert data["ok"] is True
    # The hardcoded config default carries through when no override is set.
    assert data["share_url"]
    assert data["source"] == "config.py"
    assert data["persisted_value"] == ""
    assert data["env_var"] == "ADOPTIQ_CORPUS_SHARE_URL"
    assert data["env_value_set"] is False


def test_r84_get_source_settings_json_when_override_persisted(client, monkeypatch, tmp_path):
    """When settings.json carries a valid override, the source label
    MUST be ``settings.json`` -- the operator can see at a glance that
    their UI flip is active."""
    import adoptiq_settings as _s
    monkeypatch.setattr(_s, "_app_support_dir", lambda: tmp_path)
    monkeypatch.delenv("ADOPTIQ_CORPUS_SHARE_URL", raising=False)
    canonical = "https://contoso.sharepoint.com/sites/team?e=xyz"
    _s.save_settings({"corpus_share_url": canonical})
    rv = client.get("/api/settings/corpus-share-url")
    data = rv.get_json()
    assert data["ok"] is True
    assert data["share_url"] == canonical
    assert data["source"] == "settings.json"
    assert data["persisted_value"] == canonical


def test_r84_get_source_env_when_only_env_set(client, monkeypatch, tmp_path):
    """With settings.json absent but env set, the source label MUST
    be ``env``. The persisted_value remains empty so the UI can show
    that the value comes from outside settings.json."""
    import adoptiq_settings as _s
    monkeypatch.setattr(_s, "_app_support_dir", lambda: tmp_path)
    canonical = "https://cisco-my.sharepoint.com/personal/abc?e=env-token"
    monkeypatch.setenv("ADOPTIQ_CORPUS_SHARE_URL", canonical)
    rv = client.get("/api/settings/corpus-share-url")
    data = rv.get_json()
    assert data["ok"] is True
    assert data["share_url"] == canonical
    assert data["source"] == "env"
    assert data["persisted_value"] == ""
    assert data["env_value_set"] is True


# ---------------------------------------------------------------------------
# Section 2: POST endpoint - happy path + persistence
# ---------------------------------------------------------------------------


def test_r84_post_persists_valid_url_and_returns_active(client, monkeypatch, tmp_path):
    """A valid SharePoint URL POSTed to the endpoint MUST round-trip
    to settings.json AND be reflected in the response payload's
    ``share_url`` + ``source=settings.json``."""
    import adoptiq_settings as _s
    monkeypatch.setattr(_s, "_app_support_dir", lambda: tmp_path)
    canonical = "https://cisco-my.sharepoint.com/personal/jestory_cisco_com/Documents/AI%20Projects/AdoptIQ_CSOne_Reports?e=newtoken"
    rv = client.post(
        "/api/settings/corpus-share-url",
        json={"url": canonical},
    )
    assert rv.status_code == 200
    data = rv.get_json()
    assert data["ok"] is True
    assert data["share_url"] == canonical
    assert data["source"] == "settings.json"
    assert data["persisted_value"] == canonical
    on_disk = json.loads((tmp_path / "settings.json").read_text())
    assert on_disk["corpus_share_url"] == canonical


def test_r84_post_settings_json_written_with_mode_0o600(client, monkeypatch, tmp_path):
    """The settings.json file MUST land on disk with owner-only
    permissions (the same posture as the R32 / R69 keys). Mirrors
    the contract pinned by ``test_round32_settings_writes_with_0o600``."""
    import adoptiq_settings as _s
    monkeypatch.setattr(_s, "_app_support_dir", lambda: tmp_path)
    rv = client.post(
        "/api/settings/corpus-share-url",
        json={"url": "https://cisco-my.sharepoint.com/personal/x?e=t"},
    )
    assert rv.status_code == 200
    target = tmp_path / _s.SETTINGS_FILENAME
    assert target.exists()
    if os.name == "posix":
        # Windows ignores chmod; only assert on POSIX.
        mode = stat.S_IMODE(target.stat().st_mode)
        assert mode == 0o600, f"settings.json mode is {oct(mode)}; expected 0o600"


def test_r84_post_empty_string_clears_override(client, monkeypatch, tmp_path):
    """POST with ``{"url": ""}`` MUST clear the override -- the
    resolver then falls through to env / config so the source label
    flips back from ``settings.json`` to ``env`` or ``config.py``.

    The persisted value remains the empty string rather than
    deleting the key; that keeps the contract symmetric with the R69
    model_name endpoint (an empty string is the explicit "no
    override" sentinel)."""
    import adoptiq_settings as _s
    monkeypatch.setattr(_s, "_app_support_dir", lambda: tmp_path)
    monkeypatch.delenv("ADOPTIQ_CORPUS_SHARE_URL", raising=False)
    # Step 1: persist a real URL
    rv1 = client.post(
        "/api/settings/corpus-share-url",
        json={"url": "https://cisco-my.sharepoint.com/personal/x?e=t1"},
    )
    assert rv1.status_code == 200
    assert rv1.get_json()["source"] == "settings.json"
    # Step 2: clear it
    rv2 = client.post("/api/settings/corpus-share-url", json={"url": ""})
    assert rv2.status_code == 200
    data = rv2.get_json()
    assert data["ok"] is True
    assert data["persisted_value"] == ""
    # Resolver should now hit the config.py default.
    assert data["source"] == "config.py"
    assert data["share_url"]  # config default is still a valid URL


# ---------------------------------------------------------------------------
# Section 3: POST endpoint - validation rejection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_url,expected_kind", [
    ("http://cisco-my.sharepoint.com/foo", "scheme"),
    ("javascript:alert(1)", "scheme"),
    ("data:text/html,<script>alert(1)</script>", "scheme"),
    ("ftp://x.sharepoint.com/y", "scheme"),
    ("https://evil.example.com/foo", "host"),
    ("https://cisco-my.sharepoint.com", "path"),  # bare host w/o path
])
def test_r84_post_rejects_invalid_share_url_with_400(client, monkeypatch, tmp_path,
                                                      bad_url, expected_kind):
    """Validator gates the URL BEFORE write so a non-Cisco / non-https
    URL never lands in settings.json. ``expected_kind`` is just for
    test-failure ergonomics -- the endpoint returns the same
    ``invalid_share_url`` error code for every rejection."""
    import adoptiq_settings as _s
    monkeypatch.setattr(_s, "_app_support_dir", lambda: tmp_path)
    rv = client.post("/api/settings/corpus-share-url", json={"url": bad_url})
    assert rv.status_code == 400, (
        f"expected 400 for {bad_url!r} ({expected_kind}), got {rv.status_code}"
    )
    data = rv.get_json()
    assert data["ok"] is False
    assert data["error"] == "invalid_share_url"
    # The detail field MUST mention the canonical https + sharepoint
    # host requirements so the operator can fix their input.
    assert "https" in data.get("detail", "").lower()
    # And settings.json MUST NOT have been touched.
    target = tmp_path / "settings.json"
    if target.exists():
        on_disk = json.loads(target.read_text())
        assert on_disk.get("corpus_share_url") != bad_url


def test_r84_post_rejects_oversize_url_with_400(client, monkeypatch, tmp_path):
    """The 2048-byte cap mirrors ``_R83_SHARE_URL_MAX_BYTES``."""
    import adoptiq_settings as _s
    monkeypatch.setattr(_s, "_app_support_dir", lambda: tmp_path)
    base = "https://cisco-my.sharepoint.com/personal/abc?csf=1&"
    payload = base + ("k=v&" * 1000)
    assert len(payload) > 2048
    rv = client.post("/api/settings/corpus-share-url", json={"url": payload})
    assert rv.status_code == 400
    assert rv.get_json()["error"] == "invalid_share_url"


def test_r84_post_rejects_non_string_payload_with_400(client, monkeypatch, tmp_path):
    """A JSON payload with a non-string ``url`` field (int, list, dict)
    must be rejected at the type-check layer BEFORE reaching the
    validator."""
    import adoptiq_settings as _s
    monkeypatch.setattr(_s, "_app_support_dir", lambda: tmp_path)
    rv = client.post("/api/settings/corpus-share-url", json={"url": 123})
    assert rv.status_code == 400
    data = rv.get_json()
    assert data["ok"] is False
    assert data["error"] == "url_must_be_string"


def test_r84_post_handles_missing_url_field_as_empty(client, monkeypatch, tmp_path):
    """A POST without the ``url`` field is interpreted as "clear the
    override" (empty-string sentinel). This keeps the endpoint robust
    to a future client that posts an empty body."""
    import adoptiq_settings as _s
    monkeypatch.setattr(_s, "_app_support_dir", lambda: tmp_path)
    monkeypatch.delenv("ADOPTIQ_CORPUS_SHARE_URL", raising=False)
    rv = client.post("/api/settings/corpus-share-url", json={})
    assert rv.status_code == 200
    data = rv.get_json()
    assert data["ok"] is True
    assert data["persisted_value"] == ""


# ---------------------------------------------------------------------------
# Section 4: CSRF dual-auth contract
# ---------------------------------------------------------------------------


def test_r84_post_rejects_unauthenticated_when_csrf_enabled(monkeypatch, tmp_path):
    """When CSRF is ENABLED (the production posture), a POST without
    a valid CSRF token AND without ``X-AdoptIQ-Internal`` MUST be
    rejected with 403. The dual-auth contract mirrors
    ``/api/corpus/refresh`` and ``/api/settings/intelligence``."""
    from app_simple import app as flask_app
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = True
    monkeypatch.delenv("ADOPTIQ_INTERNAL_TOKEN", raising=False)
    import adoptiq_settings as _s
    monkeypatch.setattr(_s, "_app_support_dir", lambda: tmp_path)
    try:
        with flask_app.test_client() as c:
            rv = c.post(
                "/api/settings/corpus-share-url",
                json={"url": "https://cisco-my.sharepoint.com/x?e=t"},
            )
            assert rv.status_code == 403
            data = rv.get_json()
            assert data["ok"] is False
    finally:
        flask_app.config["WTF_CSRF_ENABLED"] = False


def test_r84_post_accepts_x_adoptiq_internal_header_when_token_set(monkeypatch, tmp_path):
    """The internal-token path: when ``ADOPTIQ_INTERNAL_TOKEN`` is set
    AND the request carries the matching header, CSRF is bypassed.
    This is the path the admin app proxy uses."""
    from app_simple import app as flask_app
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = True
    monkeypatch.setenv("ADOPTIQ_INTERNAL_TOKEN", "test-internal-token")
    import adoptiq_settings as _s
    monkeypatch.setattr(_s, "_app_support_dir", lambda: tmp_path)
    try:
        with flask_app.test_client() as c:
            rv = c.post(
                "/api/settings/corpus-share-url",
                json={"url": "https://cisco-my.sharepoint.com/personal/x?e=t"},
                headers={"X-AdoptIQ-Internal": "test-internal-token"},
            )
            assert rv.status_code == 200
            assert rv.get_json()["ok"] is True
    finally:
        flask_app.config["WTF_CSRF_ENABLED"] = False


def test_r84_post_rejects_wrong_internal_token(monkeypatch, tmp_path):
    """A wrong ``X-AdoptIQ-Internal`` header MUST be rejected with
    constant-time comparison (``secrets.compare_digest``) so a
    timing oracle cannot leak the configured token."""
    from app_simple import app as flask_app
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = True
    monkeypatch.setenv("ADOPTIQ_INTERNAL_TOKEN", "test-internal-token")
    import adoptiq_settings as _s
    monkeypatch.setattr(_s, "_app_support_dir", lambda: tmp_path)
    try:
        with flask_app.test_client() as c:
            rv = c.post(
                "/api/settings/corpus-share-url",
                json={"url": "https://cisco-my.sharepoint.com/x?e=t"},
                headers={"X-AdoptIQ-Internal": "wrong-token"},
            )
            assert rv.status_code == 403
    finally:
        flask_app.config["WTF_CSRF_ENABLED"] = False


# ---------------------------------------------------------------------------
# Section 5: route registration + sensitive-endpoint protection
# ---------------------------------------------------------------------------


def test_r84_endpoint_route_is_registered():
    """Defensive: the route must register on app boot."""
    from app_simple import app as flask_app
    rules = {r.rule for r in flask_app.url_map.iter_rules()}
    assert "/api/settings/corpus-share-url" in rules


def test_r84_endpoint_listed_in_sensitive_endpoints_set():
    """Round 13 / Phase 4.1 sensitive-endpoints contract: the new
    endpoint must be loopback-restricted by default (the same posture
    as ``api_settings_intelligence`` / ``api_settings_ask_ai_model``).
    Source-shape grep on app_simple.py."""
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "app_simple.py").read_text()
    assert "'api_settings_corpus_share_url'" in src, (
        "api_settings_corpus_share_url must be listed in _SENSITIVE_ENDPOINTS"
    )
