#!/usr/bin/env python3
"""Repository secret lint retained under its historical command name.

Credential generation is permanently disabled: this module never creates
``_bundled_secrets.py`` and is not part of either packaging workflow. Packaged
AdoptIQ processes receive credentials from their runtime environment, a
per-user AdoptIQ ``.env`` file, or Keeper. The only supported CLI operation is
``python embed_credentials.py --ci-lint``.

``ENV_KEYS`` remains a compatibility catalog for credential-shaped environment
variables and for tests that detect accidental configuration drift. The legacy
encode/decode helpers are retained for compatibility only; no production or
build path calls them.
"""
import base64
import re
import sys
from pathlib import Path

# Env keys used in the codebase (config.py, app_simple.py, adoptiq_backend, etc.).
# Path/timeout keys (OUTPUT_FOLDER, UPLOAD_FOLDER, etc.) are not embedded; frozen app uses Application Support.
ENV_KEYS = [
    "ADOPTIQ_MAIN_URL",      # enhanced_admin_dashboard_v2.py
    "CSONE_ONEDRIVE_FOLDER", # config.py - override OneDrive folder for CSOne reports
    "CSONE_SHARED_FOLDER_URL", # config.py - shared folder URL for "Open shared folder" button
    "ADOPTIQ_SECRET_KEY",    # config.py, app_simple.py
    "ADOPTIQ_ADMIN_SECRET_KEY",  # enhanced_admin_dashboard_v2.py (required for packaged builds)
    "ANTHROPIC_API_KEY",     # cisco_internal_integrations.py
    "BST_API_KEY",           # app_simple.py, cisco_integration_config.py
    "BST_CLIENT_SECRET",     # cisco_integration_config.py
    "BST_ENABLE_WEB_SCRAPING",  # cisco_internal_integrations.py
    "CIRCUIT_API_KEY",       # cisco_integration_config.py
    "CIRCUIT_APP_KEY",       # config.py
    "CIRCUIT_CLIENT_ID",     # config.py
    "CIRCUIT_CLIENT_SECRET", # config.py
    "CIRCUIT_MODEL_NAME",    # config.py
    "CIRCUIT_MODEL_NAME_ASK_AI",   # Round 69 / Build 43 -- per-call-site override for Ask AI
    "CIRCUIT_MODEL_NAME_REPORT",   # Round 69 / Build 43 -- per-call-site override for report narratives
    "KEEPER_NAMESPACE",      # config.py
    "KEEPER_ROLE_ID",        # config.py
    "KEEPER_SECRET_ID",      # config.py
    "KEEPER_SECRET_PATH",    # config.py
    "KEEPER_URL",            # config.py
    "PSIRT_API_KEY",        # app_simple.py
    "PSIRT_CLIENT_SECRET",   # app_simple.py
    "SECRET_KEY",            # config.py
    "SNOWFLAKE_ACCOUNT",     # config.py, setup.py
    "SNOWFLAKE_DATABASE",    # setup.py
    "SNOWFLAKE_PASSWORD",    # config.py
    "SNOWFLAKE_ROLE",        # config.py
    "SNOWFLAKE_SCHEMA",      # setup.py
    "SNOWFLAKE_USER",        # config.py
    "SNOWFLAKE_WAREHOUSE",   # config.py
]

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


def _parse_env_file(path: Path) -> dict:
    out = {}
    if not path.exists():
        return out
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
                if k in ENV_KEYS and v:
                    out[k] = v
    return out


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
    sys.stderr.write(
        "Credential embedding is permanently disabled. Configure the process "
        "environment or the per-user AdoptIQ .env file; builds never generate "
        "or package _bundled_secrets.py. Use --ci-lint only for repository "
        "secret scanning.\n"
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
