"""Round 73 / Phase 4 (UX-3): strict 2-option model dropdown + allow-list.

Pre-Round-73 the LLM model picker UIs accepted any string that passed
the loose ``adoptiq_settings.is_valid_model_name`` regex
(``[A-Za-z0-9._-]{1,128}``), which let the operator paste literally
any CircuIT model id -- including ones their tenant had never
provisioned, which then failed at first ``CircuitChatClient`` call
with an unfriendly authorization error.

R73 / UX-3 narrows BOTH the UI surface and the API surface to two
canonical model ids:

* ``gemini-3.1-flash-lite`` -- AdoptIQ's tested default for both Ask AI
  and report narratives (Round 77 / Build 53 flipped this from
  ``gpt-5-nano`` -- operator testing showed flash-lite delivered
  materially lower per-customer LLM latency on the comprehensive
  report's per-customer storyboard loop while preserving the
  R66/B11 + R67/B8 grounding-pass rate).
* ``gpt-5-nano`` -- the supported alternative; one-click toggle in
  every preferences surface.

The new contract has three layers, all pinned by this test file:

1. Server-side allow-list (``_R73_ALLOWED_MODEL_IDS`` frozenset in
   ``app_simple.py``) intercepts non-empty values that are not in
   the set and returns a 400 with a structured error payload that
   names the supported ids.  Empty string is intentionally still
   accepted as the legacy "clear override / fall back to env / config
   default" sentinel so a hand-edited ``settings.json`` that already
   carries an out-of-allow-list value remains loadable.
2. ``/preferences`` UI: the ``data-r69-model-input`` element is now a
   ``<select>`` carrying exactly the two ``<option>`` rows -- no
   "Default" sentinel option (the user explicitly chose the strict
   2-option form over a 3-option Default + N form).
3. Admin console (``enhanced_admin_dashboard_v2.py``) inline picker:
   matching ``<select>`` swap so a power user with admin access
   cannot bypass the UI dropdown contract.

Future expansion: when a third model is supported, expand the
frozenset AND add matching ``<option>`` rows in BOTH templates AND
update the test below in lockstep.  The lockstep is intentional --
the goal is that the allow-list cannot drift between the API layer
and either UI surface.
"""

from __future__ import annotations

from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent

EXPECTED_ALLOWED_MODEL_IDS = {"gpt-5-nano", "gemini-3.1-flash-lite"}


def _read(p: str) -> str:
    return (PROJECT_ROOT / p).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Source-shape pins: server-side allow-list + frozen set
# ---------------------------------------------------------------------------


def test_app_simple_defines_r73_allowed_model_ids_frozenset():
    """Source-shape: the assignment ``_R73_ALLOWED_MODEL_IDS = frozenset(...)``
    or ``_R73_ALLOWED_MODEL_IDS: frozenset[str] = frozenset(...)`` MUST
    be present.  We anchor on the declaration form rather than a loose
    substring scan because the explanatory comment block ABOVE the
    declaration also names ``_R73_ALLOWED_MODEL_IDS`` (intentional --
    operators reading the source see the rationale)."""
    import re
    src = _read("app_simple.py")
    pattern = re.compile(
        r"^_R73_ALLOWED_MODEL_IDS(?:\s*:\s*frozenset\[str\])?\s*=\s*frozenset\(",
        re.MULTILINE,
    )
    assert pattern.search(src), (
        "Round 73 / UX-3: _R73_ALLOWED_MODEL_IDS frozenset assignment "
        "missing in app_simple.py -- the server-side allow-list seam is "
        "not in place"
    )


def test_app_simple_r73_allow_list_contains_exactly_expected_models():
    """Import-time check -- the frozenset literal in source MUST list
    exactly the two expected ids (gpt-5-nano and gemini-3.1-flash-lite).
    """
    import app_simple

    allowed = getattr(app_simple, "_R73_ALLOWED_MODEL_IDS", None)
    assert allowed is not None, (
        "Round 73 / UX-3: app_simple._R73_ALLOWED_MODEL_IDS is undefined"
    )
    assert isinstance(allowed, frozenset), (
        f"Round 73 / UX-3: _R73_ALLOWED_MODEL_IDS is {type(allowed).__name__}, "
        "must be frozenset"
    )
    assert set(allowed) == EXPECTED_ALLOWED_MODEL_IDS, (
        f"Round 73 / UX-3: _R73_ALLOWED_MODEL_IDS = {sorted(allowed)} but "
        f"expected {sorted(EXPECTED_ALLOWED_MODEL_IDS)}"
    )


def test_app_simple_post_handler_invokes_r73_allowlist():
    """Source-shape: the POST handler MUST call the allow-list checker
    (or inline its membership test) BEFORE writing settings.json so a
    400 is returned instead of a sneaky persistence."""
    src = _read("app_simple.py")
    # The handler is _r69_handle_model_setting; locate the body and
    # confirm the allow-list short-circuit lives before save_settings.
    assert "_r69_handle_model_setting" in src
    handler_body = src.split("def _r69_handle_model_setting", 1)[1].split("\n\n\n", 1)[0]
    assert "_R73_ALLOWED_MODEL_IDS" in handler_body or "_r73_is_in_allowed_model_ids" in handler_body, (
        "Round 73 / UX-3: _r69_handle_model_setting does not consult the "
        "R73 allow-list -- a non-allow-listed value would still persist"
    )
    # Save MUST happen AFTER the allow-list check so a rejection short-
    # circuits before persistence.  Find both indices and assert order.
    idx_check = handler_body.find("_R73_ALLOWED_MODEL_IDS")
    if idx_check < 0:
        idx_check = handler_body.find("_r73_is_in_allowed_model_ids")
    idx_save = handler_body.find("save_settings")
    assert idx_check >= 0 and idx_save >= 0, (
        "Round 73 / UX-3: handler body missing one of the required calls"
    )
    assert idx_check < idx_save, (
        "Round 73 / UX-3: allow-list check fires AFTER save_settings -- "
        "rejected values will still hit the on-disk store"
    )


# ---------------------------------------------------------------------------
# Source-shape pins: UI dropdowns
# ---------------------------------------------------------------------------


def test_preferences_template_uses_select_dropdowns():
    src = _read("templates/preferences.html")
    # Two cards with data-r69-model-input must each be a <select>, not
    # a <input>.  Find each card's input element and confirm the
    # surrounding tag is <select>.
    # Quick check: there should be NO <input ... data-r69-model-input>
    # anywhere on the page, but there MUST be at least 2 <select ...>
    # tags carrying that attribute.
    assert "<input" not in src or "data-r69-model-input" not in src.split("<input", 1)[-1].split(">", 1)[0], (
        "Round 73 / UX-3: /preferences still has <input data-r69-model-input> "
        "-- replace with <select> carrying the two <option> rows"
    )
    # More rigorous: the count of <select ... data-r69-model-input>
    # must be exactly 2 (one per card).
    select_count = src.count("data-r69-model-input")
    assert select_count == 2, (
        f"Round 73 / UX-3: /preferences carries {select_count} "
        "data-r69-model-input elements; expected exactly 2 (Ask AI + Report)"
    )


def test_preferences_template_carries_both_option_values():
    src = _read("templates/preferences.html")
    for model_id in EXPECTED_ALLOWED_MODEL_IDS:
        # Each option must appear at least twice (once per card).
        # Use an exact ``value="..."`` match so a stale code/<p>
        # mention does not satisfy the assertion.
        marker = f'value="{model_id}"'
        count = src.count(marker)
        assert count >= 2, (
            f"Round 73 / UX-3: /preferences has {count} option(s) with "
            f'value="{model_id}"; expected at least 2 (one per card)'
        )


def test_preferences_template_has_no_default_sentinel_option():
    """The user explicitly picked the strict 2-option form over a
    3-option (Default + N) form.  Pin the absence of any "Default"
    or empty-value sentinel option so a future revert that adds one
    fails this test."""
    src = _read("templates/preferences.html")
    # Common "default sentinel" patterns: <option value=""> or
    # <option value="" selected>, often paired with text "Default"
    # or "Use env / config default".
    assert '<option value=""' not in src, (
        "Round 73 / UX-3: /preferences carries a <option value=''> "
        "sentinel -- the user picked the strict 2-option form, no "
        "Default option is allowed"
    )


def test_admin_template_uses_select_dropdowns():
    """Source-shape: the admin Ask AI + Report cards each carry a
    ``<select ... data-r69-admin-input>`` element.  Counting the bare
    substring is unreliable because the inline JS module also references
    ``[data-r69-admin-input]`` selectors; we instead grep for the
    HTML-attribute form (``data-r69-admin-input`` followed by whitespace
    or end-of-tag) so the test reflects actual elements rather than
    JS string literals."""
    import re
    src = _read("enhanced_admin_dashboard_v2.py")
    # HTML attribute form: data-r69-admin-input followed by whitespace,
    # newline, or '>'.  JS selector form: '[data-r69-admin-input]'.
    # We exclude the JS selector form by anchoring on the trailing
    # whitespace / newline / '>'.
    attr_pattern = re.compile(r"data-r69-admin-input(?:[\s>]|\n)")
    matches = attr_pattern.findall(src)
    assert len(matches) == 2, (
        f"Round 73 / UX-3: admin dashboard carries {len(matches)} "
        "data-r69-admin-input HTML attribute occurrences; expected "
        "exactly 2 (Ask AI + Report)"
    )
    # Both must live on a <select> tag.  Walk each match.
    cursor = 0
    select_hits = 0
    while True:
        i = src.find("data-r69-admin-input", cursor)
        if i < 0:
            break
        cursor = i + 1
        # Skip the JS selector form '[data-r69-admin-input]'.
        if i > 0 and src[i - 1] == "[":
            continue
        # Walk backwards to the opening tag (`<`).
        tag_open = src.rfind("<", max(0, i - 400), i)
        if tag_open < 0:
            continue
        tag_segment = src[tag_open:i].lower()
        assert "<select" in tag_segment, (
            "Round 73 / UX-3: admin dashboard data-r69-admin-input is on "
            f"a non-<select> element: {tag_segment!r}"
        )
        select_hits += 1
    assert select_hits == 2, (
        f"Round 73 / UX-3: expected 2 <select data-r69-admin-input> "
        f"elements; found {select_hits}"
    )


def test_admin_template_carries_both_option_values():
    src = _read("enhanced_admin_dashboard_v2.py")
    for model_id in EXPECTED_ALLOWED_MODEL_IDS:
        marker = f'value="{model_id}"'
        count = src.count(marker)
        assert count >= 2, (
            f"Round 73 / UX-3: admin dashboard has {count} option(s) with "
            f'value="{model_id}"; expected at least 2'
        )


def test_admin_template_has_no_default_sentinel_option():
    src = _read("enhanced_admin_dashboard_v2.py")
    # Look for the form area only, not the rest of the file (which may
    # legitimately have other "" patterns elsewhere).
    if "data-r69-admin-card" in src:
        start = src.find("data-r69-admin-card")
        # The two admin cards live in a contiguous ~60-line block;
        # 4000 chars is enough to cover both.
        block = src[start:start + 4000]
        assert '<option value=""' not in block, (
            "Round 73 / UX-3: admin dashboard carries a <option value=''> "
            "sentinel inside a model-picker form"
        )


# ---------------------------------------------------------------------------
# JS module: handles <select> as well as <input>
# ---------------------------------------------------------------------------


def test_r69_js_module_handles_select_change_event():
    """The shared static/js/r69_model_preferences.js module MUST bind
    BOTH ``input`` and ``change`` events on the picker control so the
    Save-locks-on-edit contract holds for the new <select> form (some
    browsers fire only ``change`` on a <select>, not ``input``)."""
    src = _read("static/js/r69_model_preferences.js")
    assert "addEventListener('change'" in src, (
        "Round 73 / UX-3: r69_model_preferences.js does not bind "
        "'change' on the picker -- the <select> form may not lock "
        "Save when the operator picks a different option"
    )
    assert "addEventListener('input'" in src, (
        "Round 73 / UX-3: r69_model_preferences.js dropped the legacy "
        "'input' event binding -- the <input type='text'> form (still "
        "supported on legacy surfaces) loses Save-lock semantics"
    )


def test_r69_js_module_handles_select_load_active():
    """The active-value loader MUST detect the <select> case and skip
    the no-op .value assignment when the persisted value is not one
    of the declared options."""
    src = _read("static/js/r69_model_preferences.js")
    assert "tagName === 'SELECT'" in src or 'tagName === "SELECT"' in src, (
        "Round 73 / UX-3: r69_model_preferences.js does not branch on "
        "input.tagName === 'SELECT' in loadActive -- a persisted value "
        "outside the dropdown options would silently no-op the assignment"
    )


def test_admin_inline_js_handles_select_change_event():
    """Admin console carries its own inline copy of the same logic
    (the admin template renders inline; no static asset chain).  Same
    contract applies -- bind ``change`` for the <select> form."""
    src = _read("enhanced_admin_dashboard_v2.py")
    # Locate the admin r69 IIFE block and confirm the change binding.
    # Use a coarse anchor and a wide window so a small refactor that
    # moves the binding within the IIFE doesn't break this test.
    iife_start = src.find("function r69BindForm")
    assert iife_start >= 0, (
        "Round 73 / UX-3: admin r69BindForm function missing"
    )
    iife_block = src[iife_start:iife_start + 4000]
    assert "addEventListener('change'" in iife_block, (
        "Round 73 / UX-3: admin inline r69BindForm does not bind "
        "'change' on the picker -- the <select> form will lose "
        "Save-lock semantics on selection change"
    )


# ---------------------------------------------------------------------------
# Runtime: POST handler enforces the allow-list
# ---------------------------------------------------------------------------


def test_post_ask_ai_model_accepts_each_allowed_value(client):
    for model_id in EXPECTED_ALLOWED_MODEL_IDS:
        resp = client.post(
            "/api/settings/ask-ai-model",
            json={"model_name": model_id},
            headers={"X-AdoptIQ-Internal": "1"},
        )
        assert resp.status_code == 200, (
            f"Round 73 / UX-3: POST /api/settings/ask-ai-model "
            f"with allow-listed value {model_id!r} returned "
            f"{resp.status_code}"
        )
        body = resp.get_json() or {}
        assert body.get("ok") is True, (
            f"Round 73 / UX-3: allow-listed POST {model_id!r} returned "
            f"ok=False: {body}"
        )


def test_post_report_model_accepts_each_allowed_value(client):
    for model_id in EXPECTED_ALLOWED_MODEL_IDS:
        resp = client.post(
            "/api/settings/report-model",
            json={"model_name": model_id},
            headers={"X-AdoptIQ-Internal": "1"},
        )
        assert resp.status_code == 200
        body = resp.get_json() or {}
        assert body.get("ok") is True


def test_post_rejects_value_outside_allowlist_with_400(client):
    """The exact regression -- a typo or unprovisioned model id MUST
    be rejected at the API layer regardless of which UI emitted the
    POST."""
    resp = client.post(
        "/api/settings/ask-ai-model",
        json={"model_name": "claude-3.5-sonnet"},
        headers={"X-AdoptIQ-Internal": "1"},
    )
    assert resp.status_code == 400, (
        f"Round 73 / UX-3: out-of-allow-list value returned "
        f"{resp.status_code}; expected 400"
    )
    body = resp.get_json() or {}
    assert body.get("ok") is False
    assert body.get("error") == "model_not_in_r73_allowlist", (
        f"Round 73 / UX-3: rejection error code wrong: {body!r}"
    )
    # Response carries the allow-list so the operator (or a future API
    # consumer) can see exactly what's permitted.
    assert sorted(body.get("allowed_model_ids") or []) == sorted(EXPECTED_ALLOWED_MODEL_IDS), (
        f"Round 73 / UX-3: rejection payload missing or incorrect "
        f"allowed_model_ids: {body!r}"
    )


def test_post_rejects_value_outside_allowlist_for_report_model_too(client):
    """Same allow-list contract on the report-model endpoint."""
    resp = client.post(
        "/api/settings/report-model",
        json={"model_name": "gpt-4o-mini"},
        headers={"X-AdoptIQ-Internal": "1"},
    )
    assert resp.status_code == 400
    body = resp.get_json() or {}
    assert body.get("error") == "model_not_in_r73_allowlist"


def test_post_still_accepts_empty_string_to_clear_override(client):
    """Empty string MUST still be accepted as the "clear override /
    fall back to env / config default" sentinel.  This back-compat is
    intentional -- a hand-edited settings.json or a power-user env
    flow needs to remain reachable."""
    resp = client.post(
        "/api/settings/ask-ai-model",
        json={"model_name": ""},
        headers={"X-AdoptIQ-Internal": "1"},
    )
    assert resp.status_code == 200, (
        "Round 73 / UX-3: empty-string POST should be accepted as the "
        "legacy 'clear override' sentinel"
    )
    body = resp.get_json() or {}
    assert body.get("ok") is True


def test_post_rejects_typo_with_helpful_detail(client):
    """The rejection payload's ``detail`` field MUST name the allowed
    ids in plain English so an operator-facing 400 surface is
    actionable rather than cryptic."""
    resp = client.post(
        "/api/settings/ask-ai-model",
        json={"model_name": "gpt-five-nano"},  # plausible typo
        headers={"X-AdoptIQ-Internal": "1"},
    )
    assert resp.status_code == 400
    body = resp.get_json() or {}
    detail = body.get("detail", "")
    for model_id in EXPECTED_ALLOWED_MODEL_IDS:
        assert model_id in detail, (
            f"Round 73 / UX-3: rejection detail missing {model_id!r}: "
            f"{detail!r}"
        )


# ---------------------------------------------------------------------------
# Runtime: /preferences renders the dropdown
# ---------------------------------------------------------------------------


def test_preferences_route_renders_two_option_dropdown(client):
    resp = client.get("/preferences")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    for model_id in EXPECTED_ALLOWED_MODEL_IDS:
        # Each model_id must appear at least twice (one per card).
        marker = f'value="{model_id}"'
        count = body.count(marker)
        assert count >= 2, (
            f"Round 73 / UX-3: rendered /preferences body has {count} "
            f"option(s) with value={model_id!r}; expected at least 2"
        )


def test_preferences_route_dropdown_carries_no_default_sentinel(client):
    resp = client.get("/preferences")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert '<option value=""' not in body, (
        "Round 73 / UX-3: rendered /preferences body carries an "
        '<option value=""> sentinel'
    )
