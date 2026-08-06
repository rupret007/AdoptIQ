"""Developer-only frozen candidate must exclude credentials and corpus data."""
from source_shape_utils import assert_in_source

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _source(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def test_mac_build_has_explicit_credential_free_developer_mode() -> None:
    source = _source("build_mac.sh")
    assert_in_source(source, "ADOPTIQ_DEVELOPER_ONLY", label='source')
    assert_in_source(source, "bundled credentials are disabled", label='source')
    assert_in_source(source, "embed_credentials.py", label='source')
    assert_in_source(source, "if [[ \"$ADOPTIQ_DEVELOPER_ONLY\" == \"1\" ]]", label='source')
    assert_in_source(source, 'OUTBOX_DIR="$ROOT_DIR/developer_candidates"', label='source')
    assert_in_source(source, "build/developer-only-payload/macos/README.md", label='source')
    assert_in_source(source, 'if [[ "$ADOPTIQ_DEVELOPER_ONLY" == "1" ]]', label='source')


def test_dmg_wrapper_forces_no_bake_and_rejects_release_gate() -> None:
    source = _source("build_mac_dmg.sh")
    assert_in_source(source, "export ADOPTIQ_BAKE_CORPUS=0", label='source')
    assert_in_source(source, "cannot pass ADOPTIQ_RELEASE_GATE=1", label='source')
    assert_in_source(source, "Release manifests and external mirrors were intentionally skipped", label='source')


def test_spec_excludes_secrets_corpus_and_embeddings_in_developer_mode() -> None:
    source = _source("adoptiq_mac.spec")
    assert_in_source(source, "prepare_developer_payload", label='source')
    assert_in_source(source, "(developer_payload['marker'], '.')", label='source')
    assert_in_source(source, "(developer_payload['metadata'], '.')", label='source')
    assert_in_source(source, "analysis_excludes.append('_bundled_secrets')", label='source')
    assert_in_source(source, "if not DEVELOPER_ONLY and os.path.isdir(embeddings_dir)", label='source')
    assert_in_source(source, "if DEVELOPER_ONLY:", label='source')
    assert "(os.path.join(root, 'team_config.json'), '.')" in source


def test_candidate_verifier_checks_marker_archive_signature_and_dmg() -> None:
    source = _source("scripts/verify_developer_candidate.py")
    for required in (
        "developer_candidate_security",
        "archive_inventory_contract",
        "validate_developer_payload",
        "scan_tree",
        "sha256_tree",
        "archive_viewer",
        "codesign",
        "hdiutil",
        "production_ready",
    ):
        assert required in source


def test_windows_spec_is_credential_free_and_chart_capable_in_developer_mode() -> None:
    source = _source("adoptiq_pc.spec")
    for required in (
        "ADOPTIQ_DEVELOPER_ONLY",
        "prepare_developer_payload",
        "(developer_payload['metadata'], '.')",
        "analysis_excludes.append('_bundled_secrets')",
        "'matplotlib.backends.backend_agg'",
        "'PIL.Image'",
        "'ask_ai_reranker'",
        "'corpus_share_url_resolver'",
    ):
        assert required in source
    excludes = source[source.index("analysis_excludes = [") : source.index("a = Analysis(")]
    assert "'matplotlib'" not in excludes
    assert "'PIL'" not in excludes
    assert "(os.path.join(root, 'team_config.json'), '.')" in source


def test_windows_candidate_verifier_checks_native_archive_and_sensitive_data() -> None:
    source = _source("scripts/verify_windows_developer_candidate.py")
    for required in (
        "developer_candidate_security",
        "archive_inventory_contract",
        "validate_developer_payload",
        "scan_pyinstaller_carchive",
        "scan_file",
        "archive_viewer",
        'b"MZ"',
        "production_ready",
    ):
        assert required in source

    shared_security = _source("scripts/developer_candidate_security.py")
    assert "CArchiveReader" in shared_security


def test_cloud_build_defaults_to_credential_free_native_candidates() -> None:
    source = _source(".github/workflows/build.yml")
    for required in (
        "developer_only:",
        "default: true",
        "ADOPTIQ_DEVELOPER_ONLY",
        "Verify developer-only macOS candidate",
        "Verify developer-only Windows candidate",
        "Smoke developer-only Windows candidate",
        "windows_developer_candidate_verification.json",
        "build\\developer-only-payload\\windows\\README.md",
        "Copy-Item $developerReadme OUTBOX\\README.md",
    ):
        assert required in source
    # Secrets + embedding stay production-only on both platforms, and the two
    # production upload steps remain isolated from developer artifacts.
    assert source.count("if: env.ADOPTIQ_DEVELOPER_ONLY != '1'") == 6
