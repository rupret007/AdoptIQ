"""Native candidate trust gates for the Round 147 delivery contract."""

from __future__ import annotations
from source_shape_utils import assert_in_source

import importlib
import hashlib
import io
import json
import marshal
import plistlib
import sys
import types
import zipfile
import zlib
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


def _write_minimal_developer_inputs(root: Path) -> None:
    for module_name in (
        "connectivity_diagnostics",
        "corpus_bootstrap",
        "error_classifier",
    ):
        (root / f"{module_name}.py").write_text(
            f'"""Synthetic {module_name}."""\nVALUE = "safe"\n',
            encoding="utf-8",
        )
    for directory_name in ("templates", "static"):
        directory = root / directory_name
        directory.mkdir(exist_ok=True)
        (directory / "fixture.txt").write_text("safe\n", encoding="utf-8")


@pytest.fixture(scope="module")
def release_modules() -> dict[str, object]:
    sys.path.insert(0, str(SCRIPTS))
    try:
        return {
            name: importlib.import_module(name)
            for name in (
                "candidate_provenance",
                "developer_build_payload",
                "developer_candidate_security",
                "smoke_frozen_candidate",
                "verify_developer_candidate",
                "verify_windows_developer_candidate",
            )
        }
    finally:
        sys.path.remove(str(SCRIPTS))


def test_developer_payload_is_synthetic_and_commit_bound(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    release_modules: dict[str, object],
) -> None:
    payload_module = release_modules["developer_build_payload"]
    security = release_modules["developer_candidate_security"]
    (tmp_path / "config.py").write_text(
        'ADOPTIQ_VERSION = "9.8.7"\nADOPTIQ_BUILD = "654"\n',
        encoding="utf-8",
    )
    (tmp_path / "team_config.json").write_text(
        '{"team_roster":[{"email":"real.person@example.com"}]}', encoding="utf-8"
    )
    (tmp_path / "customer_aliases.defaults.json").write_text(
        '{"groups":[{"canonical":"REAL CUSTOMER"}]}', encoding="utf-8"
    )
    _write_minimal_developer_inputs(tmp_path)
    monkeypatch.setenv("GITHUB_SHA", "a" * 40)

    paths = payload_module.prepare_developer_payload(tmp_path, target_platform="macos")
    resources = {Path(path).name: Path(path).read_bytes() for path in paths.values() if Path(path).is_file()}
    result = security.validate_developer_payload(
        resources,
        expected_platform="macos",
        expected_version="9.8.7",
        expected_build="654",
        expected_commit="a" * 40,
    )

    assert result["ok"] is True
    assert json.loads(resources["customer_aliases.defaults.json"])["groups"] == []
    roster = json.loads(resources["team_config.json"])["team_roster"]
    assert roster[0]["email"].endswith("@example.invalid")
    assert "real.person@example.com" not in b"".join(resources.values()).decode()
    assert json.loads((tmp_path / "customer_aliases.defaults.json").read_text())["groups"]
    readme = resources["README.md"].decode("utf-8")
    assert "# AdoptIQ Developer Candidate" in readme
    assert "Version: 9.8.7" in readme
    assert "Build: 654" in readme
    assert "Platform: macos" in readme
    assert "not production-ready" in readme
    assert "release notes" not in readme.casefold()
    assert security.scan_blob("README.md", readme.encode()) == []


def test_developer_config_overlay_removes_internal_defaults_and_binds_exactly_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    release_modules: dict[str, object],
) -> None:
    payload_module = release_modules["developer_build_payload"]
    security = release_modules["developer_candidate_security"]
    original = (ROOT / "config.py").read_text(encoding="utf-8")
    (tmp_path / "config.py").write_text(original, encoding="utf-8")
    _write_minimal_developer_inputs(tmp_path)
    for module_name in (
        "connectivity_diagnostics",
        "corpus_bootstrap",
        "error_classifier",
    ):
        (tmp_path / f"{module_name}.py").write_text(
            (ROOT / f"{module_name}.py").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
    monkeypatch.setenv("GITHUB_SHA", "9" * 40)

    original_findings = security.scan_blob("config.py", original.encode(), include_assignments=False)
    assert {item["rule"] for item in original_findings}.issuperset(
        {"known_internal_identifier", "internal_service_locator"}
    )

    paths = payload_module.prepare_developer_payload(tmp_path, target_platform="windows")
    overlay = Path(paths["config_overlay"])
    overlay_source = overlay.read_text(encoding="utf-8")
    assert payload_module.DEVELOPER_CONFIG_OVERLAY_MARKER in overlay_source
    assert security.scan_blob("config.py", overlay_source.encode(), include_assignments=False) == []

    compiled = marshal.dumps(compile(overlay_source, str(overlay), "exec", optimize=2))
    assert payload_module.DEVELOPER_CONFIG_OVERLAY_MARKER.encode() in compiled
    assert security.scan_blob("archive/PYZ.pyz/config", compiled, include_assignments=False) == []

    for module_name in (
        "connectivity_diagnostics",
        "corpus_bootstrap",
        "error_classifier",
    ):
        module_overlay = Path(paths[f"{module_name}_overlay"])
        module_source = module_overlay.read_text(encoding="utf-8")
        assert (
            security.scan_blob(
                f"archive/PYZ.pyz/{module_name}",
                marshal.dumps(compile(module_source, str(module_overlay), "exec", optimize=2)),
                include_assignments=False,
            )
            == []
        )

    pure_toc = [
        ("another_module", str(tmp_path / "another.py"), "PYMODULE"),
        ("config", str(tmp_path / "unsafe-config.py"), "PYMODULE"),
    ]
    bound = payload_module.bind_developer_config_overlay(pure_toc, overlay)
    assert Path(bound) == overlay.resolve()
    assert Path(pure_toc[1][1]) == overlay.resolve()
    with pytest.raises(RuntimeError, match="exactly one config module"):
        payload_module.bind_developer_config_overlay(
            [*pure_toc, ("config", str(tmp_path / "duplicate.py"), "PYMODULE")],
            overlay,
        )

    overlay_map = {
        module_name: paths[f"{module_name}_overlay"] for module_name in payload_module.DEVELOPER_SOURCE_OVERLAY_MODULES
    }
    complete_toc = [
        (module_name, str(tmp_path / f"unsafe-{module_name}.py"), "PYMODULE")
        for module_name in payload_module.DEVELOPER_SOURCE_OVERLAY_MODULES
    ]
    bound_overlays = payload_module.bind_developer_source_overlays(
        complete_toc,
        overlay_map,
    )
    assert set(bound_overlays) == set(payload_module.DEVELOPER_SOURCE_OVERLAY_MODULES)
    with pytest.raises(RuntimeError, match="overlay set is incomplete"):
        payload_module.bind_developer_source_overlays(
            complete_toc,
            {"config": overlay},
        )


def test_developer_resource_overlay_redacts_without_mutating_production_inputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    release_modules: dict[str, object],
) -> None:
    payload_module = release_modules["developer_build_payload"]
    security = release_modules["developer_candidate_security"]
    (tmp_path / "config.py").write_text(
        'ADOPTIQ_VERSION = "9.8.7"\nADOPTIQ_BUILD = "654"\n',
        encoding="utf-8",
    )
    _write_minimal_developer_inputs(tmp_path)
    synthetic_locator = b"private-service.synthetic.invalid"
    monkeypatch.setattr(
        security,
        "_KNOWN_INTERNAL_LOCATOR_DIGESTS",
        security._KNOWN_INTERNAL_LOCATOR_DIGESTS | {hashlib.sha256(synthetic_locator).hexdigest()},
    )
    production_resource = tmp_path / "templates" / "fixture.txt"
    production_resource.write_bytes(b"before:" + synthetic_locator + b":after")

    paths = payload_module.prepare_developer_payload(
        tmp_path,
        target_platform="windows",
    )

    developer_resource = Path(paths["templates_root"]) / "fixture.txt"
    assert synthetic_locator in production_resource.read_bytes()
    assert synthetic_locator not in developer_resource.read_bytes()
    assert security.scan_tree(Path(paths["templates_root"]))["ok"] is True


@pytest.mark.parametrize(
    ("target_platform", "launch_marker"),
    [("macos", "AdoptIQ.app"), ("windows", "AdoptIQ.exe")],
)
def test_generated_developer_readme_is_short_safe_and_platform_specific(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    release_modules: dict[str, object],
    target_platform: str,
    launch_marker: str,
) -> None:
    payload_module = release_modules["developer_build_payload"]
    security = release_modules["developer_candidate_security"]
    (tmp_path / "config.py").write_text(
        'ADOPTIQ_VERSION = "9.8.7"\nADOPTIQ_BUILD = "654"\n',
        encoding="utf-8",
    )
    _write_minimal_developer_inputs(tmp_path)
    monkeypatch.setenv("GITHUB_SHA", "b" * 40)

    paths = payload_module.prepare_developer_payload(tmp_path, target_platform=target_platform)
    readme_path = Path(paths["readme"])
    readme = readme_path.read_text(encoding="utf-8")

    assert readme_path.name == "README.md"
    assert len(readme.encode("utf-8")) < 2_500
    assert f"Platform: {target_platform}" in readme
    assert launch_marker in readme
    assert security.scan_blob("README.md", readme.encode()) == []


def test_tree_digest_hashes_contents_not_only_the_path(tmp_path: Path, release_modules: dict[str, object]) -> None:
    security = release_modules["developer_candidate_security"]
    bundle = tmp_path / "AdoptIQ.app"
    bundle.mkdir()
    item = bundle / "payload.bin"
    item.write_bytes(b"first")
    first = security.sha256_tree(bundle)
    item.write_bytes(b"second")
    second = security.sha256_tree(bundle)

    assert len(first) == 64
    assert len(second) == 64
    assert first != second


def test_credential_scanner_reports_rule_without_secret_value(
    release_modules: dict[str, object],
) -> None:
    security = release_modules["developer_candidate_security"]
    token = "ghp_" + "A" * 36
    findings = security.scan_blob("fixture.bin", token.encode())

    assert findings == [{"rule": "github_classic_token", "path": "fixture.bin"}]
    assert token not in repr(findings)
    assert security.scan_blob("aws-docs.json", b"AKIAIOSFODNN7EXAMPLE") == []
    assert security.scan_blob("config.bin", b"AKIA1234567890ABCDEF") == [
        {"rule": "aws_access_key", "path": "config.bin"}
    ]
    assert security.scan_blob("embedded-font.bin", b"xAKIA1234567890ABCDEFy") == []
    private_key = b"-----BEGIN PRIVATE KEY-----\n" + b"A" * 64 + b"\n-----END PRIVATE KEY-----"
    assert security.scan_blob("private-key.pem", private_key) == [{"rule": "private_key", "path": "private-key.pem"}]
    assert security.scan_blob("settings.ini", b"DATABASE_PASSWORD=ordinary-passphrase-123") == [
        {"rule": "credential_assignment", "path": "settings.ini"}
    ]
    assert (
        security.scan_blob(
            "documented-example.env",
            b"PASSWORD=YOUR_PASSWORD\nAWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        )
        == []
    )
    internal_address = b"synthetic.user" + b"@" + b"cisco.com"
    representative_roster_value = b"Samuel" + b" " + b"Tamayo"
    organization_findings = security.scan_blob(
        "compiled-roster.bin",
        internal_address + b"\0" + representative_roster_value,
    )
    assert organization_findings == [
        {"rule": "internal_cisco_email", "path": "compiled-roster.bin"},
        {"rule": "known_roster_identifier", "path": "compiled-roster.bin"},
    ]
    assert internal_address.decode() not in repr(organization_findings)
    assert representative_roster_value.decode() not in repr(organization_findings)


def test_windows_native_verifier_rejects_every_forbidden_archive_name_and_assignment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    release_modules: dict[str, object],
) -> None:
    verifier = release_modules["verify_windows_developer_candidate"]
    executable = tmp_path / "AdoptIQ.exe"
    executable.write_bytes(b"MZ-safe-native-fixture")
    expected_commit = "c" * 40
    payloads = {
        "DEVELOPER_ONLY_BUILD.txt": (
            b"Developer-only AdoptIQ candidate. Synthetic configuration only. "
            b"No bundled credentials or prebaked customer corpus. Not production-ready.\n"
        ),
        "DEVELOPER_BUILD_METADATA.json": json.dumps(
            {
                "schema_version": "developer-build-metadata/v2",
                "developer_only": True,
                "production_ready": False,
                "sanitized_configuration": True,
                "sanitized_config_overlay_required": True,
                "prebaked_corpus_included": False,
                "bundled_credentials_included": False,
                "target_platform": "windows",
                "version": "9.8.7",
                "build": "654",
                "source_commit_sha": expected_commit,
            }
        ).encode(),
        "customer_aliases.defaults.json": b'{"schema_version":1,"groups":[]}',
        "team_config.json": (b'{"team_roster":[{"email":"team.member@example.invalid"}]}'),
        r"nested\secrets.env": b"PASSWORD=YOUR_PASSWORD",
        r"config\settings.ini": b"DATABASE_PASSWORD=ordinary-passphrase-123",
        r"compiled\adoptiq_backend.pyc": (
            b"compiled-roster\0" + b"Samuel" + b" " + b"Tamayo" + b"\0synthetic.user" + b"@" + b"cisco.com"
        ),
        r"reports\AdoptIQ_Report_customer.docx": b"synthetic report bytes",
        r"botocore\data\iam\2010-05-08\examples-1.json": (b'{"Password":"ExampleDocsPassphrase"}'),
        r"snowflake_connector.dist-info\METADATA": b"password = docs-passphrase",
    }

    class FakeCArchiveReader:
        def __init__(self, _path: str) -> None:
            self.toc = dict(payloads)

        def extract(self, name: str) -> bytes:
            if name == r"nested\secrets.env":
                raise ValueError("synthetic unreadable forbidden entry")
            return payloads[name]

    readers = types.ModuleType("PyInstaller.archive.readers")
    readers.CArchiveReader = FakeCArchiveReader
    archive = types.ModuleType("PyInstaller.archive")
    archive.readers = readers
    pyinstaller = types.ModuleType("PyInstaller")
    pyinstaller.archive = archive
    monkeypatch.setitem(sys.modules, "PyInstaller", pyinstaller)
    monkeypatch.setitem(sys.modules, "PyInstaller.archive", archive)
    monkeypatch.setitem(sys.modules, "PyInstaller.archive.readers", readers)

    inventory = "\n".join(
        f"  {name}"
        for name in (
            "ask_ai_grounded",
            "canonical_report_adapter",
            "decision_report_delivery",
            "manager_decision_workspace",
        )
    )
    monkeypatch.setattr(
        verifier.subprocess,
        "run",
        lambda *_args, **_kwargs: types.SimpleNamespace(returncode=0, stdout=inventory, stderr=""),
    )

    result = verifier.verify_candidate(
        executable,
        sys.executable,
        expected_version="9.8.7",
        expected_build="654",
        expected_commit=expected_commit,
    )

    findings = result["exe"]["archive_content_scan"]["findings"]
    assert result["ok"] is False
    assert result["exe"]["developer_payload"]["ok"] is True
    assert {finding["rule"] for finding in findings} == {
        "credential_assignment",
        "forbidden_resource_name",
        "generated_customer_artifact",
        "internal_cisco_email",
        "known_roster_identifier",
    }
    assert {finding["path"] for finding in findings} == {
        "archive/config/settings.ini",
        "archive/compiled/adoptiq_backend.pyc",
        "archive/nested/secrets.env",
        "archive/reports/AdoptIQ_Report_customer.docx",
    }
    assert result["exe"]["archive_content_scan"]["errors"] == [
        r"archive entry could not be extracted: nested\secrets.env",
        "nested ZIP could not be read: archive/reports/AdoptIQ_Report_customer.docx",
        "developer candidate must contain exactly one config module",
        "developer config overlay marker is missing from frozen config",
    ]


def test_mac_native_verifier_extracts_compressed_payloads_and_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    release_modules: dict[str, object],
) -> None:
    verifier = release_modules["verify_developer_candidate"]
    app = tmp_path / "AdoptIQ.app"
    resources = app / "Contents" / "Resources"
    macos = app / "Contents" / "MacOS"
    resources.mkdir(parents=True)
    macos.mkdir(parents=True)
    executable = macos / "AdoptIQ.bin"
    executable.write_bytes(b"opaque-outer-binary-with-no-credential")
    executable.chmod(0o755)
    expected_commit = "d" * 40
    with (app / "Contents" / "Info.plist").open("wb") as handle:
        plistlib.dump(
            {
                "CFBundleIdentifier": "com.example.AdoptIQ",
                "CFBundleShortVersionString": "9.8.7",
                "CFBundleVersion": "654",
            },
            handle,
        )
    (resources / "DEVELOPER_ONLY_BUILD.txt").write_text(
        "Developer-only AdoptIQ candidate. Synthetic configuration only. "
        "No bundled credentials or prebaked customer corpus. Not production-ready.\n",
        encoding="utf-8",
    )
    (resources / "DEVELOPER_BUILD_METADATA.json").write_text(
        json.dumps(
            {
                "schema_version": "developer-build-metadata/v2",
                "developer_only": True,
                "production_ready": False,
                "sanitized_configuration": True,
                "sanitized_config_overlay_required": True,
                "prebaked_corpus_included": False,
                "bundled_credentials_included": False,
                "target_platform": "macos",
                "version": "9.8.7",
                "build": "654",
                "source_commit_sha": expected_commit,
            }
        ),
        encoding="utf-8",
    )
    (resources / "customer_aliases.defaults.json").write_text('{"schema_version":1,"groups":[]}', encoding="utf-8")
    (resources / "team_config.json").write_text(
        '{"team_roster":[{"email":"team.member@example.invalid"}]}',
        encoding="utf-8",
    )

    hidden_token = ("ghp_" + "Z" * 36).encode()
    payloads = {
        "compressed/innocent_module.pyc": b"compiled-prefix\0" + hidden_token,
        "compressed/unreadable_module.pyc": b"not-returned",
    }

    class FakeCArchiveReader:
        def __init__(self, _path: str) -> None:
            self.toc = dict(payloads)

        def extract(self, name: str) -> bytes:
            if name == "compressed/unreadable_module.pyc":
                raise ValueError("synthetic decompression failure")
            return payloads[name]

    readers = types.ModuleType("PyInstaller.archive.readers")
    readers.CArchiveReader = FakeCArchiveReader
    archive = types.ModuleType("PyInstaller.archive")
    archive.readers = readers
    pyinstaller = types.ModuleType("PyInstaller")
    pyinstaller.archive = archive
    monkeypatch.setitem(sys.modules, "PyInstaller", pyinstaller)
    monkeypatch.setitem(sys.modules, "PyInstaller.archive", archive)
    monkeypatch.setitem(sys.modules, "PyInstaller.archive.readers", readers)

    inventory = "\n".join(
        f"  {name}"
        for name in (
            "ask_ai_grounded",
            "canonical_report_adapter",
            "decision_report_delivery",
            "manager_decision_workspace",
        )
    )

    def fake_run(command: list[str]) -> tuple[dict[str, object], str, str]:
        stdout = inventory if "archive_viewer" in command else ""
        return {"ok": True, "return_code": 0, "output_sha256": "0" * 64}, stdout, ""

    monkeypatch.setattr(verifier, "_run", fake_run)
    result = verifier.verify_app(
        app,
        sys.executable,
        expected_version="9.8.7",
        expected_build="654",
        expected_commit=expected_commit,
    )

    archive_scan = result["archive_content_scan"]
    assert result["ok"] is False
    assert result["content_scan"]["ok"] is True
    assert archive_scan["entries_total"] == 2
    assert archive_scan["entries_scanned"] == 1
    assert archive_scan["findings"] == [
        {
            "rule": "github_classic_token",
            "path": "archive/compressed/innocent_module.pyc",
        }
    ]
    assert archive_scan["errors"] == [
        "archive entry could not be extracted: compressed/unreadable_module.pyc",
        "developer candidate must contain exactly one config module",
        "developer config overlay marker is missing from frozen config",
    ]
    assert hidden_token.decode() not in repr(result)


def test_carchive_scan_recurses_into_compressed_pyz_and_zip_payloads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    release_modules: dict[str, object],
) -> None:
    security = release_modules["developer_candidate_security"]
    executable = tmp_path / "AdoptIQ.exe"
    executable.write_bytes(b"MZ-opaque-native-fixture")

    hidden_pyz_token = ("ghp_" + "Q" * 36).encode()
    compressed_pyz_member = zlib.compress(b"compiled-prefix:" + hidden_pyz_token)
    assert hidden_pyz_token not in compressed_pyz_member

    hidden_zip_assignment = b"DATABASE_PASSWORD=ordinary-passphrase-123\n" * 100
    inner_zip_buffer = io.BytesIO()
    with zipfile.ZipFile(inner_zip_buffer, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        archive.writestr("config/runtime.ini", hidden_zip_assignment)
        archive.writestr("data/corpus.db.enc", b"synthetic-encrypted-corpus")
    inner_compressed_zip = inner_zip_buffer.getvalue()
    assert hidden_zip_assignment not in inner_compressed_zip

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        archive.writestr("vendor/resources.zip", inner_compressed_zip)
    compressed_zip = zip_buffer.getvalue()
    assert hidden_zip_assignment not in compressed_zip

    class FakePyzReader:
        toc = {
            "fsspec.implementations.zip": (0, 0, 13),
            "hidden.module": (0, 0, len(compressed_pyz_member)),
            "namespace.package": (3, 0, 0),
        }

        def extract(self, name: str, *, raw: bool = False) -> bytes | None:
            assert raw is True
            if name == "namespace.package":
                return None
            if name == "fsspec.implementations.zip":
                return b"compiled-safe"
            return zlib.decompress(compressed_pyz_member)

    payloads = {
        "PYZ.pyz": b"opaque-pyz-container:" + compressed_pyz_member,
        "base_library.zip": compressed_zip,
    }

    class FakeCArchiveReader:
        def __init__(self, _path: str) -> None:
            self.toc = {
                "PYZ.pyz": (0, len(payloads["PYZ.pyz"]), 0, 1, "z"),
                "base_library.zip": (
                    0,
                    len(payloads["base_library.zip"]),
                    0,
                    1,
                    "x",
                ),
            }

        def extract(self, name: str) -> bytes:
            return payloads[name]

        def open_embedded_archive(self, name: str) -> FakePyzReader:
            assert name == "PYZ.pyz"
            return FakePyzReader()

    readers = types.ModuleType("PyInstaller.archive.readers")
    readers.CArchiveReader = FakeCArchiveReader
    archive_module = types.ModuleType("PyInstaller.archive")
    archive_module.readers = readers
    pyinstaller = types.ModuleType("PyInstaller")
    pyinstaller.archive = archive_module
    monkeypatch.setitem(sys.modules, "PyInstaller", pyinstaller)
    monkeypatch.setitem(sys.modules, "PyInstaller.archive", archive_module)
    monkeypatch.setitem(sys.modules, "PyInstaller.archive.readers", readers)

    result, resources = security.scan_pyinstaller_carchive(executable)

    assert result["ok"] is False
    assert result["entries_total"] == 8
    assert result["entries_scanned"] == 8
    assert result["containers_scanned"] == 3
    assert result["namespace_entries_scanned"] == 1
    assert result["errors"] == []
    assert result["findings"] == [
        {
            "rule": "github_classic_token",
            "path": "archive/PYZ.pyz/hidden.module",
        },
        {
            "rule": "credential_assignment",
            "path": ("archive/base_library.zip/vendor/resources.zip/config/runtime.ini"),
        },
        {
            "rule": "forbidden_resource_name",
            "path": ("archive/base_library.zip/vendor/resources.zip/data/corpus.db.enc"),
        },
    ]
    assert resources == {}
    assert hidden_pyz_token.decode() not in repr(result)
    assert b"ordinary-passphrase-123" not in compressed_zip


def test_carchive_requires_compiled_developer_config_overlay_marker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    release_modules: dict[str, object],
) -> None:
    security = release_modules["developer_candidate_security"]
    executable = tmp_path / "AdoptIQ.exe"
    executable.write_bytes(b"MZ-opaque-native-fixture")
    marker = security.DEVELOPER_CONFIG_OVERLAY_MARKER

    class FakePyzReader:
        toc = {"config": (0, 0, len(marker))}

        def extract(self, name: str, *, raw: bool = False) -> bytes:
            assert name == "config"
            assert raw is True
            return b"compiled-prefix:" + marker

    class FakeCArchiveReader:
        def __init__(self, _path: str) -> None:
            self.toc = {"PYZ.pyz": (0, 8, 0, 1, "z")}

        def extract(self, name: str) -> bytes:
            assert name == "PYZ.pyz"
            return b"opaque-pyz"

        def open_embedded_archive(self, name: str) -> FakePyzReader:
            assert name == "PYZ.pyz"
            return FakePyzReader()

    readers = types.ModuleType("PyInstaller.archive.readers")
    readers.CArchiveReader = FakeCArchiveReader
    archive_module = types.ModuleType("PyInstaller.archive")
    archive_module.readers = readers
    pyinstaller = types.ModuleType("PyInstaller")
    pyinstaller.archive = archive_module
    monkeypatch.setitem(sys.modules, "PyInstaller", pyinstaller)
    monkeypatch.setitem(sys.modules, "PyInstaller.archive", archive_module)
    monkeypatch.setitem(sys.modules, "PyInstaller.archive.readers", readers)

    result, _resources = security.scan_pyinstaller_carchive(
        executable,
        require_developer_config_overlay=True,
    )

    assert result["ok"] is True
    assert result["config_module_entries"] == 1
    assert result["developer_config_overlay_marker_found"] is True


def test_scan_tree_recurses_into_external_and_self_extracting_zip_payloads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    release_modules: dict[str, object],
) -> None:
    security = release_modules["developer_candidate_security"]
    app = tmp_path / "AdoptIQ.app" / "Contents" / "Resources"
    app.mkdir(parents=True)
    hidden_token = ("ghp_" + "R" * 36).encode()
    internal_locator = b"private-service.synthetic.invalid"
    monkeypatch.setattr(
        security,
        "_KNOWN_INTERNAL_LOCATOR_DIGESTS",
        {hashlib.sha256(internal_locator).hexdigest()},
    )

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        archive.writestr("stdlib/hidden.pyc", hidden_token * 100)
        archive.writestr("config/internal.txt", (internal_locator + b"\n") * 100)
    compressed = zip_buffer.getvalue()
    assert hidden_token not in compressed
    assert internal_locator not in compressed
    (app / "base_library.zip").write_bytes(compressed)
    (app / "self-extracting.bin").write_bytes(b"synthetic-stub" + compressed)

    result = security.scan_tree(tmp_path / "AdoptIQ.app")

    assert result["ok"] is False
    assert result["archive_containers_scanned"] == 2
    assert result["archive_entries_total"] == 4
    assert result["archive_entries_scanned"] == 4
    assert {item["rule"] for item in result["findings"]} == {
        "github_classic_token",
        "internal_service_locator",
    }
    assert all(
        "base_library.zip/" in item["path"] or "self-extracting.bin/" in item["path"] for item in result["findings"]
    )
    assert hidden_token.decode() not in repr(result)
    assert internal_locator.decode() not in repr(result)


def test_scan_tree_fails_closed_for_corrupt_unsafe_and_over_budget_zips(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    release_modules: dict[str, object],
) -> None:
    security = release_modules["developer_candidate_security"]
    root = tmp_path / "bundle"
    root.mkdir()
    (root / "corrupt.zip").write_bytes(b"PK\x03\x04not-a-readable-zip")

    with zipfile.ZipFile(root / "unsafe.zip", "w") as archive:
        archive.writestr("../escape.txt", b"safe")
    with zipfile.ZipFile(root / "budget.zip", "w") as archive:
        archive.writestr("large.bin", b"12345")
    monkeypatch.setattr(security, "_MAX_NESTED_TOTAL_BYTES", 4)

    result = security.scan_tree(root)

    assert result["ok"] is False
    assert any("nested ZIP could not be read: corrupt.zip" in item for item in result["archive_errors"])
    assert any("unsafe path: unsafe.zip/../escape.txt" in item for item in result["archive_errors"])
    assert any("decompressed-byte limit exceeded: budget.zip/large.bin" in item for item in result["archive_errors"])


def test_carchive_scan_fails_closed_for_unreadable_nested_pyz_and_zip_entries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    release_modules: dict[str, object],
) -> None:
    security = release_modules["developer_candidate_security"]
    executable = tmp_path / "AdoptIQ.exe"
    executable.write_bytes(b"MZ-opaque-native-fixture")

    original_zip_payload = b"safe-but-corrupt"
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("broken/member.bin", original_zip_payload)
    corrupt_zip = bytearray(zip_buffer.getvalue())
    payload_offset = corrupt_zip.index(original_zip_payload)
    corrupt_zip[payload_offset] ^= 0x01

    class UnreadablePyzReader:
        toc = {"broken.module": (0, 0, 32)}

        def extract(self, _name: str, *, raw: bool = False) -> bytes:
            assert raw is True
            raise ValueError("synthetic PYZ decompression failure")

    payloads = {
        "PYZ.pyz": b"opaque-pyz-container",
        "base_library.zip": bytes(corrupt_zip),
    }

    class FakeCArchiveReader:
        def __init__(self, _path: str) -> None:
            self.toc = {
                "PYZ.pyz": (0, len(payloads["PYZ.pyz"]), 0, 1, "z"),
                "base_library.zip": (
                    0,
                    len(payloads["base_library.zip"]),
                    0,
                    1,
                    "x",
                ),
            }

        def extract(self, name: str) -> bytes:
            return payloads[name]

        def open_embedded_archive(self, name: str) -> UnreadablePyzReader:
            assert name == "PYZ.pyz"
            return UnreadablePyzReader()

    readers = types.ModuleType("PyInstaller.archive.readers")
    readers.CArchiveReader = FakeCArchiveReader
    archive_module = types.ModuleType("PyInstaller.archive")
    archive_module.readers = readers
    pyinstaller = types.ModuleType("PyInstaller")
    pyinstaller.archive = archive_module
    monkeypatch.setitem(sys.modules, "PyInstaller", pyinstaller)
    monkeypatch.setitem(sys.modules, "PyInstaller.archive", archive_module)
    monkeypatch.setitem(sys.modules, "PyInstaller.archive.readers", readers)

    result, _resources = security.scan_pyinstaller_carchive(executable)

    assert result["ok"] is False
    assert result["entries_total"] == 4
    assert result["entries_scanned"] == 2
    assert result["containers_scanned"] == 2
    assert result["findings"] == []
    assert result["errors"] == [
        "embedded PYZ entry could not be extracted: archive/PYZ.pyz/broken.module",
        ("nested ZIP entry could not be extracted: archive/base_library.zip/broken/member.bin"),
    ]


def test_mounted_dmg_scans_helper_files_outside_the_app(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    release_modules: dict[str, object],
) -> None:
    verifier = release_modules["verify_developer_candidate"]
    dmg = tmp_path / "AdoptIQ-v9.8.7-build654.dmg"
    dmg.write_bytes(b"synthetic-dmg")
    mounted = tmp_path / "mounted"
    (mounted / "AdoptIQ.app").mkdir(parents=True)
    (mounted / "Unblock AdoptIQ.command").write_text("CLIENT_SECRET=outer-helper-live-value\n", encoding="utf-8")
    attach_stdout = plistlib.dumps({"system-entities": [{"mount-point": str(mounted)}]}).decode("utf-8")

    def fake_run(command: list[str]) -> tuple[dict[str, object], str, str]:
        stdout = attach_stdout if "attach" in command else ""
        return {"ok": True, "return_code": 0, "output_sha256": "0" * 64}, stdout, ""

    monkeypatch.setattr(verifier, "_run", fake_run)
    monkeypatch.setattr(verifier, "verify_app", lambda *_args, **_kwargs: {"ok": True})
    result = verifier._mounted_dmg_app(
        dmg,
        sys.executable,
        expected_version="9.8.7",
        expected_build="654",
        expected_commit="e" * 40,
    )

    assert result["ok"] is False
    assert result["volume_content_scans"][0]["findings"] == [
        {
            "rule": "credential_assignment",
            "path": "Unblock AdoptIQ.command",
        }
    ]
    assert "mounted DMG outer payload content or credential scan failed" in result["errors"]


def test_archive_contract_requires_round147_modules_and_rejects_secrets(
    release_modules: dict[str, object],
) -> None:
    security = release_modules["developer_candidate_security"]
    valid_inventory = "\n".join(f"  {name}" for name in security.REQUIRED_ARCHIVE_MODULES)
    assert security.archive_inventory_contract(valid_inventory)["ok"] is True

    missing = security.archive_inventory_contract("  ask_ai_grounded\n")
    assert "canonical_report_adapter" in missing["missing_required_modules"]
    forbidden = security.archive_inventory_contract(valid_inventory + "\n  _bundled_secrets\n")
    assert forbidden["ok"] is False
    assert forbidden["forbidden_entries"] == ["_bundled_secrets"]


def test_staged_candidate_round_trip_and_tamper_detection(tmp_path: Path, release_modules: dict[str, object]) -> None:
    provenance = release_modules["candidate_provenance"]
    source = tmp_path / "AdoptIQ.exe"
    source.write_bytes(b"MZ-native-fixture")
    stage = tmp_path / "nested" / "candidate"
    stage.mkdir(parents=True)
    (stage / "stale.txt").write_text("stale", encoding="utf-8")

    created = provenance.stage_candidate(
        stage,
        [source],
        target_platform="windows",
        version="9.8.7",
        build="654",
        source_commit_sha="b" * 40,
    )
    assert created["ok"] is True
    assert not (stage / "stale.txt").exists()
    assert (stage / "CHECKSUMS.sha256").is_file()
    assert (stage / "candidate_provenance.json").is_file()
    assert created["outer_payload_scan"]["ok"] is True
    assert {item["name"] for item in created["outer_payload_scan"]["files"]} == {
        "AdoptIQ.exe",
        "CHECKSUMS.sha256",
        "candidate_provenance.json",
    }

    (stage / "AdoptIQ.exe").write_bytes(b"MZ-tampered")
    checked = provenance.verify_staged_candidate(
        stage,
        expected_platform="windows",
        expected_version="9.8.7",
        expected_build="654",
        expected_commit="b" * 40,
    )
    assert checked["ok"] is False
    assert "checksum mismatch: AdoptIQ.exe" in checked["errors"]


@pytest.mark.parametrize("target_platform", ["macos", "windows"])
def test_staged_outer_payload_scan_rejects_malicious_helpers_docs_and_corpus(
    tmp_path: Path,
    target_platform: str,
    release_modules: dict[str, object],
) -> None:
    provenance = release_modules["candidate_provenance"]
    sources = tmp_path / f"sources-{target_platform}"
    sources.mkdir()
    candidate_name = "AdoptIQ.dmg" if target_platform == "macos" else "AdoptIQ.exe"
    candidate = sources / candidate_name
    candidate.write_bytes(b"safe-native-outer-bytes")
    readme = sources / "README.md"
    readme.write_text(
        "ACCESS_TOKEN=outer-doc-live-value\n" + "Roster example: " + "Samuel" + " " + "Tamayo\n",
        encoding="utf-8",
    )
    helper = sources / "Run_AdoptIQ.bat"
    helper.write_text(
        "CLIENT_SECRET=outer-helper-live-value\n" + "synthetic.user" + "@" + "cisco.com\n",
        encoding="utf-8",
    )
    corpus = sources / "corpus.db.enc"
    corpus.write_bytes(b"synthetic-customer-corpus")
    report = sources / "AdoptIQ_Report_real_customer.docx"
    report.write_bytes(b"synthetic-generated-report")
    stage = tmp_path / "stages" / target_platform

    with pytest.raises(RuntimeError, match="failed self-verification"):
        provenance.stage_candidate(
            stage,
            [candidate, readme, helper, corpus, report],
            target_platform=target_platform,
            version="9.8.7",
            build="654",
            source_commit_sha="f" * 40,
        )
    checked = provenance.verify_staged_candidate(
        stage,
        expected_platform=target_platform,
        expected_version="9.8.7",
        expected_build="654",
        expected_commit="f" * 40,
    )

    assert checked["ok"] is False
    outer_scan = checked["outer_payload_scan"]
    assert {finding["rule"] for finding in outer_scan["findings"]} == {
        "credential_assignment",
        "forbidden_resource_name",
        "generated_customer_artifact",
        "internal_cisco_email",
        "known_roster_identifier",
    }
    assert {finding["path"] for finding in outer_scan["findings"]} == {
        "outer/README.md",
        "outer/Run_AdoptIQ.bat",
        "outer/corpus.db.enc",
        "outer/AdoptIQ_Report_real_customer.docx",
    }
    integrity = {
        item["name"]: item
        for item in outer_scan["files"]
        if item["name"] in {"CHECKSUMS.sha256", "candidate_provenance.json"}
    }
    assert set(integrity) == {"CHECKSUMS.sha256", "candidate_provenance.json"}
    assert all(item["content_scanned"] and item["ok"] for item in integrity.values())


def test_runtime_identity_requires_exact_version_build_and_frozen(
    release_modules: dict[str, object],
) -> None:
    smoke = release_modules["smoke_frozen_candidate"]
    valid = {"ok": True, "frozen": True, "version": "9.8.7", "build": "654"}
    assert smoke.validate_version_payload(valid, expected_version="9.8.7", expected_build="654") == []
    invalid = {**valid, "frozen": False, "build": "653"}
    errors = smoke.validate_version_payload(invalid, expected_version="9.8.7", expected_build="654")
    assert "version endpoint did not report frozen=true" in errors
    assert "running build does not match the expected build" in errors


def test_specs_pin_round147_modules_and_sanitized_payload() -> None:
    for spec_name in ("adoptiq_mac.spec", "adoptiq_pc.spec"):
        source = (ROOT / spec_name).read_text(encoding="utf-8")
        for module in (
            "ask_ai_grounded",
            "canonical_report_adapter",
            "decision_report_delivery",
            "manager_decision_workspace",
        ):
            assert_in_source(source, f"'{module}'", label='source')
        assert_in_source(source, "prepare_developer_payload", label='source')
        assert_in_source(source, "bind_developer_source_overlays", label='source')
        assert_in_source(source, "DEVELOPER_SOURCE_OVERLAY_MODULES", label='source')
        assert_in_source(source, "DEVELOPER_PAYLOAD[f'{module_name}_overlay']", label='source')
        assert_in_source(source, "optimize=2 if DEVELOPER_ONLY else -1", label='source')
        assert_in_source(source, "developer_payload['team_config']", label='source')
        assert_in_source(source, "developer_payload['metadata']", label='source')
        assert_in_source(source, "developer_payload['templates_root']", label='source')
        assert_in_source(source, "developer_payload['static_root']", label='source')
        assert "(os.path.join(root, 'team_config.json'), '.')" in source


def test_developer_readmes_replace_internal_release_history() -> None:
    mac_build = (ROOT / "build_mac.sh").read_text(encoding="utf-8")
    mac_wrapper = (ROOT / "build_mac_dmg.sh").read_text(encoding="utf-8")
    workflow = (ROOT / ".github/workflows/build.yml").read_text(encoding="utf-8")

    assert "build/developer-only-payload/macos/README.md" in mac_build
    assert "generated developer-candidate README is missing" in mac_build
    assert "generated developer-candidate README is missing" in mac_wrapper
    assert r"build\developer-only-payload\windows\README.md" in workflow
    assert "Copy-Item $developerReadme OUTBOX\\README.md" in workflow
    assert "Copy-Item README.md OUTBOX\\README.md" in workflow


def test_workflow_smokes_both_platforms_and_rechecks_uploaded_bytes() -> None:
    source = (ROOT / ".github/workflows/build.yml").read_text(encoding="utf-8")
    assert source.count("scripts/smoke_frozen_candidate.py") == 2
    assert source.count(r"scripts\smoke_frozen_candidate.py") == 2
    assert source.count("actions/download-artifact@v4") == 2
    assert source.count("scripts/candidate_provenance.py verify") == 1
    assert source.count(r"scripts\candidate_provenance.py verify") == 1
    assert source.count("--expected-commit") == 4
    assert_in_source(source, "--skip-loose-app", label='source')
    assert "CHECKSUMS.sha256" not in source  # generated, never hand-authored
