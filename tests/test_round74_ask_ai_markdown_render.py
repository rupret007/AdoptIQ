r"""Round 74 / Build 48 / Phase 2 (P2) -- Ask AI markdown render pins.

Pre-R74 the Ask AI answer area used ``white-space: pre-wrap`` and a
hand-rolled line-by-line renderer that displayed markdown tables /
fenced code blocks / inline emphasis as raw syntax (``**bold**``,
``| col |``, ``\`code\```).  Director-facing answers deserve proper
formatting.

R74/P2 introduces:

1.  ``marked@11.1.1`` and ``dompurify@3.0.8`` loaded from cdn.jsdelivr.net
    (already CSP-allow-listed for ``script-src``) with explicit SHA-384
    SRI integrity attributes -- no future CDN swap can inject untrusted
    JS into the page.
2.  ``_r74RenderMarkdownSafe(rawText)`` and ``_r74PostProcessSourceBadges``
    helpers in ``static/js/ask_ai.js`` that build the rendered DOM via
    marked.parse + DOMPurify.sanitize, then walk the result to swap
    ``[Source: ID]`` literals for clickable badges.
3.  ``formatAnswerInto`` is rewired to try the marked path first and
    fall back to the pre-R74 line-by-line renderer when either library
    is unavailable -- so a CDN outage / offline DMG still ships a
    readable answer.

These tests pin the SOURCE SHAPE (CDN script tags + integrity attrs +
helper definitions + dispatch logic).  Browser-rendered behaviour is
beyond the scope of pytest; the equivalent acceptance lives in the
manual smoke check in Phase 7.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ---------------------------------------------------------------------------
# Layer 1: CDN script tags in templates/ask_ai.html.
# ---------------------------------------------------------------------------


def test_ask_ai_html_loads_marked_from_jsdelivr_cdn() -> None:
    """``ask_ai.html`` must load marked@11 from cdn.jsdelivr.net."""

    body = (PROJECT_ROOT / "templates" / "ask_ai.html").read_text(encoding="utf-8")
    pat = re.compile(
        r'<script\s+src="https://cdn\.jsdelivr\.net/npm/marked@11(?:\.\d+)*'
        r"/marked\.min\.js\""
    )
    assert pat.search(body), (
        "Round 74 / P2: ask_ai.html must load marked@11 from "
        "cdn.jsdelivr.net so the renderer can parse markdown answers."
    )


def test_ask_ai_html_loads_dompurify_from_jsdelivr_cdn() -> None:
    """``ask_ai.html`` must load DOMPurify@3 from cdn.jsdelivr.net."""

    body = (PROJECT_ROOT / "templates" / "ask_ai.html").read_text(encoding="utf-8")
    pat = re.compile(
        r'<script\s+src="https://cdn\.jsdelivr\.net/npm/dompurify@3(?:\.\d+)*'
        r"/dist/purify\.min\.js\""
    )
    assert pat.search(body), (
        "Round 74 / P2: ask_ai.html must load DOMPurify@3 from "
        "cdn.jsdelivr.net so the marked-rendered HTML is sanitised "
        "before injection."
    )


def test_marked_and_dompurify_carry_sri_integrity_attributes() -> None:
    """Both CDN scripts must carry SHA-384 ``integrity`` + ``crossorigin``
    attributes so a CDN compromise cannot ship arbitrary JS to the
    Ask AI page.  Mirror of the Bootstrap CSS hardening in base.html.
    """

    body = (PROJECT_ROOT / "templates" / "ask_ai.html").read_text(encoding="utf-8")
    # Both scripts must carry integrity="sha384-..." with non-empty value.
    sri_pattern = re.compile(
        r'<script\s+src="https://cdn\.jsdelivr\.net/npm/(marked|dompurify)@[^"]+"'
        r'\s+integrity="sha384-[A-Za-z0-9+/=]{30,}"'
        r'\s+crossorigin="anonymous"',
        re.DOTALL,
    )
    matches = sri_pattern.findall(body)
    assert "marked" in matches and "dompurify" in matches, (
        "Round 74 / P2: marked and DOMPurify CDN <script> tags must "
        "carry SHA-384 integrity + crossorigin attributes; got "
        f"matches={matches!r}"
    )


def test_csp_still_allows_jsdelivr_for_script_src() -> None:
    """The page-level CSP override must continue to allow
    ``cdn.jsdelivr.net`` for ``script-src`` (otherwise the new CDN
    scripts will not load), and MUST NOT relax to ``unsafe-inline``."""

    body = (PROJECT_ROOT / "templates" / "ask_ai.html").read_text(encoding="utf-8")
    # Extract just the live CSP meta tag content -- the file contains a
    # historical comment that mentions retired ``unsafe-inline`` flags
    # which would otherwise false-match a naive substring guard.
    csp_match = re.search(
        r'<meta\s+http-equiv="Content-Security-Policy"\s+'
        r'content="([^"]+)"',
        body,
    )
    assert csp_match, (
        "Round 74 / P2: ask_ai.html must carry a Content-Security-Policy "
        "meta tag (the page-level override that drops 'unsafe-inline' "
        "from script-src)."
    )
    csp_content = csp_match.group(1)
    csp_re = re.compile(
        r"script-src\s+'self'\s+https://cdn\.jsdelivr\.net",
        re.MULTILINE,
    )
    assert csp_re.search(csp_content), (
        "Round 74 / P2: ask_ai.html CSP override must keep "
        "``script-src 'self' https://cdn.jsdelivr.net`` so the new "
        f"marked + DOMPurify CDN scripts pass the policy.  Got: {csp_content!r}"
    )
    # Negative guard within the LIVE CSP only -- no ``script-src
    # 'unsafe-inline'`` regression.
    bad_re = re.compile(r"script-src[^;]*'unsafe-inline'")
    assert not bad_re.search(csp_content), (
        "Round 74 / P2: ask_ai.html CSP must NOT add 'unsafe-inline' "
        "to script-src -- the marked + DOMPurify rendering pipeline "
        "is designed to work under the strict 'self' + jsdelivr "
        f"policy.  Got: {csp_content!r}"
    )


# ---------------------------------------------------------------------------
# Layer 2: ask_ai.js helper definitions.
# ---------------------------------------------------------------------------


def test_ask_ai_js_defines_render_markdown_safe_helper() -> None:
    """``static/js/ask_ai.js`` must define ``_r74RenderMarkdownSafe`` --
    the marked.parse + DOMPurify.sanitize pipeline."""

    body = (PROJECT_ROOT / "static" / "js" / "ask_ai.js").read_text(encoding="utf-8")
    pat = re.compile(r"function\s+_r74RenderMarkdownSafe\(rawText\)")
    assert pat.search(body), (
        "Round 74 / P2: ask_ai.js must define _r74RenderMarkdownSafe "
        "as the marked + DOMPurify pipeline entry point."
    )
    # Sanity: the body must call BOTH ``window.marked.parse`` and
    # ``window.DOMPurify.sanitize`` so a stub implementation that
    # forgets either gate is caught.
    assert "window.marked.parse(" in body, (
        "Round 74 / P2: _r74RenderMarkdownSafe must call window.marked.parse"
    )
    assert "window.DOMPurify.sanitize(" in body, (
        "Round 74 / P2: _r74RenderMarkdownSafe must call "
        "window.DOMPurify.sanitize on the marked output"
    )


def test_ask_ai_js_defines_post_process_source_badges_helper() -> None:
    """``static/js/ask_ai.js`` must define
    ``_r74PostProcessSourceBadges`` so ``[Source: ID]`` literals in
    the rendered DOM become clickable badges."""

    body = (PROJECT_ROOT / "static" / "js" / "ask_ai.js").read_text(encoding="utf-8")
    pat = re.compile(r"function\s+_r74PostProcessSourceBadges\(rootEl\)")
    assert pat.search(body), (
        "Round 74 / P2: ask_ai.js must define "
        "_r74PostProcessSourceBadges so the rendered DOM swaps "
        "[Source: ID] markers for clickable badges (Phase 4 wires "
        "these to the offcanvas drawer)."
    )


def test_ask_ai_js_defines_r74_source_badge_builder() -> None:
    """``static/js/ask_ai.js`` must define ``_r74BuildSourceBadge`` --
    the canonical R74 source-badge factory.  The badge must carry
    the ``r74-source-badge`` class, ``data-source-id`` attribute,
    ``tabindex="0"``, and ``role="button"`` so Phase 4's offcanvas
    drawer can attach click + keyboard handlers."""

    body = (PROJECT_ROOT / "static" / "js" / "ask_ai.js").read_text(encoding="utf-8")
    pat = re.compile(r"function\s+_r74BuildSourceBadge\(sid,\s*originalText\)")
    assert pat.search(body), (
        "Round 74 / P2: ask_ai.js must define _r74BuildSourceBadge."
    )
    # Source-shape pins on the badge's required attributes.
    must_have = [
        "'r74-source-badge'",
        "'data-source-id'",
        "'tabindex'",
        "'role'",
    ]
    for needle in must_have:
        assert needle in body, (
            f"Round 74 / P2: _r74BuildSourceBadge must set {needle} "
            "on every badge so Phase 4's drawer can attach handlers "
            "and assistive tech can announce + activate it."
        )


def test_format_answer_into_dispatches_to_r74_when_libraries_available() -> None:
    """``formatAnswerInto`` must call ``_r74RenderMarkdownSafe`` first
    and fall back to the pre-R74 renderer ONLY when the helper returns
    ``null`` (CDN blocked, offline DMG run, etc.)."""

    body = (PROJECT_ROOT / "static" / "js" / "ask_ai.js").read_text(encoding="utf-8")
    # Source-shape pin: the dispatch must read
    # ``var sanitisedHtml = _r74RenderMarkdownSafe(text)`` BEFORE the
    # ``var lines = text.split(...)`` line that drives the legacy
    # fallback.  Index check enforces ordering.
    dispatch_pat = re.compile(
        r"var\s+sanitisedHtml\s*=\s*_r74RenderMarkdownSafe\(text\)"
    )
    fallback_pat = re.compile(r"var\s+lines\s*=\s*text\.split\(/\\r\?\\n/\)")
    dispatch_match = dispatch_pat.search(body)
    fallback_match = fallback_pat.search(body)
    assert dispatch_match is not None, (
        "Round 74 / P2: formatAnswerInto must invoke _r74RenderMarkdownSafe."
    )
    assert fallback_match is not None, (
        "Round 74 / P2: formatAnswerInto must keep the line-by-line "
        "fallback so a CDN outage or offline run still renders a "
        "readable answer."
    )
    assert dispatch_match.start() < fallback_match.start(), (
        "Round 74 / P2: marked dispatch must run BEFORE the "
        "line-by-line fallback so the fallback is never the default."
    )


def test_dompurify_allow_list_blocks_dangerous_tags() -> None:
    """Source-shape pin: the DOMPurify config must explicitly block
    ``script``, ``iframe``, ``object``, ``embed``, ``form`` and inline
    event handlers (defense in depth on top of the allow-list).
    A future edit that drops these from FORBID_TAGS / FORBID_ATTR
    must update this test deliberately."""

    body = (PROJECT_ROOT / "static" / "js" / "ask_ai.js").read_text(encoding="utf-8")
    forbidden_tags = ["script", "iframe", "object", "embed", "form"]
    for tag in forbidden_tags:
        assert f"'{tag}'" in body, (
            f"Round 74 / P2: DOMPurify config must FORBID_TAGS '{tag}' "
            "so a future model jailbreak that injects raw HTML cannot "
            "smuggle privileged surface area into the Ask AI page."
        )
    # Inline event handlers blocked too.
    forbidden_attrs = ["onerror", "onload", "onclick"]
    for attr in forbidden_attrs:
        assert f"'{attr}'" in body, (
            f"Round 74 / P2: DOMPurify config must FORBID_ATTR '{attr}'."
        )


def test_ai_answer_table_styles_present_in_template() -> None:
    """Source-shape pin: the new ``.ai-answer table`` / ``.ai-answer th``
    polish CSS must be present so marked-rendered tables don't render
    as cramped against the surrounding prose."""

    body = (PROJECT_ROOT / "templates" / "ask_ai.html").read_text(encoding="utf-8")
    # Light-touch pin: just confirm the selector + key polish rules.
    assert ".ai-answer table {" in body, (
        "Round 74 / P2: ask_ai.html must polish .ai-answer table for "
        "marked-rendered markdown tables."
    )
    assert ".ai-answer thead th {" in body, (
        "Round 74 / P2: ask_ai.html must polish .ai-answer thead th."
    )


def test_r74_source_badge_class_styled() -> None:
    """Source-shape pin: ``.r74-source-badge`` CSS class must be styled
    so the clickable citation badges have a visible affordance."""

    body = (PROJECT_ROOT / "templates" / "ask_ai.html").read_text(encoding="utf-8")
    assert ".r74-source-badge {" in body, (
        "Round 74 / P2: ask_ai.html must style .r74-source-badge -- "
        "the class Phase 2 PostProcess walker produces."
    )
