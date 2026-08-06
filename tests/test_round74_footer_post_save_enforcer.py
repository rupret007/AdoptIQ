"""Round 74 / Build 48 / Phase 1 (F1) -- post-save footer enforcer tests.

Build 47 acceptance confirmed all 4 production DOCX artifacts ship with
empty footers despite the R73/F1 in-memory hardening.  Some upstream
writer call site is bypassing the wrapped ``.save()`` and the rendered
footer never carries the build label.

This module pins the defense-in-depth post-save XML-level enforcer:

1.  ``_r74_footer_enforcer.enforce_build_label_footer`` is defined,
    accepts a path-like, returns a structured diag dict, and never
    raises on input edge cases.
2.  The 4 report-completion call sites in ``app_simple.py``
    (Compact, Renewal, Comprehensive, Leader) call
    ``_r74_enforce_footer_safe`` adjacent to their R57 injector call.
3.  Idempotent behaviour: a doc that already carries the build label
    is a no-op.
4.  Round-trip: a synthetic empty-footer .docx gets a canonical
    AdoptIQ footer after enforcement, surviving a fresh open via
    ``python-docx``.
5.  Defensive: malformed input (missing file, not a zip, missing
    word/document.xml) returns a structured diag and never raises.
6.  PyInstaller pin: both spec files list ``_r74_footer_enforcer`` in
    ``hidden_imports`` so the frozen build bundles the module.

Per CLAUDE.md / quality-gate.mdc: every fixed bug ships with a
regression test.
"""

from __future__ import annotations
from source_shape_utils import assert_in_source

import re
import sys
import zipfile
from io import BytesIO
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ---------------------------------------------------------------------------
# Layer 1: module shape -- the enforcer is callable and has the right
# return contract.
# ---------------------------------------------------------------------------


def test_module_exposes_enforce_build_label_footer() -> None:
    """``_r74_footer_enforcer`` must export ``enforce_build_label_footer``."""

    import _r74_footer_enforcer as mod  # noqa: PLC0415

    assert hasattr(mod, "enforce_build_label_footer"), (
        "Round 74 / F1: _r74_footer_enforcer must export "
        "enforce_build_label_footer"
    )
    assert callable(mod.enforce_build_label_footer)


def test_enforcer_returns_diag_dict_on_missing_file(tmp_path: Path) -> None:
    """Missing path must return a structured diag dict, never raise."""

    from _r74_footer_enforcer import enforce_build_label_footer  # noqa: PLC0415

    missing = tmp_path / "does_not_exist.docx"
    diag = enforce_build_label_footer(missing)
    assert isinstance(diag, dict)
    assert diag.get("injected") is False
    assert "file not found" in str(diag.get("reason", ""))
    assert diag.get("footer_parts_replaced") == []


def test_enforcer_returns_diag_dict_on_malformed_zip(tmp_path: Path) -> None:
    """A non-zip file that ends in .docx must return a structured diag,
    never raise."""

    from _r74_footer_enforcer import enforce_build_label_footer  # noqa: PLC0415

    bogus = tmp_path / "bogus.docx"
    bogus.write_bytes(b"this is definitely not a zip")
    diag = enforce_build_label_footer(bogus)
    assert isinstance(diag, dict)
    assert diag.get("injected") is False
    assert "zip read failed" in str(diag.get("reason", ""))


# ---------------------------------------------------------------------------
# Layer 2: round-trip behaviour -- empty-footer .docx gets a real footer.
# ---------------------------------------------------------------------------


def _make_empty_footer_docx(tmp_path: Path, name: str = "empty_footer.docx") -> Path:
    """Build a minimal .docx with an empty default footer paragraph.

    Mirrors the Build 47 production artifacts: ``word/footer1.xml`` is
    present in the zip but its body paragraph carries no run -- exactly
    what we observed in every Build 47 .docx.

    python-docx's default ``Document()`` does NOT materialise
    ``word/footer1.xml`` in the zip until something touches the section's
    footer (the part is lazy-loaded), so we explicitly access
    ``section.footer.paragraphs`` to force the part into existence
    before saving.  This is the same code path the writers take when
    they call ``apply_word_footer`` -- the ONLY difference in production
    is that some downstream pass (still under investigation) strips the
    run python-docx wrote.
    """

    pytest.importorskip("docx")
    from docx import Document  # noqa: PLC0415

    out = tmp_path / name
    doc = Document()
    doc.add_paragraph("body content for footer enforcer round-trip")
    # Force footer1.xml materialisation -- accessing the footer's
    # paragraphs property triggers python-docx's lazy footer-part
    # creation.  We then save WITHOUT touching the runs so the result
    # matches the Build 47 production shape: footer1.xml is present
    # with a single empty paragraph, no AdoptIQ build label.
    for section in doc.sections:
        _ = list(section.footer.paragraphs)
    doc.save(str(out))
    # Sanity: confirm word/footer1.xml is in the resulting zip.
    with zipfile.ZipFile(str(out), mode="r") as zf:
        assert "word/footer1.xml" in zf.namelist(), (
            "test fixture broken: python-docx did not materialise "
            "word/footer1.xml"
        )
    return out


def _read_footer_text(docx_path: Path) -> str:
    """Read all footer paragraph text from word/footer1.xml directly."""

    with zipfile.ZipFile(str(docx_path), mode="r") as zf:
        names = zf.namelist()
        if "word/footer1.xml" not in names:
            return ""
        xml = zf.read("word/footer1.xml").decode("utf-8", errors="replace")
    # Crude but adequate for tests: extract text inside <w:t> elements.
    return " ".join(re.findall(r"<w:t[^>]*>([^<]*)</w:t>", xml))


def test_enforcer_replaces_empty_footer_with_canonical_label(tmp_path: Path) -> None:
    """The Build 47 production-shape .docx has an empty footer1.xml
    paragraph -- the enforcer MUST replace it with the canonical
    AdoptIQ build label run."""

    from _r74_footer_enforcer import enforce_build_label_footer  # noqa: PLC0415

    docx = _make_empty_footer_docx(tmp_path)
    # Sanity check: the empty footer carries no AdoptIQ text.
    pre_text = _read_footer_text(docx)
    assert "AdoptIQ" not in pre_text

    diag = enforce_build_label_footer(docx)
    assert diag.get("injected") is True, diag
    assert "footer enforced post-save" in str(diag.get("reason", ""))
    assert "word/footer1.xml" in (diag.get("footer_parts_replaced") or [])

    post_text = _read_footer_text(docx)
    assert re.search(r"AdoptIQ v\d+\.\d+\.\d+ build \S+ - generated", post_text), (
        f"Round 74 / F1: post-enforcement footer text missing AdoptIQ "
        f"label; got {post_text!r}"
    )


def test_enforcer_idempotent_when_footer_already_stamped(tmp_path: Path) -> None:
    """Re-running the enforcer on a doc that already carries the label
    must be a no-op (does NOT duplicate the run)."""

    from _r74_footer_enforcer import enforce_build_label_footer  # noqa: PLC0415

    docx = _make_empty_footer_docx(tmp_path, name="idempotent.docx")
    first = enforce_build_label_footer(docx)
    assert first.get("injected") is True

    second = enforce_build_label_footer(docx)
    assert second.get("injected") is False, second
    assert "already stamped" in str(second.get("reason", ""))
    # The label must appear EXACTLY once in the footer text.
    text = _read_footer_text(docx)
    matches = re.findall(r"AdoptIQ v\d+\.\d+\.\d+ build \S+ - generated", text)
    assert len(matches) == 1, (
        f"Round 74 / F1: enforcer is non-idempotent -- got {len(matches)} "
        f"AdoptIQ labels in footer; expected 1.  Text={text!r}"
    )


def test_enforcer_preserves_other_parts_of_zip(tmp_path: Path) -> None:
    """The enforcer must NOT touch parts other than footer*.xml --
    document.xml body content, styles, etc. must round-trip byte-for-byte."""

    from _r74_footer_enforcer import enforce_build_label_footer  # noqa: PLC0415

    docx = _make_empty_footer_docx(tmp_path, name="preserve.docx")

    with zipfile.ZipFile(str(docx), mode="r") as zf:
        pre_doc = zf.read("word/document.xml")
        pre_styles = zf.read("word/styles.xml")

    diag = enforce_build_label_footer(docx)
    assert diag.get("injected") is True

    with zipfile.ZipFile(str(docx), mode="r") as zf:
        post_doc = zf.read("word/document.xml")
        post_styles = zf.read("word/styles.xml")

    # document.xml may have been touched ONLY in the footer-creation
    # path; for the empty-footer case it should be byte-identical.
    assert post_doc == pre_doc, (
        "Round 74 / F1: enforcer mutated word/document.xml when only "
        "footer1.xml should have been replaced"
    )
    assert post_styles == pre_styles, (
        "Round 74 / F1: enforcer mutated word/styles.xml -- it must "
        "leave non-footer parts untouched"
    )


def test_enforcer_round_trip_via_python_docx(tmp_path: Path) -> None:
    """After enforcement, opening the .docx with python-docx and reading
    section footers must surface the build label."""

    pytest.importorskip("docx")
    from docx import Document  # noqa: PLC0415

    from _r74_footer_enforcer import enforce_build_label_footer  # noqa: PLC0415

    docx = _make_empty_footer_docx(tmp_path, name="round_trip.docx")
    diag = enforce_build_label_footer(docx)
    assert diag.get("injected") is True

    reopened = Document(str(docx))
    text_pieces = []
    for section in reopened.sections:
        for para in section.footer.paragraphs:
            t = (para.text or "").strip()
            if t:
                text_pieces.append(t)
    rendered = "\n".join(text_pieces)
    assert re.search(r"AdoptIQ v\d+\.\d+\.\d+ build \S+ - generated", rendered), (
        f"Round 74 / F1: python-docx round-trip lost the build label; "
        f"rendered={rendered!r}"
    )


# ---------------------------------------------------------------------------
# Layer 3: source-shape pins -- 4 call sites + helper definition.
# ---------------------------------------------------------------------------


def test_app_simple_defines_r74_enforce_footer_safe_helper() -> None:
    """``app_simple.py`` must define a top-level ``_r74_enforce_footer_safe``
    helper next to ``_r57_inject_citations_safe``."""

    body = (PROJECT_ROOT / "app_simple.py").read_text(encoding="utf-8")
    pat = re.compile(
        r"^def _r74_enforce_footer_safe\(docx_path[^)]*\)\s*->",
        re.MULTILINE,
    )
    assert pat.search(body), (
        "Round 74 / F1: app_simple.py must define _r74_enforce_footer_safe "
        "as a top-level helper (alongside _r57_inject_citations_safe) "
        "so every report finalisation path can call it."
    )


_R74_CALL_SITE_PINS = [
    "_r74_enforce_footer_safe(exec_report_path, scenario_key='compact')",
    "_r74_enforce_footer_safe(renewal_word_path, scenario_key='renewal')",
    "_r74_enforce_footer_safe(docx_path, scenario_key='comprehensive')",
    "_r74_enforce_footer_safe(filepath, scenario_key='leader')",
]


@pytest.mark.parametrize("needle", _R74_CALL_SITE_PINS)
def test_app_simple_wires_r74_enforce_at_every_report_completion(needle: str) -> None:
    """All 4 report-completion paths must call ``_r74_enforce_footer_safe``
    so the post-save enforcer runs regardless of which writer the path
    used."""

    body = (PROJECT_ROOT / "app_simple.py").read_text(encoding="utf-8")
    assert_in_source(body, needle, label='body')


def test_r74_calls_are_adjacent_to_r57_calls() -> None:
    """Each ``_r74_enforce_footer_safe`` call must be within ~10 lines of
    the matching ``_r57_inject_citations_safe`` call -- pins the
    intentional ordering (R57 first, then R74) so a future refactor that
    splits them is forced to update this test deliberately."""

    body = (PROJECT_ROOT / "app_simple.py").read_text(encoding="utf-8").splitlines()
    r57_lines = [i for i, ln in enumerate(body) if "_r57_inject_citations_safe(" in ln and "def " not in ln]
    r74_lines = [i for i, ln in enumerate(body) if "_r74_enforce_footer_safe(" in ln and "def " not in ln]
    assert len(r57_lines) >= 4, "Expected at least 4 R57 call sites"
    assert len(r74_lines) >= 4, "Expected at least 4 R74 call sites"
    # Every R74 call must be within 12 lines AFTER an R57 call.
    for r74 in r74_lines:
        ahead = [r for r in r57_lines if 0 < r74 - r <= 12]
        assert ahead, (
            f"Round 74 / F1: _r74_enforce_footer_safe call at line {r74 + 1} "
            f"is not adjacent to (within 12 lines of) any "
            f"_r57_inject_citations_safe call -- both must run on the "
            f"same finalised .docx in the canonical R57-then-R74 order."
        )


# ---------------------------------------------------------------------------
# Layer 4: PyInstaller pins.
# ---------------------------------------------------------------------------


def test_mac_spec_pins_r74_footer_enforcer_in_hidden_imports() -> None:
    """``adoptiq_mac.spec`` must list ``_r74_footer_enforcer`` in
    ``hidden_imports`` so PyInstaller bundles the enforcer in the .app.
    """

    spec_text = (PROJECT_ROOT / "adoptiq_mac.spec").read_text(encoding="utf-8")
    assert_in_source(spec_text, "'_r74_footer_enforcer'", label='spec_text')


def test_pc_spec_pins_r74_footer_enforcer_in_hidden_imports() -> None:
    """Mirror of the mac-spec pin so the Windows build is equally
    protected."""

    spec_text = (PROJECT_ROOT / "adoptiq_pc.spec").read_text(encoding="utf-8")
    assert_in_source(spec_text, "'_r74_footer_enforcer'", label='spec_text')


def test_app_simple_carries_top_level_r74_enforcer_import() -> None:
    """``app_simple.py`` must carry a top-level ``import _r74_footer_enforcer``
    so PyInstaller's analyser picks up the module via the entry-point
    regardless of whether the spec pin is dropped (defense layer 2 of 2)."""

    body = (PROJECT_ROOT / "app_simple.py").read_text(encoding="utf-8")
    pat = re.compile(
        r"^import _r74_footer_enforcer(?:\s+as\s+\w+)?(?:\s|$)",
        re.MULTILINE,
    )
    assert pat.search(body), (
        "Round 74 / F1: app_simple.py must carry a top-level "
        "``import _r74_footer_enforcer`` so PyInstaller bundles the "
        "module via entry-point analysis."
    )
