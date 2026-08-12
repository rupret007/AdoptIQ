from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import stat
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent


def _load_script(name: str, filename: str):
    path = ROOT / "scripts" / filename
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def smoke_module():
    module = _load_script("round164_candidate_smoke", "smoke_frozen_candidate.py")
    try:
        yield module
    finally:
        sys.modules.pop("round164_candidate_smoke", None)


@pytest.fixture()
def promotion_module():
    module = _load_script("round164_release_promotion", "promote_mac_release.py")
    try:
        yield module
    finally:
        sys.modules.pop("round164_release_promotion", None)


def _release_corpus_payload() -> dict:
    return {
        "ok": True,
        "enabled": True,
        "available": True,
        "boot": {
            "completed": True,
            "in_progress": False,
            "last_error": None,
            "source": "baked",
            "embedder_status": "ready",
            "reranker_status": "ready",
            "dense_retrieval_status": "ready",
            "dense_rows_remaining": 0,
            "ask_ai_retrieval_method": "hybrid",
        },
        "corpus": {
            "files_total": 12,
            "files_parsed": 12,
            "customers": 4,
            "chunks": 80,
        },
    }


def _smoke_summary(candidate: Path, smoke_module) -> dict:
    identity = smoke_module.candidate_identity(candidate)
    corpus = _release_corpus_payload()
    return {
        "schema_version": "frozen-candidate-smoke/v1",
        "ok": True,
        "expected_version": "1.0.4",
        "expected_build": "113",
        "release_corpus_required": True,
        "offline_model_probe": True,
        **identity,
        "checks": {
            "version": {
                "ok": True,
                "actual": {
                    "version": "1.0.4",
                    "build": "113",
                    "frozen": True,
                    "restart_required": False,
                },
            },
            "corpus": {
                "ok": True,
                "release_requirements_enforced": True,
                "evidence": smoke_module._corpus_evidence(corpus),
            },
            "candidate_integrity": {
                "ok": True,
                "identity_unchanged": True,
            },
        },
    }


def _acceptance_summary() -> dict:
    return {
        "schema_version": "round146-portable-acceptance/v1",
        "profile": "work-machine",
        "all_passed": True,
        "acceptance_complete": True,
        "live_validation_performed": True,
        "live_validation_passed": True,
        "skipped_gates": [],
        "git": {"sha": "abc123", "branch": "main", "dirty": False},
        "gates": {
            "runtime_identity": {
                "ok": True,
                "status": "passed",
                "version": "1.0.4",
                "build": "113",
                "frozen": True,
                "restart_required": False,
                "live_validation_performed": True,
                "status_code": 200,
            }
        },
    }


def _pc_slot() -> dict:
    return {
        "build": 112,
        "version": "1.0.4",
        "artifact": "AdoptIQ_PC/AdoptIQ-v1.0.4-build112.exe",
        "sha256": "a" * 64,
        "size_bytes": 42,
        "released_at_utc": "2026-08-10T10:00:00Z",
    }


def test_promotion_reads_canonical_config_version_and_build(
    tmp_path, promotion_module
) -> None:
    (tmp_path / "config.py").write_text(
        'ADOPTIQ_VERSION = "1.0.4"\nADOPTIQ_BUILD = "113"\n',
        encoding="utf-8",
    )
    assert promotion_module._read_config_identity(tmp_path) == ("1.0.4", "113")
    (tmp_path / "config.py").write_text(
        'ADOPTIQ_VERSION = "1.0.4"\n', encoding="utf-8"
    )
    with pytest.raises(ValueError, match="version/build"):
        promotion_module._read_config_identity(tmp_path)


def test_candidate_identity_binds_resolved_path_hash_and_size(
    tmp_path, smoke_module
) -> None:
    candidate = tmp_path / "candidate.dmg"
    candidate.write_bytes(b"exact-candidate-bytes")

    identity = smoke_module.candidate_identity(candidate)

    assert identity == {
        "candidate_path": str(candidate.resolve()),
        "candidate_kind": "file",
        "candidate_sha256": hashlib.sha256(candidate.read_bytes()).hexdigest(),
        "candidate_size_bytes": candidate.stat().st_size,
    }
    link = tmp_path / "linked.dmg"
    link.symlink_to(candidate)
    with pytest.raises(RuntimeError, match="symlink"):
        smoke_module.candidate_identity(link)


def test_release_version_requires_exact_frozen_restart_ready(smoke_module) -> None:
    valid = {
        "ok": True,
        "version": "1.0.4",
        "build": "113",
        "frozen": True,
        "restart_required": False,
    }
    assert not smoke_module.validate_version_payload(
        valid,
        expected_version="1.0.4",
        expected_build="113",
        require_restart_ready=True,
    )
    for key, invalid_value in (
        ("version", "1.0.3"),
        ("build", "112"),
        ("frozen", False),
        ("restart_required", True),
    ):
        invalid = {**valid, key: invalid_value}
        assert smoke_module.validate_version_payload(
            invalid,
            expected_version="1.0.4",
            expected_build="113",
            require_restart_ready=True,
        )


def test_release_corpus_requires_nonempty_hybrid_ready_zero_backlog(
    smoke_module,
) -> None:
    valid = _release_corpus_payload()
    assert smoke_module.validate_release_corpus_payload(valid) == []

    mutations = (
        (("available",), False),
        (("boot", "completed"), False),
        (("boot", "in_progress"), True),
        (("boot", "source"), "fresh"),
        (("boot", "embedder_status"), "unavailable"),
        (("boot", "reranker_status"), "unavailable"),
        (("boot", "dense_retrieval_status"), "stale_or_lexical"),
        (("boot", "dense_rows_remaining"), 1),
        (("boot", "ask_ai_retrieval_method"), "lexical"),
        (("corpus", "chunks"), 0),
    )
    for path, value in mutations:
        invalid = copy.deepcopy(valid)
        target = invalid
        for part in path[:-1]:
            target = target[part]
        target[path[-1]] = value
        assert smoke_module.validate_release_corpus_payload(invalid), path


def test_release_corpus_poll_waits_for_bootstrap_completion(
    monkeypatch, smoke_module
) -> None:
    incomplete = _release_corpus_payload()
    incomplete["boot"].update({"completed": False, "in_progress": True})
    models_warming = _release_corpus_payload()
    models_warming["boot"].update(
        {"embedder_status": None, "reranker_status": None}
    )
    responses = iter(
        (
            (200, incomplete, ""),
            (200, models_warming, ""),
            (200, _release_corpus_payload(), ""),
        )
    )
    monkeypatch.setattr(smoke_module, "_json_response", lambda _url: next(responses))
    monkeypatch.setattr(smoke_module.time, "sleep", lambda _seconds: None)

    class RunningProcess:
        @staticmethod
        def poll():
            return None

    check = smoke_module._poll_release_corpus(
        "http://127.0.0.1:1234",
        timeout=5,
        process=RunningProcess(),
    )
    assert check["ok"] is True
    assert check["release_requirements_enforced"] is True
    assert check["evidence"]["dense_rows_remaining"] == 0


def test_release_smoke_forces_an_empty_offline_model_cache() -> None:
    source = (ROOT / "scripts" / "smoke_frozen_candidate.py").read_text(
        encoding="utf-8"
    )

    assert '"HF_HUB_OFFLINE": "1"' in source
    assert '"TRANSFORMERS_OFFLINE": "1"' in source
    assert '"ADOPTIQ_FASTEMBED_CACHE": str(model_cache)' in source
    assert '"FASTEMBED_CACHE_PATH": str(model_cache)' in source
    assert '"ADOPTIQ_KNOWLEDGE_DIR": str(knowledge)' in source
    assert '"ADOPTIQ_OUTPUTS_DIR": str(outputs)' in source
    assert '"CSONE_ONEDRIVE_FOLDER": str(unavailable_onedrive)' in source
    assert '"HOME": str(home)' in source
    assert '"reranker_status"' in source


def test_release_smoke_runtime_environment_replaces_inherited_user_state(
    tmp_path, monkeypatch, smoke_module
) -> None:
    inherited = tmp_path / "operator-home"
    old_knowledge = inherited / "Library/Application Support/AdoptIQ/knowledge"
    old_outputs = inherited / "Documents/AdoptIQ Reports"
    old_onedrive = inherited / "Library/CloudStorage/OneDrive-Cisco/corpus"
    old_model_cache = inherited / "Library/Caches/AdoptIQ/fastembed"
    for directory in (old_knowledge, old_outputs, old_onedrive, old_model_cache):
        directory.mkdir(parents=True)
    (old_knowledge / "corpus.db.enc").write_bytes(b"old-decryptable-corpus")
    monkeypatch.setenv("HOME", str(inherited))
    monkeypatch.setenv("ADOPTIQ_KNOWLEDGE_DIR", str(old_knowledge))
    monkeypatch.setenv("ADOPTIQ_OUTPUTS_DIR", str(old_outputs))
    monkeypatch.setenv("CSONE_ONEDRIVE_FOLDER", str(old_onedrive))
    monkeypatch.setenv("ADOPTIQ_FASTEMBED_CACHE", str(old_model_cache))

    runtime_root = tmp_path / "isolated-smoke"
    environment = smoke_module._isolated_candidate_environment(
        port=5159,
        runtime_root=runtime_root,
    )

    assert environment["HOME"] == str((runtime_root / "home").resolve())
    assert environment["USERPROFILE"] == environment["HOME"]
    assert environment["APPDATA"] == str((runtime_root / "appdata").resolve())
    assert environment["LOCALAPPDATA"] == str(
        (runtime_root / "local-appdata").resolve()
    )
    assert environment["ADOPTIQ_KNOWLEDGE_DIR"] == str(
        (runtime_root / "knowledge").resolve()
    )
    assert environment["ADOPTIQ_OUTPUTS_DIR"] == str(
        (runtime_root / "outputs").resolve()
    )
    assert environment["CSONE_ONEDRIVE_FOLDER"] == str(
        (runtime_root / "unavailable-onedrive").resolve()
    )
    assert environment["ADOPTIQ_FASTEMBED_CACHE"] == str(
        (runtime_root / "model-cache").resolve()
    )
    assert not Path(environment["CSONE_ONEDRIVE_FOLDER"]).exists()
    assert list(Path(environment["ADOPTIQ_KNOWLEDGE_DIR"]).iterdir()) == []
    assert list(Path(environment["ADOPTIQ_OUTPUTS_DIR"]).iterdir()) == []
    assert list(Path(environment["ADOPTIQ_FASTEMBED_CACHE"]).iterdir()) == []


def test_release_corpus_evidence_exposes_exact_bundled_boot_source(
    smoke_module,
) -> None:
    payload = _release_corpus_payload()
    assert smoke_module._corpus_evidence(payload)["boot_source"] == "baked"

    for source in (None, "", "fresh", "self_healed_baked"):
        invalid = copy.deepcopy(payload)
        invalid["boot"]["source"] = source
        assert "bundled snapshot" in " ".join(
            smoke_module.validate_release_corpus_payload(invalid)
        )


def test_promotion_smoke_is_bound_to_exact_candidate_and_release_corpus(
    tmp_path, smoke_module, promotion_module
) -> None:
    candidate = tmp_path / "AdoptIQ-v1.0.4-build113.dmg"
    candidate.write_bytes(b"candidate-one")
    summary = tmp_path / "smoke.json"
    payload = _smoke_summary(candidate, smoke_module)
    summary.write_text(json.dumps(payload), encoding="utf-8")
    digest = hashlib.sha256(candidate.read_bytes()).hexdigest()

    assert promotion_module._validate_smoke(
        summary,
        version="1.0.4",
        build="113",
        candidate=candidate,
        candidate_sha256=digest,
        candidate_size_bytes=candidate.stat().st_size,
    )["ok"]

    candidate.write_bytes(b"candidate-two")
    with pytest.raises(ValueError, match="exact candidate DMG"):
        promotion_module._validate_smoke(
            summary,
            version="1.0.4",
            build="113",
            candidate=candidate,
            candidate_sha256=hashlib.sha256(candidate.read_bytes()).hexdigest(),
            candidate_size_bytes=candidate.stat().st_size,
        )

    candidate.write_bytes(b"candidate-one")
    payload["release_corpus_required"] = False
    summary.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="release-corpus"):
        promotion_module._validate_smoke(
            summary,
            version="1.0.4",
            build="113",
            candidate=candidate,
            candidate_sha256=digest,
            candidate_size_bytes=candidate.stat().st_size,
        )


def test_live_acceptance_requires_clean_main_and_exact_runtime(
    tmp_path, promotion_module
) -> None:
    summary = tmp_path / "acceptance.json"
    valid = _acceptance_summary()
    summary.write_text(json.dumps(valid), encoding="utf-8")
    assert promotion_module._validate_live_acceptance(
        summary,
        expected_commit="abc123",
        version="1.0.4",
        build="113",
    )["all_passed"]

    for path, value in (
        (("git", "branch"), "feature"),
        (("git", "dirty"), True),
        (("gates", "runtime_identity", "version"), "1.0.3"),
        (("gates", "runtime_identity", "frozen"), False),
        (("gates", "runtime_identity", "restart_required"), True),
    ):
        invalid = copy.deepcopy(valid)
        target = invalid
        for part in path[:-1]:
            target = target[part]
        target[path[-1]] = value
        summary.write_text(json.dumps(invalid), encoding="utf-8")
        with pytest.raises(ValueError, match="work-machine"):
            promotion_module._validate_live_acceptance(
                summary,
                expected_commit="abc123",
                version="1.0.4",
                build="113",
            )


def test_pc_slot_shape_and_artifact_are_strict(tmp_path, promotion_module) -> None:
    slot = _pc_slot()
    manifest = tmp_path / "latest.json"
    manifest.write_text(json.dumps({"schema": 1, "pc": slot}), encoding="utf-8")
    assert promotion_module._strict_existing_manifest(manifest)["pc"] == slot

    for field, value in (
        ("build", True),
        ("artifact", "../AdoptIQ.exe"),
        ("sha256", "A" * 64),
        ("size_bytes", 0),
        ("released_at_utc", "not-a-date"),
    ):
        invalid = {**slot, field: value}
        manifest.write_text(
            json.dumps({"schema": 1, "pc": invalid}), encoding="utf-8"
        )
        with pytest.raises(ValueError, match="PC"):
            promotion_module._strict_existing_manifest(manifest)

    releases = tmp_path / "releases"
    artifact = releases / slot["artifact"]
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"x" * slot["size_bytes"])
    slot["sha256"] = hashlib.sha256(artifact.read_bytes()).hexdigest()
    promotion_module._verify_pc_artifact(releases, slot)
    artifact.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="does not match"):
        promotion_module._verify_pc_artifact(releases, slot)


def test_rollback_backup_is_create_once_and_idempotent_safe(
    tmp_path, promotion_module
) -> None:
    backup = tmp_path / "rollback.json"
    payload = {"schema": 1, "pc": _pc_slot(), "notes": "before"}
    assert promotion_module._ensure_rollback_backup(backup, payload) is True
    assert stat.S_IMODE(backup.stat().st_mode) == 0o600
    before = backup.read_bytes()
    assert promotion_module._ensure_rollback_backup(backup, payload) is False
    with pytest.raises(ValueError, match="different release state"):
        promotion_module._ensure_rollback_backup(
            backup, {**payload, "notes": "different"}
        )
    assert backup.read_bytes() == before


def test_promotion_retry_preserves_pc_and_never_overwrites_rollback(
    tmp_path, monkeypatch, smoke_module, promotion_module
) -> None:
    root = tmp_path / "repo"
    source_outbox = root / "OUTBOX"
    source_outbox.mkdir(parents=True)
    (root / "config.py").write_text(
        'ADOPTIQ_VERSION = "1.0.4"\nADOPTIQ_BUILD = "113"\n',
        encoding="utf-8",
    )
    (source_outbox / "README.md").write_text("release notes\n", encoding="utf-8")
    (source_outbox / "build_info.txt").write_text(
        "AdoptIQ v1.0.4 build 113\nSource commit: abc123\n",
        encoding="utf-8",
    )
    dmg = source_outbox / "AdoptIQ-v1.0.4-build113.dmg"
    dmg.write_bytes(b"verified-dmg")
    smoke = tmp_path / "smoke.json"
    smoke.write_text(json.dumps(_smoke_summary(dmg, smoke_module)), encoding="utf-8")
    acceptance = tmp_path / "acceptance.json"
    acceptance.write_text(json.dumps(_acceptance_summary()), encoding="utf-8")

    staging = tmp_path / "cloud" / "AI Projects" / "Staging" / "AdoptIQ_MAC" / "OUTBOX"
    releases = tmp_path / "cloud" / "AI Projects" / "OUTBOX"
    staging.mkdir(parents=True)
    releases.mkdir(parents=True)
    pc = _pc_slot()
    pc_artifact = releases / pc["artifact"]
    pc_artifact.parent.mkdir()
    pc_artifact.write_bytes(b"preserved-pc")
    pc["size_bytes"] = pc_artifact.stat().st_size
    pc["sha256"] = hashlib.sha256(pc_artifact.read_bytes()).hexdigest()
    old_manifest = {
        "schema": 1,
        "version": "1.0.3",
        "build": 112,
        "channel": "stable",
        "released_at_utc": "2026-08-10T10:00:00Z",
        "pc": pc,
        "mac": {
            "build": 111,
            "version": "1.0.3",
            "artifact": "AdoptIQ/AdoptIQ-v1.0.3-build111.dmg",
            "sha256": "b" * 64,
            "size_bytes": 50,
            "released_at_utc": "2026-08-09T10:00:00Z",
        },
        "notes": "before",
    }
    manifest = releases / "latest.json"
    manifest.write_text(json.dumps(old_manifest), encoding="utf-8")
    monkeypatch.setattr(promotion_module, "_require_clean_source", lambda *_args: None)
    monkeypatch.setattr(promotion_module, "_verify_dmg", lambda *_args, **_kwargs: None)
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))

    arguments = {
        "root": root,
        "dmg": dmg,
        "smoke_summary": smoke,
        "acceptance_summary": acceptance,
        "staging_dir": staging,
        "releases_root": releases,
        "version": "1.0.4",
        "build": "113",
        "expected_commit": "abc123",
        "manual_source_reconciliation_complete": True,
        "visual_review_complete": True,
        "approved": True,
    }
    first = promotion_module.promote(**arguments)
    backup = source_outbox / "latest.before-mac-build113.json"
    rollback_bytes = backup.read_bytes()
    published = json.loads(manifest.read_text(encoding="utf-8"))

    assert first["already_promoted"] is False
    assert first["rollback_created"] is True
    assert json.loads(rollback_bytes) == old_manifest
    assert published["pc"] == pc
    assert published["mac"]["sha256"] == hashlib.sha256(dmg.read_bytes()).hexdigest()

    second = promotion_module.promote(**arguments)
    assert second["already_promoted"] is True
    assert second["rollback_created"] is False
    assert backup.read_bytes() == rollback_bytes
    assert json.loads(manifest.read_text(encoding="utf-8"))["pc"] == pc
