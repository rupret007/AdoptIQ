"""Round 8 compatibility guards for the retired bundled-secret artifact."""
from __future__ import annotations

import ast
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_bundled_secrets_artifact_is_absent() -> None:
    assert not REPO_ROOT.joinpath('_bundled_secrets.py').exists()


def test_production_modules_never_import_bundled_secrets() -> None:
    offenders: list[str] = []
    production_paths = list(REPO_ROOT.glob('*.py'))
    production_paths.extend((REPO_ROOT / 'scripts').rglob('*.py'))
    for path in production_paths:
        tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
        for node in ast.walk(tree):
            imported: tuple[str, ...] = ()
            if isinstance(node, ast.Import):
                imported = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported = (node.module,)
            if any(name == '_bundled_secrets' for name in imported):
                offenders.append(str(path.relative_to(REPO_ROOT)))
    assert offenders == []
