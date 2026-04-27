"""
Regression tests for Round 26 / Phase D: POST /api/intel/upload (intel file drop).

Covers feature-flag gating, auth passthrough under WTF_CSRF_ENABLED=False,
extension allow-list, empty/missing file handling, unique saved names, and
sanitized paths under CSONE_INTEL_UPLOADS_FOLDER.
"""
import io

import pytest

from config import Config


def test_upload_disabled_by_default_returns_403(client):
    """Uploads are off unless the operator enables ADOPTIQ_INTEL_UPLOAD_ENABLED."""
    data = {"file": (io.BytesIO(b"x" * 100), "report.xlsx")}
    resp = client.post(
        "/api/intel/upload",
        data=data,
        content_type="multipart/form-data",
    )
    assert resp.status_code == 403
    body = resp.get_json()
    assert body.get("ok") is False
    err = body.get("error", "")
    assert "ADOPTIQ_INTEL_UPLOAD_ENABLED" in err


def test_upload_accepts_xlsx_when_flag_enabled(client, monkeypatch, tmp_path):
    monkeypatch.setattr(Config, "ADOPTIQ_INTEL_UPLOAD_ENABLED", True)
    monkeypatch.setattr(Config, "CSONE_INTEL_UPLOADS_FOLDER", str(tmp_path))

    payload = b"x" * 100
    data = {"file": (io.BytesIO(payload), "report.xlsx")}
    resp = client.post(
        "/api/intel/upload",
        data=data,
        content_type="multipart/form-data",
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body.get("ok") is True
    upload = body.get("upload") or {}
    assert upload.get("filename", "").endswith(".xlsx")
    assert upload.get("size", 0) > 0
    saved = tmp_path / upload["filename"]
    assert saved.is_file()
    assert saved.read_bytes() == payload


def test_upload_rejects_disallowed_extension(client, monkeypatch, tmp_path):
    monkeypatch.setattr(Config, "ADOPTIQ_INTEL_UPLOAD_ENABLED", True)
    monkeypatch.setattr(Config, "CSONE_INTEL_UPLOADS_FOLDER", str(tmp_path))

    data = {"file": (io.BytesIO(b"MZ"), "malware.exe")}
    resp = client.post(
        "/api/intel/upload",
        data=data,
        content_type="multipart/form-data",
    )
    assert resp.status_code == 400
    body = resp.get_json()
    assert body.get("ok") is False
    assert "Unsupported file type" in (body.get("error") or "")


def test_upload_rejects_no_file_part(client, monkeypatch, tmp_path):
    monkeypatch.setattr(Config, "ADOPTIQ_INTEL_UPLOAD_ENABLED", True)
    monkeypatch.setattr(Config, "CSONE_INTEL_UPLOADS_FOLDER", str(tmp_path))

    resp = client.post(
        "/api/intel/upload",
        data={},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 400
    body = resp.get_json()
    assert body.get("ok") is False
    assert "No file uploaded" in (body.get("error") or "")


def test_upload_rejects_empty_file(client, monkeypatch, tmp_path):
    monkeypatch.setattr(Config, "ADOPTIQ_INTEL_UPLOAD_ENABLED", True)
    monkeypatch.setattr(Config, "CSONE_INTEL_UPLOADS_FOLDER", str(tmp_path))

    data = {"file": (io.BytesIO(b""), "empty.txt")}
    resp = client.post(
        "/api/intel/upload",
        data=data,
        content_type="multipart/form-data",
    )
    assert resp.status_code == 400
    body = resp.get_json()
    assert body.get("ok") is False
    assert "empty" in (body.get("error") or "").lower()


def test_upload_filename_is_randomized_to_prevent_collisions(
    client, monkeypatch, tmp_path
):
    monkeypatch.setattr(Config, "ADOPTIQ_INTEL_UPLOAD_ENABLED", True)
    monkeypatch.setattr(Config, "CSONE_INTEL_UPLOADS_FOLDER", str(tmp_path))

    # Under pytest, _r13_unique_upload_filename hashes file content; use
    # different bodies so both uploads get distinct stored names.
    for content in (b"row1\n", b"row2\n"):
        data = {"file": (io.BytesIO(content), "same_name.csv")}
        resp = client.post(
            "/api/intel/upload",
            data=data,
            content_type="multipart/form-data",
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body.get("ok") is True

    names = sorted(p.name for p in tmp_path.iterdir() if p.is_file())
    assert len(names) == 2
    assert names[0] != names[1]
    assert all(n.endswith(".csv") for n in names)


def test_upload_rejects_path_traversal_filename(client, monkeypatch, tmp_path):
    monkeypatch.setattr(Config, "ADOPTIQ_INTEL_UPLOAD_ENABLED", True)
    monkeypatch.setattr(Config, "CSONE_INTEL_UPLOADS_FOLDER", str(tmp_path))

    evil = "../../etc/passwd.csv"
    data = {"file": (io.BytesIO(b"safe"), evil)}
    resp = client.post(
        "/api/intel/upload",
        data=data,
        content_type="multipart/form-data",
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body.get("ok") is True
    upload = body.get("upload") or {}
    saved_name = upload.get("filename", "")
    assert ".." not in saved_name
    assert saved_name.endswith(".csv")
    resolved = (tmp_path / saved_name).resolve()
    assert resolved.is_file()
    assert str(resolved).startswith(str(tmp_path.resolve()))
    assert "etc_passwd" in saved_name or saved_name.endswith("passwd.csv")


def test_upload_oversize_returns_json_413_for_api_paths(
    app, client, monkeypatch, tmp_path
):
    """Round 26 - review (R26-OPEN-004): oversize uploads to /api/*
    must return JSON 413 (matching the upload route's own
    ``{ok: False, error: ...}`` shape) so the AdoptIQ Intelligence
    poller renders the friendly "File too large" message instead
    of treating Werkzeug's default HTML 413 as a network error.

    Werkzeug aborts before any view runs once the request body
    exceeds ``MAX_CONTENT_LENGTH``, so we shrink the cap to a tiny
    value for this test and POST a body just over it.
    """
    monkeypatch.setattr(Config, "ADOPTIQ_INTEL_UPLOAD_ENABLED", True)
    monkeypatch.setattr(Config, "CSONE_INTEL_UPLOADS_FOLDER", str(tmp_path))
    # Clamp to 1 MiB for the duration of the test; the test client's
    # multipart envelope easily fits this so we can exceed it
    # deterministically without actually allocating dozens of MB.
    original_cap = app.config.get("MAX_CONTENT_LENGTH")
    app.config["MAX_CONTENT_LENGTH"] = 1 * 1024 * 1024
    try:
        oversize = b"x" * (2 * 1024 * 1024)
        data = {"file": (io.BytesIO(oversize), "huge.xlsx")}
        resp = client.post(
            "/api/intel/upload",
            data=data,
            content_type="multipart/form-data",
        )
    finally:
        app.config["MAX_CONTENT_LENGTH"] = original_cap

    assert resp.status_code == 413
    body = resp.get_json()
    assert body is not None, "errorhandler must return JSON, not HTML"
    assert body.get("ok") is False
    err = body.get("error") or ""
    assert "too large" in err.lower()
    # Message includes the megabyte cap so users see the actual limit.
    assert "MB" in err


def test_upload_returns_refresh_started_on_success(
    client, monkeypatch, tmp_path
):
    """Round 26 - review (NIT-007a): a successful upload must trigger
    an incremental refresh and report ``refresh_started == True``.

    We monkeypatch ``corpus_bootstrap.is_enabled`` and
    ``corpus_bootstrap.request_refresh`` so the test doesn't depend
    on the real corpus singleton (which might be uninitialized in a
    bare pytest run).  The assertion shape mirrors the production
    JSON contract documented in ``api_intel_upload``.
    """
    monkeypatch.setattr(Config, "ADOPTIQ_INTEL_UPLOAD_ENABLED", True)
    monkeypatch.setattr(Config, "CSONE_INTEL_UPLOADS_FOLDER", str(tmp_path))

    import corpus_bootstrap as cb

    refresh_calls: list[dict] = []

    def _fake_is_enabled() -> bool:
        return True

    def _fake_request_refresh(*, rebuild: bool = False) -> bool:
        refresh_calls.append({"rebuild": rebuild})
        return True

    monkeypatch.setattr(cb, "is_enabled", _fake_is_enabled)
    monkeypatch.setattr(cb, "request_refresh", _fake_request_refresh)

    payload = b"col1,col2\n1,2\n"
    data = {"file": (io.BytesIO(payload), "tiny.csv")}
    resp = client.post(
        "/api/intel/upload",
        data=data,
        content_type="multipart/form-data",
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body.get("ok") is True
    assert body.get("refresh_started") is True
    # Upload route is the post-upload trigger -- never a rebuild.
    assert refresh_calls == [{"rebuild": False}]


def test_upload_does_not_set_refresh_started_when_corpus_disabled(
    client, monkeypatch, tmp_path
):
    """Round 26 - review (NIT-007a): when ``corpus_bootstrap.is_enabled``
    is False (CORPUS_KNOWLEDGE_ENABLED unset), the upload still
    succeeds but ``refresh_started`` must be False so the JS doesn't
    show a "indexing now" toast that will never resolve."""
    monkeypatch.setattr(Config, "ADOPTIQ_INTEL_UPLOAD_ENABLED", True)
    monkeypatch.setattr(Config, "CSONE_INTEL_UPLOADS_FOLDER", str(tmp_path))

    import corpus_bootstrap as cb

    monkeypatch.setattr(cb, "is_enabled", lambda: False)

    def _request_refresh_should_not_be_called(*args, **kwargs):
        raise AssertionError(
            "request_refresh must not be called when is_enabled() is False"
        )

    monkeypatch.setattr(cb, "request_refresh", _request_refresh_should_not_be_called)

    data = {"file": (io.BytesIO(b"row\n"), "tiny.csv")}
    resp = client.post(
        "/api/intel/upload",
        data=data,
        content_type="multipart/form-data",
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body.get("ok") is True
    assert body.get("refresh_started") is False
