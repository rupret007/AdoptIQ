#!/usr/bin/env python3
"""
Embed configuration into the app bundle.
Put your values in secrets.env, then: python3 embed_credentials.py
Then run build_mac_dmg.sh. Do not commit secrets.env or _bundled_secrets.py.

ENV_KEYS: env keys used in config.py, app_simple.py, adoptiq_backend.py,
cisco_integration_config.py, cisco_internal_integrations.py, enhanced_admin_dashboard_v2.py, setup.py.
"""
import base64
import importlib.util
import re
import sys
from pathlib import Path

# Non-secret configuration that may be XOR-obfuscated into ``_bundled_secrets.py``.
# Path/timeout keys (OUTPUT_FOLDER, UPLOAD_FOLDER, etc.) are not embedded; the
# frozen app uses Application Support defaults instead.
ENV_KEYS = [
    "ADOPTIQ_MAIN_URL",      # enhanced_admin_dashboard_v2.py
    "CSONE_ONEDRIVE_FOLDER", # config.py - override OneDrive folder for CSOne reports
    "CSONE_SHARED_FOLDER_URL", # config.py - shared folder URL for "Open shared folder" button
    "CIRCUIT_MODEL_NAME",    # config.py
    "CIRCUIT_MODEL_NAME_ASK_AI",   # Round 69 / Build 43 -- per-call-site override for Ask AI
    "CIRCUIT_MODEL_NAME_REPORT",   # Round 69 / Build 43 -- per-call-site override for report narratives
    "KEEPER_NAMESPACE",      # config.py
    "KEEPER_SECRET_PATH",    # config.py
    "KEEPER_URL",            # config.py
    "SNOWFLAKE_ACCOUNT",     # config.py, setup.py
    "SNOWFLAKE_DATABASE",    # setup.py
    "SNOWFLAKE_ROLE",        # config.py
    "SNOWFLAKE_SCHEMA",      # setup.py
    "SNOWFLAKE_USER",        # config.py
    "SNOWFLAKE_WAREHOUSE",   # config.py
]

# Authentication material must never be recoverable from a frozen archive.  The XOR
# bundle is not a confidentiality boundary; packaged builds read these from the
# owner-protected Application Support ``.env`` instead.
RUNTIME_ONLY_ENV_KEYS = frozenset(
    {
        "ADOPTIQ_ADMIN_SECRET_KEY",
        "ADOPTIQ_SECRET_KEY",
        "ANTHROPIC_API_KEY",
        "CIRCUIT_API_KEY",
        "CIRCUIT_APP_KEY",
        "CIRCUIT_CLIENT_ID",
        "CIRCUIT_CLIENT_SECRET",
        "KEEPER_ROLE_ID",
        "KEEPER_SECRET_ID",
        "PSIRT_API_KEY",
        "PSIRT_CLIENT_SECRET",
        "SECRET_KEY",
        "SNOWFLAKE_PASSWORD",
    }
)

# Minimum runtime credentials a packaged build needs before it can start reports.
REQUIRED_RUNTIME_CREDENTIAL_KEYS = frozenset(
    {
        "ADOPTIQ_ADMIN_SECRET_KEY",
        "ADOPTIQ_SECRET_KEY",
        "CIRCUIT_APP_KEY",
        "CIRCUIT_CLIENT_ID",
        "CIRCUIT_CLIENT_SECRET",
        "KEEPER_ROLE_ID",
        "KEEPER_SECRET_ID",
        "PSIRT_API_KEY",
        "PSIRT_CLIENT_SECRET",
    }
)

# Obfuscation key (same in script and generated module)
OBFUSCATE_KEY = "AdoptIQ-Mac-2024"


def _xor_bytes(data: bytes, key: str) -> bytes:
    kb = key.encode("utf-8")
    return bytes(b ^ kb[i % len(kb)] for i, b in enumerate(data))


def _encode_value(s: str) -> str:
    raw = _xor_bytes(s.encode("utf-8"), OBFUSCATE_KEY)
    return base64.b64encode(raw).decode("ascii")


def _decode_value(encoded: str) -> str:
    raw = base64.b64decode(encoded.encode("ascii"))
    return _xor_bytes(raw, OBFUSCATE_KEY).decode("utf-8")


def _parse_env_file(path: Path, *, include_runtime_only: bool = False) -> dict:
    out = {}
    if not path.exists():
        return out
    allowed_keys = set(ENV_KEYS)
    if include_runtime_only:
        allowed_keys.update(RUNTIME_ONLY_ENV_KEYS)
    # encoding="utf-8-sig" so that a UTF-8 BOM at the start of the file
    # (Notepad / Windows exports / hand-edited files) is silently consumed.
    # Without this, the first non-comment key would be prefixed with U+FEFF,
    # silently fail the ENV_KEYS membership check, and ship a build that
    # is missing exactly one credential without any warning.
    with open(path, "r", encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                k, _, v = line.partition("=")
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                if k in allowed_keys and v:
                    out[k] = v
    return out


def decode_generated_bundle(path: Path) -> dict[str, str]:
    """Decode ``_bundled_secrets.py`` for release inspection (never log values)."""

    spec = importlib.util.spec_from_file_location("_bundled_secrets_inspect", path)
    if spec is None or spec.loader is None:
        raise ValueError(f"{path} could not be loaded for inspection")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    getter = getattr(module, "get_secrets", None)
    if not callable(getter):
        raise ValueError(f"{path} is missing get_secrets()")
    decoded = getter()
    if not isinstance(decoded, dict):
        raise ValueError(f"{path} get_secrets() did not return a mapping")
    return {str(key): str(value) for key, value in decoded.items()}


def bundle_contains_runtime_credentials(bundle_path: Path) -> list[str]:
    """Return runtime-only keys present in a generated bundle (should be empty)."""

    if not bundle_path.is_file():
        return []
    decoded = decode_generated_bundle(bundle_path)
    return sorted(key for key in decoded if key in RUNTIME_ONLY_ENV_KEYS)


# --------------------------------------------------------------------------
# Round 8 / Phase 6.2: CI lint for committed real secrets.
#
# This guard scans the working tree (or, in CI, the staged/committed diff)
# for *real* secret formats so that an accidental ``git add secrets.env``
# or ``git add _bundled_secrets.py`` (with real, non-XOR'd contents) is
# blocked before the commit lands.  We deliberately:
#
#   * skip the XOR-obfuscated ``_DATA`` strings inside _bundled_secrets.py
#     (they are obfuscated and not raw secrets), and
#   * skip the ``ENV_KEYS`` list and template files which only contain
#     key NAMES, not values.
#
# Patterns intentionally cover the formats called out by the
# codeguard-1-hardcoded-credentials rule (AWS, Stripe, Google, GitHub,
# Slack, Snowflake account+pwd connection strings, JWT, PEM private
# keys, Keeper One-Time tokens, generic high-entropy hex tokens).
# --------------------------------------------------------------------------

# (label, compiled regex).  Patterns must match a *value*, not a key
# name, to keep false positives low.
_SECRET_PATTERNS = [
    ("AWS access key id", re.compile(r'\b(?:AKIA|AGPA|AIDA|AROA|AIPA|ANPA|ANVA|ASIA)[A-Z0-9]{16}\b')),
    ("AWS secret access key (likely)", re.compile(r'(?i)aws(.{0,20})?(secret|private).{0,20}[\'"][A-Za-z0-9/+=]{40}[\'"]')),
    ("Stripe live secret key", re.compile(r'\bsk_live_[0-9a-zA-Z]{24,}\b')),
    ("Stripe test secret key", re.compile(r'\bsk_test_[0-9a-zA-Z]{24,}\b')),
    ("Google API key", re.compile(r'\bAIza[0-9A-Za-z_\-]{35}\b')),
    ("GitHub personal access token", re.compile(r'\bghp_[A-Za-z0-9]{36}\b')),
    ("GitHub OAuth token", re.compile(r'\bgho_[A-Za-z0-9]{36}\b')),
    ("GitHub user-to-server token", re.compile(r'\bghu_[A-Za-z0-9]{36}\b')),
    ("GitHub server-to-server token", re.compile(r'\bghs_[A-Za-z0-9]{36}\b')),
    ("GitHub refresh token", re.compile(r'\bghr_[A-Za-z0-9]{36}\b')),
    ("Slack bot token", re.compile(r'\bxox[abprs]-[0-9A-Za-z\-]{10,}\b')),
    ("JWT (3-segment)", re.compile(r'\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b')),
    ("PEM private key block", re.compile(r'-----BEGIN [A-Z ]*PRIVATE KEY-----')),
    ("Snowflake account+credentials URL", re.compile(r'(?i)snowflake://[^\s\'"<>]+:[^\s\'"<>]+@')),
    ("Generic credential URL", re.compile(r'\b[a-z][a-z0-9+.\-]*://[^\s/@\'"<>]+:[^\s/@\'"<>]{6,}@')),
    ("Cisco/Keeper one-time token", re.compile(r'\bUS:[A-Za-z0-9_\-]{20,}\b')),
]

# Files / suffixes that are allowed to contain secret-shaped strings
# (e.g. test fixtures and this lint module itself).
_LINT_SKIP_FILE_SUFFIXES = (
    '.pyc', '.pyo', '.so', '.dylib', '.dll',
    '.png', '.jpg', '.jpeg', '.gif', '.ico', '.pdf', '.zip', '.gz',
    '.dmg', '.icns', '.woff', '.woff2', '.ttf', '.otf',
)
_LINT_SKIP_DIR_NAMES = {
    '.git', '.venv', 'venv', 'env', '__pycache__',
    'node_modules', 'build', 'dist', '.mypy_cache', '.pytest_cache',
    'AdoptIQ.app',
}
# Files that legitimately discuss secret formats / examples and should
# not trigger the lint.  ``embed_credentials.py`` is ourselves, and the
# template intentionally lists key names without values.
_LINT_SKIP_FILE_NAMES = {
    'embed_credentials.py',
    'secrets.env.template',
    '.env.template',
    '_bundled_secrets.py',  # XOR-obfuscated; rejected separately by lint
}


def _iter_lint_targets(root: Path):
    """Yield text files to scan, skipping vendor / build / binary files."""
    for path in root.rglob('*'):
        if not path.is_file():
            continue
        # Skip vendored / cache directories anywhere in the path.
        if any(part in _LINT_SKIP_DIR_NAMES for part in path.parts):
            continue
        if path.suffix.lower() in _LINT_SKIP_FILE_SUFFIXES:
            continue
        if path.name in _LINT_SKIP_FILE_NAMES:
            continue
        yield path


def _bundled_secrets_committed(root: Path) -> bool:
    """Return True if a real, non-template _bundled_secrets.py is on disk
    AND the obfuscated ``_DATA`` map appears to be populated.  This is
    a strong signal that the build artifact was committed by mistake.
    """
    bs = root / '_bundled_secrets.py'
    if not bs.exists():
        return False
    try:
        text = bs.read_text(encoding='utf-8', errors='replace')
    except OSError:
        return False
    # An empty/template bundle would have ``_DATA = {}`` with no entries.
    return bool(re.search(r"_DATA\s*=\s*\{[^}]+:\s*'[^']+'", text, re.DOTALL))


def ci_lint(root: Path | None = None) -> int:
    """Scan the working tree for committed real-secret material.
    Returns 0 on success, 1 on any finding.  Designed for CI hooks.
    """
    root = root or Path(__file__).resolve().parent
    findings: list[tuple[str, int, str, str]] = []

    # Hard-fail if a populated _bundled_secrets.py is present alongside
    # source.  Build artifacts must not live in the repo.
    if _bundled_secrets_committed(root):
        findings.append((
            str(root / '_bundled_secrets.py'),
            0,
            'populated _bundled_secrets.py present',
            'Generated build artifact must not be committed; remove and add to .gitignore.',
        ))

    for path in _iter_lint_targets(root):
        try:
            with open(path, 'r', encoding='utf-8', errors='replace') as fh:
                for lineno, line in enumerate(fh, start=1):
                    # Hard cap line scans to avoid pathological lines.
                    if len(line) > 4096:
                        continue
                    for label, pattern in _SECRET_PATTERNS:
                        m = pattern.search(line)
                        if m:
                            snippet = m.group(0)
                            # Always truncate so we never echo a complete
                            # credential back via the lint output, even
                            # for short formats (AWS access key id is
                            # only 20 chars).  Keep enough prefix/suffix
                            # for an operator to locate the source line.
                            if len(snippet) > 12:
                                snippet = snippet[:6] + '…' + snippet[-4:]
                            else:
                                snippet = snippet[:3] + '…'
                            findings.append((str(path), lineno, label, snippet))
        except OSError:
            continue

    if findings:
        sys.stderr.write(
            "[embed_credentials] CI secret lint failed: "
            f"{len(findings)} potential real-secret findings.\n"
        )
        for fp, ln, label, snippet in findings[:50]:
            sys.stderr.write(f"  {fp}:{ln}: {label} -> {snippet}\n")
        if len(findings) > 50:
            sys.stderr.write(f"  ... ({len(findings) - 50} more)\n")
        sys.stderr.write(
            "Fix: rotate any leaked credential, remove from history, and source "
            "secrets from Keeper / secret manager instead of source files.\n"
        )
        return 1
    print("[embed_credentials] CI secret lint passed (no real-secret formats detected).")
    return 0


def main():
    # --ci-lint mode: scan the repo for committed real secrets and exit.
    if any(arg in ('--ci-lint', '--lint') for arg in sys.argv[1:]):
        return ci_lint()
    root = Path(__file__).resolve().parent
    # Prefer secrets.env so .env can stay for local dev without embedding
    env_path = root / "secrets.env"
    if not env_path.exists():
        env_path = root / ".env"
    secrets = _parse_env_file(env_path)
    if not secrets:
        print("No secrets.env or .env found with values. Copy secrets.env.template to secrets.env, fill in values, then run again.")
        return 1

    # Generate _bundled_secrets.py (decoder + obfuscated data)
    # Round 8 / Phase 6.1 (re-applied after Round 10 audit found the
    # generator had been silently regenerated without the warning
    # banner, which made the bundle look like a confidentiality
    # boundary to operators).  The XOR+base64 obfuscation in this
    # file is **NOT A CONFIDENTIALITY BOUNDARY** — it only prevents
    # casual ``strings`` discovery and trivial grep over the .app
    # bundle.  Anyone with read access to the .app can decode every
    # value in microseconds.  Real credential material MUST be sourced
    # from Keeper / a real secret manager and the bundle file MUST be
    # written ``0o600`` (owner-only) and never committed to source
    # control.  We also emit a runtime ``logging`` warning the first
    # time the module is imported so packaged builds leave an audit
    # trail when they fall back to the bundled values.
    lines = [
        "# Auto-generated by embed_credentials.py - do not edit.",
        "#",
        "# WARNING: NOT A CONFIDENTIALITY BOUNDARY.  The XOR+base64",
        "# obfuscation below only blocks casual ``strings`` discovery",
        "# inside the .app bundle.  It is reversible in microseconds by",
        "# anyone with read access.  Real secrets MUST live in Keeper /",
        "# a managed secret store; this file is a packaging fallback.",
        "# The generator writes this file 0o600 (owner-only) so the",
        "# packaged ``.app`` cannot be world-readable.",
        "import base64",
        "import logging as _logging",
        "import os as _os",
        "",
        "_logging.getLogger(__name__).warning(",
        "    \"_bundled_secrets imported: XOR-obfuscated bundle is NOT a \"",
        "    \"confidentiality boundary; rotate any leaked value via Keeper.\"",
        ")",
        "",
        "_KEY = " + repr(OBFUSCATE_KEY),
        "_DATA = {",
    ]
    for k, v in sorted(secrets.items()):
        enc = _encode_value(v)
        lines.append(f'    {repr(k)}: {repr(enc)},')
    lines.append("}")
    lines.append("")
    lines.append("def _xor_bytes(data: bytes, key: str):")
    lines.append("    kb = key.encode(\"utf-8\")")
    lines.append("    return bytes(b ^ kb[i % len(kb)] for i, b in enumerate(data))")
    lines.append("")
    lines.append("def get_secrets():")
    lines.append("    out = {}")
    lines.append("    for k, enc in _DATA.items():")
    lines.append("        raw = base64.b64decode(enc.encode(\"ascii\"))")
    lines.append("        out[k] = _xor_bytes(raw, _KEY).decode(\"utf-8\")")
    lines.append("    return out")
    lines.append("")

    out_path = root / "_bundled_secrets.py"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    # Round 8 / Phase 6.1: tighten file mode to 0o600 (owner read/write
    # only) so the packaged .app cannot be world-readable on shared
    # macOS hosts.  ``os.chmod`` is best-effort; on Windows builds the
    # POSIX bits are advisory and we tolerate the failure.
    try:
        import os as _post_os
        _post_os.chmod(out_path, 0o600)
    except OSError as _chmod_err:
        print(f"WARNING: could not chmod 0o600 on {out_path}: {_chmod_err}")
    print(f"Wrote {out_path} with {len(secrets)} obfuscated keys (mode 0o600).")

    # Warn if Snowflake/Keeper credentials are missing (app will fail at runtime).
    # Runtime-only keys are deliberately absent from ``secrets``, so evaluate
    # availability against the source file or every build reports a false alarm.
    configured = _parse_env_file(env_path, include_runtime_only=True)
    has_snowflake = all(configured.get(k) for k in ("SNOWFLAKE_USER", "SNOWFLAKE_ACCOUNT", "SNOWFLAKE_PASSWORD"))
    has_keeper = all(configured.get(k) for k in ("KEEPER_ROLE_ID", "KEEPER_SECRET_ID"))
    if not has_snowflake and not has_keeper:
        print("WARNING: No Snowflake or Keeper credentials configured. Add SNOWFLAKE_USER, SNOWFLAKE_ACCOUNT, SNOWFLAKE_PASSWORD (or KEEPER_ROLE_ID, KEEPER_SECRET_ID) to secrets.env and re-run to avoid runtime errors.")
    runtime_only_present = sorted(k for k in RUNTIME_ONLY_ENV_KEYS if configured.get(k))
    if runtime_only_present:
        print(
            "Runtime-only credential(s) intentionally NOT embedded: "
            + ", ".join(runtime_only_present)
            + ". Provision them in the Application Support .env (mode 0600) on each machine."
        )
    missing_runtime_required = sorted(
        key for key in REQUIRED_RUNTIME_CREDENTIAL_KEYS if not configured.get(key)
    )
    if missing_runtime_required:
        print(
            "WARNING: Missing required runtime credential(s): "
            + ", ".join(missing_runtime_required)
            + ". Add them to secrets.env and run scripts/provision_runtime_credentials.py --apply."
        )
    leaked = bundle_contains_runtime_credentials(out_path)
    if leaked:
        print(
            "ERROR: Generated bundle contains runtime-only credential key(s): "
            + ", ".join(leaked)
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
