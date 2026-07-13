"""Regression guards for runtime-only packaged credential loading."""

from __future__ import annotations

import ast
import os
import re
import stat
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent


def _read(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def test_build_workflow_never_materializes_or_embeds_credentials() -> None:
    source = _read(".github/workflows/build.yml")

    assert "SECRETS_ENV_FILE" not in source
    assert "secrets.env" not in source
    assert "_bundled_secrets" not in source
    assert re.search(
        r"(?m)^\s*run:\s*python(?:3)?\s+embed_credentials\.py\s*$",
        source,
    ) is None

    embed_commands = [
        line.strip()
        for line in source.splitlines()
        if "embed_credentials.py" in line
    ]
    assert embed_commands == ["run: python embed_credentials.py --ci-lint"]

    quality_job = source.split("  build-mac:", maxsplit=1)[0]
    assert "python embed_credentials.py --ci-lint" in quality_job
    assert quality_job.index("python embed_credentials.py --ci-lint") < quality_job.index(
        "Install dependencies"
    )


@pytest.mark.parametrize(
    "guide",
    [
        "BUILD_WINDOWS.md",
        "CURSOR_BUILD_GUIDE.md",
        "CURSOR_PC_BUILD_INSTRUCTIONS.md",
        "CURSOR_MAC_BUILD_INSTRUCTIONS.md",
    ],
)
def test_active_build_guides_never_direct_credential_packaging(guide: str) -> None:
    source = _read(guide)

    assert "python embed_credentials.py --ci-lint" in source
    assert "SECRETS_ENV_FILE" not in source
    assert "_bundled_secrets" not in source
    assert re.search(
        r"(?m)^\s*python(?:3)?\s+embed_credentials\.py\s*$",
        source,
    ) is None

    for retired_instruction in (
        "Create `secrets.env`",
        "Copy `secrets.env.template` to `secrets.env`",
        "Generate bundled secrets",
        "Embed credentials from `secrets.env`",
        "Credentials are embedded at build time",
    ):
        assert retired_instruction not in source


@pytest.mark.parametrize(
    "source_path",
    ["CLAUDE.md", "SNOWFLAKE_USAGE.md", "bandit.yaml"],
)
def test_active_project_guidance_has_no_retired_bundle_path(source_path: str) -> None:
    source = _read(source_path)

    assert "_bundled_secrets" not in source
    assert "embedded in built app" not in source
    assert "Credentials are embedded at build time" not in source

    if source_path == "CLAUDE.md":
        assert "python embed_credentials.py --ci-lint" in source


@pytest.mark.parametrize("script", ["build_mac.sh", "build_pc.bat"])
def test_build_scripts_never_generate_bundled_secrets(script: str) -> None:
    source = _read(script)
    assert "embed_credentials.py" not in source
    assert "_bundled_secrets" not in source
    assert "Credential packaging is disabled." in source


@pytest.mark.parametrize("spec", ["adoptiq_mac.spec", "adoptiq_pc.spec"])
def test_pyinstaller_specs_never_bundle_reversible_secrets(spec: str) -> None:
    assert "_bundled_secrets" not in _read(spec)


def test_frozen_startup_uses_runtime_dotenv_without_bundled_secrets() -> None:
    source = _read("app_simple.py")
    tree = ast.parse(source)
    dotenv_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "load_dotenv"
    ]
    assert len(dotenv_calls) == 1
    dotenv_arg = ast.get_source_segment(source, dotenv_calls[0].args[0])
    assert dotenv_arg == "_APP_SUPPORT / '.env'"
    assert "load_dotenv(_BASE_PATH / '.env')" not in source
    assert "load_dotenv(Path(sys._MEIPASS)" not in source
    # After the explicit per-user read, disable python-dotenv so downstream
    # bare load_dotenv() calls cannot search the frozen bundle root.
    explicit_load = source.index("load_dotenv(_APP_SUPPORT / '.env')")
    disable_fallback = source.index("os.environ['PYTHON_DOTENV_DISABLED'] = '1'")
    assert disable_fallback > explicit_load
    assert "_bundled_secrets" not in source


def _isolated_session_key_loader():
    """Compile only the lightweight helper without importing the Flask app."""
    source = _read("app_simple.py")
    tree = ast.parse(source)
    helper = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_load_or_create_session_key"
    )
    module = ast.fix_missing_locations(ast.Module(body=[helper], type_ignores=[]))
    namespace = {"Path": Path, "os": os, "secrets": __import__("secrets")}
    exec(compile(module, "app_simple.py", "exec"), namespace)  # noqa: S102 - trusted repo AST
    return namespace["_load_or_create_session_key"]


def test_frozen_session_key_is_persistent_and_mode_0600(tmp_path: Path) -> None:
    loader = _isolated_session_key_loader()
    key_path = tmp_path / "AdoptIQ" / ".session_key"

    first = loader(key_path)
    second = loader(key_path)

    assert len(first) >= 32
    assert second == first
    assert key_path.read_text(encoding="utf-8").strip() == first
    assert stat.S_IMODE(key_path.stat().st_mode) == 0o600


def test_frozen_session_key_tightens_existing_permissions(tmp_path: Path) -> None:
    loader = _isolated_session_key_loader()
    key_path = tmp_path / ".session_key"
    existing = "persisted-session-key-value-that-is-long-enough"
    key_path.write_text(existing + "\n", encoding="utf-8")
    key_path.chmod(0o644)

    assert loader(key_path) == existing
    assert stat.S_IMODE(key_path.stat().st_mode) == 0o600


def test_connectivity_diagnostic_preserves_environment_metadata() -> None:
    """Diagnostics retain environment config after removing bundle fallback."""
    source = _read("app_simple.py")
    tree = ast.parse(source)
    route = next(
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "api_diag_connectivity"
    )
    route_source = ast.get_source_segment(source, route) or ""

    for non_secret_field in (
        "KEEPER_URL",
        "KEEPER_NAMESPACE",
        "KEEPER_SECRET_PATH",
        "SNOWFLAKE_USER",
        "SNOWFLAKE_ACCOUNT",
        "SNOWFLAKE_ROLE",
        "SNOWFLAKE_WAREHOUSE",
    ):
        assert non_secret_field in route_source

    assert "os.environ.get(key)" in route_source
    assert "_bundled_secrets" not in route_source
