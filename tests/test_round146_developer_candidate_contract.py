"""Developer-only frozen candidate must exclude credentials and corpus data."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _source(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def test_mac_build_has_explicit_credential_free_developer_mode() -> None:
    source = _source("build_mac.sh")
    assert "ADOPTIQ_DEVELOPER_ONLY" in source
    assert "bundled credentials are disabled" in source
    assert "embed_credentials.py" in source
    assert "if [[ \"$ADOPTIQ_DEVELOPER_ONLY\" == \"1\" ]]" in source
    assert 'OUTBOX_DIR="$ROOT_DIR/developer_candidates"' in source


def test_dmg_wrapper_forces_no_bake_and_rejects_release_gate() -> None:
    source = _source("build_mac_dmg.sh")
    assert "export ADOPTIQ_BAKE_CORPUS=0" in source
    assert "cannot pass ADOPTIQ_RELEASE_GATE=1" in source
    assert "Release manifests and external mirrors were intentionally skipped" in source


def test_spec_excludes_secrets_corpus_and_embeddings_in_developer_mode() -> None:
    source = _source("adoptiq_mac.spec")
    assert "DEVELOPER_ONLY_BUILD.txt" in source
    assert "datas.append((marker_path, '.'))" in source
    assert "datas.append((marker_path, 'Resources'))" not in source
    assert "analysis_excludes.append('_bundled_secrets')" in source
    assert "if not DEVELOPER_ONLY and os.path.isdir(embeddings_dir)" in source
    assert "if DEVELOPER_ONLY:" in source


def test_candidate_verifier_checks_marker_archive_signature_and_dmg() -> None:
    source = _source("scripts/verify_developer_candidate.py")
    for required in (
        "DEVELOPER_ONLY_BUILD.txt",
        "_bundled_secrets",
        "corpus.db.enc",
        "archive_viewer",
        "codesign",
        "hdiutil",
        "production_ready",
    ):
        assert required in source
