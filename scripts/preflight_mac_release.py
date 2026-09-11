#!/usr/bin/env python3
"""Fail-closed, read-only preflight for an AdoptIQ macOS release build.

The preflight intentionally does not generate ``_bundled_secrets.py``, bake the
corpus, modify output folders, or contact Cisco services. It validates that the
native build host is ready before ``build_mac_dmg.sh`` starts expensive work.
Secret values are never returned in the result or written to the summary.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Sequence
from importlib.metadata import PackageNotFoundError, version as package_version


# Direct execution (``python scripts/preflight_mac_release.py``) makes Python
# place ``scripts/`` rather than the repository root on ``sys.path``.  The
# release path imports the shared, secret-safe env parser only after several
# checks have run, so module-loaded tests previously missed this packaging-only
# crash.  Bind the source root before any local import can occur.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


MIN_PYTHON = (3, 12)
MAX_PYTHON_EXCLUSIVE = (3, 14)
DEFAULT_MIN_FREE_GB = 15.0
SUPPORTED_CORPUS_SUFFIXES = frozenset({".csv", ".docx", ".xlsx"})
REQUIRED_TOOLS = (
    "bash",
    "codesign",
    "ditto",
    "file",
    "git",
    "hdiutil",
    "open",
    "xattr",
)
REQUIRED_PACKAGES = (
    "PyInstaller",
    "cryptography",
    "docx",
    "fastembed",
    "flask",
    "numpy",
    "openpyxl",
    "pandas",
)
REQUIRED_SECRET_KEYS = frozenset(
    {
        "ADOPTIQ_ADMIN_SECRET_KEY",
        "ADOPTIQ_SECRET_KEY",
        "CIRCUIT_APP_KEY",
        "CIRCUIT_CLIENT_ID",
        "CIRCUIT_CLIENT_SECRET",
    }
)
SNOWFLAKE_DIRECT_KEYS = frozenset(
    {"SNOWFLAKE_ACCOUNT", "SNOWFLAKE_PASSWORD", "SNOWFLAKE_USER"}
)
KEEPER_KEYS = frozenset({"KEEPER_ROLE_ID", "KEEPER_SECRET_ID"})
# Round 165: BST is web-only (no API); PSIRT openVuln is the Cisco security API.
ALL_SOURCE_INTEGRATION_PAIRS = (
    ("PSIRT", frozenset({"PSIRT_API_KEY", "PSIRT_CLIENT_SECRET"})),
)
SAFE_OUTPUT_NAMES = ("bake", "build", "dist", "OUTBOX")
_VERSION_RE = re.compile(r'^ADOPTIQ_VERSION\s*=\s*"([^"]+)"', re.MULTILINE)
_BUILD_RE = re.compile(r'^ADOPTIQ_BUILD\s*=\s*"([^"]+)"', re.MULTILINE)


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: str
    detail: str


def _run(argv: Sequence[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(argv),
        cwd=str(cwd),
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )


def _check(name: str, ok: bool, success: str, failure: str) -> CheckResult:
    return CheckResult(name=name, status="PASS" if ok else "FAIL", detail=success if ok else failure)


def _normalise_arch(value: str) -> str:
    arch = str(value or "").strip().lower()
    if arch in {"aarch64", "arm64"}:
        return "arm64"
    if arch in {"amd64", "x64", "x86_64"}:
        return "x86_64"
    return arch or "unknown"


def _read_version(root: Path) -> tuple[str, str]:
    text = (root / "config.py").read_text(encoding="utf-8")
    version = _VERSION_RE.search(text)
    build = _BUILD_RE.search(text)
    return (version.group(1) if version else "", build.group(1) if build else "")


def _git_value(root: Path, *args: str) -> str:
    result = _run(("git", *args), cwd=root)
    return result.stdout.strip() if result.returncode == 0 else ""


def check_git_checkout(
    root: Path,
    *,
    expected_branch: str,
    expected_commit: str,
) -> tuple[list[CheckResult], dict[str, str]]:
    results: list[CheckResult] = []
    head = _git_value(root, "rev-parse", "HEAD")
    branch = _git_value(root, "branch", "--show-current")
    upstream = _git_value(root, "rev-parse", "@{upstream}")
    status = _run(("git", "status", "--porcelain=v1", "--untracked-files=all"), cwd=root)
    dirty_lines = [line for line in status.stdout.splitlines() if line.strip()]

    results.append(_check("git repository", bool(head), "Git repository resolved", "Not a Git checkout"))
    approved_branches = {
        branch.strip()
        for branch in (expected_branch, os.environ.get("ADOPTIQ_APPROVED_BRANCH", ""))
        if branch and branch.strip()
    }
    branch_ok = branch in approved_branches if approved_branches else branch == expected_branch
    branch_detail = (
        f"Branch is {branch}"
        if branch_ok
        else (
            f"Expected one of {sorted(approved_branches) or [expected_branch]}; "
            f"found {branch or 'detached HEAD'}"
        )
    )
    results.append(
        _check(
            "release branch",
            branch_ok,
            branch_detail if branch_ok else "",
            branch_detail if not branch_ok else "",
        )
    )
    results.append(
        _check(
            "clean checkout",
            status.returncode == 0 and not dirty_lines,
            "Tracked and untracked source tree is clean",
            f"Checkout has {len(dirty_lines)} uncommitted path(s)",
        )
    )
    results.append(
        _check(
            "upstream parity",
            bool(upstream) and head == upstream,
            "HEAD matches the configured upstream",
            "HEAD does not match the configured upstream; fetch/pull before packaging",
        )
    )
    if expected_commit:
        results.append(
            _check(
                "pinned source commit",
                head == expected_commit,
                f"HEAD matches pinned commit {expected_commit[:12]}",
                f"Expected commit {expected_commit[:12]}; found {head[:12] or 'none'}",
            )
        )
    return results, {"branch": branch, "commit": head, "upstream_commit": upstream}


def check_version(
    root: Path,
    *,
    expected_version: str,
    expected_build: str,
) -> tuple[list[CheckResult], dict[str, str]]:
    version, build = _read_version(root)
    results = [
        _check("version source", bool(version and build), f"Resolved v{version} build {build}", "Version/build missing"),
    ]
    if expected_version:
        results.append(
            _check(
                "expected version",
                version == expected_version,
                f"Version is {version}",
                f"Expected version {expected_version}; found {version}",
            )
        )
    if expected_build:
        results.append(
            _check(
                "expected build",
                build == expected_build,
                f"Build is {build}",
                f"Expected build {expected_build}; found {build}",
            )
        )
    return results, {"version": version, "build": build}


def _secret_mapping(path: Path) -> dict[str, str]:
    from embed_credentials import _parse_env_file

    return {
        str(key): str(value)
        for key, value in _parse_env_file(path, include_runtime_only=True).items()
    }


def _usable_secret(value: str | None) -> bool:
    body = str(value or "").strip()
    folded = body.casefold()
    if not body or len(body) < 4:
        return False
    placeholder_markers = (
        "<replace",
        "changeme",
        "example-value",
        "insert_",
        "replace-me",
        "replace_with",
        "todo",
        "your_",
        "your-",
    )
    return not any(marker in folded for marker in placeholder_markers)


def check_secrets(path: Path, *, root: Path) -> tuple[list[CheckResult], dict[str, object]]:
    results: list[CheckResult] = []
    try:
        relative_path = path.resolve().relative_to(root.resolve())
    except ValueError:
        return [
            CheckResult(
                "secrets location",
                "FAIL",
                "secrets.env must be the repository-root secrets.env file",
            )
        ], {"path": path.name, "key_count": 0}
    if relative_path != Path("secrets.env"):
        return [
            CheckResult(
                "secrets location",
                "FAIL",
                "secrets.env must be the repository-root secrets.env file",
            )
        ], {"path": path.name, "key_count": 0}
    exists = path.is_file() and not path.is_symlink()
    results.append(
        _check(
            "secrets file",
            exists,
            "secrets.env is a regular non-symlink file",
            "secrets.env is missing, not regular, or is a symlink",
        )
    )
    if not exists:
        return results, {"path": path.name, "key_count": 0}

    file_stat = path.stat()
    mode = stat.S_IMODE(file_stat.st_mode)
    safe_mode = mode & 0o077 == 0
    results.append(
        _check(
            "secrets permissions",
            safe_mode,
            f"Owner-only permissions ({mode:04o})",
            f"Permissions {mode:04o} expose the file to group/other; run chmod 600 secrets.env",
        )
    )
    tracked = _run(("git", "ls-files", "--error-unmatch", str(relative_path)), cwd=root)
    ignored = _run(("git", "check-ignore", "-q", str(relative_path)), cwd=root)
    results.append(
        _check(
            "secrets Git boundary",
            tracked.returncode != 0 and ignored.returncode == 0,
            "secrets.env is ignored and untracked",
            "secrets.env must be untracked and explicitly ignored",
        )
    )

    secrets = _secret_mapping(path)
    keys = set(secrets)
    missing_required = sorted(key for key in REQUIRED_SECRET_KEYS if not _usable_secret(secrets.get(key)))
    results.append(
        _check(
            "application and AI credentials",
            not missing_required,
            "Required app and CircuIT credential keys are populated",
            "Missing required key(s): " + ", ".join(missing_required),
        )
    )
    has_snowflake = all(_usable_secret(secrets.get(key)) for key in SNOWFLAKE_DIRECT_KEYS)
    has_keeper = all(_usable_secret(secrets.get(key)) for key in KEEPER_KEYS)
    results.append(
        _check(
            "Snowflake credential path",
            has_snowflake or has_keeper,
            "Snowflake access is configured through direct or Keeper credentials",
            "Need all direct Snowflake keys or the Keeper role/secret pair",
        )
    )
    for label, required in ALL_SOURCE_INTEGRATION_PAIRS:
        missing = sorted(key for key in required if not _usable_secret(secrets.get(key)))
        results.append(
            _check(
                f"{label} integration credentials",
                not missing,
                f"{label} credential pair is populated",
                f"Missing {label} key(s): " + ", ".join(missing),
            )
        )
    results.extend(_runtime_only_credential_checks(secrets))
    results.extend(_bundled_secrets_boundary_checks(root))
    return results, {"path": path.name, "key_count": len(keys)}


def _runtime_only_credential_checks(secrets: dict[str, str]) -> list[CheckResult]:
    """Verify runtime-only credentials are provisioned rather than embedded.

    Keys in ``RUNTIME_ONLY_ENV_KEYS`` are deliberately excluded from the frozen
    bundle, so a packaged build only works when the owner-protected Application
    Support ``.env`` already carries them.  Checking this before the build keeps
    the failure at preflight instead of at a user's first report.
    """

    from embed_credentials import RUNTIME_ONLY_ENV_KEYS

    results: list[CheckResult] = []
    runtime_path = _runtime_env_path()
    configured = sorted(key for key in RUNTIME_ONLY_ENV_KEYS if _usable_secret(secrets.get(key)))

    if not runtime_path.is_file():
        results.append(
            _check(
                "runtime credential provisioning",
                False,
                "",
                f"{runtime_path} is missing; run scripts/provision_runtime_credentials.py --apply",
            )
        )
        return results

    mode = stat.S_IMODE(runtime_path.stat().st_mode)
    results.append(
        _check(
            "runtime credential permissions",
            mode & 0o077 == 0,
            f"Runtime .env is owner-only ({mode:04o})",
            f"Permissions {mode:04o} expose the runtime .env; run chmod 600 on it",
        )
    )

    provisioned = _read_runtime_env(runtime_path)
    stale = sorted(
        key
        for key in configured
        if provisioned.get(key, "") != secrets.get(key, "")
    )
    results.append(
        _check(
            "runtime credential freshness",
            not stale,
            "Runtime .env matches the configured runtime-only credential(s)",
            "Runtime .env is missing or stale for: " + ", ".join(stale)
            + "; run scripts/provision_runtime_credentials.py --apply",
        )
    )
    return results


def _runtime_env_path() -> Path:
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", str(Path.home()))) / "AdoptIQ" / ".env"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "AdoptIQ" / ".env"
    return Path.home() / ".adoptiq" / ".env"


def _bundled_secrets_boundary_checks(root: Path) -> list[CheckResult]:
    """Fail closed when a generated bundle still carries runtime credentials."""

    from embed_credentials import bundle_contains_runtime_credentials

    bundle_path = root / "_bundled_secrets.py"
    if not bundle_path.is_file():
        return [
            _check(
                "bundled credential boundary",
                True,
                "No generated _bundled_secrets.py present before packaging",
                "",
            )
        ]
    leaked = bundle_contains_runtime_credentials(bundle_path)
    return [
        _check(
            "bundled credential boundary",
            not leaked,
            "Generated bundle contains only non-secret configuration keys",
            "Generated bundle contains runtime-only credential key(s): "
            + ", ".join(leaked),
        )
    ]


def _read_runtime_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError:
        return values
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, raw = line.partition("=")
        values[key.strip()] = raw.strip().strip('"').strip("'")
    return values


def resolve_corpus_source(explicit: Path | None) -> Path | None:
    if explicit is not None:
        return explicit.expanduser()
    env_path = os.environ.get("ADOPTIQ_BAKE_FIXTURE_DIR", "").strip()
    if env_path:
        return Path(env_path).expanduser()
    # A release must name the approved snapshot explicitly. Auto-discovery is
    # useful at runtime, but can silently pick the wrong hydrated OneDrive tree
    # on a machine with several historical corpus folders.
    return None


def check_corpus_source(path: Path | None) -> tuple[list[CheckResult], dict[str, object]]:
    if path is None:
        return [
            CheckResult(
                "approved corpus source",
                "FAIL",
                "Set ADOPTIQ_BAKE_FIXTURE_DIR or pass --corpus-source to the approved local snapshot",
            )
        ], {"file_count": 0}
    is_safe_dir = path.is_dir() and not path.is_symlink()
    results = [
        _check(
            "approved corpus source",
            is_safe_dir,
            "Approved local corpus directory resolved",
            "Corpus source is missing, not a directory, or is a symlink",
        )
    ]
    if not is_safe_dir:
        return results, {"path": str(path), "file_count": 0}

    supported: list[Path] = []
    unsafe_supported = 0
    symlinks = 0
    for child in path.rglob("*"):
        # Reject every link entry, not only links whose own filename has a
        # supported suffix. A directory link (or an unsupported-looking link)
        # can still redirect traversal outside the operator-approved snapshot.
        if child.is_symlink():
            symlinks += 1
            continue
        if not child.is_file() or child.suffix.lower() not in SUPPORTED_CORPUS_SUFFIXES:
            continue
        try:
            if child.stat().st_size > 0:
                supported.append(child)
            else:
                unsafe_supported += 1
        except OSError:
            unsafe_supported += 1
    results.append(
        _check(
            "parseable corpus inputs",
            bool(supported),
            f"Found {len(supported)} non-empty supported recursive input(s)",
            "No non-empty .csv/.docx/.xlsx inputs found",
        )
    )
    results.append(
        _check(
            "corpus symlink boundary",
            symlinks == 0,
            "No source symlink entries",
            f"Corpus source contains {symlinks} symlink(s); hydrate/copy approved files locally",
        )
    )
    results.append(
        _check(
            "OneDrive hydration",
            unsafe_supported == 0,
            "All supported inputs are non-empty and readable",
            f"Found {unsafe_supported} zero-byte or unreadable supported input(s); fully hydrate before release",
        )
    )
    return results, {
        "path": str(path.resolve()),
        "file_count": len(supported),
        "zero_byte_count": unsafe_supported,
        "symlink_count": symlinks,
    }


def _rosetta_translated(root: Path) -> bool:
    result = _run(("/usr/sbin/sysctl", "-in", "sysctl.proc_translated"), cwd=root)
    return result.returncode == 0 and result.stdout.strip() == "1"


def check_host(
    root: Path,
    *,
    expected_arch: str,
    min_free_gb: float,
    allow_system_python: bool,
) -> tuple[list[CheckResult], dict[str, object]]:
    results: list[CheckResult] = []
    is_macos = platform.system() == "Darwin"
    actual_arch = _normalise_arch(platform.machine())
    expected = _normalise_arch(expected_arch) if expected_arch else actual_arch
    translated = _rosetta_translated(root) if is_macos else False
    results.append(_check("macOS host", is_macos, "Host is macOS", f"Host is {platform.system() or 'unknown'}"))
    results.append(
        _check(
            "native architecture",
            actual_arch in {"arm64", "x86_64"} and actual_arch == expected and not translated,
            f"Native {actual_arch} process (not Rosetta translated)",
            f"Expected native {expected}; actual={actual_arch}, rosetta={translated}",
        )
    )
    python_ok = MIN_PYTHON <= sys.version_info[:2] < MAX_PYTHON_EXCLUSIVE
    results.append(
        _check(
            "Python version",
            python_ok,
            f"Python {platform.python_version()} is supported",
            f"Need Python >=3.12,<3.14; found {platform.python_version()}",
        )
    )
    venv_python = root / ".venv" / "bin" / "python"
    using_venv = venv_python.exists() and Path(sys.executable).resolve() == venv_python.resolve()
    results.append(
        _check(
            "release virtualenv",
            using_venv or allow_system_python,
            "Preflight uses the repository .venv interpreter" if using_venv else "System Python explicitly allowed",
            "Run .venv/bin/python scripts/preflight_mac_release.py (or explicitly allow system Python)",
        )
    )

    missing_tools = [name for name in REQUIRED_TOOLS if shutil.which(name) is None]
    results.append(
        _check(
            "macOS packaging tools",
            not missing_tools,
            "Required macOS packaging tools are available",
            "Missing executable(s): " + ", ".join(missing_tools),
        )
    )
    missing_packages = [name for name in REQUIRED_PACKAGES if importlib.util.find_spec(name) is None]
    results.append(
        _check(
            "Python build packages",
            not missing_packages,
            "Required Python build packages are importable",
            "Missing Python package(s): " + ", ".join(missing_packages),
        )
    )
    pip_check = _run((sys.executable, "-m", "pip", "check"), cwd=root)
    results.append(
        _check(
            "dependency consistency",
            pip_check.returncode == 0,
            "pip check reports no broken requirements",
            "pip check failed; repair the .venv before packaging",
        )
    )

    constraints_path = root / "constraints-build113.txt"
    constraint_errors: list[str] = []
    if constraints_path.is_file():
        for raw_line in constraints_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "==" not in line:
                continue
            package, expected_version = (part.strip() for part in line.split("==", 1))
            try:
                actual_version = package_version(package)
            except PackageNotFoundError:
                constraint_errors.append(f"{package}=missing")
            else:
                if actual_version != expected_version:
                    constraint_errors.append(f"{package}=mismatch")
    else:
        constraint_errors.append("constraints-build113.txt=missing")
    results.append(
        _check(
            "Build 113 dependency constraints",
            not constraint_errors,
            "Installed direct dependencies match the validated Build 113 constraints",
            "Constraint mismatch(es): " + ", ".join(constraint_errors),
        )
    )

    free_gb = shutil.disk_usage(root).free / (1024**3)
    results.append(
        _check(
            "free disk space",
            free_gb >= min_free_gb,
            f"{free_gb:.2f} GiB free (minimum {min_free_gb:.2f} GiB)",
            f"Only {free_gb:.2f} GiB free; need at least {min_free_gb:.2f} GiB",
        )
    )
    unsafe_links = [name for name in SAFE_OUTPUT_NAMES if (root / name).is_symlink()]
    results.append(
        _check(
            "output path safety",
            not unsafe_links,
            "Build output paths are not symlinks",
            "Refusing symlinked output path(s): " + ", ".join(unsafe_links),
        )
    )
    configured_outbox = Path(os.environ.get("ADOPTIQ_OUTBOX_DIR", str(root / "OUTBOX"))).expanduser().resolve()
    expected_outbox = (root / "OUTBOX").resolve()
    results.append(
        _check(
            "release output root",
            configured_outbox == expected_outbox,
            "Release output is pinned to repository OUTBOX",
            "ADOPTIQ_OUTBOX_DIR must resolve to the repository OUTBOX for a release build",
        )
    )
    mounted = Path("/Volumes/AdoptIQ").exists()
    results.append(
        _check(
            "DMG mount state",
            not mounted,
            "No existing /Volumes/AdoptIQ mount",
            "Detach the existing AdoptIQ DMG before packaging",
        )
    )
    constraints_sha = ""
    if constraints_path.is_file():
        constraints_sha = hashlib.sha256(constraints_path.read_bytes()).hexdigest()
    return results, {
        "architecture": actual_arch,
        "rosetta_translated": translated,
        "python": platform.python_version(),
        "python_executable": str(Path(sys.executable).resolve()),
        "free_gb": round(free_gb, 2),
        "constraints_sha256": constraints_sha,
    }


def check_models() -> list[CheckResult]:
    results: list[CheckResult] = []
    try:
        from ask_ai_embeddings import embed_texts, reset_embedder_for_tests

        reset_embedder_for_tests()
        vectors = embed_texts(
            [
                "Which customers have adoption barriers blocking production rollout?",
                "Customer ACME has an adoption barrier blocking rollout.",
                "This unrelated record describes a maintenance notice.",
            ]
        )
        relevant_score = float((vectors[0] * vectors[1]).sum()) if vectors is not None else 0.0
        unrelated_score = float((vectors[0] * vectors[2]).sum()) if vectors is not None else 0.0
        embed_ok = bool(
            vectors is not None
            and tuple(vectors.shape) == (3, 384)
            and bool((vectors == vectors).all())
            and all(math.isfinite(float(value)) for value in vectors.reshape(-1))
            and all(float((row * row).sum()) > 0.0 for row in vectors)
            and relevant_score > unrelated_score + 0.02
        )
        results.append(
            CheckResult(
                "embedding model self-test",
                "PASS" if embed_ok else "FAIL",
                "Embedding model returned finite vectors with expected semantic ordering"
                if embed_ok
                else "Embedding model failed the release shape/semantic probe",
            )
        )
    except Exception as exc:  # noqa: BLE001 - release preflight must report any backend failure
        results.append(CheckResult("embedding model self-test", "FAIL", f"{type(exc).__name__}: {exc}"))

    try:
        from ask_ai_reranker import bake_self_test

        ok, detail = bake_self_test()
    except Exception as exc:  # noqa: BLE001 - release preflight must report any backend failure
        results.append(CheckResult("reranker model self-test", "FAIL", f"{type(exc).__name__}: {exc}"))
    else:
        results.append(CheckResult("reranker model self-test", "PASS" if ok else "FAIL", str(detail)))
    return results


def check_model_cache() -> tuple[list[CheckResult], dict[str, object]]:
    raw = (
        os.environ.get("ADOPTIQ_FASTEMBED_CACHE")
        or os.environ.get("FASTEMBED_CACHE_PATH")
        or ""
    ).strip()
    if not raw:
        return [
            CheckResult(
                "persistent model cache",
                "FAIL",
                "Set ADOPTIQ_FASTEMBED_CACHE to a dedicated local release cache",
            )
        ], {"configured": False}
    path = Path(raw).expanduser()
    safe = path.is_dir() and not path.is_symlink()
    return [
        _check(
            "persistent model cache",
            safe,
            "Dedicated release model cache is a real directory",
            "Release model cache is missing, not a directory, or is a symlink",
        )
    ], {"configured": True, "path": str(path.resolve()) if safe else path.name}


def _write_summary(path: Path, payload: dict[str, object]) -> None:
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--secrets-file", type=Path, default=Path("secrets.env"))
    parser.add_argument("--corpus-source", type=Path)
    parser.add_argument("--expected-version", default="")
    parser.add_argument("--expected-build", default="")
    parser.add_argument("--expected-commit", default="")
    parser.add_argument("--expected-branch", default="main")
    parser.add_argument("--expected-arch", choices=("arm64", "x86_64"), default="")
    parser.add_argument(
        "--min-free-gb",
        type=float,
        default=float(os.environ.get("ADOPTIQ_MAC_BUILD_MIN_FREE_GB", DEFAULT_MIN_FREE_GB)),
    )
    parser.add_argument("--allow-system-python", action="store_true")
    parser.add_argument("--skip-model-self-test", action="store_true")
    parser.add_argument("--summary", type=Path)
    return parser


def _print_results(results: Iterable[CheckResult]) -> None:
    for result in results:
        print(f"[{result.status}] {result.name}: {result.detail}")


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    root = args.repo_root.expanduser().resolve()
    secrets_path = args.secrets_file.expanduser()
    if not secrets_path.is_absolute():
        secrets_path = root / secrets_path

    results: list[CheckResult] = []
    metadata: dict[str, object] = {}
    git_results, metadata["git"] = check_git_checkout(
        root,
        expected_branch=args.expected_branch,
        expected_commit=args.expected_commit,
    )
    results.extend(git_results)
    version_results, metadata["identity"] = check_version(
        root,
        expected_version=args.expected_version,
        expected_build=args.expected_build,
    )
    results.extend(version_results)
    secret_results, metadata["secrets"] = check_secrets(secrets_path, root=root)
    results.extend(secret_results)
    corpus_source = resolve_corpus_source(args.corpus_source)
    corpus_results, metadata["corpus"] = check_corpus_source(corpus_source)
    results.extend(corpus_results)
    host_results, metadata["host"] = check_host(
        root,
        expected_arch=args.expected_arch,
        min_free_gb=args.min_free_gb,
        allow_system_python=args.allow_system_python,
    )
    results.extend(host_results)
    cache_results, metadata["model_cache"] = check_model_cache()
    results.extend(cache_results)
    if args.skip_model_self_test:
        results.append(CheckResult("embedding and reranker model self-tests", "WARN", "Explicitly skipped"))
    else:
        results.extend(check_models())

    failures = sum(result.status == "FAIL" for result in results)
    warnings = sum(result.status == "WARN" for result in results)
    payload: dict[str, object] = {
        "schema_version": "adoptiq-mac-release-preflight/v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "ok": failures == 0,
        "failure_count": failures,
        "warning_count": warnings,
        "checks": [asdict(result) for result in results],
        "metadata": metadata,
    }
    _print_results(results)
    print(f"Preflight result: {'PASS' if failures == 0 else 'FAIL'} ({failures} failure(s), {warnings} warning(s))")
    if args.summary:
        _write_summary(args.summary, payload)
        print(f"Summary written: {args.summary.expanduser().resolve()}")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
