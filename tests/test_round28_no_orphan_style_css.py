"""Round 28 / Phase 5 -- ``static/css/style.css`` is gone and stays gone.

``static/css/style.css`` (448 lines) was a parallel theme system
that drifted from the canonical tokens in ``templates/base.html``.
Crucially no template ever ``link``-ed it, so the rules never
took effect at runtime; the only way they affected anything was
when an engineer edited them by mistake instead of editing
base.html, producing a "mysterious it works on my branch" bug.

Round 28 deleted the file and migrated the only test reference
(``tests/test_round13_markers.py::test_marker_phase_5_8_css_risk_vars``)
to a tombstone that asserts the file's absence.

This test pins:

1. The file no longer exists on disk.
2. No template references it via ``href``, ``src``, or
   ``url_for('static', filename=...)``.
3. No Python module references it, with the deliberate exception
   of the Round-28 tombstone test (which exists to enforce the
   no-readd rule) and AGENTS-style documentation files
   (``QUALITY_AUDIT.md``, ``.cursor/rules/adoptiq.mdc``) that
   merely log the historical state.
"""

from __future__ import annotations

import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

_ALLOWED_REFS = {
    # Tombstone test deliberately mentions the path so a future
    # accidental re-add fails the assertion.
    REPO_ROOT / "tests" / "test_round28_no_orphan_style_css.py",
    REPO_ROOT / "tests" / "test_round13_markers.py",
}


def _iter_source_files():
    for ext in ("*.py", "*.html", "*.htm", "*.js", "*.jinja", "*.jinja2"):
        for path in REPO_ROOT.rglob(ext):
            # Skip vendored / build directories.
            parts = set(path.parts)
            if any(skip in parts for skip in (".venv", "venv", "build", "dist", "node_modules", "__pycache__", ".git")):
                continue
            yield path


def test_orphan_style_css_does_not_exist():
    style_css = REPO_ROOT / "static" / "css" / "style.css"
    assert not style_css.exists(), (
        "Round 28 / Phase 5: static/css/style.css was deleted because "
        "it was an orphan parallel theme system.  Do NOT re-add it -- "
        "put any new theme tokens directly in templates/base.html so "
        "the dark/light toggle keeps working from a single source of "
        "truth.  If you genuinely need a separate stylesheet, link it "
        "from base.html and update this test with the new file path."
    )


def test_no_template_references_style_css():
    for path in REPO_ROOT.rglob("*.html"):
        if any(skip in path.parts for skip in (".venv", "venv", "build", "dist", "node_modules", "__pycache__", ".git")):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        # Look for both the literal filename and the Flask
        # ``url_for('static', filename='css/style.css')`` form.
        assert "css/style.css" not in text, (
            "Round 28 / Phase 5: %s still references "
            "static/css/style.css; that file was removed.  "
            "Replace the link with tokens from base.html or "
            "import a per-page <style> block." % path.relative_to(REPO_ROOT)
        )


def test_no_runtime_python_module_references_style_css():
    # Round 29: relaxed the scan from "static/css/style.css" to
    # "css/style.css" so it now also catches the
    # ``url_for('static', filename='css/style.css')`` form (the
    # HTML-side ``test_no_template_references_style_css`` already
    # uses this looser pattern; matching it here means a Python
    # module that builds the same href via ``url_for`` cannot
    # silently slip a regression through).  ``_ALLOWED_REFS`` still
    # exempts the two tombstone-style guards that mention the path
    # on purpose.
    for path in REPO_ROOT.rglob("*.py"):
        if any(skip in path.parts for skip in (".venv", "venv", "build", "dist", "node_modules", "__pycache__", ".git")):
            continue
        if path in _ALLOWED_REFS:
            continue
        # Don't trip on test files OUTSIDE the allow-list either,
        # because tests should not depend on a deleted file.
        text = path.read_text(encoding="utf-8", errors="replace")
        assert "css/style.css" not in text, (
            "Round 28 / Phase 5: %s still references "
            "static/css/style.css (or url_for('static', filename="
            "'css/style.css')); remove the reference (the file "
            "was deleted to consolidate the theme system on "
            "templates/base.html).  If you are adding a NEW "
            "tombstone-style guard, allow-list its path in "
            "_ALLOWED_REFS at the top of this test." %
            path.relative_to(REPO_ROOT)
        )
