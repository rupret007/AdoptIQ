"""Round 71 / Phase 7 (#36) -- ENV_KEYS completeness lint.

The build harness (``embed_credentials.py``) reads the operator's
``secrets.env`` and bakes a subset of those env vars into the obfuscated
``_bundled_secrets.py`` module that ships inside the frozen ``.app`` /
``.exe``.  The exact list is hard-coded in ``embed_credentials.ENV_KEYS``.

If the codebase starts depending on a NEW env var (e.g. a Round-N feature
adds ``KEEPER_NEW_THING`` or ``CIRCUIT_NEW_KNOB``) and the developer
forgets to extend ``ENV_KEYS``, the local dev machine works (the var is
in the developer's shell), the test suite passes (it doesn't actually
hit the integration), and the FROZEN BUILD silently degrades because the
bundled secrets module has no entry for the new var -- the runtime
falls back to whatever default ``os.environ.get(...)`` returns, often
``None``, and the affected feature mysteriously stops working only on
the operator's machine.

This lint scans the ``KEY_NAME`` literal arguments of every
``os.environ.get(...)`` and ``os.getenv(...)`` call in the canonical
secret-bearing modules and asserts that each unambiguous secret-shaped
key is present in ``ENV_KEYS``.  An allow-list captures the
intentionally-unbundled keys (paths, timeouts, runtime feature flags,
test-only knobs) so the lint stays green for non-secret env reads.

The set of "secret-shaped" prefixes is intentionally narrow
(``SNOWFLAKE_``, ``KEEPER_``, ``BST_``, ``CIRCUIT_``, ``PSIRT_``,
``CISCO_``, ``ANTHROPIC_``, ``OPENAI_``) so the lint catches the
high-blast-radius miss without forcing every minor env knob through the
bundling pipeline.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Set

import pytest


# --------------------------------------------------------------------------- #
# Config -- which modules to scan and which keys are intentionally unbundled. #
# --------------------------------------------------------------------------- #


# The canonical secret-bearing modules that get scanned for ``os.environ.get(KEY)``
# / ``os.getenv(KEY)`` reads.  Restricted to root-level production modules so
# that test fixtures, build helpers, and CLI scripts don't inflate the lint
# surface with their own non-bundled env reads (e.g. ``ADOPTIQ_TESTING``).
SCANNED_MODULES = (
    "config.py",
    "app_simple.py",
    "adoptiq_backend.py",
    "enhanced_admin_dashboard_v2.py",
    "cisco_internal_integrations.py",
    "snowflake_prefetch.py",
    "ask_ai_grounded.py",
    "model_resolver.py",
)


# Prefixes that mark a key as "secret-shaped" -- cannot be a runtime path /
# timeout / feature flag.  Anything that matches MUST be in ``ENV_KEYS``.
SECRET_PREFIXES = (
    "SNOWFLAKE_",
    "KEEPER_",
    "BST_",
    "CIRCUIT_",
    "PSIRT_",
    "CISCO_",
    "ANTHROPIC_",
    "OPENAI_",
)


# Keys that ARE secret-shaped by prefix but are intentionally NOT bundled
# (e.g. they are runtime-only knobs, or they are pulled from Keeper at
# runtime instead of baked at build time).  Every entry needs a one-line
# rationale so the next maintainer knows why it's exempt.
INTENTIONALLY_UNBUNDLED: Set[str] = {
    # Runtime-only behaviour switches (operator flips at runtime; never a secret).
    "BST_ENABLE_FALLBACKS",       # cisco_internal_integrations.py - runtime debug knob
    "BST_REQUEST_TIMEOUT_SECS",   # cisco_internal_integrations.py - timeout, not a credential
    "CIRCUIT_REQUEST_TIMEOUT_SECS",  # cisco_internal_integrations.py - timeout, not a credential
    "OPENAI_BASE_URL",            # cisco_internal_integrations.py - non-secret URL override
    "OPENAI_API_TYPE",            # cisco_internal_integrations.py - non-secret config knob
}


# Generic env vars that aren't secret-shaped at all (they just happen to live
# alongside the secret reads in the same module).  Listed here for the
# secondary ``ALL_ENV_READS_DOCUMENTED`` lint so reviewers can see at a glance
# which non-bundled keys the production modules consult.
NONSECRET_RUNTIME_KEYS: Set[str] = {
    # AdoptIQ-app runtime behaviour
    "ADOPTIQ_BIND_HOST", "ADOPTIQ_BIND_PUBLIC", "ADOPTIQ_ADMIN_HOST",
    "ADOPTIQ_ADMIN_BIND_PUBLIC", "ADOPTIQ_TRUSTED_PROXY_IPS",
    "ADOPTIQ_TRUST_PROXY_HEADERS", "ADOPTIQ_TESTING",
    "ADOPTIQ_BAKE_CORPUS", "ADOPTIQ_BAKE_FIXTURE_DIR",
    "ADOPTIQ_BAKE_AUTH_MODE", "ADOPTIQ_BAKE_EXTRA_ARGS",
    "ADOPTIQ_BAKE_PYTHON",
    "ADOPTIQ_VERSION", "ADOPTIQ_BUILD",
    "ADOPTIQ_CORPUS_SHARE_URL", "ADOPTIQ_SHAREPOINT_FOLDER_URL",
    "ADOPTIQ_SHAREPOINT_ENABLED",
    "ADOPTIQ_INTEL_DB_PATH", "ADOPTIQ_INTEL_REFRESH_INTERVAL_S",
    "ADOPTIQ_LOG_LEVEL",
    "ADOPTIQ_DISABLE_DAILY_REFRESH",
    "CORPUS_KNOWLEDGE_ENABLED",
    # Folder / cache path overrides
    "OUTPUT_FOLDER", "UPLOAD_FOLDER", "CSONE_OUTPUT_DIR",
    "ADOPTIQ_APP_SUPPORT", "ADOPTIQ_CACHE_DIR",
    # Generic / pre-existing env knobs
    "PYTHONPATH", "PATH", "HOME", "USER",
    "TZ", "LANG", "LC_ALL",
    # Misc Cisco BST/CDETS feature flags (not secrets)
    "BST_DISABLE_WEB", "CISCO_BST_DEFAULT_AUTH_MODE",
}


# --------------------------------------------------------------------------- #
# AST helpers                                                                 #
# --------------------------------------------------------------------------- #


def _is_environ_get(node: ast.Call) -> bool:
    """``os.environ.get(...)`` -- ``ast.Attribute -> ast.Attribute -> ast.Name('os')``."""
    func = node.func
    if not isinstance(func, ast.Attribute) or func.attr != "get":
        return False
    inner = func.value
    if not isinstance(inner, ast.Attribute) or inner.attr != "environ":
        return False
    return isinstance(inner.value, ast.Name) and inner.value.id == "os"


def _is_environ_subscript(node: ast.Subscript) -> bool:
    """``os.environ['KEY']`` -- bracket access form."""
    val = node.value
    if not isinstance(val, ast.Attribute) or val.attr != "environ":
        return False
    return isinstance(val.value, ast.Name) and val.value.id == "os"


def _is_getenv(node: ast.Call) -> bool:
    """``os.getenv(...)``."""
    func = node.func
    if not isinstance(func, ast.Attribute) or func.attr != "getenv":
        return False
    return isinstance(func.value, ast.Name) and func.value.id == "os"


def _extract_string_arg(value: ast.AST) -> str | None:
    """Return the literal string when ``value`` is a constant string node."""
    if isinstance(value, ast.Constant) and isinstance(value.value, str):
        return value.value
    return None


def _scan_module_for_env_keys(module_path: Path) -> Set[str]:
    """Return the set of env-key string literals read by the module."""
    src = module_path.read_text(encoding="utf-8", errors="replace")
    try:
        tree = ast.parse(src, filename=str(module_path))
    except SyntaxError as exc:  # pragma: no cover - lint surface
        pytest.fail(f"{module_path.name}: SyntaxError while parsing for env-key lint: {exc!r}")
    found: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if _is_environ_get(node) or _is_getenv(node):
                if node.args:
                    key = _extract_string_arg(node.args[0])
                    if key is not None:
                        found.add(key)
        elif isinstance(node, ast.Subscript) and _is_environ_subscript(node):
            slc = node.slice
            # Python 3.9+: ast.Subscript.slice is the value node directly.
            key = _extract_string_arg(slc)
            if key is not None:
                found.add(key)
    return found


def _gather_all_env_reads() -> dict:
    """Return ``{module_name: {keys}}`` for every scanned module that exists."""
    repo_root = Path(__file__).resolve().parent.parent
    out = {}
    for rel in SCANNED_MODULES:
        path = repo_root / rel
        if not path.exists():
            continue
        out[rel] = _scan_module_for_env_keys(path)
    return out


def _load_env_keys() -> Set[str]:
    """Read ``ENV_KEYS`` from ``embed_credentials.py`` without importing it."""
    import embed_credentials  # type: ignore[import-not-found]
    return set(embed_credentials.ENV_KEYS)


# --------------------------------------------------------------------------- #
# The lint                                                                    #
# --------------------------------------------------------------------------- #


def test_round71_env_keys_covers_every_secret_shaped_key_referenced_in_code() -> None:
    """Every secret-shaped env key read by production code must be in ENV_KEYS.

    Round 71 / Phase 7 (#36): catches the failure mode where a developer
    adds a new ``os.environ.get('SNOWFLAKE_NEW_THING')`` (or similar) but
    forgets to add it to ``embed_credentials.ENV_KEYS``.  The local dev
    machine works (the key is in the dev's shell), tests pass (no
    integration), but the FROZEN ``.app`` / ``.exe`` silently degrades
    because the bundled secrets module has no entry for the new key.
    """
    env_keys = _load_env_keys()
    reads = _gather_all_env_reads()
    assert reads, (
        "test_round71_env_keys_covers_every_secret_shaped_key_referenced_in_code: "
        "_gather_all_env_reads() returned no modules; the SCANNED_MODULES list "
        "may be stale or the repo layout has changed."
    )

    missing: dict = {}
    for module, keys in reads.items():
        for key in keys:
            if not any(key.startswith(p) for p in SECRET_PREFIXES):
                continue
            if key in INTENTIONALLY_UNBUNDLED:
                continue
            if key in env_keys:
                continue
            missing.setdefault(module, set()).add(key)

    if missing:
        rendered = "\n".join(
            f"  {mod}: {sorted(keys)}" for mod, keys in sorted(missing.items())
        )
        pytest.fail(
            "Round 71 / Phase 7 (#36): the following secret-shaped env keys are "
            "read by production code but are NOT in embed_credentials.ENV_KEYS. "
            "Either add them to ENV_KEYS (so the frozen build bundles them) or "
            "extend INTENTIONALLY_UNBUNDLED in this test with a one-line "
            "rationale for why the key should NOT be baked.\n"
            f"{rendered}"
        )


def test_round71_env_keys_does_not_drift_below_known_minimum() -> None:
    """ENV_KEYS must remain >= 27 entries (sized at Round 69 / Build 43).

    Pre-R71 ENV_KEYS was 32 entries (Round 69 / Build 43 added the two
    Round-69 model knobs).  Guard against an accidental shrink that
    would silently drop a credential from the frozen build.  This is a
    "thermometer" lint: it does not pin the exact contents (those drift
    legitimately as features land) but it does pin the lower bound so
    a regression that drops a key shows up immediately.
    """
    env_keys = _load_env_keys()
    assert len(env_keys) >= 27, (
        f"embed_credentials.ENV_KEYS shrank to {len(env_keys)} entries; the "
        "Round 71 floor is 27.  If a key was intentionally retired, lower "
        "the floor in this test with a comment explaining why."
    )


def test_round71_env_keys_has_round_69_model_knobs() -> None:
    """Round 69 / Build 43 added per-call-site model overrides.

    Pin them explicitly so a careless cleanup of ``ENV_KEYS`` cannot
    silently drop the operator-flippable LLM model selection contract.
    """
    env_keys = _load_env_keys()
    for required in ("CIRCUIT_MODEL_NAME_ASK_AI", "CIRCUIT_MODEL_NAME_REPORT"):
        assert required in env_keys, (
            f"Round 69 / Build 43 contract: ``{required}`` MUST be in "
            "embed_credentials.ENV_KEYS so the frozen build can honour the "
            "operator's per-call-site model override.  Pre-R71 the resolver "
            "would silently fall back to the platform default if this key "
            "was not bundled."
        )
