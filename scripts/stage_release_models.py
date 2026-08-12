#!/usr/bin/env python3
"""Stage the validated FastEmbed cache consumed by frozen releases.

Model bytes are copied without conversion or quantization. The source is the
operator-owned persistent cache populated by release preflight; the destination
is an ignored build-only tree used by both PyInstaller specs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


MAX_FILE_BYTES = 1_500_000_000
MAX_TOTAL_BYTES = 2_500_000_000
_OBJECT_ID_RE = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
_COMMON_REQUIRED_FILES = frozenset(
    {
        "config.json",
        "special_tokens_map.json",
        "tokenizer.json",
        "tokenizer_config.json",
    }
)
_COMMON_OPTIONAL_FILES = frozenset({"preprocessor_config.json"})


@dataclass(frozen=True)
class _ModelLayout:
    cache_name: str
    role: str
    model_name: str
    model_file: str

    @property
    def required_files(self) -> frozenset[str]:
        return _COMMON_REQUIRED_FILES | {self.model_file}

    @property
    def allowed_files(self) -> frozenset[str]:
        return self.required_files | _COMMON_OPTIONAL_FILES


_MODEL_LAYOUTS = (
    _ModelLayout(
        cache_name="models--qdrant--bge-small-en-v1.5-onnx-q",
        role="embedding",
        model_name="BAAI/bge-small-en-v1.5",
        model_file="model_optimized.onnx",
    ),
    _ModelLayout(
        cache_name="models--Xenova--ms-marco-MiniLM-L-6-v2",
        role="reranker",
        model_name="Xenova/ms-marco-MiniLM-L-6-v2",
        model_file="onnx/model.onnx",
    ),
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _resolved_file(path: Path, *, source_root: Path, label: str) -> Path:
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise ValueError(f"{label} is missing or unreadable") from exc
    if not _inside(resolved, source_root) or not resolved.is_file():
        raise ValueError(f"{label} resolves outside the approved cache")
    return resolved


def _snapshot_revision(model_root: Path, *, source_root: Path, role: str) -> str:
    reference = model_root / "refs" / "main"
    if reference.is_symlink():
        raise ValueError(f"{role} cache refs/main must be a regular file")
    resolved = _resolved_file(
        reference,
        source_root=source_root,
        label=f"{role} cache refs/main",
    )
    try:
        raw_revision = resolved.read_text(encoding="ascii")
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"{role} cache refs/main is invalid") from exc
    revision = raw_revision.strip()
    if raw_revision != revision:
        raise ValueError(f"{role} cache refs/main is not canonical")
    if _OBJECT_ID_RE.fullmatch(revision) is None:
        raise ValueError(f"{role} cache refs/main is not a pinned revision")
    snapshot = model_root / "snapshots" / revision
    if snapshot.is_symlink() or not snapshot.is_dir():
        raise ValueError(f"{role} cache pinned snapshot is missing or unsafe")
    return revision


def _validate_json_file(path: Path, *, label: str) -> Any:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise ValueError(f"{label} is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must contain a JSON object")
    return payload


def _validate_fastembed_metadata(
    metadata: Any,
    *,
    snapshot_root: Path,
    allowed_snapshot_files: set[str],
    role: str,
) -> None:
    for raw_path, raw_detail in metadata.items():
        if not isinstance(raw_path, str) or raw_path not in allowed_snapshot_files:
            raise ValueError(f"{role} files_metadata.json contains an unrelated path")
        if not isinstance(raw_detail, dict) or set(raw_detail) != {"size", "blob_id"}:
            raise ValueError(f"{role} files_metadata.json has invalid file evidence")
        size = raw_detail.get("size")
        blob_id = raw_detail.get("blob_id")
        if (
            isinstance(size, bool)
            or not isinstance(size, int)
            or size <= 0
            or not isinstance(blob_id, str)
            or _OBJECT_ID_RE.fullmatch(blob_id) is None
        ):
            raise ValueError(f"{role} files_metadata.json has invalid file evidence")
        if (snapshot_root / raw_path).stat().st_size != size:
            raise ValueError(f"{role} files_metadata.json size evidence does not match")


def _validate_tree_metadata(metadata: Any, *, role: str) -> None:
    if metadata.get("format_version") != 1 or not isinstance(metadata.get("files"), dict):
        raise ValueError(f"{role} HuggingFace tree metadata has an invalid schema")
    for raw_path, raw_detail in metadata["files"].items():
        if not isinstance(raw_path, str):
            raise ValueError(f"{role} HuggingFace tree metadata has an unsafe path")
        relative = Path(raw_path)
        if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
            raise ValueError(f"{role} HuggingFace tree metadata has an unsafe path")
        if not isinstance(raw_detail, dict):
            raise ValueError(f"{role} HuggingFace tree metadata has invalid evidence")
        size = raw_detail.get("size")
        blob_id = raw_detail.get("blob_id")
        if (
            isinstance(size, bool)
            or not isinstance(size, int)
            or size < 0
            or not isinstance(blob_id, str)
            or _OBJECT_ID_RE.fullmatch(blob_id) is None
        ):
            raise ValueError(f"{role} HuggingFace tree metadata has invalid evidence")


def _cache_plumbing_file(relative: Path) -> bool:
    parts = relative.parts
    if len(parts) == 3 and parts[0] == ".locks":
        return (
            parts[1] in {layout.cache_name for layout in _MODEL_LAYOUTS}
            and parts[2].endswith(".lock")
            and _OBJECT_ID_RE.fullmatch(parts[2][:-5]) is not None
        )
    if len(parts) == 3 and parts[0] in {
        layout.cache_name for layout in _MODEL_LAYOUTS
    }:
        return parts[1] == "blobs" and _OBJECT_ID_RE.fullmatch(parts[2]) is not None
    return False


def _source_files(source: Path) -> list[tuple[Path, Path]]:
    if not source.is_dir() or source.is_symlink():
        raise ValueError("model cache source must be a real directory")
    source_root = source.resolve()
    staged: dict[Path, Path] = {}
    allowed_directories: set[Path] = {Path("."), Path(".locks")}
    allowed_files: set[Path] = set()

    for layout in _MODEL_LAYOUTS:
        model_root = source_root / layout.cache_name
        if model_root.is_symlink() or not model_root.is_dir():
            raise ValueError(f"model cache is missing the exact {layout.role} layout")
        revision = _snapshot_revision(
            model_root,
            source_root=source_root,
            role=layout.role,
        )
        snapshot_root = model_root / "snapshots" / revision
        allowed_directories.update(
            {
                Path(layout.cache_name),
                Path(layout.cache_name) / "blobs",
                Path(layout.cache_name) / "refs",
                Path(layout.cache_name) / "snapshots",
                Path(layout.cache_name) / "snapshots" / revision,
                Path(layout.cache_name) / "trees",
                Path(".locks") / layout.cache_name,
            }
        )
        for relative_name in layout.allowed_files:
            relative = Path(relative_name)
            for parent in relative.parents:
                if parent != Path("."):
                    allowed_directories.add(
                        Path(layout.cache_name) / "snapshots" / revision / parent
                    )
            candidate = snapshot_root / relative
            if not candidate.exists():
                if relative_name in layout.required_files:
                    raise ValueError(
                        f"model cache is missing required {layout.role} artifact"
                    )
                continue
            resolved = _resolved_file(
                candidate,
                source_root=source_root,
                label=f"{layout.role} model artifact",
            )
            if candidate.is_symlink():
                blob_root = (model_root / "blobs").resolve()
                if resolved.parent != blob_root or _OBJECT_ID_RE.fullmatch(resolved.name) is None:
                    raise ValueError(
                        f"{layout.role} snapshot symlink does not target its model blobs"
                    )
            if relative.suffix.casefold() == ".json":
                _validate_json_file(resolved, label=f"{layout.role} {relative_name}")
            cache_relative = candidate.relative_to(source_root)
            allowed_files.add(cache_relative)
            staged[cache_relative] = resolved

        reference = model_root / "refs" / "main"
        metadata_path = model_root / "files_metadata.json"
        if metadata_path.is_symlink():
            raise ValueError(f"{layout.role} files_metadata.json must be regular")
        metadata_resolved = _resolved_file(
            metadata_path,
            source_root=source_root,
            label=f"{layout.role} files_metadata.json",
        )
        metadata = _validate_json_file(
            metadata_resolved,
            label=f"{layout.role} files_metadata.json",
        )
        present_snapshot_files = {
            relative_name
            for relative_name in layout.allowed_files
            if (snapshot_root / relative_name).exists()
        }
        _validate_fastembed_metadata(
            metadata,
            snapshot_root=snapshot_root,
            allowed_snapshot_files=present_snapshot_files,
            role=layout.role,
        )
        for candidate in (reference, metadata_path):
            cache_relative = candidate.relative_to(source_root)
            allowed_files.add(cache_relative)
            staged[cache_relative] = candidate.resolve(strict=True)

        tree_metadata_path = model_root / "trees" / f"{revision}.json"
        if tree_metadata_path.exists():
            if tree_metadata_path.is_symlink():
                raise ValueError(f"{layout.role} HuggingFace tree metadata must be regular")
            tree_metadata_resolved = _resolved_file(
                tree_metadata_path,
                source_root=source_root,
                label=f"{layout.role} HuggingFace tree metadata",
            )
            tree_metadata = _validate_json_file(
                tree_metadata_resolved,
                label=f"{layout.role} HuggingFace tree metadata",
            )
            _validate_tree_metadata(tree_metadata, role=layout.role)
            cache_relative = tree_metadata_path.relative_to(source_root)
            allowed_files.add(cache_relative)
            staged[cache_relative] = tree_metadata_resolved

    files: list[tuple[Path, Path]] = []
    total = 0
    for directory, dirnames, filenames in os.walk(source_root, followlinks=False):
        current = Path(directory)
        current_relative = current.relative_to(source_root)
        if current_relative not in allowed_directories:
            raise ValueError(
                f"model cache contains unrelated directory: {current_relative.as_posix()}"
            )
        for dirname in list(dirnames):
            if (current / dirname).is_symlink():
                raise ValueError("model cache contains a symlinked directory")
        for filename in filenames:
            candidate = current / filename
            relative = candidate.relative_to(source_root)
            if _cache_plumbing_file(relative):
                if candidate.is_symlink() or not candidate.is_file():
                    raise ValueError("model cache plumbing contains an unsafe file")
                continue
            if relative not in allowed_files:
                raise ValueError(
                    f"model cache contains unrelated file: {relative.as_posix()}"
                )
            resolved = staged[relative]
            size = resolved.stat().st_size
            if size <= 0 or size > MAX_FILE_BYTES:
                raise ValueError("model cache contains an empty or oversized file")
            total += size
            if total > MAX_TOTAL_BYTES:
                raise ValueError("model cache exceeds the release size limit")
            files.append((relative, resolved))
    if not files:
        raise ValueError("model cache contains no stageable files")
    return sorted(files, key=lambda pair: str(pair[0]).casefold())


def stage_release_models(source: Path, destination: Path, *, repo_root: Path) -> dict[str, object]:
    repo = repo_root.resolve()
    expected = repo / "embeddings" / "release_fastembed_cache"
    destination = destination.resolve()
    if destination != expected or destination == repo or destination.is_symlink():
        raise ValueError("destination must be the ignored release_fastembed_cache directory")
    files = _source_files(source.expanduser())
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
    if temporary.exists():
        shutil.rmtree(temporary)
    temporary.mkdir(parents=True, mode=0o700)
    manifest_files: list[dict[str, object]] = []
    try:
        for relative, resolved in files:
            target = temporary / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(resolved, target)
            os.chmod(target, 0o600)
            manifest_files.append(
                {"path": relative.as_posix(), "sha256": _sha256(target), "size": target.stat().st_size}
            )
        payload: dict[str, object] = {
            "schema_version": "adoptiq-release-models/v1",
            "embedding_model": "BAAI/bge-small-en-v1.5",
            "reranker_model": "Xenova/ms-marco-MiniLM-L-6-v2",
            "files": manifest_files,
            "file_count": len(manifest_files),
            "total_bytes": sum(int(item["size"]) for item in manifest_files),
        }
        manifest = temporary / "adoptiq_model_manifest.json"
        manifest.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.chmod(manifest, 0o600)
        if destination.exists():
            shutil.rmtree(destination)
        os.replace(temporary, destination)
        return payload
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def _parser() -> argparse.ArgumentParser:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--destination", type=Path, default=root / "embeddings" / "release_fastembed_cache")
    parser.add_argument("--repo-root", type=Path, default=root)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        payload = stage_release_models(args.source, args.destination, repo_root=args.repo_root)
    except (OSError, ValueError) as exc:
        print(f"FAIL: release model staging: {exc}", file=sys.stderr)
        return 1
    print(f"PASS: staged release models files={payload['file_count']} bytes={payload['total_bytes']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
