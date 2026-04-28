"""Round 35 / native-corpus: smoke-test ``scripts/bake_corpus.py``.

Drives the bake script in ``--auth-mode=offline_fixture`` mode (no
network, no MSAL) so the test suite can verify, end-to-end, that:

* The four ship-ready artifacts get written under ``--bake-dir``:
  ``corpus.db.enc``, ``sentinel.json``, ``salt.bin``,
  ``corpus.sentinel.lock.json``.
* Each artifact is mode 0600 and the bake-dir is mode 0700, so a
  multi-user macOS host cannot read another account's encrypted DB
  or sentinel material.
* A subsequent ``open_corpus_for_user`` against the bake-dir
  succeeds (i.e. the sentinel + salt + lock that the bake minted
  agree with each other and with the encrypted DB).
* ``--no-bake`` and ``ADOPTIQ_BAKE_CORPUS=0`` short-circuit and
  emit the ``.bake-skipped`` marker, leaving no stale artifacts.

The test never imports MSAL, never hits Microsoft Graph, never
requires keychain access.  It is the contract pin for the bake
script's deterministic, build-time outputs.
"""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Importing here (after sys.path adjustment) so the bake-script module
# resolves alongside the rest of the AdoptIQ tree.
from scripts import bake_corpus as bake_module  # noqa: E402


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


_SAMPLE_CSV = (
    # Schema must satisfy ``corpus_indexer._parse_csv``: at minimum a
    # ``customer_name`` column so the parser yields ParsedRecord rows.
    "customer_name,technology,case_number,severity_norm,case_status_norm,Title\n"
    "Acme Corp,Routing,12345,Sev3,Open,Routing flap on edge\n"
    "Wile Coyote,Wireless,12346,Sev2,Open,AP onboarding stuck in DHCP\n"
    "Roadrunner LLC,Security,12347,Sev1,Closed,Tunnel re-key failure\n"
)


def _seed_offline_fixture(dir_: Path) -> None:
    """Drop a single small CSV into ``dir_`` so the indexer has
    something parseable to ingest.  CSV is the cheapest format the
    indexer accepts -- no openpyxl / docx2txt required."""
    dir_.mkdir(parents=True, exist_ok=True)
    fixture = dir_ / "tac_demo.csv"
    fixture.write_text(_SAMPLE_CSV, encoding="utf-8")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_bake_offline_fixture_writes_four_artifacts(tmp_path, monkeypatch):
    bake_dir = tmp_path / "bake"
    fixture_dir = tmp_path / "fixture"
    _seed_offline_fixture(fixture_dir)

    # Ensure the env flag does not force skip-mode.
    monkeypatch.delenv("ADOPTIQ_BAKE_CORPUS", raising=False)

    rc = bake_module.main([
        "--bake-dir", str(bake_dir),
        "--auth-mode", "offline_fixture",
        "--offline-fixture", str(fixture_dir),
    ])
    assert rc == 0, f"bake_corpus.main returned non-zero exit: {rc}"

    # All four ship-ready artifacts must exist after the bake.
    # ``corpus.db.salt`` is the salt filename ``corpus_crypto`` mints
    # via ``_salt_path_for(<encrypted>.db.enc).with_suffix('.salt')``.
    for fname in (
        "corpus.db.enc",
        "sentinel.json",
        "corpus.db.salt",
        "corpus.sentinel.lock.json",
    ):
        assert (bake_dir / fname).exists(), f"missing baked artifact {fname}"


def test_bake_offline_fixture_artifacts_are_0600(tmp_path):
    bake_dir = tmp_path / "bake"
    fixture_dir = tmp_path / "fixture"
    _seed_offline_fixture(fixture_dir)

    rc = bake_module.main([
        "--bake-dir", str(bake_dir),
        "--auth-mode", "offline_fixture",
        "--offline-fixture", str(fixture_dir),
    ])
    assert rc == 0

    # Bake dir 0o700; artifacts 0o600.
    bake_mode = stat.S_IMODE(bake_dir.stat().st_mode)
    assert bake_mode == 0o700, f"bake_dir mode {oct(bake_mode)} != 0o700"
    for fname in (
        "corpus.db.enc",
        "sentinel.json",
        "corpus.db.salt",
        "corpus.sentinel.lock.json",
    ):
        path = bake_dir / fname
        mode = stat.S_IMODE(path.stat().st_mode)
        assert mode == 0o600, (
            f"{fname} mode {oct(mode)} != 0o600 -- a multi-user "
            "host would otherwise leak the encrypted DB or "
            "sentinel material to other accounts."
        )


def test_bake_offline_fixture_corpus_can_be_reopened(tmp_path):
    """End-to-end: bake → open via corpus_crypto → assert SQLite
    connection is usable.  Catches any drift between the bake-time
    sentinel/salt/lock minting and the runtime open path."""
    bake_dir = tmp_path / "bake"
    fixture_dir = tmp_path / "fixture"
    _seed_offline_fixture(fixture_dir)

    rc = bake_module.main([
        "--bake-dir", str(bake_dir),
        "--auth-mode", "offline_fixture",
        "--offline-fixture", str(fixture_dir),
    ])
    assert rc == 0

    from corpus_crypto import open_corpus_for_user

    handle = open_corpus_for_user(
        onedrive_root=None,
        encrypted_path=bake_dir / "corpus.db.enc",
        create_if_missing=False,
        sharepoint_root=None,
        allow_local_sentinel=True,
    )
    try:
        # The indexer ran during bake; we should have at least one
        # row in the customers table (3 fixture rows -> 3 customers).
        cur = handle.conn.execute(
            "SELECT COUNT(*) FROM customers"
        )
        count = int(cur.fetchone()[0])
        assert count >= 1, (
            f"baked corpus had {count} customers; expected >=1 from "
            "the offline fixture CSV"
        )
    finally:
        handle.close(persist=False)


def test_bake_no_bake_flag_emits_skip_marker(tmp_path, monkeypatch):
    bake_dir = tmp_path / "bake"
    monkeypatch.delenv("ADOPTIQ_BAKE_CORPUS", raising=False)

    rc = bake_module.main([
        "--bake-dir", str(bake_dir),
        "--no-bake",
    ])
    assert rc == 0
    marker = bake_dir / ".bake-skipped"
    assert marker.exists(), "--no-bake must emit the skip marker"
    # No real artifacts should be present.
    for fname in (
        "corpus.db.enc",
        "sentinel.json",
        "corpus.db.salt",
        "corpus.sentinel.lock.json",
    ):
        assert not (bake_dir / fname).exists(), (
            f"skip-mode left a stale {fname} -- PyInstaller would "
            "ship a phantom corpus."
        )


def test_bake_env_flag_zero_emits_skip_marker(tmp_path, monkeypatch):
    bake_dir = tmp_path / "bake"
    monkeypatch.setenv("ADOPTIQ_BAKE_CORPUS", "0")

    rc = bake_module.main([
        "--bake-dir", str(bake_dir),
    ])
    assert rc == 0
    marker = bake_dir / ".bake-skipped"
    assert marker.exists()
    marker_mode = stat.S_IMODE(marker.stat().st_mode)
    assert marker_mode == 0o600, (
        f"skip marker mode {oct(marker_mode)} != 0o600"
    )


def test_bake_skip_removes_stale_artifacts(tmp_path, monkeypatch):
    """If a previous successful bake left artifacts and a subsequent
    run is skipped, the stale artifacts MUST be removed -- otherwise
    PyInstaller would ship a stale corpus from a previous bake."""
    bake_dir = tmp_path / "bake"
    fixture_dir = tmp_path / "fixture"
    _seed_offline_fixture(fixture_dir)

    # First, do a real bake.
    monkeypatch.delenv("ADOPTIQ_BAKE_CORPUS", raising=False)
    rc = bake_module.main([
        "--bake-dir", str(bake_dir),
        "--auth-mode", "offline_fixture",
        "--offline-fixture", str(fixture_dir),
    ])
    assert rc == 0
    assert (bake_dir / "corpus.db.enc").exists()

    # Then skip with the env flag.
    monkeypatch.setenv("ADOPTIQ_BAKE_CORPUS", "0")
    rc = bake_module.main(["--bake-dir", str(bake_dir)])
    assert rc == 0
    for fname in (
        "corpus.db.enc",
        "sentinel.json",
        "corpus.db.salt",
        "corpus.sentinel.lock.json",
    ):
        assert not (bake_dir / fname).exists(), (
            f"{fname} should have been cleaned up on skip"
        )


def test_bake_offline_fixture_requires_directory(tmp_path):
    bake_dir = tmp_path / "bake"
    rc = bake_module.main([
        "--bake-dir", str(bake_dir),
        "--auth-mode", "offline_fixture",
        # Note: no --offline-fixture argument
    ])
    assert rc == 1, f"missing --offline-fixture should exit 1, got {rc}"


def test_bake_offline_fixture_empty_dir_fails(tmp_path):
    bake_dir = tmp_path / "bake"
    fixture_dir = tmp_path / "empty_fixture"
    fixture_dir.mkdir(parents=True, exist_ok=True)

    rc = bake_module.main([
        "--bake-dir", str(bake_dir),
        "--auth-mode", "offline_fixture",
        "--offline-fixture", str(fixture_dir),
    ])
    # Empty fixture dir → no files staged → exit 1 (pre-index error).
    assert rc == 1, f"empty offline fixture should exit 1, got {rc}"


def test_bake_rejects_non_sharepoint_share_url(tmp_path, monkeypatch):
    """The bake script enforces ``_encode_share_url_for_graph``'s
    allow-list before opening any network connection, so an env-
    override that points at a non-sharepoint host is refused early
    with exit 1."""
    bake_dir = tmp_path / "bake"
    monkeypatch.delenv("ADOPTIQ_BAKE_CORPUS", raising=False)
    rc = bake_module.main([
        "--bake-dir", str(bake_dir),
        "--share-url", "https://attacker.example.com/x",
        "--auth-mode", "device_code",
    ])
    assert rc == 1, (
        f"non-sharepoint share URL should be rejected with exit 1, got {rc}"
    )
