"""Round 35 + Round 36 + Round 53 / native-corpus: smoke-test
``scripts/bake_corpus.py``.

Drives the bake script in local-source mode (no network, no MSAL --
the legacy device-code path was removed in Round 36) so the test
suite can verify, end-to-end, that:

* The two ship-ready artifacts get written under ``--bake-dir``:
  ``corpus.db.enc`` and ``corpus.db.salt`` (Round 53 / Phase 53.1
  shrunk the bundle from 4 -> 2; the sentinel + lock are now
  resolved at runtime against the user's OneDrive sync of
  ``AI Projects/AdoptIQ_CSOne_Reports``).
* The bake STRICTLY refuses to run when the OneDrive sentinel is
  missing -- exit code 3.  The pre-Round-53 auto-mint-local
  sentinel fallback is gone; an absent sentinel is the contract
  for "this build operator has not signed in to OneDrive yet."
* The ``corpus.sentinel.lock.json`` and ``sentinel.json`` sidecars
  are NEVER present in the bake dir after a successful bake,
  because pre-Round-53 they were the offline-decryption foothold
  documented in QUALITY_AUDIT.md Round 52.2.
* Each artifact is mode 0600 and the bake-dir is mode 0700, so a
  multi-user macOS host cannot read another account's encrypted DB.
* A subsequent ``open_corpus_for_user`` against the bake-dir +
  the same OneDrive sentinel root SUCCEEDS (positive self-test),
  while the same call with ``onedrive_root=None`` +
  ``allow_local_sentinel=False`` FAILS (negative self-test --
  proves the bake is NOT offline-decryptable).
* ``--no-bake`` and ``ADOPTIQ_BAKE_CORPUS=0`` short-circuit and
  emit the ``.bake-skipped`` marker, leaving no stale artifacts.

Round 36: the ``--auth-mode``, ``--share-url``, and
``--device-code-timeout-s`` flags are still accepted for back-compat
with the build harness but are no-ops; the corresponding
"--auth-mode=device_code" tests have been removed because that path
no longer exists.

The test never imports MSAL, never hits Microsoft Graph, never
requires keychain access.  It is the contract pin for the bake
script's deterministic, build-time outputs and the Round 53
fail-closed posture.
"""

from __future__ import annotations

import secrets
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


@pytest.fixture(autouse=True)
def _stub_optional_model_bake_checks(monkeypatch):
    """Keep the Round 35 bake smoke focused on corpus I/O.

    Round 66/95 added dense-vector and reranker self-tests that require
    local model dependencies. Those paths have their own source-shape
    tests; this smoke uses deterministic stubs so ``make verify`` does
    not depend on a developer workstation having the model cache loaded.
    """
    monkeypatch.setattr(
        bake_module,
        "_bake_chunk_vectors",
        lambda conn: (1, "test-model", 384),
    )
    monkeypatch.setattr(
        bake_module,
        "_bake_reranker_self_test",
        lambda: (True, "stubbed"),
    )


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


def _seed_onedrive_sentinel(dir_: Path) -> Path:
    """Round 53 / Phase 53.1: drop a 32-byte canonical sentinel into
    ``dir_`` so the bake's pre-flight check passes.  Returns the
    sentinel path so tests can assert on it.  Uses the same name +
    byte length as ``scripts/mint_corpus_sentinel.py``."""
    from corpus_crypto import DEFAULT_SENTINEL_NAME
    dir_.mkdir(parents=True, exist_ok=True)
    sentinel_path = dir_ / DEFAULT_SENTINEL_NAME
    sentinel_path.write_bytes(secrets.token_bytes(32))
    return sentinel_path


# ---------------------------------------------------------------------------
# Tests -- happy path
# ---------------------------------------------------------------------------


def test_bake_local_source_writes_two_artifacts(tmp_path, monkeypatch):
    """Round 53 / Phase 53.1: the bake now produces TWO artifacts,
    not four.  ``sentinel.json`` and ``corpus.sentinel.lock.json``
    MUST NOT exist in the bake dir after a successful bake."""
    bake_dir = tmp_path / "bake"
    fixture_dir = tmp_path / "fixture"
    onedrive_dir = tmp_path / "onedrive"
    _seed_offline_fixture(fixture_dir)
    _seed_onedrive_sentinel(onedrive_dir)

    monkeypatch.delenv("ADOPTIQ_BAKE_CORPUS", raising=False)

    rc = bake_module.main([
        "--bake-dir", str(bake_dir),
        "--source", str(fixture_dir),
        "--onedrive-sentinel-root", str(onedrive_dir),
    ])
    assert rc == 0, f"bake_corpus.main returned non-zero exit: {rc}"

    # Round 53: the TWO ship-ready artifacts must exist.
    for fname in ("corpus.db.enc", "corpus.db.salt"):
        assert (bake_dir / fname).exists(), (
            f"missing baked artifact {fname}"
        )

    # Round 53 contract: the sidecars MUST NOT be present in the
    # bake dir after commit -- they're scrubbed by
    # ``_index_into_encrypted_corpus`` precisely so the PyInstaller
    # spec cannot bundle them by accident.
    for legacy in ("sentinel.json", "corpus.sentinel.lock.json"):
        assert not (bake_dir / legacy).exists(), (
            f"Round 53 contract violated: {legacy} present in bake "
            f"dir after commit -- this is the offline-decryption "
            f"foothold documented in QUALITY_AUDIT.md Round 52.2."
        )


def test_bake_local_source_artifacts_are_0600(tmp_path):
    bake_dir = tmp_path / "bake"
    fixture_dir = tmp_path / "fixture"
    onedrive_dir = tmp_path / "onedrive"
    _seed_offline_fixture(fixture_dir)
    _seed_onedrive_sentinel(onedrive_dir)

    rc = bake_module.main([
        "--bake-dir", str(bake_dir),
        "--source", str(fixture_dir),
        "--onedrive-sentinel-root", str(onedrive_dir),
    ])
    assert rc == 0

    # Bake dir 0o700; artifacts 0o600.
    bake_mode = stat.S_IMODE(bake_dir.stat().st_mode)
    assert bake_mode == 0o700, f"bake_dir mode {oct(bake_mode)} != 0o700"
    for fname in ("corpus.db.enc", "corpus.db.salt"):
        path = bake_dir / fname
        mode = stat.S_IMODE(path.stat().st_mode)
        assert mode == 0o600, (
            f"{fname} mode {oct(mode)} != 0o600 -- a multi-user "
            "host would otherwise leak the encrypted DB to other "
            "accounts."
        )


def test_bake_local_source_corpus_can_be_reopened(tmp_path):
    """End-to-end: bake -> open via corpus_crypto with the SAME
    OneDrive sentinel root + ``allow_local_sentinel=False`` -> assert
    SQLite connection is usable.  Catches any drift between the
    bake-time sealing and the runtime open path under the Round 53
    fail-closed contract."""
    bake_dir = tmp_path / "bake"
    fixture_dir = tmp_path / "fixture"
    onedrive_dir = tmp_path / "onedrive"
    _seed_offline_fixture(fixture_dir)
    _seed_onedrive_sentinel(onedrive_dir)

    rc = bake_module.main([
        "--bake-dir", str(bake_dir),
        "--source", str(fixture_dir),
        "--onedrive-sentinel-root", str(onedrive_dir),
    ])
    assert rc == 0

    from corpus_crypto import open_corpus_for_user

    handle = open_corpus_for_user(
        onedrive_root=onedrive_dir,
        encrypted_path=bake_dir / "corpus.db.enc",
        create_if_missing=False,
        allow_local_sentinel=False,
    )
    try:
        cur = handle.conn.execute("SELECT COUNT(*) FROM customers")
        count = int(cur.fetchone()[0])
        assert count >= 1, (
            f"baked corpus had {count} customers; expected >=1 from "
            "the offline fixture CSV"
        )
    finally:
        handle.close(persist=False)


# ---------------------------------------------------------------------------
# Tests -- Round 53 fail-closed contracts
# ---------------------------------------------------------------------------


def test_bake_fails_closed_when_onedrive_sentinel_missing(tmp_path, monkeypatch):
    """Round 53 / Phase 53.1: passing a OneDrive root that exists
    but does NOT contain the canonical sentinel must fail with
    exit code 3 -- the bake refuses to auto-mint a local sentinel
    because that's exactly the regression we're closing."""
    bake_dir = tmp_path / "bake"
    fixture_dir = tmp_path / "fixture"
    # Empty OneDrive dir -- exists, but no sentinel inside.
    onedrive_dir = tmp_path / "onedrive_empty"
    onedrive_dir.mkdir(parents=True, exist_ok=True)
    _seed_offline_fixture(fixture_dir)
    monkeypatch.delenv("ADOPTIQ_BAKE_CORPUS", raising=False)

    rc = bake_module.main([
        "--bake-dir", str(bake_dir),
        "--source", str(fixture_dir),
        "--onedrive-sentinel-root", str(onedrive_dir),
    ])
    assert rc == 3, (
        f"missing OneDrive sentinel must exit 3, got {rc}.  This is "
        "the Round 53 fail-closed gate -- if it stops returning 3 "
        "the auto-local-mint regression has reappeared."
    )

    # Defense: nothing should be left in the bake dir except (maybe)
    # the bake dir itself.  Specifically the two artifacts MUST NOT
    # be present.
    for fname in ("corpus.db.enc", "corpus.db.salt", "sentinel.json"):
        assert not (bake_dir / fname).exists(), (
            f"fail-closed bake left a stale {fname} in {bake_dir}"
        )


def test_bake_fails_closed_when_onedrive_root_missing(tmp_path, monkeypatch):
    """Round 53: passing a OneDrive root path that does not exist
    on disk also fails fast -- ``_resolve_onedrive_sentinel_root``
    treats a missing dir as 'no root configured'."""
    bake_dir = tmp_path / "bake"
    fixture_dir = tmp_path / "fixture"
    onedrive_missing = tmp_path / "no_such_dir"
    _seed_offline_fixture(fixture_dir)
    monkeypatch.delenv("ADOPTIQ_BAKE_CORPUS", raising=False)
    monkeypatch.delenv("ADOPTIQ_BAKE_SENTINEL_ROOT", raising=False)

    rc = bake_module.main([
        "--bake-dir", str(bake_dir),
        "--source", str(fixture_dir),
        "--onedrive-sentinel-root", str(onedrive_missing),
    ])
    # Exit 3 = OneDrive sentinel root missing or sentinel absent.
    assert rc == 3, (
        f"missing OneDrive root path must exit 3, got {rc}"
    )


# ---------------------------------------------------------------------------
# Tests -- skip mode
# ---------------------------------------------------------------------------


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
    # Round 53: the skip path scrubs even the legacy 4-tuple so a
    # dev iteration coming from a pre-Round-53 bake cannot leak
    # the sentinel into the .app even with --no-bake.
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
    PyInstaller would ship a stale corpus from a previous bake.

    Round 53: the cleanup list ALSO covers the legacy 4-tuple so a
    pre-Round-53 dev bake cannot leak its sentinel/lock into a
    skip-mode build."""
    bake_dir = tmp_path / "bake"
    fixture_dir = tmp_path / "fixture"
    onedrive_dir = tmp_path / "onedrive"
    _seed_offline_fixture(fixture_dir)
    _seed_onedrive_sentinel(onedrive_dir)

    monkeypatch.delenv("ADOPTIQ_BAKE_CORPUS", raising=False)
    rc = bake_module.main([
        "--bake-dir", str(bake_dir),
        "--source", str(fixture_dir),
        "--onedrive-sentinel-root", str(onedrive_dir),
    ])
    assert rc == 0
    assert (bake_dir / "corpus.db.enc").exists()

    # Drop fake legacy artifacts to ensure the skip path scrubs them
    # too -- mimicks a pre-Round-53 iteration that had bundled them.
    (bake_dir / "sentinel.json").write_bytes(b"legacy")
    (bake_dir / "corpus.sentinel.lock.json").write_bytes(b"legacy")

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


# ---------------------------------------------------------------------------
# Tests -- input validation
# ---------------------------------------------------------------------------


def test_bake_empty_source_dir_fails(tmp_path):
    bake_dir = tmp_path / "bake"
    fixture_dir = tmp_path / "empty_fixture"
    fixture_dir.mkdir(parents=True, exist_ok=True)
    onedrive_dir = tmp_path / "onedrive"
    _seed_onedrive_sentinel(onedrive_dir)

    rc = bake_module.main([
        "--bake-dir", str(bake_dir),
        "--source", str(fixture_dir),
        "--onedrive-sentinel-root", str(onedrive_dir),
    ])
    # Empty source dir -> no files staged -> exit 1 (pre-index error).
    assert rc == 1, f"empty source dir should exit 1, got {rc}"


def test_bake_back_compat_offline_fixture_alias(tmp_path, monkeypatch):
    """Round 36: ``--offline-fixture`` (the Round 35 flag name) must
    still be accepted as an alias for ``--source`` so existing build
    scripts keep working through the transition window."""
    bake_dir = tmp_path / "bake"
    fixture_dir = tmp_path / "fixture"
    onedrive_dir = tmp_path / "onedrive"
    _seed_offline_fixture(fixture_dir)
    _seed_onedrive_sentinel(onedrive_dir)
    monkeypatch.delenv("ADOPTIQ_BAKE_CORPUS", raising=False)

    rc = bake_module.main([
        "--bake-dir", str(bake_dir),
        "--auth-mode", "offline_fixture",  # accepted (no-op) for back-compat
        "--offline-fixture", str(fixture_dir),
        "--onedrive-sentinel-root", str(onedrive_dir),
    ])
    assert rc == 0, f"--offline-fixture alias must still work, got rc={rc}"
    assert (bake_dir / "corpus.db.enc").exists()


def test_bake_share_url_flag_is_no_op(tmp_path, monkeypatch):
    """Round 36: ``--share-url`` is accepted but ignored.  A bake
    invocation that *also* passes ``--share-url`` (with a valid
    source + OneDrive sentinel) must still succeed.  Either way,
    the flag must NOT cause a network attempt or a non-zero exit
    purely on its own."""
    bake_dir = tmp_path / "bake"
    fixture_dir = tmp_path / "fixture"
    onedrive_dir = tmp_path / "onedrive"
    _seed_offline_fixture(fixture_dir)
    _seed_onedrive_sentinel(onedrive_dir)
    monkeypatch.delenv("ADOPTIQ_BAKE_CORPUS", raising=False)

    rc = bake_module.main([
        "--bake-dir", str(bake_dir),
        "--source", str(fixture_dir),
        "--onedrive-sentinel-root", str(onedrive_dir),
        "--share-url", "https://attacker.example.com/x",  # ignored
    ])
    assert rc == 0, (
        f"--share-url is a deprecated no-op in Round 36; bake should "
        f"still succeed, got rc={rc}"
    )
