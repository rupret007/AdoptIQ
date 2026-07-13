"""Round 29 / L2 -- ``progress.html`` uses ``|tojson``, not ``|safe``.

The previous template body interpolated the analysis id and CSRF
token into JS as ``var ANALYSIS_ID = {{ analysis_id_json|safe }};``
where ``analysis_id_json`` was pre-built via ``json.dumps(...)`` in
the route.  That works in the common case but is fragile: anyone
who later routes a non-jsonified value into the same Jinja slot
loses the ``</script>`` escape, and the route ends up needing a
matching ``json.dumps`` for every JS-context value.

Round 29 switches the template to use Jinja's ``|tojson`` filter,
which (a) escapes ``<`` / ``>`` / ``&`` for JS-in-HTML, (b) emits a
JSON literal directly so the route only needs to pass the raw value,
and (c) is the framework-blessed pattern for JS-context interpolation
in Jinja/Flask.

This test asserts the structural markers in the template source.
"""

from __future__ import annotations

import pathlib
import re

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
PROGRESS_HTML = REPO_ROOT / "templates" / "progress.html"


def test_progress_template_uses_tojson_for_analysis_id_and_csrf():
    src = PROGRESS_HTML.read_text(encoding="utf-8")

    # Positive assertions: ``|tojson`` is applied to the raw values.
    assert re.search(r"\{\{\s*analysis_id\s*\|\s*tojson\s*\}\}", src), (
        "Round 29 / L2: templates/progress.html must interpolate "
        "``analysis_id`` via ``|tojson`` (not ``|safe`` against a "
        "pre-jsonified ``analysis_id_json``).  This is the "
        "framework-blessed pattern for dropping a Python value into "
        "a JS context safely."
    )
    assert re.search(r"\{\{\s*csrf_token_value\s*\|\s*tojson\s*\}\}", src), (
        "Round 29 / L2: templates/progress.html must interpolate "
        "``csrf_token_value`` via ``|tojson`` so a hostile token "
        "string cannot break out of the JS context."
    )

    # Negative assertions: the prior ``|safe`` patterns are gone.
    assert "analysis_id_json|safe" not in src and "analysis_id_json | safe" not in src, (
        "Round 29 / L2: templates/progress.html still references "
        "``analysis_id_json|safe``; switch to ``analysis_id|tojson`` "
        "so Jinja owns the JS-context escaping."
    )
    assert "csrf_token_json|safe" not in src and "csrf_token_json | safe" not in src, (
        "Round 29 / L2: templates/progress.html still references "
        "``csrf_token_json|safe``; switch to ``csrf_token_value|tojson``."
    )


def test_progress_template_no_remaining_safe_filter_in_js_block():
    """Belt-and-braces: the JS body inside ``progress.html`` should
    not carry any ``|safe`` filter on user-controlled scalars.  A
    ``|safe`` on a route-controlled HTML fragment is fine elsewhere
    in the file (none today), so we narrow the scan to lines that
    look like JS variable declarations.
    """
    src = PROGRESS_HTML.read_text(encoding="utf-8")
    offending = [
        line.strip()
        for line in src.splitlines()
        if "|safe" in line
        and re.search(r"\bvar\s+[A-Za-z_][A-Za-z0-9_]*\s*=", line)
    ]
    assert not offending, (
        "Round 29 / L2: templates/progress.html still uses ``|safe`` "
        "in a ``var FOO = ...`` JS-context interpolation: %r.  "
        "Switch to ``|tojson``." % offending
    )
