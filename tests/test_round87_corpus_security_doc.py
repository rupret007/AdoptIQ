"""Round 87 / Phase 2 — operator-facing corpus security note.

Pre-R87 the README claimed "encrypted at rest" but had no subsection
explaining the runtime ephemeral plaintext temp file at
``$TMPDIR/adoptiq_corpus/`` that ``EncryptedCorpusHandle`` must
maintain to back the SQLite connection.  Stakeholders reading "encrypted
at rest" reasonably (but incorrectly) inferred "never plaintext on
disk."

R87 / Phase 2 adds a "Corpus security model" subsection inside the
existing CSOne Knowledge Corpus section that documents:

  * AT-REST contract -- AES-256-GCM, 0o600, and the Build 76 bundled
    corpus + local sentinel trade-off.
  * RUNTIME EPHEMERAL contract -- the plaintext temp file under
    ``$TMPDIR/adoptiq_corpus/`` (mode 0o600, parent 0o700), why
    SQLite needs it, the best-effort scrub on close, and the
    ``atexit`` registration so a clean shutdown fires the scrub.
  * STAKEHOLDER CLARIFICATION -- "encrypted at rest" is true,
    "never plaintext on disk" is NOT, the SharePoint URL is not
    the secret.

These pins guard against a future docs edit silently dropping the
runtime-plaintext discussion.  Source-shape only.

Pinned by these tests:
  * ``test_readme_corpus_security_section_anchor`` -- the
    ``#### Corpus security model`` heading exists.
  * ``test_readme_documents_at_rest_encryption`` -- AES-256-GCM,
    ``corpus.db.enc``, ``corpus.db.salt`` named.
  * ``test_readme_documents_runtime_plaintext_temp_file`` --
    ``$TMPDIR/adoptiq_corpus/``, ``0o600``, parent ``0o700`` named.
  * ``test_readme_documents_temp_file_lifecycle`` -- ephemeral,
    scrubbed, ``atexit`` named.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
README = REPO_ROOT / "README.md"


def _read_readme() -> str:
    assert README.exists(), f"missing README: {README}"
    return README.read_text(encoding="utf-8")


def test_readme_corpus_security_section_anchor() -> None:
    """``#### Corpus security model`` heading is present.

    Without this anchor a future docs edit reorganising the README
    could silently strip the new subsection (the at-rest /
    ephemeral / stakeholder material would scatter into other
    sections and lose the consolidated audit trail).  This test
    asserts the canonical anchor lives in the file.
    """
    body = _read_readme()
    assert "#### Corpus security model" in body, (
        "missing '#### Corpus security model' heading; required by "
        "Round 87 / Phase 2 contract so stakeholders can find the "
        "at-rest vs runtime-plaintext discussion in the README"
    )


def test_readme_documents_at_rest_encryption() -> None:
    """At-rest encryption contract is named precisely.

    These four phrases are the SSoT for the at-rest contract; they
    mirror the implementation pins in ``corpus_crypto.py`` so a
    future code change that diverges from the contract surfaces
    here.
    """
    body = _read_readme()

    # AES-256-GCM is the actual encryption algorithm
    # (corpus_crypto.encrypt_bytes uses AES-GCM with a 32-byte key).
    assert "AES-256-GCM" in body, (
        "missing 'AES-256-GCM' in README; required by Round 87 / Phase 2 "
        "to name the at-rest cipher"
    )

    # The two encrypted artifacts shipped with the DMG.
    assert "corpus.db.enc" in body, "missing 'corpus.db.enc' citation in README"
    assert "corpus.db.salt" in body, "missing 'corpus.db.salt' citation in README"

    # Round 107 / Build 76 intentionally ships the corpus plus local sentinel.
    assert "Treat the DMG as containing the corpus dataset" in body, (
        "missing Build 76 bundled-corpus trade-off framing in README; "
        "stakeholders must understand the installer now contains corpus data"
    )


def test_readme_documents_runtime_plaintext_temp_file() -> None:
    """Runtime ephemeral plaintext contract is named precisely.

    These three phrases are the SSoT for the runtime contract; they
    mirror the implementation pins in
    ``corpus_crypto.open_encrypted_corpus`` so a future code change
    that diverges from the contract surfaces here.
    """
    body = _read_readme()

    # The path the operator may notice in their temp dir.
    assert "$TMPDIR/adoptiq_corpus/" in body, (
        "missing '$TMPDIR/adoptiq_corpus/' citation in README; required "
        "so operators who inspect their temp dir can identify the file "
        "and confirm it matches the documented contract"
    )

    # Mode for the file itself (0o600).
    assert "0o600" in body, (
        "missing '0o600' mode citation in README; required so a "
        "stakeholder reading the doc can confirm the temp file is "
        "single-user-readable"
    )

    # Parent directory mode (0o700).
    assert "0o700" in body, (
        "missing '0o700' parent-dir mode citation in README"
    )


def test_readme_documents_temp_file_lifecycle() -> None:
    """Lifecycle contract: ephemeral + scrubbed + atexit-registered.

    Without these three phrases a stakeholder could reasonably
    infer the temp file is durable.  Pinning them here ensures the
    docs faithfully describe ``EncryptedCorpusHandle.close``'s
    best-effort scrub + unlink path AND the
    ``app_simple._r17_corpus_shutdown`` atexit registration.
    """
    body = _read_readme()

    # Either word communicates the transient nature.  Use a strict
    # match because "ephemeral" is the contract term used in the
    # implementation comments.
    assert "ephemeral" in body.lower(), (
        "missing 'ephemeral' framing for the runtime plaintext file "
        "in README; required by Round 87 / Phase 2 contract"
    )

    # The close-time scrub is a documented contract, not just an
    # implementation detail.
    assert "scrub" in body.lower(), (
        "missing 'scrub' framing for the runtime plaintext file "
        "in README; required by Round 87 / Phase 2 contract"
    )

    # The atexit registration ties scrub to clean shutdown.
    assert "atexit" in body, (
        "missing 'atexit' citation in README; required by Round 87 / "
        "Phase 2 contract so the docs are honest that the scrub fires "
        "on clean shutdown but NOT on SIGKILL / hard crash"
    )

    # The honest framing about what AdoptIQ can / cannot claim.
    # Case-insensitive so editors can capitalise the quote at sentence
    # start; both straight and curly quote variants accepted because
    # markdown editors / formatters often auto-substitute.
    body_lower = body.lower()
    assert (
        '"never plaintext on disk"' in body_lower
        or '“never plaintext on disk”' in body_lower
    ), (
        "missing 'never plaintext on disk' honest disclaimer in README; "
        "required by Round 87 / Phase 2 contract -- stakeholders need "
        "to know AdoptIQ cannot make that claim because SQLite cannot "
        "operate without a file descriptor"
    )
