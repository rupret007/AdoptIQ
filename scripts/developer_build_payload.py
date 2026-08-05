#!/usr/bin/env python3
"""Create the sanitized configuration bundled into developer candidates.

Production builds continue to use the repository's configured roster and
customer-alias defaults.  Developer-only candidates are portable validation
artifacts, so they receive a small synthetic roster, an empty alias registry,
and machine-readable build provenance instead.
"""

from __future__ import annotations

import ast
import json
import os
import re
import shutil
from pathlib import Path
from typing import Callable, Final, Mapping, MutableSequence


SCHEMA_VERSION: Final = "developer-build-metadata/v2"
REQUIRED_MARKER = "DEVELOPER_ONLY_BUILD.txt"
BUILD_METADATA = "DEVELOPER_BUILD_METADATA.json"
DEVELOPER_README = "README.md"
DEVELOPER_CONFIG_OVERLAY = "config.py"
DEVELOPER_CONFIG_OVERLAY_MARKER = "adoptiq-developer-config-overlay/v1"
DEVELOPER_SOURCE_OVERLAY_MODULES: Final = (
    "config",
    "connectivity_diagnostics",
    "corpus_bootstrap",
    "error_classifier",
)

_VERSION_RE = re.compile(r'ADOPTIQ_VERSION\s*=\s*"([^"]+)"')
_BUILD_RE = re.compile(r'ADOPTIQ_BUILD\s*=\s*"([^"]+)"')

# These runtime defaults point at person-specific or production-only services.
# A developer candidate may receive explicit values from its environment, but
# it must never compile the repository defaults into the frozen executable.
_ENV_ONLY_CONFIG_NAMES = {
    "ADOPTIQ_CORPUS_SHARE_URL",
    "ADOPTIQ_RELEASES_SHARE_URL",
    "CSONE_REPORT_URL",
}


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_bytes(data)
    os.replace(temporary, path)


def _config_identity(root: Path) -> tuple[str, str]:
    body = (root / "config.py").read_text(encoding="utf-8")
    version_match = _VERSION_RE.search(body)
    build_match = _BUILD_RE.search(body)
    if version_match is None or build_match is None:
        raise RuntimeError("config.py is missing canonical version/build metadata")
    return version_match.group(1), build_match.group(1)


def _drop_docstring(body: list[ast.stmt]) -> list[ast.stmt]:
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body = body[1:]
    return body or [ast.Pass()]


def _env_only_value(name: str) -> ast.expr:
    return ast.parse(
        f"os.environ.get({name!r}) or ''",
        mode="eval",
    ).body


class _DeveloperSourceTransformer(ast.NodeTransformer):
    """Strip docs and hash-redact known internal values from compiled source."""

    def __init__(self, redactor: Callable[[bytes], bytes]) -> None:
        self._redactor = redactor
        super().__init__()

    def visit_Module(self, node: ast.Module) -> ast.Module:  # noqa: N802
        self.generic_visit(node)
        node.body = _drop_docstring(node.body)
        return node

    def visit_ClassDef(self, node: ast.ClassDef) -> ast.ClassDef:  # noqa: N802
        self.generic_visit(node)
        node.body = _drop_docstring(node.body)
        return node

    def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.FunctionDef:  # noqa: N802
        self.generic_visit(node)
        node.body = _drop_docstring(node.body)
        return node

    def visit_AsyncFunctionDef(  # noqa: N802
        self, node: ast.AsyncFunctionDef
    ) -> ast.AsyncFunctionDef:
        self.generic_visit(node)
        node.body = _drop_docstring(node.body)
        return node

    def visit_Constant(self, node: ast.Constant) -> ast.Constant:  # noqa: N802
        value = node.value
        if isinstance(value, str):
            encoded = value.encode("utf-8")
            redacted = self._redactor(encoded)
            if redacted != encoded:
                return ast.copy_location(
                    ast.Constant(redacted.decode("utf-8")),
                    node,
                )
        elif isinstance(value, bytes):
            redacted = self._redactor(value)
            if redacted != value:
                return ast.copy_location(ast.Constant(redacted), node)
        return node


class _DeveloperConfigTransformer(_DeveloperSourceTransformer):
    """Also replace production-only configuration defaults with env-only values."""

    def visit_Module(self, node: ast.Module) -> ast.Module:  # noqa: N802
        node = super().visit_Module(node)
        node.body.append(
            ast.Assign(
                targets=[ast.Name(id="DEVELOPER_CONFIG_OVERLAY_MARKER", ctx=ast.Store())],
                value=ast.Constant(DEVELOPER_CONFIG_OVERLAY_MARKER),
            )
        )
        return node

    def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.FunctionDef:  # noqa: N802
        self.generic_visit(node)
        if node.name == "_csone_onedrive_candidates":
            node.body = ast.parse(
                "home = os.path.expanduser('~')\nreturn [os.path.join(home, '.adoptiq', 'local-corpus')]\n"
            ).body
        elif node.name == "_releases_candidates":
            node.body = ast.parse(
                "home = os.path.expanduser('~')\nreturn [os.path.join(home, '.adoptiq', 'local-releases')]\n"
            ).body
        else:
            node.body = _drop_docstring(node.body)
        return node

    def visit_Assign(self, node: ast.Assign) -> ast.Assign:  # noqa: N802
        self.generic_visit(node)
        target_names = {target.id for target in node.targets if isinstance(target, ast.Name)}
        if "_R80_CORPUS_OWNER_DISPLAY" in target_names:
            node.value = ast.Constant("Synthetic Corpus Owner")
        else:
            env_only = target_names.intersection(_ENV_ONLY_CONFIG_NAMES)
            if env_only:
                if len(env_only) != 1:
                    raise RuntimeError("developer config overlay assignment is ambiguous")
                node.value = _env_only_value(next(iter(env_only)))
            elif "KEEPER_CONFIG" in target_names:
                node.value = ast.parse(
                    "{\n"
                    "  'url': os.environ.get('KEEPER_URL') or '',\n"
                    "  'namespace': os.environ.get('KEEPER_NAMESPACE') or '',\n"
                    "  'role_id': os.environ.get('KEEPER_ROLE_ID') or '',\n"
                    "  'secret_id': os.environ.get('KEEPER_SECRET_ID') or '',\n"
                    "  'secret_path': os.environ.get('KEEPER_SECRET_PATH') or '',\n"
                    "}\n",
                    mode="eval",
                ).body
        return node


def _developer_source_overlay(
    project_root: Path,
    payload_dir: Path,
    module_name: str,
    *,
    redactor: Callable[[bytes], bytes],
) -> Path:
    source_path = project_root / f"{module_name}.py"
    if not source_path.is_file():
        raise RuntimeError(f"required developer overlay module is missing: {module_name}")
    source = source_path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=str(source_path))
    except SyntaxError as exc:
        raise RuntimeError(f"{module_name}.py cannot be parsed for developer sanitization") from exc
    transformer = (
        _DeveloperConfigTransformer(redactor) if module_name == "config" else _DeveloperSourceTransformer(redactor)
    )
    transformed = transformer.visit(tree)
    ast.fix_missing_locations(transformed)
    overlay_source = ast.unparse(transformed) + "\n"

    # Import lazily so the generator remains usable from a PyInstaller spec
    # without coupling module import order. The shared scanner emits only rule
    # names and paths; sensitive values never enter build output.
    from developer_candidate_security import scan_blob  # noqa: PLC0415

    findings = scan_blob(
        f"developer-source-overlay/{module_name}.py",
        overlay_source.encode(),
        include_assignments=False,
    )
    if findings:
        raise RuntimeError(f"generated developer source overlay failed security scan: {module_name}")
    overlay_path = (
        payload_dir / DEVELOPER_CONFIG_OVERLAY
        if module_name == "config"
        else payload_dir / "source-overlays" / f"{module_name}.py"
    )
    compile(overlay_source, str(overlay_path), "exec", optimize=2)
    _atomic_write(overlay_path, overlay_source)
    return overlay_path


def _developer_source_overlays(project_root: Path, payload_dir: Path) -> dict[str, Path]:
    from developer_candidate_security import (  # noqa: PLC0415
        redact_known_internal_values,
    )

    return {
        module_name: _developer_source_overlay(
            project_root,
            payload_dir,
            module_name,
            redactor=redact_known_internal_values,
        )
        for module_name in DEVELOPER_SOURCE_OVERLAY_MODULES
    }


def _developer_resource_trees(project_root: Path, payload_dir: Path) -> dict[str, Path]:
    """Copy and sanitize raw resources without changing production sources."""

    from developer_candidate_security import (  # noqa: PLC0415
        redact_known_internal_values,
        scan_tree,
    )

    resource_root = payload_dir / "resources"
    if resource_root.exists():
        if resource_root.is_symlink() or resource_root.parent.resolve() != payload_dir.resolve():
            raise RuntimeError("developer resource overlay path is unsafe")
        shutil.rmtree(resource_root)
    resource_root.mkdir(parents=True)

    trees: dict[str, Path] = {}
    for directory_name in ("templates", "static"):
        source_root = project_root / directory_name
        if not source_root.is_dir() or source_root.is_symlink():
            raise RuntimeError(f"required developer resource directory is missing: {directory_name}")
        destination_root = resource_root / directory_name
        destination_root.mkdir()
        for source_path in sorted(
            source_root.rglob("*"),
            key=lambda path: path.relative_to(source_root).as_posix(),
        ):
            relative = source_path.relative_to(source_root)
            if source_path.is_symlink():
                raise RuntimeError(f"developer resources may not contain symlinks: {directory_name}")
            destination = destination_root / relative
            if source_path.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
            elif source_path.is_file():
                _atomic_write_bytes(
                    destination,
                    redact_known_internal_values(source_path.read_bytes()),
                )
                shutil.copymode(source_path, destination)
            else:
                raise RuntimeError(f"developer resources contain an unsupported entry: {directory_name}")
        trees[directory_name] = destination_root

    scan = scan_tree(resource_root)
    if not scan.get("ok"):
        raise RuntimeError("generated developer resource overlays failed security scan")
    return trees


def _bind_developer_source_overlay(
    pure_toc: MutableSequence[tuple[str, str, str]],
    module_name: str,
    overlay_path: str | Path,
) -> str:
    overlay = Path(overlay_path).expanduser().resolve()
    if not overlay.is_file() or overlay.name != f"{module_name}.py":
        raise RuntimeError(f"generated developer source overlay is missing: {module_name}")
    if module_name == "config":
        body = overlay.read_text(encoding="utf-8")
        if DEVELOPER_CONFIG_OVERLAY_MARKER not in body:
            raise RuntimeError("generated developer config overlay marker is missing")

    matches = [index for index, entry in enumerate(pure_toc) if entry[0] == module_name]
    if len(matches) != 1:
        raise RuntimeError(f"PyInstaller module graph must contain exactly one {module_name} module")
    index = matches[0]
    destination, _source, typecode = pure_toc[index]
    pure_toc[index] = (destination, str(overlay), typecode)
    if Path(pure_toc[index][1]).resolve() != overlay:
        raise RuntimeError(f"PyInstaller source overlay binding failed: {module_name}")
    return str(overlay)


def bind_developer_config_overlay(pure_toc: MutableSequence[tuple[str, str, str]], overlay_path: str | Path) -> str:
    """Replace exactly one PyInstaller ``config`` source entry, or fail closed."""

    return _bind_developer_source_overlay(pure_toc, "config", overlay_path)


def bind_developer_source_overlays(
    pure_toc: MutableSequence[tuple[str, str, str]],
    overlays: Mapping[str, str | Path],
) -> dict[str, str]:
    """Bind the complete developer overlay set to the analyzed module graph."""

    expected = set(DEVELOPER_SOURCE_OVERLAY_MODULES)
    if set(overlays) != expected:
        raise RuntimeError("developer source overlay set is incomplete")
    return {
        module_name: _bind_developer_source_overlay(
            pure_toc,
            module_name,
            overlays[module_name],
        )
        for module_name in DEVELOPER_SOURCE_OVERLAY_MODULES
    }


def prepare_developer_payload(root: str | Path, *, target_platform: str) -> dict[str, str]:
    """Write and return paths for a corpus-free, synthetic build payload."""

    project_root = Path(root).expanduser().resolve()
    platform = str(target_platform or "").strip().casefold()
    if platform not in {"macos", "windows"}:
        raise ValueError("target_platform must be macos or windows")

    version, build = _config_identity(project_root)
    source_commit = (
        os.environ.get("GITHUB_SHA") or os.environ.get("ADOPTIQ_SOURCE_COMMIT") or "local-uncommitted"
    ).strip()
    payload_dir = project_root / "build" / "developer-only-payload" / platform
    source_overlays = _developer_source_overlays(project_root, payload_dir)
    resource_trees = _developer_resource_trees(project_root, payload_dir)

    team_config = {
        "managers": ["Local Fixture Manager", "All Managers"],
        "team_roster": [
            {
                "manager": "Local Fixture Manager",
                "name": "Synthetic Team Member",
                "email": "team.member@example.invalid",
            }
        ],
    }
    aliases = {"schema_version": 1, "groups": []}
    metadata = {
        "schema_version": SCHEMA_VERSION,
        "developer_only": True,
        "production_ready": False,
        "sanitized_configuration": True,
        "sanitized_config_overlay_required": True,
        "prebaked_corpus_included": False,
        "bundled_credentials_included": False,
        "target_platform": platform,
        "version": version,
        "build": build,
        "source_commit_sha": source_commit,
    }
    marker_text = (
        "Developer-only AdoptIQ candidate. Synthetic configuration only. "
        "No bundled credentials or prebaked customer corpus. Not production-ready.\n"
    )
    platform_instructions = (
        "Open the DMG, drag AdoptIQ.app to Applications, and use the included "
        "unblock helper if macOS requests approval."
        if platform == "macos"
        else "Run the included unblock helper, then start AdoptIQ.exe."
    )
    readme_text = f"""# AdoptIQ Developer Candidate

Version: {version}
Build: {build}
Platform: {platform}
Source commit: {source_commit}

This artifact is for local validation only and is not production-ready. It
contains a synthetic team configuration and an empty customer-alias registry.
It does not bundle production credentials or a customer knowledge corpus, and
person-specific and production-only service defaults are removed. Live
Snowflake connectivity is not expected in the local acceptance mode.

## Start

{platform_instructions} AdoptIQ serves its local interface at
`http://localhost:5151/` after startup.

## Verify

Keep `CHECKSUMS.sha256` and `candidate_provenance.json` beside the artifact.
They bind the files to the version, build, platform, and source commit above.
"""

    paths = {
        "team_config": payload_dir / "team_config.json",
        "customer_aliases": payload_dir / "customer_aliases.defaults.json",
        "metadata": payload_dir / BUILD_METADATA,
        "marker": payload_dir / REQUIRED_MARKER,
        "readme": payload_dir / DEVELOPER_README,
        "config_overlay": source_overlays["config"],
        "connectivity_diagnostics_overlay": source_overlays["connectivity_diagnostics"],
        "corpus_bootstrap_overlay": source_overlays["corpus_bootstrap"],
        "error_classifier_overlay": source_overlays["error_classifier"],
        "templates_root": resource_trees["templates"],
        "static_root": resource_trees["static"],
    }
    _atomic_write(paths["team_config"], json.dumps(team_config, indent=2, sort_keys=True) + "\n")
    _atomic_write(paths["customer_aliases"], json.dumps(aliases, indent=2, sort_keys=True) + "\n")
    _atomic_write(paths["metadata"], json.dumps(metadata, indent=2, sort_keys=True) + "\n")
    _atomic_write(paths["marker"], marker_text)
    _atomic_write(paths["readme"], readme_text)
    return {name: str(path) for name, path in paths.items()}


__all__ = [
    "BUILD_METADATA",
    "DEVELOPER_CONFIG_OVERLAY",
    "DEVELOPER_CONFIG_OVERLAY_MARKER",
    "DEVELOPER_SOURCE_OVERLAY_MODULES",
    "DEVELOPER_README",
    "REQUIRED_MARKER",
    "SCHEMA_VERSION",
    "bind_developer_config_overlay",
    "bind_developer_source_overlays",
    "prepare_developer_payload",
]
