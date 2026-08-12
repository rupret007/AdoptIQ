from __future__ import annotations

import importlib.util
import json
import stat
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture()
def preflight_module():
    path = ROOT / "scripts" / "preflight_mac_release.py"
    spec = importlib.util.spec_from_file_location("round164_mac_preflight", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    try:
        yield module
    finally:
        sys.modules.pop(spec.name, None)


@pytest.fixture()
def promotion_module():
    path = ROOT / "scripts" / "promote_mac_release.py"
    spec = importlib.util.spec_from_file_location("round164_mac_promotion", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    try:
        yield module
    finally:
        sys.modules.pop(spec.name, None)


def test_secret_check_is_fail_closed_and_never_returns_values(tmp_path, monkeypatch, preflight_module) -> None:
    secret_value = "NEVER_ECHO_THIS_SECRET_VALUE"
    secrets = tmp_path / "secrets.env"
    secrets.write_text(
        "\n".join(
            [
                f"ADOPTIQ_SECRET_KEY={secret_value}",
                "ADOPTIQ_ADMIN_SECRET_KEY=admin-value",
                "CIRCUIT_APP_KEY=app-value",
                "CIRCUIT_CLIENT_ID=client-value",
                "CIRCUIT_CLIENT_SECRET=client-secret",
                "SNOWFLAKE_ACCOUNT=account-value",
                "SNOWFLAKE_PASSWORD=password-value",
                "SNOWFLAKE_USER=user-value",
                "BST_API_KEY=bst-key",
                "BST_CLIENT_SECRET=bst-secret",
                "PSIRT_API_KEY=psirt-key",
                "PSIRT_CLIENT_SECRET=psirt-secret",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    secrets.chmod(0o600)

    class Result:
        def __init__(self, returncode: int):
            self.returncode = returncode
            self.stdout = ""

    def fake_run(argv, *, cwd):
        del cwd
        return Result(1 if "ls-files" in argv else 0)

    monkeypatch.setattr(preflight_module, "_run", fake_run)
    results, metadata = preflight_module.check_secrets(secrets, root=tmp_path)
    rendered = repr((results, metadata))

    assert all(result.status == "PASS" for result in results)
    assert secret_value not in rendered
    assert metadata == {"path": "secrets.env", "key_count": 12}
    assert stat.S_IMODE(secrets.stat().st_mode) == 0o600


def test_secret_check_requires_all_source_integration_pairs(tmp_path, monkeypatch, preflight_module) -> None:
    secrets = tmp_path / "secrets.env"
    secrets.write_text(
        "ADOPTIQ_SECRET_KEY=main-key-value\n"
        "ADOPTIQ_ADMIN_SECRET_KEY=admin-key-value\n"
        "CIRCUIT_APP_KEY=circuit-app-value\n"
        "CIRCUIT_CLIENT_ID=circuit-client-value\n"
        "CIRCUIT_CLIENT_SECRET=circuit-secret-value\n"
        "KEEPER_ROLE_ID=keeper-role-value\n"
        "KEEPER_SECRET_ID=keeper-secret-value\n",
        encoding="utf-8",
    )
    secrets.chmod(0o600)

    class Result:
        def __init__(self, returncode: int):
            self.returncode = returncode
            self.stdout = ""

    monkeypatch.setattr(
        preflight_module,
        "_run",
        lambda argv, *, cwd: Result(1 if "ls-files" in argv else 0),
    )
    results, _ = preflight_module.check_secrets(secrets, root=tmp_path)
    failures = {result.name: result.detail for result in results if result.status == "FAIL"}

    assert "BST integration credentials" in failures
    assert "PSIRT integration credentials" in failures
    assert "Snowflake credential path" not in failures


def test_secret_check_rejects_file_outside_repository(tmp_path, preflight_module) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    external = tmp_path / "secrets.env"
    external.write_text("ADOPTIQ_SECRET_KEY=never-used\n", encoding="utf-8")
    external.chmod(0o600)

    results, metadata = preflight_module.check_secrets(external, root=root)

    assert [(result.name, result.status) for result in results] == [
        ("secrets location", "FAIL")
    ]
    assert metadata == {"path": "secrets.env", "key_count": 0}


def test_corpus_check_rejects_unsafe_supported_inputs_recursively(
    tmp_path, preflight_module
) -> None:
    source = tmp_path / "approved-corpus"
    source.mkdir()
    (source / "cases.xlsx").write_bytes(b"not-empty")
    (source / "placeholder.csv").write_bytes(b"")
    external = tmp_path / "external.docx"
    external.write_bytes(b"not-empty")
    (source / "linked.docx").symlink_to(external)

    results, metadata = preflight_module.check_corpus_source(source)
    by_name = {result.name: result for result in results}

    assert by_name["parseable corpus inputs"].status == "PASS"
    assert by_name["corpus symlink boundary"].status == "FAIL"
    assert by_name["OneDrive hydration"].status == "FAIL"
    assert metadata["file_count"] == 1
    assert metadata["zero_byte_count"] == 1
    assert metadata["symlink_count"] == 1


@pytest.mark.parametrize("link_kind", ["unsupported_file", "directory"])
def test_corpus_check_rejects_every_nested_symlink_entry(
    tmp_path, preflight_module, link_kind: str
) -> None:
    source = tmp_path / "approved-corpus"
    nested = source / "nested"
    nested.mkdir(parents=True)
    (source / "cases.csv").write_bytes(b"customer_name\nAcme\n")

    if link_kind == "unsupported_file":
        outside = tmp_path / "outside.pdf"
        outside.write_bytes(b"not-a-corpus-input")
        (nested / "linked.pdf").symlink_to(outside)
    else:
        outside = tmp_path / "outside-directory"
        outside.mkdir()
        (outside / "escaped.csv").write_bytes(b"customer_name\nEscaped\n")
        (nested / "linked-directory").symlink_to(outside, target_is_directory=True)

    results, metadata = preflight_module.check_corpus_source(source)
    by_name = {result.name: result for result in results}

    assert by_name["parseable corpus inputs"].status == "PASS"
    assert by_name["corpus symlink boundary"].status == "FAIL"
    assert metadata["file_count"] == 1
    assert metadata["symlink_count"] == 1


def test_bake_staging_skips_only_inputs_the_indexer_cannot_use(tmp_path) -> None:
    from scripts.bake_corpus import _stage_source_files

    source = tmp_path / "source"
    staged = tmp_path / "staged"
    source.mkdir()
    (source / "cases.csv").write_bytes(b"customer_name\nAcme\n")
    (source / "notes.docx").write_bytes(b"docx")
    (source / "barriers.xlsx").write_bytes(b"xlsx")
    (source / "ignored.pdf").write_bytes(b"pdf")
    (source / "placeholder.xlsx").write_bytes(b"")
    outside = tmp_path / "outside.csv"
    outside.write_bytes(b"outside")
    (source / "linked.csv").symlink_to(outside)

    assert _stage_source_files(source, staged) == 2
    (source / "placeholder.xlsx").unlink()
    (source / "linked.csv").unlink()
    nested = source / "nested"
    nested.mkdir()
    (nested / "more.csv").write_bytes(b"customer_name\nBeta\n")
    assert _stage_source_files(source, staged) == 0
    assert sorted(path.name for path in staged.iterdir()) == [
        "barriers.xlsx",
        "cases.csv",
        "nested",
        "notes.docx",
    ]
    assert (staged / "nested" / "more.csv").is_file()


@pytest.mark.parametrize("link_kind", ["unsupported_file", "directory"])
def test_bake_staging_rejects_every_nested_symlink_before_copy(
    tmp_path, link_kind: str
) -> None:
    from scripts.bake_corpus import _stage_source_files

    source = tmp_path / "source"
    nested = source / "nested"
    staged = tmp_path / "staged"
    nested.mkdir(parents=True)
    (source / "cases.csv").write_bytes(b"customer_name\nAcme\n")

    if link_kind == "unsupported_file":
        outside = tmp_path / "outside.pdf"
        outside.write_bytes(b"not-a-corpus-input")
        (nested / "linked.pdf").symlink_to(outside)
    else:
        outside = tmp_path / "outside-directory"
        outside.mkdir()
        (outside / "escaped.csv").write_bytes(b"customer_name\nEscaped\n")
        (nested / "linked-directory").symlink_to(outside, target_is_directory=True)

    assert _stage_source_files(source, staged) == 2
    assert list(staged.rglob("*")) == []


def test_bake_emits_phase_timings_without_skipping_quality_gates() -> None:
    source = (ROOT / "scripts" / "bake_corpus.py").read_text(encoding="utf-8")

    for phase in (
        "stage_inputs",
        "parse_and_lexical_index",
        "dense_vectors",
        "reranker_self_test",
        "encrypt_and_commit",
        "decrypt_round_trip",
        "total",
    ):
        assert f'"{phase}"' in source
    assert "strict=True" in source
    assert "lexical-only validation corpus" in source
    assert "refusing to bake a partial corpus" in source


def test_bake_seals_finished_database_once_and_reopens(tmp_path, monkeypatch) -> None:
    import corpus_crypto
    from scripts import bake_corpus

    source = tmp_path / "source"
    source.mkdir()
    (source / "cases.csv").write_text(
        "customer_name,case_number,Title\n"
        "Acme,CASE-1,Calling adoption is blocked by a persistent routing issue "
        "that requires a validated remediation plan before production rollout\n",
        encoding="utf-8",
    )
    bake_dir = tmp_path / "bake"
    original_commit = corpus_crypto.EncryptedCorpusHandle.commit_to_disk
    commit_count = 0

    def counted_commit(handle):
        nonlocal commit_count
        commit_count += 1
        return original_commit(handle)

    monkeypatch.setattr(corpus_crypto.EncryptedCorpusHandle, "commit_to_disk", counted_commit)
    def fake_vectors(conn):
        chunk_ids = [row[0] for row in conn.execute('SELECT "id" FROM "playbook_chunks"')]
        for chunk_id in chunk_ids:
            conn.execute(
                'INSERT INTO "chunk_vectors" ("chunk_id", "model_id", "model_dim", "vector") '
                'VALUES (?, ?, ?, ?)',
                (chunk_id, "test-model", 384, b"\x00" * (384 * 4)),
            )
        conn.commit()
        return (len(chunk_ids), "test-model", 384)

    monkeypatch.setattr(bake_corpus, "_bake_chunk_vectors", fake_vectors)
    monkeypatch.setattr(bake_corpus, "_bake_reranker_self_test", lambda: (True, "stubbed"))
    monkeypatch.delenv("ADOPTIQ_BAKE_CORPUS", raising=False)

    assert bake_corpus.main(["--bake-dir", str(bake_dir), "--source", str(source)]) == 0
    assert commit_count == 1

    reopened = corpus_crypto.open_corpus_for_user(
        onedrive_root=None,
        encrypted_path=bake_dir / "corpus.db.enc",
        create_if_missing=False,
        allow_local_sentinel=True,
    )
    try:
        assert reopened.conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0] == 1
    finally:
        reopened.close(persist=False)


def test_release_bake_rejects_nonempty_but_corrupt_supported_source(
    tmp_path, monkeypatch
) -> None:
    from scripts import bake_corpus

    source = tmp_path / "source"
    source.mkdir()
    (source / "corrupt.xlsx").write_bytes(b"not-an-xlsx-container")
    monkeypatch.delenv("ADOPTIQ_BAKE_CORPUS", raising=False)

    assert bake_corpus.main(
        ["--bake-dir", str(tmp_path / "bake"), "--source", str(source)]
    ) == 6


def test_release_bake_rejects_vector_count_mismatch(tmp_path, monkeypatch) -> None:
    from scripts import bake_corpus

    source = tmp_path / "source"
    source.mkdir()
    (source / "cases.csv").write_text(
        "customer_name,case_number,Title\n"
        "Acme,CASE-1,Calling adoption is blocked by a persistent routing issue "
        "that requires a validated remediation plan before production rollout\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        bake_corpus,
        "_bake_chunk_vectors",
        lambda _conn: (1, "test-model", 384),
    )
    monkeypatch.setattr(
        bake_corpus,
        "_bake_reranker_self_test",
        lambda: (True, "not reached"),
    )
    monkeypatch.delenv("ADOPTIQ_BAKE_CORPUS", raising=False)

    assert bake_corpus.main(
        ["--bake-dir", str(tmp_path / "bake"), "--source", str(source)]
    ) == 7


def test_strict_vector_writer_rejects_zero_vectors(monkeypatch) -> None:
    import sqlite3

    import numpy as np

    from ask_ai_vector_store import upsert_chunk_vectors
    from knowledge_schema import apply_schema

    connection = sqlite3.connect(":memory:")
    apply_schema(connection)
    monkeypatch.setattr("ask_ai_embeddings.get_embedder", lambda: object())
    monkeypatch.setattr(
        "ask_ai_embeddings.embed_texts",
        lambda _texts: np.zeros((1, 384), dtype=np.float32),
    )
    with pytest.raises(RuntimeError, match="invalid vector"):
        upsert_chunk_vectors(
            connection,
            chunk_rows=[(1, "a meaningful release corpus passage")],
            strict=True,
        )
    assert connection.execute('SELECT COUNT(*) FROM "chunk_vectors"').fetchone()[0] == 0
    connection.close()


def test_embedding_and_reranker_share_persistent_release_model_cache() -> None:
    embeddings = (ROOT / "ask_ai_embeddings.py").read_text(encoding="utf-8")
    reranker = (ROOT / "ask_ai_reranker.py").read_text(encoding="utf-8")

    for source in (embeddings, reranker):
        assert 'os.environ.get("ADOPTIQ_FASTEMBED_CACHE")' in source
        assert 'os.environ.get("FASTEMBED_CACHE_PATH")' in source
        assert 'kwargs["cache_dir"]' in source
        assert 'kwargs["local_files_only"] = True' in source


def _write_fastembed_model_cache(
    source: Path,
    *,
    cache_name: str,
    model_file: str,
    revision_character: str,
) -> None:
    revision = revision_character * 40
    model_root = source / cache_name
    snapshot = model_root / "snapshots" / revision
    (model_root / "refs").mkdir(parents=True)
    (model_root / "refs" / "main").write_text(revision, encoding="ascii")
    files = {
        "config.json": '{"pad_token_id": 0}',
        "special_tokens_map.json": '{"pad_token": "[PAD]"}',
        "tokenizer.json": "{}",
        "tokenizer_config.json": (
            '{"model_max_length": 512, "pad_token": "[PAD]"}'
        ),
    }
    for relative, body in files.items():
        target = snapshot / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    model = snapshot / model_file
    model.parent.mkdir(parents=True, exist_ok=True)
    model.write_bytes((cache_name + "-model-bytes").encode())
    metadata = {
        relative: {
            "size": (snapshot / relative).stat().st_size,
            "blob_id": revision_character * 64,
        }
        for relative in (*files, model_file)
    }
    (model_root / "files_metadata.json").write_text(
        json.dumps(metadata), encoding="utf-8"
    )
    tree_metadata = {
        "format_version": 1,
        "files": {
            relative: {
                "size": (snapshot / relative).stat().st_size,
                "blob_id": revision_character * 40,
            }
            for relative in (*files, model_file)
        },
    }
    tree = model_root / "trees" / f"{revision}.json"
    tree.parent.mkdir()
    tree.write_text(json.dumps(tree_metadata), encoding="utf-8")


def _write_fastembed_cache(source: Path, *, include_reranker: bool = True) -> None:
    source.mkdir()
    _write_fastembed_model_cache(
        source,
        cache_name="models--qdrant--bge-small-en-v1.5-onnx-q",
        model_file="model_optimized.onnx",
        revision_character="a",
    )
    if include_reranker:
        _write_fastembed_model_cache(
            source,
            cache_name="models--Xenova--ms-marco-MiniLM-L-6-v2",
            model_file="onnx/model.onnx",
            revision_character="b",
        )


def test_release_model_staging_is_deterministic_and_rejects_escape(tmp_path) -> None:
    from scripts.stage_release_models import stage_release_models

    root = tmp_path / "repo"
    source = tmp_path / "approved-cache"
    _write_fastembed_cache(source)
    destination = root / "embeddings" / "release_fastembed_cache"

    payload = stage_release_models(source, destination, repo_root=root)
    manifest = json.loads(
        (destination / "adoptiq_model_manifest.json").read_text(encoding="utf-8")
    )
    assert payload == manifest
    assert manifest["file_count"] == 16
    assert all(len(item["sha256"]) == 64 for item in manifest["files"])
    assert all(
        (destination / str(item["path"])).is_file() for item in manifest["files"]
    )

    with pytest.raises(ValueError, match="destination"):
        stage_release_models(source, tmp_path / "unsafe", repo_root=root)


def test_release_model_staging_requires_both_model_payloads(tmp_path) -> None:
    from scripts.stage_release_models import stage_release_models

    root = tmp_path / "repo"
    source = tmp_path / "incomplete-cache"
    _write_fastembed_cache(source, include_reranker=False)
    with pytest.raises(ValueError, match="exact reranker layout"):
        stage_release_models(
            source,
            root / "embeddings" / "release_fastembed_cache",
            repo_root=root,
        )


@pytest.mark.parametrize(
    "unrelated_path",
    (
        Path("secrets.env"),
        Path("models--qdrant--bge-small-en-v1.5-onnx-q")
        / "snapshots"
        / ("a" * 40)
        / "extra-notes.txt",
    ),
)
def test_release_model_staging_rejects_unrelated_files_without_replacing_output(
    tmp_path, unrelated_path
) -> None:
    from scripts.stage_release_models import stage_release_models

    root = tmp_path / "repo"
    source = tmp_path / "approved-cache"
    destination = root / "embeddings" / "release_fastembed_cache"
    _write_fastembed_cache(source)
    stage_release_models(source, destination, repo_root=root)
    manifest_before = (destination / "adoptiq_model_manifest.json").read_bytes()

    extra = source / unrelated_path
    extra.parent.mkdir(parents=True, exist_ok=True)
    extra.write_text("must-never-enter-the-release", encoding="utf-8")
    with pytest.raises(ValueError, match="unrelated file"):
        stage_release_models(source, destination, repo_root=root)

    assert (destination / "adoptiq_model_manifest.json").read_bytes() == manifest_before
    assert not any(path.name == unrelated_path.name for path in destination.rglob("*"))


def test_build_113_release_gate_invokes_preflight_before_bake() -> None:
    source = (ROOT / "build_mac_dmg.sh").read_text(encoding="utf-8")
    preflight_at = source.index('scripts/preflight_mac_release.py "${PREFLIGHT_ARGS[@]}"')
    bake_at = source.index('scripts/bake_corpus.py \\\n')

    assert preflight_at < bake_at
    assert 'if [[ "${ADOPTIQ_RELEASE_GATE:-0}" == "1" ]]' in source
    assert "--expected-commit" in source
    assert "--expected-arch" in source
    assert "ADOPTIQ_MAC_PREFLIGHT_SUMMARY" in source
    assert "ADOPTIQ_RELEASE_GATE=1 requires ADOPTIQ_BAKE_CORPUS=1" in source
    assert "ADOPTIQ_BAKE_EXTRA_ARGS cannot include --no-bake" in source
    assert "scripts/stage_release_models.py" in source
    assert "source changed while PyInstaller was running" in source
    assert "same-name local evidence" in source


def test_mac_candidate_is_stage_only_until_explicit_promotion() -> None:
    source = (ROOT / "build_mac_dmg.sh").read_text(encoding="utf-8")

    gate = source.index('PUBLISH_RELEASE_RAW="${ADOPTIQ_PUBLISH_RELEASE:-0}"')
    mirrors = source.index('MAC_STAGING_DIR="${MAC_STAGING_DIR:-')
    assert gate < mirrors
    assert "publication intentionally skipped" in source
    assert "Direct publish during packaging is retired" in source
    assert "assert_safe_release_mirror" in source
    assert '"AdoptIQ.exe"|"Run_AdoptIQ.bat"|"Unblock_AdoptIQ.bat"' in source


def test_mac_build_scrubs_generated_credential_bundle_on_every_exit() -> None:
    source = (ROOT / "build_mac_dmg.sh").read_text(encoding="utf-8")

    assert "cleanup_generated_secrets" in source
    assert 'rm -f "$ROOT_DIR/_bundled_secrets.py"' in source
    assert source.index("trap cleanup_generated_secrets EXIT") < source.index("scripts/bake_corpus.py")


def test_promotion_requires_live_same_commit_acceptance(tmp_path, promotion_module) -> None:
    summary = tmp_path / "acceptance.json"
    summary.write_text(
        """{
          "schema_version": "round146-portable-acceptance/v1",
          "profile": "work-machine",
          "all_passed": true,
          "acceptance_complete": true,
          "live_validation_performed": true,
          "live_validation_passed": true,
          "skipped_gates": [],
          "git": {"sha": "abc123", "branch": "main", "dirty": false},
          "gates": {
            "runtime_identity": {
              "ok": true,
              "status": "passed",
              "version": "1.0.4",
              "build": "113",
              "frozen": true,
              "restart_required": false,
              "live_validation_performed": true,
              "status_code": 200
            }
          }
        }""",
        encoding="utf-8",
    )

    payload = promotion_module._validate_live_acceptance(
        summary,
        expected_commit="abc123",
        version="1.0.4",
        build="113",
    )
    assert payload["profile"] == "work-machine"
    with pytest.raises(ValueError, match="another commit"):
        promotion_module._validate_live_acceptance(
            summary,
            expected_commit="different",
            version="1.0.4",
            build="113",
        )


def test_promotion_rejects_corrupt_or_pc_less_manifest(tmp_path, promotion_module) -> None:
    manifest = tmp_path / "latest.json"
    manifest.write_text("not-json", encoding="utf-8")
    with pytest.raises(ValueError, match="valid JSON"):
        promotion_module._strict_existing_manifest(manifest)

    manifest.write_text('{"schema": 1, "mac": {}}', encoding="utf-8")
    with pytest.raises(ValueError, match="PC slot"):
        promotion_module._strict_existing_manifest(manifest)


def test_promotion_pins_manifest_digest_before_atomic_update() -> None:
    source = (ROOT / "scripts" / "promote_mac_release.py").read_text(encoding="utf-8")

    read_at = source.index("manifest_digest_before = _sha256(manifest_path)")
    compare_at = source.index("_sha256(manifest_path) != manifest_digest_before")
    write_at = source.index("write_atomic(str(manifest_path), merged)")
    assert read_at < compare_at < write_at


def test_promotion_managed_paths_are_narrow(tmp_path, promotion_module) -> None:
    valid = tmp_path / "OneDrive-Cisco" / "AI Projects" / "OUTBOX"
    valid.mkdir(parents=True)
    assert promotion_module._managed_path(
        valid,
        expected_tail=("AI Projects", "OUTBOX"),
        label="release root",
    ) == valid.resolve()

    with pytest.raises(ValueError, match="managed"):
        promotion_module._managed_path(
            tmp_path,
            expected_tail=("AI Projects", "OUTBOX"),
            label="release root",
        )


def test_handoff_pins_one_source_commit_for_mac_then_pc() -> None:
    source = (ROOT / "NEXT_MACHINE_PROMPT.md").read_text(encoding="utf-8")

    assert "BUILD_SHA=\"$(git rev-parse HEAD)\"" in source
    assert "ADOPTIQ_EXPECTED_COMMIT=\"$BUILD_SHA\"" in source
    assert "The later Windows build must use this exact commit" in source


def test_work_machine_runtime_identity_gate_is_exact_and_fail_closed() -> None:
    from scripts.run_round146_acceptance import _runtime_identity_gate

    valid = _runtime_identity_gate(
        {
            "ok": True,
            "version": "1.0.4",
            "build": "113",
            "frozen": True,
            "restart_required": False,
        },
        status_code=200,
        expected_version="1.0.4",
        expected_build="113",
    )
    assert valid == {
        "ok": True,
        "status": "passed",
        "version": "1.0.4",
        "build": "113",
        "frozen": True,
        "restart_required": False,
        "live_validation_performed": True,
        "status_code": 200,
    }
    for invalid in (
        {**valid, "version": "1.0.3"},
        {**valid, "build": "112"},
        {**valid, "frozen": False},
        {**valid, "restart_required": True},
        {key: value for key, value in valid.items() if key != "restart_required"},
    ):
        payload = {key: value for key, value in invalid.items() if key not in {"status", "status_code"}}
        assert not _runtime_identity_gate(
            payload,
            status_code=200,
            expected_version="1.0.4",
            expected_build="113",
        )["ok"]
