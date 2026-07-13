"""Round 29 / M1 -- ``progress.html`` and ``previous_reports.html``
no longer carry inline ``rgba(...)`` status tints.

The pre-Round-29 templates hardcoded the ``--success-bg`` /
``--warning-bg`` / ``--danger-bg`` / ``--info-tint-bg`` derivatives
inline (``rgba(0, 166, 81, 0.08)`` etc.) instead of routing through
the semantic tokens declared in ``base.html``.  That meant the
dark-theme variant had to be re-coded selector-by-selector inside
the same template, and any future palette change had to be done in
several places.

Round 29 added the missing tokens to ``base.html`` and switched the
two templates to consume them.  This test pins the migration: the
canonical inline tints must no longer appear in either template.

We intentionally allow ``rgba(...)`` literals elsewhere (e.g. shadow
helpers, accent glow on the navbar) because those are not the
status-tint surfaces the round was about.
"""

from __future__ import annotations

import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

# The canonical tints that were inlined pre-Round-29.  Each string
# was either copied from the brand hex or chosen specifically to
# tint a status surface; together they cover every site we migrated.
_BANNED_TINTS = (
    # Light-theme status tints (status-box.completed/error,
    # csone-status, partial-warnings-box, file-type.excel pre-fix).
    "rgba(0, 166, 81, 0.08)",
    "rgba(0, 166, 81, 0.12)",
    "rgba(255, 140, 0, 0.08)",
    "rgba(227, 28, 61, 0.08)",
    # Dark-theme status tints that previously lived in
    # ``[data-bs-theme="dark"] .status-box.running`` /
    # ``.previous-reports-strip`` / ``.report-item:hover``.
    "rgba(255, 122, 26, 0.06)",
    "rgba(255, 122, 26, 0.08)",
    # Light-theme info tints inlined on .status-box.running /
    # .previous-reports-strip / .file-type.word.
    "rgba(0, 188, 235, 0.06)",
    "rgba(0, 188, 235, 0.12)",
)


def _read(rel: str) -> str:
    return (REPO_ROOT / rel).read_text(encoding="utf-8")


def test_progress_html_has_no_inline_status_rgba():
    src = _read("templates/progress.html")
    offending = [tint for tint in _BANNED_TINTS if tint in src]
    assert not offending, (
        "Round 29 / M1: templates/progress.html still contains "
        "inline status tints %r.  Replace each occurrence with the "
        "matching semantic token from base.html (--success-bg, "
        "--warning-bg, --danger-bg, --info-tint-bg) so the dark/light "
        "toggle re-skins the surface from a single source of truth."
        % offending
    )


def test_previous_reports_html_has_no_inline_status_rgba():
    src = _read("templates/previous_reports.html")
    offending = [tint for tint in _BANNED_TINTS if tint in src]
    assert not offending, (
        "Round 29 / M1: templates/previous_reports.html still "
        "contains inline status tints %r.  Replace each occurrence "
        "with the matching semantic token from base.html "
        "(--success-bg, --info-tint-bg) so the dark theme can pivot "
        "them to the AdoptIQ-orange-friendly variants without the "
        "template having to dual-write a ``[data-bs-theme=\"dark\"]`` "
        "rule for every selector." % offending
    )


def test_progress_html_references_round29_tokens():
    """Positive flank: the migration should leave ``var(--success-bg)``
    style references in the template body so we know the tokens are
    actually being consumed (not just the negative-rgba flank passing
    because someone deleted the rules entirely).
    """
    src = _read("templates/progress.html")
    for token in ("var(--success-bg)", "var(--danger-bg)", "var(--warning-bg)", "var(--info-tint-bg)"):
        assert token in src, (
            "Round 29 / M1: templates/progress.html should consume "
            "``%s`` after the rgba migration; if you removed the "
            "selector that used it, also remove this assertion." % token
        )


def test_previous_reports_html_references_round29_tokens():
    src = _read("templates/previous_reports.html")
    for token in ("var(--info-tint-bg)", "var(--success-bg)"):
        assert token in src, (
            "Round 29 / M1: templates/previous_reports.html should "
            "consume ``%s`` after the rgba migration." % token
        )
