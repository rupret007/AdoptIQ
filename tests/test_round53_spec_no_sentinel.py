"""Round 53 / Phase 53.2: pin the PyInstaller spec contract that
the .app bundle SHIPS only the encrypted DB + HKDF salt (not the
sentinel material that would key the AES decryption).

Background
----------
QUALITY_AUDIT.md Round 52.2 documented a HIGH-severity offline
decryption flaw in the Build30 DMG: the .app bundle's
``Resources/baked_corpus/`` carried four files (``corpus.db.enc``,
``sentinel.json``, ``corpus.db.salt``, ``corpus.sentinel.lock.json``).
Anyone who obtained the DMG could derive the AES key from the
sentinel + salt and decrypt the corpus offline -- the Cisco
OneDrive ACL was effectively bypassed.

Round 53 / Phase 53.2 closes that gap by shrinking the spec's
bundle list from 4 entries to 2:

* ``corpus.db.enc`` -- encrypted SQLite snapshot.
* ``corpus.db.salt`` -- HKDF salt (NOT a secret -- only the
  sentinel is the AES-key input).

The runtime resolves the sentinel + lock against the user's own
OneDrive sync of ``AI Projects/AdoptIQ_CSOne_Reports`` (only
Cisco-signed-in users can sync that folder).

These tests pin the spec by reading its source -- we don't
actually run PyInstaller (it would take 5+ minutes per test and
require a clean Python environment).  Source-text inspection is
sufficient because the spec is a tiny, declarative file.
"""

from __future__ import annotations

from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parent.parent
_SPEC_PATH = _REPO_ROOT / "adoptiq_mac.spec"


def test_spec_file_exists():
    """Round 53 / Phase 53.2: the macOS spec must exist; this test
    is the safety net for accidental deletion in a future refactor."""
    assert _SPEC_PATH.exists(), (
        f"adoptiq_mac.spec missing at {_SPEC_PATH}; the macOS build "
        "pipeline depends on it."
    )


def test_spec_does_not_bundle_sentinel_json():
    """The single most important Round 53 contract: the .app bundle
    MUST NOT ship ``sentinel.json``.  If this assertion ever flips
    we have re-introduced the QUALITY_AUDIT.md Round 52.2 HIGH
    severity offline-decryption regression."""
    src = _SPEC_PATH.read_text(encoding="utf-8")
    # The spec must not list ``sentinel.json`` inside the
    # _datas() loop (look for the literal string in the bundle
    # tuple and as a quoted token).
    for forbidden in ("'sentinel.json'", '"sentinel.json"'):
        assert forbidden not in src, (
            f"adoptiq_mac.spec contains {forbidden} -- this would "
            "ship the AES-key material inside the .app bundle and "
            "make the encrypted corpus offline-decryptable.  This "
            "is the canonical Round 53 regression -- DO NOT MERGE."
        )


def test_spec_does_not_bundle_corpus_sentinel_lock_json():
    """Companion to the sentinel test -- the digest-pin sidecar
    must also stay out of the bundle.  Without the lock the runtime
    re-mints one against the user's OneDrive sentinel, which is the
    correct behavior."""
    src = _SPEC_PATH.read_text(encoding="utf-8")
    for forbidden in (
        "'corpus.sentinel.lock.json'",
        '"corpus.sentinel.lock.json"',
    ):
        assert forbidden not in src, (
            f"adoptiq_mac.spec contains {forbidden} -- the runtime "
            "must mint its own lock against the user's OneDrive "
            "sentinel; pinning it at bake time bricks every install "
            "if the canonical sentinel is ever rotated."
        )


def test_spec_bundles_corpus_db_enc():
    """The encrypted snapshot itself MUST be bundled -- without it
    the bake serves no purpose."""
    src = _SPEC_PATH.read_text(encoding="utf-8")
    assert (
        "'corpus.db.enc'" in src or '"corpus.db.enc"' in src
    ), (
        "adoptiq_mac.spec must bundle corpus.db.enc inside "
        "Resources/baked_corpus/; without it the runtime falls back "
        "to fresh-mint on every install."
    )


def test_spec_bundles_corpus_db_salt():
    """The HKDF salt MUST be bundled (it is NOT a secret -- only the
    sentinel is the AES-key input).  The runtime needs the same salt
    that the bake sealed under, otherwise the decrypt fails with
    InvalidTag."""
    src = _SPEC_PATH.read_text(encoding="utf-8")
    assert (
        "'corpus.db.salt'" in src or '"corpus.db.salt"' in src
    ), (
        "adoptiq_mac.spec must bundle corpus.db.salt; the runtime "
        "needs the bake-time salt to derive the same AES key."
    )


def test_spec_baked_corpus_loop_is_two_entries_only():
    """Stricter contract: the for-loop that enumerates baked artifacts
    must contain EXACTLY 2 entries.  This catches a future regression
    that re-adds sentinel/lock to the loop even if someone removes
    the literal strings elsewhere in the file."""
    src = _SPEC_PATH.read_text(encoding="utf-8")
    # Find the bundle loop -- it iterates a tuple of filenames.
    # Round 53 contract: the tuple between ``for fname in (`` and
    # the closing ``):`` contains 2 quoted entries.
    marker_open = "for fname in ("
    idx = src.find(marker_open)
    assert idx >= 0, (
        "adoptiq_mac.spec lost its baked-corpus enumeration loop"
    )
    after_open = src[idx + len(marker_open):]
    close_idx = after_open.find("):")
    assert close_idx >= 0, (
        "adoptiq_mac.spec baked-corpus loop is malformed (no closing ')')"
    )
    body = after_open[:close_idx]
    # Count the quoted filenames.  Allow either single or double quotes.
    entry_count = (
        body.count("'corpus.db.enc'")
        + body.count('"corpus.db.enc"')
        + body.count("'corpus.db.salt'")
        + body.count('"corpus.db.salt"')
        + body.count("'sentinel.json'")
        + body.count('"sentinel.json"')
        + body.count("'corpus.sentinel.lock.json'")
        + body.count('"corpus.sentinel.lock.json"')
    )
    assert entry_count == 2, (
        f"Round 53 contract violated: baked-corpus loop has "
        f"{entry_count} entries (expected exactly 2 -- "
        f"corpus.db.enc + corpus.db.salt only).  The legacy 4-tuple "
        f"is the canonical offline-decryption regression."
    )


def test_spec_documents_round_53_security_posture():
    """Documentation contract: the spec must carry a Round 53 marker
    in its comments so future readers understand WHY the bundle was
    shrunk.  Without this comment, a well-meaning future refactor
    might re-add the sentinel without realizing the security
    implications."""
    src = _SPEC_PATH.read_text(encoding="utf-8")
    assert "Round 53" in src, (
        "adoptiq_mac.spec must mention Round 53 in its comments so "
        "future readers know WHY the baked-corpus bundle was shrunk "
        "from 4 to 2 files (and don't accidentally re-add the "
        "sentinel)."
    )
