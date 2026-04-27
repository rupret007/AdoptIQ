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
