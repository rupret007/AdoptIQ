"""Round 83 / Build 59 — signed_in_no_corpus panel state pins.

Pins the new ``signed_in_no_corpus`` state introduced in Round 83:

* ``static/js/intel_status.js::classifyCorpusPanel`` recognises
  ``boot.source == 'signed_in_no_corpus'`` BEFORE the legacy
  ``blocked_no_onedrive`` branch (precedence matters -- both states
  emit different copy and different bootstrap CTAs).
* ``corpusPanelLabel`` returns the new "Add corpus share to OneDrive"
  label.
* ``corpusPanelPillClass`` reuses the warning class (same severity
  as ``blocked_no_onedrive`` -- corpus is unavailable until user
  action).
* ``corpusPanelDetail`` emits new copy distinct from the legacy
  ``blocked_no_onedrive`` copy.
* ``paintDeepLink`` unhides the button on BOTH ``blocked_no_onedrive``
  AND ``signed_in_no_corpus``, with a dynamic label that changes
  per state.

Plus the new ``GET /api/corpus/bootstrap-shortcut`` endpoint:

* Returns ``{ok: true, share_url: <https://...>}`` when the
  configured URL passes the scheme allow-list.
* Returns ``{ok: false, error: ...}`` when the URL is missing /
  malformed / over the size cap.

Plus ``_r17_corpus_status_payload`` projects ``boot.signed_in_proxy``
through the JSON response.

Round 83 / Build 59
"""
# Round 83
from __future__ import annotations
from source_shape_utils import assert_in_source

import json
from pathlib import Path
from unittest.mock import patch

import pytest


JS_PATH = Path(__file__).parent.parent / "static" / "js" / "intel_status.js"
JS_SRC = JS_PATH.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. JS source pins: classifier recognises the new state
# ---------------------------------------------------------------------------


def test_classifier_recognises_signed_in_no_corpus_state():
    """``classifyCorpusPanel`` must dispatch ``boot.source ==
    'signed_in_no_corpus'`` to the new state."""
    # Round 83
    assert "'signed_in_no_corpus'" in JS_SRC
    assert (
        "if (source === 'signed_in_no_corpus')" in JS_SRC
        or 'if (source === "signed_in_no_corpus")' in JS_SRC
    ), (
        "Round 83 contract violated: classifyCorpusPanel must "
        "recognise the new signed_in_no_corpus source label."
    )


def test_classifier_signed_in_no_corpus_takes_precedence_over_blocked():
    """The classifier MUST check ``signed_in_no_corpus`` BEFORE
    ``blocked_no_onedrive`` because they're mutually exclusive on
    the server side but precedence in the JS branch matters if the
    payload ever gets corrupted."""
    # Round 83
    classifier_start = JS_SRC.find("function classifyCorpusPanel")
    assert classifier_start != -1
    # Use the NEXT function definition as the terminator (the
    # ``if (!boot) return 'unknown'`` short-circuit is also a valid
    # ``return 'unknown'`` that would land BEFORE the source
    # dispatches and confuse a naive end-finder).
    classifier_end = JS_SRC.find(
        "function corpusPanelLabel", classifier_start,
    )
    assert classifier_end != -1
    sig_in_classifier = JS_SRC.find(
        "source === 'signed_in_no_corpus'",
        classifier_start, classifier_end,
    )
    blk_in_classifier = JS_SRC.find(
        "source === 'blocked_no_onedrive'",
        classifier_start, classifier_end,
    )
    assert sig_in_classifier != -1, (
        "signed_in_no_corpus dispatch missing from classifier"
    )
    assert blk_in_classifier != -1, (
        "blocked_no_onedrive dispatch missing from classifier"
    )
    assert sig_in_classifier < blk_in_classifier, (
        "Round 83 contract: signed_in_no_corpus check must come BEFORE "
        "blocked_no_onedrive in the classifier branch order."
    )


# ---------------------------------------------------------------------------
# 2. JS source pins: label / pill / detail copy
# ---------------------------------------------------------------------------


def test_label_for_signed_in_no_corpus():
    """``corpusPanelLabel`` returns 'Add corpus share to OneDrive'
    for the new state."""
    # Round 83
    assert "'Add corpus share to OneDrive'" in JS_SRC \
        or '"Add corpus share to OneDrive"' in JS_SRC


def test_pill_class_for_signed_in_no_corpus_is_warning():
    """``corpusPanelPillClass`` returns the warning class (matches
    ``blocked_no_onedrive`` -- same severity tier)."""
    # Round 83
    # Look for the ``signed_in_no_corpus`` case in the pill function
    # and verify it returns ``bg-warning text-dark``.
    pill_func_start = JS_SRC.find("function corpusPanelPillClass")
    pill_func_end = JS_SRC.find(
        "default:", pill_func_start,
    )
    pill_func = JS_SRC[pill_func_start:pill_func_end]
    assert "'signed_in_no_corpus'" in pill_func
    # The case body must include the warning class.
    sig_idx = pill_func.find("'signed_in_no_corpus'")
    case_body = pill_func[sig_idx:sig_idx + 200]
    assert "bg-warning" in case_body


def test_detail_text_for_signed_in_no_corpus_distinct_from_blocked():
    """``corpusPanelDetail`` emits text for the new state that is
    DISTINCT from the legacy ``blocked_no_onedrive`` copy. The new
    state emphasises the shorter remediation flow (one click vs
    sign-in-then-add-shortcut)."""
    # Round 83
    detail_func_start = JS_SRC.find("function corpusPanelDetail")
    detail_func_end = JS_SRC.find(
        "default:", detail_func_start,
    )
    detail_func = JS_SRC[detail_func_start:detail_func_end]
    # The signed_in_no_corpus case body must include the distinct
    # phrase "Add corpus share to my OneDrive".
    assert "'signed_in_no_corpus'" in detail_func
    sig_case_idx = detail_func.find("case 'signed_in_no_corpus'")
    assert sig_case_idx != -1
    # Take the case body up to the next case statement (or break).
    next_case = detail_func.find("case '", sig_case_idx + 30)
    if next_case == -1:
        case_body = detail_func[sig_case_idx:]
    else:
        case_body = detail_func[sig_case_idx:next_case]
    assert "Add corpus share to my OneDrive" in case_body


# ---------------------------------------------------------------------------
# 3. JS source pins: paintDeepLink unhides on both states + dynamic label
# ---------------------------------------------------------------------------


def test_paint_deeplink_unhides_on_signed_in_no_corpus():
    """``paintDeepLink`` unhides the button on
    ``signed_in_no_corpus`` (Round 83) AND ``blocked_no_onedrive``
    (legacy)."""
    # Round 83
    paint_start = JS_SRC.find("function paintDeepLink")
    paint_end = JS_SRC.find(
        "function paintSharepointPanel", paint_start,
    )
    paint_body = JS_SRC[paint_start:paint_end]
    assert "'signed_in_no_corpus'" in paint_body
    assert "'blocked_no_onedrive'" in paint_body


def test_paint_deeplink_emits_dynamic_button_label():
    """``paintDeepLink`` rewrites the button text per state:
    'Add corpus share to my OneDrive' on ``signed_in_no_corpus``,
    'Open AdoptIQ corpus folder in OneDrive' on
    ``blocked_no_onedrive``."""
    # Round 83
    paint_start = JS_SRC.find("function paintDeepLink")
    paint_end = JS_SRC.find(
        "function paintSharepointPanel", paint_start,
    )
    paint_body = JS_SRC[paint_start:paint_end]
    assert "'Add corpus share to my OneDrive'" in paint_body \
        or '"Add corpus share to my OneDrive"' in paint_body
    assert "Open AdoptIQ corpus folder in OneDrive" in paint_body


def test_paint_deeplink_uses_text_content_not_innerhtml():
    """The label rewrite uses ``textContent`` (XSS-safe) -- not
    ``innerHTML`` which could allow injection if the label ever
    flows from server-side data."""
    # Round 83
    paint_start = JS_SRC.find("function paintDeepLink")
    paint_end = JS_SRC.find(
        "function paintSharepointPanel", paint_start,
    )
    paint_body = JS_SRC[paint_start:paint_end]
    # The function MUST set textContent for the label rewrite.
    assert ".textContent = buttonText" in paint_body
    # The function MUST NOT use innerHTML for the label rewrite.
    assert ".innerHTML" not in paint_body


# ---------------------------------------------------------------------------
# 4. _r17_corpus_status_payload projects boot.signed_in_proxy
# ---------------------------------------------------------------------------


def test_status_payload_carries_signed_in_proxy_field():
    """``_r17_corpus_status_payload`` projects
    ``boot.signed_in_proxy`` from the bootstrap state."""
    # Round 83
    import app_simple
    payload = app_simple._r17_corpus_status_payload()
    assert "signed_in_proxy" in payload["boot"]


def test_status_payload_default_signed_in_proxy_is_none():
    """The default payload (no boot state populated) has
    ``boot.signed_in_proxy = None``."""
    # Round 83
    import app_simple
    # Even when the bootstrap import fails or boot state is fresh,
    # the field must be present in the JSON shape.
    payload = app_simple._r17_corpus_status_payload()
    # When bootstrap is healthy, the field is whatever the proxy
    # returned (one of the four enum values + None). We just pin
    # that the key is always present, not its value.
    val = payload["boot"]["signed_in_proxy"]
    assert val is None or val in {
        "signed_in_cisco", "signed_in_other",
        "not_signed_in", "unknown",
    }


# ---------------------------------------------------------------------------
# 5. /api/corpus/bootstrap-shortcut endpoint
# ---------------------------------------------------------------------------


@pytest.fixture
def app_client():
    """Round 83 app client fixture matching the project conftest
    pattern."""
    # Round 83
    import app_simple
    app_simple.app.config["TESTING"] = True
    app_simple.app.config["WTF_CSRF_ENABLED"] = False
    with app_simple.app.test_client() as client:
        yield client


def test_bootstrap_shortcut_endpoint_returns_share_url(app_client):
    """``GET /api/corpus/bootstrap-shortcut`` returns the configured
    SharePoint URL when the scheme passes the allow-list."""
    # Round 83
    import app_simple
    fake_url = "https://cisco-my.sharepoint.com/personal/test/folder"
    with patch.object(
        app_simple, "_r83_safe_share_url", return_value=fake_url,
    ):
        resp = app_client.get("/api/corpus/bootstrap-shortcut")
    assert resp.status_code == 200
    body = json.loads(resp.data)
    assert body["ok"] is True
    assert body["share_url"] == fake_url


def test_bootstrap_shortcut_endpoint_returns_error_when_missing(app_client):
    """When the share URL is not configured / fails validation,
    the endpoint returns 200 with ``{ok: false, error: ...}`` so
    the frontend can render an honest error."""
    # Round 83
    import app_simple
    with patch.object(
        app_simple, "_r83_safe_share_url", return_value=None,
    ):
        resp = app_client.get("/api/corpus/bootstrap-shortcut")
    assert resp.status_code == 200
    body = json.loads(resp.data)
    assert body["ok"] is False
    assert_in_source(body, "error", label='body')
    assert "share_url" not in body


def test_safe_share_url_validator_rejects_javascript_scheme():
    """``_r83_safe_share_url`` rejects ``javascript:`` URLs
    (defense-in-depth on top of Config validation)."""
    # Round 83
    import app_simple
    from config import Config
    original = getattr(Config, "ADOPTIQ_CORPUS_SHARE_URL", None)
    try:
        Config.ADOPTIQ_CORPUS_SHARE_URL = "javascript:alert(1)"
        result = app_simple._r83_safe_share_url()
        assert result is None
    finally:
        Config.ADOPTIQ_CORPUS_SHARE_URL = original


def test_safe_share_url_validator_rejects_data_scheme():
    """``_r83_safe_share_url`` rejects ``data:`` URLs."""
    # Round 83
    import app_simple
    from config import Config
    original = getattr(Config, "ADOPTIQ_CORPUS_SHARE_URL", None)
    try:
        Config.ADOPTIQ_CORPUS_SHARE_URL = "data:text/html,<script>"
        result = app_simple._r83_safe_share_url()
        assert result is None
    finally:
        Config.ADOPTIQ_CORPUS_SHARE_URL = original


def test_safe_share_url_validator_accepts_https():
    """``_r83_safe_share_url`` accepts ``https://`` URLs (the only
    allowed scheme).

    Round 84 / Build 60 update: the resolver now tightens validation
    upstream by also requiring a ``*.sharepoint.com`` host (operator
    UX promises only Cisco-managed shares can be configured, and the
    pre-R84 ``https://example.com`` would let an operator point at
    an arbitrary host). The test now uses a sharepoint host so it
    flows cleanly through both the resolver tier and the R83
    https/size guard. The R83 contract -- "only https" -- is
    preserved, just narrowed to the actually-deployable sharepoint
    domain."""
    # Round 83 / Round 84 (host-narrowing)
    import app_simple
    from config import Config
    original = getattr(Config, "ADOPTIQ_CORPUS_SHARE_URL", None)
    canonical = "https://example.sharepoint.com/folder"
    try:
        Config.ADOPTIQ_CORPUS_SHARE_URL = canonical
        result = app_simple._r83_safe_share_url()
        assert result == canonical
    finally:
        Config.ADOPTIQ_CORPUS_SHARE_URL = original


def test_safe_share_url_validator_rejects_http_only_https_allowed():
    """Round 83's allow-list is narrower than R53's deep-link list:
    only ``https://`` is allowed (not ``http://``, not ``odopen://``)
    because the bootstrap UX should always be SSL-protected."""
    # Round 83
    import app_simple
    from config import Config
    original = getattr(Config, "ADOPTIQ_CORPUS_SHARE_URL", None)
    try:
        Config.ADOPTIQ_CORPUS_SHARE_URL = "http://example.com/folder"
        result = app_simple._r83_safe_share_url()
        assert result is None, (
            "Round 83 contract: only https:// URLs allowed for the "
            "bootstrap shortcut to keep the flow SSL-protected."
        )
    finally:
        Config.ADOPTIQ_CORPUS_SHARE_URL = original


def test_safe_share_url_validator_enforces_size_cap():
    """``_r83_safe_share_url`` rejects URLs over 2048 bytes."""
    # Round 83
    import app_simple
    from config import Config
    original = getattr(Config, "ADOPTIQ_CORPUS_SHARE_URL", None)
    try:
        # 2048-byte boundary: 9 chars for "https://" + 2040-char path.
        Config.ADOPTIQ_CORPUS_SHARE_URL = "https://" + ("x" * 2040) + "/y"
        result = app_simple._r83_safe_share_url()
        assert result is None
    finally:
        Config.ADOPTIQ_CORPUS_SHARE_URL = original


def test_safe_share_url_validator_handles_none():
    """``_r83_safe_share_url`` returns ``None`` when the config
    value is ``None`` (never raises)."""
    # Round 83
    import app_simple
    from config import Config
    original = getattr(Config, "ADOPTIQ_CORPUS_SHARE_URL", None)
    try:
        Config.ADOPTIQ_CORPUS_SHARE_URL = None
        result = app_simple._r83_safe_share_url()
        assert result is None
    finally:
        Config.ADOPTIQ_CORPUS_SHARE_URL = original


# ---------------------------------------------------------------------------
# 6. analyze.html docblock mentions the new state
# ---------------------------------------------------------------------------


def test_analyze_html_docblock_mentions_signed_in_no_corpus():
    """The analyze.html corpus panel docblock MUST mention
    ``signed_in_no_corpus`` so the operator reading the template
    can find the JS-side state name without grepping intel_status.js."""
    # Round 83
    template = Path(__file__).parent.parent / "templates" / "analyze.html"
    src = template.read_text(encoding="utf-8")
    assert_in_source(src, "signed_in_no_corpus", label='src')
    # Round 96 retired baked/self-healed states; the docblock should
    # now describe the runtime-only state machine explicitly.
    assert_in_source(src, "runtime_synced", label='src')
    assert_in_source(src, "fresh_indexing", label='src')
