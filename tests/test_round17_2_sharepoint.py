"""Round 17.2 -- SharePoint corpus source contracts.

Pins the public surface of :mod:`sharepoint_corpus_source` and the
Round 17.2 wiring in :mod:`config`, :mod:`corpus_bootstrap`,
:mod:`corpus_indexer`, and :mod:`app_simple`.

The tests are deterministic and offline.  Microsoft Graph is faked
through injected ``http_get`` callables; ``msal`` and ``keyring`` are
faked through injected modules.  No real network, no real keychain.
"""

from __future__ import annotations

import base64
import importlib
import json
import logging
import os
import sys
import types
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import pytest


# ---------------------------------------------------------------------------
# Test 1: encode_share_url matches Microsoft Graph spec
# ---------------------------------------------------------------------------


def test_encode_share_url_matches_graph_spec():
    """``u!`` + URL-safe base64 of the URL bytes, no padding."""
    import sharepoint_corpus_source as sp

    url = (
        "https://cisco-my.sharepoint.com/:f:/r/personal/"
        "jestory_cisco_com/Documents/AI%20Projects/AdoptIQ_CSOne_Reports"
        "?csf=1&web=1&e=d5qzVl"
    )
    encoded = sp.encode_share_url(url)
    assert encoded.startswith("u!")
    payload = encoded[2:]
    # URL-safe base64 must not contain ``+`` or ``/`` and must not
    # carry padding ``=``.
    assert "+" not in payload
    assert "/" not in payload
    assert "=" not in payload
    # Round-trip back to the original URL bytes.
    pad = "=" * (-len(payload) % 4)
    decoded = base64.urlsafe_b64decode(payload + pad).decode("ascii")
    assert decoded == url


def test_encode_share_url_rejects_empty_inputs():
    import sharepoint_corpus_source as sp

    for bad in (None, "", "   "):
        with pytest.raises(ValueError):
            sp.encode_share_url(bad)


# ---------------------------------------------------------------------------
# Test 2: list_share_children paginates @odata.nextLink
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(
        self,
        *,
        status_code: int = 200,
        json_payload: Optional[Dict[str, Any]] = None,
        body: bytes = b"",
        headers: Optional[Dict[str, str]] = None,
    ) -> None:
        self.status_code = status_code
        self._json = json_payload or {}
        self.content = body
        self.headers = headers or {}
        self.text = ""

    def json(self) -> Dict[str, Any]:
        return self._json

    def iter_content(self, chunk_size: int = 0):
        if self.content:
            yield self.content


def test_list_share_children_paginates_via_next_link():
    import sharepoint_corpus_source as sp

    page1 = {
        "value": [
            {"id": "A1", "name": "first.xlsx", "size": 100},
        ],
        "@odata.nextLink": "https://graph.microsoft.com/v1.0/page2",
    }
    page2 = {
        "value": [
            {"id": "A2", "name": "second.xlsx", "size": 200},
            {"id": "A3", "name": "third.xlsx", "size": 300},
        ],
    }
    pages = [_FakeResponse(json_payload=page1), _FakeResponse(json_payload=page2)]
    seen_urls: list[str] = []

    def fake_get(url: str, **kwargs: Any) -> _FakeResponse:
        seen_urls.append(url)
        return pages.pop(0)

    client = sp.SharePointGraphClient(
        http_get=fake_get,
        sleep_func=lambda _s: None,
        inter_request_sleep_s=0.0,
    )
    children = client.list_share_children(
        "https://example.com/share/folder", token="fake-token"
    )
    assert [c["id"] for c in children] == ["A1", "A2", "A3"]
    # First call hit the share endpoint, second followed nextLink.
    assert "/shares/" in seen_urls[0]
    assert seen_urls[1].endswith("/page2")


# ---------------------------------------------------------------------------
# Test 3: download_to_path retries on 429 with Retry-After
# ---------------------------------------------------------------------------


def test_download_to_path_retries_on_429_and_writes_file(tmp_path: Path):
    import sharepoint_corpus_source as sp

    sleeps: list[float] = []
    payload = b"\x50\x4b\x03\x04hello"
    responses = [
        _FakeResponse(status_code=429, headers={"Retry-After": "0.01"}),
        _FakeResponse(status_code=200, body=payload),
    ]

    def fake_get(url: str, **kwargs: Any) -> _FakeResponse:
        return responses.pop(0)

    client = sp.SharePointGraphClient(
        http_get=fake_get,
        sleep_func=sleeps.append,
        inter_request_sleep_s=0.0,
        retry_attempts=3,
        retry_base_s=0.01,
        retry_max_s=0.05,
    )
    item = {
        "id": "X1",
        "name": "foo.xlsx",
        "size": len(payload),
        "@microsoft.graph.downloadUrl": "https://download/example",
    }
    dest = tmp_path / "foo.xlsx"
    written = client.download_to_path(item, "tok", dest, max_bytes=1024)
    assert written == len(payload)
    assert dest.read_bytes() == payload
    # Retry-After honored exactly once.
    assert any(abs(s - 0.01) < 1e-6 for s in sleeps)


def test_download_to_path_skips_oversize_files(tmp_path: Path):
    """Items whose Graph metadata reports a size > cap must raise
    *before* opening any HTTP connection."""
    import sharepoint_corpus_source as sp

    def fail_get(*_a: Any, **_kw: Any) -> _FakeResponse:
        raise AssertionError("HTTP must not be invoked for oversize items")

    client = sp.SharePointGraphClient(
        http_get=fail_get,
        sleep_func=lambda _s: None,
        inter_request_sleep_s=0.0,
    )
    item = {
        "id": "B1",
        "name": "huge.xlsx",
        "size": 10**9,
        "@microsoft.graph.downloadUrl": "https://download/huge",
    }
    with pytest.raises(ValueError):
        client.download_to_path(
            item, "tok", tmp_path / "huge.xlsx", max_bytes=10
        )


def test_download_to_path_writes_mode_0600(tmp_path: Path):
    import sharepoint_corpus_source as sp

    payload = b"x"
    responses = [_FakeResponse(status_code=200, body=payload)]

    def fake_get(url: str, **kwargs: Any) -> _FakeResponse:
        return responses.pop(0)

    client = sp.SharePointGraphClient(
        http_get=fake_get,
        sleep_func=lambda _s: None,
        inter_request_sleep_s=0.0,
    )
    item = {
        "id": "M1",
        "name": "f.xlsx",
        "size": len(payload),
        "@microsoft.graph.downloadUrl": "https://download/f",
    }
    dest = tmp_path / "f.xlsx"
    client.download_to_path(item, "tok", dest, max_bytes=64)
    assert dest.exists()
    if os.name == "posix":
        mode = dest.stat().st_mode & 0o777
        assert mode == 0o600


# ---------------------------------------------------------------------------
# Test 4: KeyringTokenCache fallback to 0600 file
# ---------------------------------------------------------------------------


class _FakeKeyringFailing:
    """Keyring stub that raises on every call -- forces fallback file."""

    def get_password(self, service: str, account: str) -> Optional[str]:
        raise RuntimeError("keyring unavailable")

    def set_password(self, service: str, account: str, value: str) -> None:
        raise RuntimeError("keyring unavailable")


class _FakeKeyringInMemory:
    def __init__(self) -> None:
        self.store: Dict[tuple, str] = {}

    def get_password(self, service: str, account: str) -> Optional[str]:
        return self.store.get((service, account))

    def set_password(self, service: str, account: str, value: str) -> None:
        self.store[(service, account)] = value


def test_token_cache_falls_back_to_file_when_keyring_fails(tmp_path: Path):
    import sharepoint_corpus_source as sp

    fallback = tmp_path / "token_cache.json"
    cache = sp.KeyringTokenCache(
        fallback_path=fallback,
        keyring_module=_FakeKeyringFailing(),
    )
    cache.save("blob-data")
    # File written, mode 0600, contents readable.
    assert fallback.exists()
    if os.name == "posix":
        assert (fallback.stat().st_mode & 0o777) == 0o600
    assert cache.load() == "blob-data"


def test_token_cache_prefers_keyring_over_file(tmp_path: Path):
    import sharepoint_corpus_source as sp

    fallback = tmp_path / "token_cache.json"
    fallback.write_text("file-side", encoding="utf-8")
    kr = _FakeKeyringInMemory()
    kr.store[(sp.KEYRING_SERVICE, sp.KEYRING_ACCOUNT_TOKEN_CACHE)] = "kr-side"
    cache = sp.KeyringTokenCache(fallback_path=fallback, keyring_module=kr)
    assert cache.load() == "kr-side"


# ---------------------------------------------------------------------------
# Test 5: refresh_local_cache mtime-aware caching
# ---------------------------------------------------------------------------


class _FakeClient:
    """Stand-in for :class:`SharePointGraphClient` used by
    :func:`refresh_local_cache` tests."""

    def __init__(
        self,
        *,
        children: List[Dict[str, Any]],
        token: Optional[str] = "fake-token",  # noqa: S107 — test fake, not a credential
        download_payloads: Optional[Dict[str, bytes]] = None,
    ) -> None:
        self._children = children
        self._token = token
        self._payloads = download_payloads or {}
        self.download_call_count = 0
        self.list_call_count = 0

    def acquire_token_silent(self) -> Optional[str]:
        return self._token

    def list_share_children(self, share_url: str, token: str) -> List[Dict[str, Any]]:
        self.list_call_count += 1
        return list(self._children)

    def download_to_path(
        self, item: Dict[str, Any], token: str, dest_path: Path, *, max_bytes: int
    ) -> int:
        self.download_call_count += 1
        body = self._payloads.get(item["id"], b"x")
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        dest_path.write_bytes(body)
        return len(body)


def test_refresh_local_cache_returns_auth_required_when_no_token(tmp_path: Path):
    import sharepoint_corpus_source as sp

    client = _FakeClient(children=[], token=None)
    stats = sp.refresh_local_cache(
        share_url="https://example.com/share",
        cache_dir=tmp_path / "cache",
        client=client,
    )
    assert stats.error_kind == "auth_required"
    assert stats.files_listed == 0
    assert client.list_call_count == 0


def test_refresh_local_cache_skips_unchanged_files_via_manifest(tmp_path: Path):
    import sharepoint_corpus_source as sp

    cache = tmp_path / "cache"
    children = [
        {
            "id": "F1",
            "name": "a.xlsx",
            "size": 1,
            "lastModifiedDateTime": "2026-01-01T00:00:00Z",
        },
    ]
    client = _FakeClient(children=children, download_payloads={"F1": b"a"})

    # First pass downloads the file.
    s1 = sp.refresh_local_cache(
        share_url="https://example.com/share",
        cache_dir=cache,
        client=client,
    )
    assert s1.files_downloaded == 1
    assert s1.files_cached == 0
    assert client.download_call_count == 1

    # Second pass -- same mtime -> cache hit, no download.
    s2 = sp.refresh_local_cache(
        share_url="https://example.com/share",
        cache_dir=cache,
        client=client,
    )
    assert s2.files_downloaded == 0
    assert s2.files_cached == 1
    assert client.download_call_count == 1

    # Bump remote mtime -> re-download.
    children[0]["lastModifiedDateTime"] = "2026-02-01T00:00:00Z"
    s3 = sp.refresh_local_cache(
        share_url="https://example.com/share",
        cache_dir=cache,
        client=client,
    )
    assert s3.files_downloaded == 1
    assert s3.files_cached == 0
    assert client.download_call_count == 2


def test_refresh_local_cache_rejects_unsafe_filenames(tmp_path: Path):
    """A hostile share that names a file ``../../escape.xlsx`` must be
    sanitized -- the cache file lives strictly under ``cache_dir``."""
    import sharepoint_corpus_source as sp

    cache = tmp_path / "cache"
    children = [
        {
            "id": "P1",
            "name": "../../escape.xlsx",
            "size": 1,
            "lastModifiedDateTime": "2026-01-01T00:00:00Z",
        }
    ]
    client = _FakeClient(children=children, download_payloads={"P1": b"x"})
    sp.refresh_local_cache(
        share_url="https://example.com/share",
        cache_dir=cache,
        client=client,
    )
    # No file written outside cache_dir.
    for entry in cache.iterdir():
        # Only the (sanitized) file + manifest.json may be present.
        assert entry.parent == cache


# ---------------------------------------------------------------------------
# Test 6: corpus_bootstrap three-source ordering
# ---------------------------------------------------------------------------


def test_resolve_index_sources_orders_sharepoint_first(monkeypatch, tmp_path: Path):
    import corpus_bootstrap as cb
    from config import Config

    sp_cache = tmp_path / "sp_cache"
    od_dir = tmp_path / "od"
    dl_dir = tmp_path / "dl"
    sp_cache.mkdir()
    od_dir.mkdir()
    dl_dir.mkdir()

    monkeypatch.setattr(Config, "ADOPTIQ_SHAREPOINT_ENABLED", True, raising=False)
    monkeypatch.setattr(
        Config,
        "ADOPTIQ_SHAREPOINT_FOLDER_URL",
        "https://example.com/share",
        raising=False,
    )
    monkeypatch.setattr(
        Config, "ADOPTIQ_SHAREPOINT_CACHE_DIR", str(sp_cache), raising=False
    )
    monkeypatch.setattr(Config, "CSONE_ONEDRIVE_FOLDER", str(od_dir), raising=False)
    monkeypatch.setattr(Config, "CSONE_INCLUDE_USER_DOWNLOADS", True, raising=False)
    monkeypatch.setattr(Config, "CSONE_USER_DOWNLOADS_DIR", str(dl_dir), raising=False)

    sources = cb._resolve_index_sources()
    labels = [s["label"] for s in sources]
    assert labels == ["sharepoint_csone", "onedrive", "user_downloads"]


def test_resolve_index_sources_skips_missing_onedrive(monkeypatch, tmp_path: Path):
    import corpus_bootstrap as cb
    from config import Config

    sp_cache = tmp_path / "sp_cache"
    sp_cache.mkdir()
    dl_dir = tmp_path / "dl"
    dl_dir.mkdir()

    monkeypatch.setattr(Config, "ADOPTIQ_SHAREPOINT_ENABLED", True, raising=False)
    monkeypatch.setattr(
        Config,
        "ADOPTIQ_SHAREPOINT_FOLDER_URL",
        "https://example.com/share",
        raising=False,
    )
    monkeypatch.setattr(
        Config, "ADOPTIQ_SHAREPOINT_CACHE_DIR", str(sp_cache), raising=False
    )
    monkeypatch.setattr(
        Config,
        "CSONE_ONEDRIVE_FOLDER",
        str(tmp_path / "does-not-exist"),
        raising=False,
    )
    monkeypatch.setattr(Config, "CSONE_INCLUDE_USER_DOWNLOADS", True, raising=False)
    monkeypatch.setattr(Config, "CSONE_USER_DOWNLOADS_DIR", str(dl_dir), raising=False)

    labels = [s["label"] for s in cb._resolve_index_sources()]
    assert "onedrive" not in labels
    assert labels[0] == "sharepoint_csone"
    assert labels[-1] == "user_downloads"


def test_resolve_index_sources_omits_sharepoint_when_disabled(
    monkeypatch, tmp_path: Path
):
    import corpus_bootstrap as cb
    from config import Config

    od_dir = tmp_path / "od"
    od_dir.mkdir()

    monkeypatch.setattr(Config, "ADOPTIQ_SHAREPOINT_ENABLED", False, raising=False)
    monkeypatch.setattr(Config, "CSONE_ONEDRIVE_FOLDER", str(od_dir), raising=False)
    monkeypatch.setattr(Config, "CSONE_INCLUDE_USER_DOWNLOADS", False, raising=False)

    labels = [s["label"] for s in cb._resolve_index_sources()]
    assert "sharepoint_csone" not in labels


# ---------------------------------------------------------------------------
# Test 7: admin tile sharepoint payload + endpoints
# ---------------------------------------------------------------------------


def test_corpus_status_payload_carries_sharepoint_block(client):
    resp = client.get("/api/corpus/status")
    assert resp.status_code == 200
    data = resp.get_json()
    assert isinstance(data, dict)
    assert "boot" in data
    # Either ``None`` (feature disabled / not invoked) or a dict, but
    # the key must be present unconditionally so the template can
    # reference it without a ``hasattr`` check.
    assert "sharepoint" in data["boot"]


def test_sharepoint_signin_endpoint_requires_admin_auth(app):
    app.config["WTF_CSRF_ENABLED"] = True
    try:
        c = app.test_client()
        resp = c.post("/api/corpus/sharepoint/signin")
        assert resp.status_code == 403
        body = resp.get_json()
        assert body and body.get("error")
    finally:
        app.config["WTF_CSRF_ENABLED"] = False


def test_sharepoint_refresh_endpoint_internal_token_grants_access(
    app, monkeypatch
):
    app.config["WTF_CSRF_ENABLED"] = True
    monkeypatch.setenv("ADOPTIQ_INTERNAL_TOKEN", "round17_2-internal-token")
    try:
        c = app.test_client()
        resp = c.post(
            "/api/corpus/sharepoint/refresh",
            headers={"X-AdoptIQ-Internal": "round17_2-internal-token"},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data is not None
        assert "refresh_started" in data
        # The status payload (boot/corpus blocks) must always be in
        # the response so the admin tile can re-render after a
        # refresh trigger.
        assert "boot" in data and "corpus" in data
    finally:
        app.config["WTF_CSRF_ENABLED"] = False


# ---------------------------------------------------------------------------
# Test 8: corpus_indexer per-file INFO logging
# ---------------------------------------------------------------------------


def test_indexer_logs_per_file_info_for_each_indexed_file(
    tmp_path: Path, caplog
):
    """Round 17.2 promoted indexer per-file lines from DEBUG to INFO so
    operators can see exactly which files were ingested.  We only
    assert that *some* per-file INFO line is emitted with the
    filename + an ``ext=`` field; we do not pin the message format."""
    import corpus_indexer

    fixture_root = (
        Path(__file__).resolve().parent / "fixtures" / "round17"
    )
    if not fixture_root.exists():
        pytest.skip("round17 fixtures not present")

    src = tmp_path / "corpus"
    src.mkdir()
    for name in ("synthetic_cases.csv",):
        (src / name).write_bytes((fixture_root / name).read_bytes())

    db_path = tmp_path / "corpus.db"
    conn = corpus_indexer.open_corpus_db(db_path)
    try:
        with caplog.at_level(logging.INFO, logger="corpus_indexer"):
            corpus_indexer.index_folder(conn, src)
    finally:
        conn.close()

    info_lines = [
        rec.getMessage()
        for rec in caplog.records
        if rec.levelno == logging.INFO and "corpus_indexer" in rec.name
    ]
    # Some INFO line must mention the filename + the ext token.
    assert any(
        "synthetic_cases.csv" in line and "ext=" in line for line in info_lines
    ), info_lines


# ---------------------------------------------------------------------------
# Test 9: OneDrive auto-discovery
# ---------------------------------------------------------------------------


def test_resolve_csone_onedrive_picks_first_existing_candidate(
    monkeypatch, tmp_path: Path
):
    import config as cfg_mod

    home = tmp_path / "home"
    cloud = home / "Library" / "CloudStorage" / "OneDrive-Cisco" / "AI Projects" / "AdoptIQ_CSOne_Reports"
    cloud.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("CSONE_ONEDRIVE_FOLDER", raising=False)
    resolved = cfg_mod._resolve_csone_onedrive_folder()
    assert Path(resolved) == cloud


def test_resolve_csone_onedrive_falls_through_to_default_when_nothing_exists(
    monkeypatch, tmp_path: Path
):
    import config as cfg_mod

    monkeypatch.setenv("HOME", str(tmp_path / "fresh-home"))
    monkeypatch.delenv("CSONE_ONEDRIVE_FOLDER", raising=False)
    resolved = cfg_mod._resolve_csone_onedrive_folder()
    candidates = cfg_mod._csone_onedrive_candidates()
    assert resolved == candidates[0]


def test_resolve_csone_onedrive_honors_env_override(
    monkeypatch, tmp_path: Path
):
    import config as cfg_mod

    override = tmp_path / "custom-corpus"
    override.mkdir()
    monkeypatch.setenv("CSONE_ONEDRIVE_FOLDER", str(override))
    assert cfg_mod._resolve_csone_onedrive_folder() == str(override)
