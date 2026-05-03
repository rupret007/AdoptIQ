"""Round 73 / Phase 1 (F1) -- make the R68/A1 build-label footer unkillable.

Build 46 acceptance audit found that 0/4 generated .docx artifacts
carried ``word/footer1.xml`` -- the entire R68/A1 stale-binary trap
detection mechanism was unreachable in the frozen build.  Root cause:
``_r68_build_label`` is loaded via lazy ``from _r68_build_label import
...`` calls inside try/except blocks at every Word writer site.
PyInstaller's static analyser does NOT follow lazy imports inside
function bodies, so the frozen build silently raised ``ModuleNotFoundError``
which was swallowed by the surrounding ``except Exception`` and logged
at ``debug`` -- below the default threshold.

This module pins the three-layer defense Round 73 ships:

1.  ``_r68_build_label`` is in the ``hidden_imports`` list of BOTH
    ``adoptiq_mac.spec`` and ``adoptiq_pc.spec``.
2.  ``app_simple.py`` carries a top-level ``import _r68_build_label``
    so PyInstaller's analyser also picks it up via the entry-point
    module.
3.  ``apply_word_footer`` itself is hardened to (a) explicitly clear
    ``is_linked_to_previous`` and ``different_first_page_header_footer``
    on each section, (b) prefer ``footer.add_paragraph()`` over reusing
    a runs-empty paragraph (some python-docx versions silently no-op
    ``.text = ""`` followed by ``.add_run(...)`` on linked-empty
    paragraphs), and (c) promote the outer swallow log from ``debug``
    to ``warning`` so the next regression is loud.

Per CLAUDE.md / quality-gate.mdc: every fixed bug ships with a
regression test.  These tests cover the source shape AND the helper's
runtime behaviour AND every call-site swallow log.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ---------------------------------------------------------------------------
# Layer 1: PyInstaller hidden_imports pin
# ---------------------------------------------------------------------------


def test_mac_spec_pins_r68_build_label_in_hidden_imports() -> None:
    """``adoptiq_mac.spec`` must list ``_r68_build_label`` in
    ``hidden_imports`` so PyInstaller bundles the module in the .app.
    """

    spec_text = (PROJECT_ROOT / "adoptiq_mac.spec").read_text(encoding="utf-8")
    assert "'_r68_build_label'" in spec_text, (
        "Round 73 / F1: adoptiq_mac.spec must pin _r68_build_label in "
        "hidden_imports so PyInstaller bundles it -- without the pin "
        "the frozen .app silently ships every report .docx without the "
        "v{VER} build {N} footer."
    )
    # Pin the contextual comment so a future edit that drops the pin
    # is forced to also drop the comment (bigger surface to notice).
    assert "Round 73 / Phase 1 (F1)" in spec_text, (
        "Round 73 / F1: spec comment block missing -- restore the "
        "rationale block alongside the pin."
    )


def test_pc_spec_pins_r68_build_label_in_hidden_imports() -> None:
    """Mirror of the mac-spec pin so the Windows build is equally
    protected."""

    spec_text = (PROJECT_ROOT / "adoptiq_pc.spec").read_text(encoding="utf-8")
    assert "'_r68_build_label'" in spec_text, (
        "Round 73 / F1: adoptiq_pc.spec must pin _r68_build_label in "
        "hidden_imports so the Windows build bundles it."
    )


# ---------------------------------------------------------------------------
# Layer 2: top-level import in app_simple.py
# ---------------------------------------------------------------------------


def test_app_simple_carries_top_level_r68_build_label_import() -> None:
    """``app_simple.py`` must carry a top-level ``import _r68_build_label``
    so PyInstaller's static analyser picks up the module via the
    entry-point regardless of whether the spec pin is dropped in a
    future edit (defense layer 2 of 3).
    """

    body = (PROJECT_ROOT / "app_simple.py").read_text(encoding="utf-8")
    # Match either ``import _r68_build_label`` (with or without alias)
    # at column 0 -- a top-level statement, not inside a function.
    pat = re.compile(
        r"^import _r68_build_label(?:\s+as\s+\w+)?(?:\s|$)",
        re.MULTILINE,
    )
    assert pat.search(body), (
        "Round 73 / F1: app_simple.py must carry a top-level "
        "``import _r68_build_label`` so PyInstaller bundles the "
        "module via entry-point analysis."
    )


# ---------------------------------------------------------------------------
# Layer 3: apply_word_footer hardening
# ---------------------------------------------------------------------------


def test_apply_word_footer_clears_is_linked_to_previous() -> None:
    """Source-shape pin: the helper must explicitly write
    ``section.footer.is_linked_to_previous = False`` so a multi-section
    document with one section linked-to-previous cannot hide the label
    by inheriting an unstamped footer.
    """

    body = (PROJECT_ROOT / "_r68_build_label.py").read_text(encoding="utf-8")
    assert "section.footer.is_linked_to_previous = False" in body, (
        "Round 73 / F1: _r68_build_label.apply_word_footer must "
        "explicitly clear is_linked_to_previous on each section."
    )


def test_apply_word_footer_clears_different_first_page_header_footer() -> None:
    """Source-shape pin: the helper must explicitly write
    ``section.different_first_page_header_footer = False`` so a document
    with a special first-page footer (the executive intelligence
    formatter sets one) does not silently route the label into the
    unused per-section default.
    """

    body = (PROJECT_ROOT / "_r68_build_label.py").read_text(encoding="utf-8")
    assert "section.different_first_page_header_footer = False" in body, (
        "Round 73 / F1: _r68_build_label.apply_word_footer must "
        "explicitly clear different_first_page_header_footer on each "
        "section."
    )


def test_apply_word_footer_prefers_add_paragraph_over_empty_first() -> None:
    """Source-shape pin: when ``footer.paragraphs[0]`` exists but has
    no runs, the helper must call ``footer.add_paragraph()`` instead of
    reusing the empty paragraph.  Some python-docx versions silently
    no-op ``.text = ""`` followed by ``.add_run(...)`` on a linked-empty
    paragraph, leaving the rendered footer blank.
    """

    body = (PROJECT_ROOT / "_r68_build_label.py").read_text(encoding="utf-8")
    # The new branch tests ``existing_paragraphs[0].runs`` -- a runs-
    # empty paragraph is treated as unhealthy and a fresh one is added.
    assert "existing_paragraphs[0].runs" in body, (
        "Round 73 / F1: apply_word_footer must guard on .runs being "
        "non-empty before reusing the first footer paragraph."
    )
    assert "footer.add_paragraph()" in body, (
        "Round 73 / F1: apply_word_footer must call "
        "footer.add_paragraph() in the runs-empty branch."
    )


def test_apply_word_footer_swallow_log_promoted_to_warning() -> None:
    """Source-shape pin: the outer swallow log inside ``apply_word_footer``
    must be ``logger.warning(...)`` (was ``logger.debug(...)`` pre-R73).
    The next regression must surface in the admin error log instead of
    hiding under the default debug threshold.
    """

    body = (PROJECT_ROOT / "_r68_build_label.py").read_text(encoding="utf-8")
    # The outer except inside apply_word_footer -- match the canonical
    # message string so we are pinning the SAME log statement.
    assert 'logger.warning("Round 68 / A1: word footer skipped' in body, (
        "Round 73 / F1: apply_word_footer outer swallow log must be "
        "logger.warning, not logger.debug."
    )
    # And there must be NO logger.debug variant of the same message
    # anywhere in the helper -- a half-applied promotion would leave a
    # stray debug call that hides the regression.
    assert 'logger.debug("Round 68 / A1: word footer skipped' not in body, (
        "Round 73 / F1: stale logger.debug variant of the apply_word_footer "
        "swallow log present -- complete the promotion to logger.warning."
    )


# ---------------------------------------------------------------------------
# Layer 3b: every call-site swallow log promoted to warning
# ---------------------------------------------------------------------------


_R73_CALL_SITE_PINS = [
    # (file, canonical-message-substring) -- each must be reachable
    # via ``logger.warning(...)`` AND must NOT be reachable via
    # ``logger.debug(...)``.  Keys mirror the audit table.
    ("advanced_renewal_analyzer.py", "Round 68 / A1: Renewal word footer skipped"),
    ("compact_report_formatter.py", "Round 68 / A1: Compact word footer skipped"),
    ("executive_intelligence_formatter.py", "Round 68 / A1: executive_intelligence word footer skipped"),
    ("executive_report_builder.py", "Round 70 / Phase 1: ExecutiveReportBuilder word footer skipped"),
    ("adoptiq_backend.py", "Round 68 / A1: append_to_word_report footer skipped"),
    ("adoptiq_backend.py", "Round 68 / A1: legacy comprehensive footer skipped"),
    ("leader_report_generator.py", "Round 68 / A1: Leader word footer skipped"),
    ("leader_report_generator.py", "Round 70 / Phase 4: post-TAC leader word footer skipped"),
    ("leader_report_generator.py", "Round 70 / Phase 4: no-TAC leader word footer skipped"),
    ("app_simple.py", "Round 70 / Phase 1: Compact enhanced fallback word footer skipped"),
    ("app_simple.py", "Round 70 / Phase 1: Renewal simple word footer skipped"),
    ("app_simple.py", "Round 70 / Phase 1: Subscription word footer skipped"),
]


@pytest.mark.parametrize(("rel_path", "needle"), _R73_CALL_SITE_PINS)
def test_call_site_swallow_log_uses_warning_not_debug(rel_path: str, needle: str) -> None:
    """Every Word-writer call-site that swallows an
    ``apply_word_footer`` import / invocation failure MUST log at
    ``warning`` (not ``debug``) so the next regression surfaces in the
    admin error log instead of hiding under the default debug threshold.
    """

    body = (PROJECT_ROOT / rel_path).read_text(encoding="utf-8")
    # The needle is the canonical message -- it must appear EXACTLY
    # once, after either ``logger.warning(`` or
    # ``...getLogger(__name__).warning(`` (executive_intelligence uses
    # the latter form).
    debug_call = f'logger.debug("{needle}'
    assert debug_call not in body, (
        f"Round 73 / F1: {rel_path} still carries logger.debug variant "
        f"of {needle!r} -- promote to logger.warning."
    )
    # The promoted form must be present in some shape (warning, with or
    # without the ``logger.`` prefix) -- match the message string after
    # the word ``warning(`` to avoid false positives on the comment
    # block above the call.
    pat = re.compile(r"\.warning\(\s*\n?\s*[\"']" + re.escape(needle))
    assert pat.search(body), (
        f"Round 73 / F1: {rel_path} missing logger.warning(...) call "
        f"with {needle!r} message -- complete the promotion."
    )


# ---------------------------------------------------------------------------
# Runtime smoke: the hardened helper still produces the canonical footer
# ---------------------------------------------------------------------------


def test_apply_word_footer_runtime_still_emits_canonical_footer() -> None:
    """The R73 hardening MUST NOT regress the R68/R70 happy path -- a
    fresh document must still pick up the canonical footer string.
    """

    pytest.importorskip("docx")
    from docx import Document  # noqa: PLC0415

    from _r68_build_label import apply_word_footer  # noqa: PLC0415

    doc = Document()
    doc.add_paragraph("body for R73 hardening smoke")
    ok = apply_word_footer(doc)
    assert ok is True

    text_pieces = []
    for section in doc.sections:
        for para in section.footer.paragraphs:
            txt = (para.text or "").strip()
            if txt:
                text_pieces.append(txt)
    text = "\n".join(text_pieces)
    assert re.search(r"AdoptIQ v\d+\.\d+\.\d+ build \S+ - generated ", text), (
        f"Round 73 / F1: hardened helper failed to emit the canonical "
        f"footer string; got {text!r}"
    )


def test_apply_word_footer_idempotent_after_hardening() -> None:
    """Round 73 must preserve the R68 idempotency contract -- calling
    twice must NOT duplicate the label paragraph in the footer.
    """

    pytest.importorskip("docx")
    from docx import Document  # noqa: PLC0415

    from _r68_build_label import apply_word_footer  # noqa: PLC0415

    doc = Document()
    doc.add_paragraph("body")
    apply_word_footer(doc)
    apply_word_footer(doc)

    label_count = 0
    for section in doc.sections:
        for para in section.footer.paragraphs:
            if para.text.strip().startswith("AdoptIQ v"):
                label_count += 1
    assert label_count == 1, (
        f"Round 73 / F1: idempotency broken -- expected exactly 1 "
        f"label paragraph after re-apply, got {label_count}."
    )


def test_apply_word_footer_never_raises_after_hardening() -> None:
    """Round 73 must preserve the R68 never-raise contract -- a malformed
    document argument must surface as ``False`` plus a warning log, not a
    raised exception that would brick the writer's ``doc.save(...)`` call.
    """

    from _r68_build_label import apply_word_footer  # noqa: PLC0415

    class _BogusDoc:
        @property
        def sections(self):  # noqa: D401 - matches the iter-of-sections contract
            raise RuntimeError("R73 smoke: bogus sections")

    ok = apply_word_footer(_BogusDoc())
    assert ok is False, (
        "Round 73 / F1: apply_word_footer must return False on a "
        "malformed doc argument, not raise."
    )


# ---------------------------------------------------------------------------
# Frozen-artifact smoke (skip when no DMG built yet)
# ---------------------------------------------------------------------------


def test_freshly_built_artifacts_carry_footer1_xml() -> None:
    """When ``OUTBOX/`` carries a freshly-built .docx (e.g. from a
    smoke run after a DMG bake), assert the bytes include
    ``word/footer1.xml`` -- the proof that the R68/A1 footer landed in
    the rendered document, not just the in-memory python-docx tree.

    Skips silently when no .docx is present in OUTBOX/ so this test
    does not flake in CI / dev where the bake has not been run.
    """

    import zipfile  # noqa: PLC0415

    outbox = PROJECT_ROOT / "OUTBOX"
    if not outbox.exists():
        pytest.skip("Round 73 / F1: OUTBOX/ does not exist (no DMG bake yet)")
    docx_files = sorted(outbox.glob("**/*.docx"))
    if not docx_files:
        pytest.skip("Round 73 / F1: no .docx artifacts in OUTBOX/ to inspect")

    missing: list[str] = []
    for docx_path in docx_files:
        try:
            with zipfile.ZipFile(str(docx_path)) as zf:
                names = zf.namelist()
            if "word/footer1.xml" not in names:
                missing.append(docx_path.name)
        except zipfile.BadZipFile:
            # Corrupted artifact -- fail loud, not silent.
            missing.append(f"{docx_path.name} (bad zip)")

    assert not missing, (
        f"Round 73 / F1: freshly-built artifacts missing word/footer1.xml: "
        f"{missing}"
    )
