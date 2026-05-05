"""Round 87 / Phase 1 — release-time bake gate (source-shape pin).

Pre-R87 ``build_mac_dmg.sh`` accepted ``ADOPTIQ_BAKE_CORPUS=0`` /
``--no-bake`` and only logged a warning when the four bake artifacts
were absent.  A future operator could ship a DMG without the baked
corpus and not notice until users hit ``blocked_no_onedrive`` on first
launch.

R87 / Phase 1 adds an opt-in ``ADOPTIQ_RELEASE_GATE=1`` block that
hard-fails the build when:

  * ``bake/.bake-skipped`` exists (skip-mode forbidden on shipping
    builds), OR
  * either ``bake/corpus.db.enc`` or ``bake/corpus.db.salt`` is missing.

The gate is opt-in (default-off) so dev iteration (``ADOPTIQ_BAKE_CORPUS=0``
or arbitrary ``--no-bake``) still works exactly as before.  CI / shipping
pipelines opt in by exporting ``ADOPTIQ_RELEASE_GATE=1`` BEFORE
invoking the script.

These are source-shape pins ONLY — the script is not executed.  We
assert the bash block is present, references the canonical artifact
filenames pinned by ``corpus_crypto._salt_path_for``, defaults
permissively, and is anchored at a Round 87 marker.

Pinned by these tests:
  * ``test_release_gate_block_present`` — the ``ADOPTIQ_RELEASE_GATE``
    guard exists at the documented location.
  * ``test_release_gate_default_permissive`` — ``${VAR:-0}`` form
    keeps dev iteration unaffected.
  * ``test_release_gate_round_marker_present`` — the
    ``# Round 87 / Phase 1`` marker is on the new lines so
    ``git diff build_mac_dmg.sh | grep 'Round 87'`` shows the
    per-file footprint.
  * ``test_bake_corpus_script_still_emits_skipped_marker`` — keeps
    the SSoT side ('.bake-skipped' marker) intact so the gate has a
    reliable signal to consult.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BUILD_SCRIPT = REPO_ROOT / "build_mac_dmg.sh"
BAKE_SCRIPT = REPO_ROOT / "scripts" / "bake_corpus.py"


def _read_build_script() -> str:
    assert BUILD_SCRIPT.exists(), f"missing build script: {BUILD_SCRIPT}"
    return BUILD_SCRIPT.read_text(encoding="utf-8")


def _read_bake_script() -> str:
    assert BAKE_SCRIPT.exists(), f"missing bake script: {BAKE_SCRIPT}"
    return BAKE_SCRIPT.read_text(encoding="utf-8")


def test_release_gate_block_present() -> None:
    """The opt-in release gate block is present in build_mac_dmg.sh.

    Two failure modes must be detectable:
      * .bake-skipped marker -> exit 1
      * corpus.db.enc OR corpus.db.salt missing -> exit 1
    """
    body = _read_build_script()

    # Gate guard with default-permissive form.
    assert 'ADOPTIQ_RELEASE_GATE:-0' in body, (
        "missing ADOPTIQ_RELEASE_GATE=1 guard with permissive default"
    )

    # Marker check (SSoT side: scripts/bake_corpus.py:_emit_skip_marker).
    assert 'bake/.bake-skipped' in body, (
        "release gate must consult the bake/.bake-skipped marker"
    )

    # Artifact existence checks (canonical filenames pinned by
    # corpus_crypto._salt_path_for + the spec file).
    assert 'bake/corpus.db.enc' in body, (
        "release gate must verify bake/corpus.db.enc is present"
    )
    assert 'bake/corpus.db.salt' in body, (
        "release gate must verify bake/corpus.db.salt is present"
    )

    # The gate must abort the build, not just warn.  Two distinct
    # exit-1 sites: one for skip-mode, one for missing artifacts.
    gate_exits = body.count(
        'ADOPTIQ_RELEASE_GATE=1 but'
    )
    assert gate_exits >= 2, (
        f"expected >=2 'ADOPTIQ_RELEASE_GATE=1 but' error blocks, found {gate_exits}"
    )


def test_release_gate_default_permissive() -> None:
    """Default-off: dev iteration with BAKE=0 must keep working.

    The shell parameter expansion ``${ADOPTIQ_RELEASE_GATE:-0}``
    yields ``"0"`` when the var is unset OR empty, so unset env =
    permissive path.  This pins the precise expansion form.
    """
    body = _read_build_script()
    # Single-line check: the comparison literal is the canonical "1".
    assert '"${ADOPTIQ_RELEASE_GATE:-0}" == "1"' in body, (
        "release gate must compare ${ADOPTIQ_RELEASE_GATE:-0} against \"1\" "
        "exactly so unset/empty preserves the dev-iteration path"
    )


def test_release_gate_round_marker_present() -> None:
    """``# Round 87`` markers anchor the new bash lines for git-diff."""
    body = _read_build_script()
    assert '# Round 87' in body, (
        "missing Round 87 marker on the release-gate block; required by "
        "the per-file footprint convention so `git diff build_mac_dmg.sh "
        "| grep 'Round 87'` shows the round's bash footprint"
    )
    # The Phase 1 anchor specifically (not just any Round 87 marker).
    assert 'Round 87 / Phase 1' in body, (
        "missing 'Round 87 / Phase 1' anchor on the release-gate block"
    )


def test_bake_corpus_script_still_emits_skipped_marker() -> None:
    """SSoT defense: the gate consults bake/.bake-skipped.

    If a future round were to rename or remove the
    ``_emit_skip_marker`` writer in ``scripts/bake_corpus.py``, the
    release gate's first check would silently never fire and a
    skip-mode bake would slip through to a shipping DMG.  This test
    pins the SSoT side so the gate has a reliable signal.
    """
    body = _read_bake_script()
    assert '_emit_skip_marker' in body, (
        "scripts/bake_corpus.py must define _emit_skip_marker -- "
        "it is the SSoT writer for bake/.bake-skipped that the R87 "
        "release gate consults"
    )
    assert '.bake-skipped' in body, (
        "scripts/bake_corpus.py must reference the literal '.bake-skipped' "
        "filename so the marker matches the gate check"
    )


def test_real_bake_clears_stale_bake_skipped_marker() -> None:
    """Round 87 / Phase 1 (corollary): a real bake MUST clear any
    prior ``.bake-skipped`` marker.

    Pre-R87 corollary the bake script's wipe-list at the top of
    ``_index_into_encrypted_corpus`` cleared the four crypto
    artifacts but NOT ``.bake-skipped`` -- so a dev iteration that
    ran ``ADOPTIQ_BAKE_CORPUS=0`` once would leave the marker on
    disk indefinitely, and the next bake-with-gate-active build
    would trip the gate even though the just-completed bake had
    produced fresh artifacts.

    The fix is in ``_index_into_encrypted_corpus`` (the real-bake
    path): the wipe list now includes ``bake/.bake-skipped`` so a
    successful bake supersedes any prior skip marker.  The
    ``_emit_skip_marker`` writer is unchanged -- it still wipes the
    four crypto artifacts on the skip path so the spec file's
    ``Path.exists()`` check tolerates the absence of the four
    artifacts.

    Pinned here as a source-shape assertion: ``.bake-skipped`` must
    appear in the wipe list inside the real-bake function.
    """
    body = _read_bake_script()
    # The real-bake wipe list is bracketed by a ``for stale in (`` ...
    # ``):`` block immediately above the ``handle = open_corpus_for_user``
    # call.  We pin the literal ``.bake-skipped`` substring inside the
    # wipe list (a separate occurrence from the ``_emit_skip_marker``
    # writer block, which is already pinned by the test above).
    skip_marker_count = body.count('".bake-skipped"')
    assert skip_marker_count >= 2, (
        f"expected >=2 occurrences of \".bake-skipped\" in scripts/"
        f"bake_corpus.py (the _emit_skip_marker writer + the "
        f"_index_into_encrypted_corpus wipe list); found "
        f"{skip_marker_count}.  A real bake must clear any prior "
        f"skip marker so the R87 release gate doesn't trip on stale "
        f"state after a successful bake."
    )
