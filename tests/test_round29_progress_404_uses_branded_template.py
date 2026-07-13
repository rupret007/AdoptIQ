"""Round 29 / L1 -- ``progress()`` 404 paths render the branded template.

Three branches in ``app_simple.progress()`` previously returned a raw
``<h1>Analysis not found</h1>`` f-string with HTTP 404, which bypassed
the Round-28 ``@app.errorhandler(404)`` and showed an unthemed page
without the navbar / sun-moon toggle.

Round 29 swaps those returns to ``abort(404)`` so the same handler
that catches unknown URLs also catches "analysis id is well-formed
but unknown" hits, giving the user a consistent themed experience.

This test exercises a well-formed-but-unknown analysis id (a UUID
that has never been registered) and asserts the response is themed:
- HTTP status is 404.
- Body comes from ``templates/404.html`` -> ``base.html`` (so
  ``data-bs-theme=`` and ``id="theme-toggle"`` are present).
- Body does NOT contain the legacy raw ``<h1>Analysis not found</h1>``
  marker.
"""

from __future__ import annotations


def test_progress_unknown_id_renders_branded_404(client):
    # ``_is_valid_analysis_id`` accepts UUID-like strings, so this
    # passes the format gate (avoiding the inline 400 path) and
    # falls through to the unknown-id branch we are testing.
    bogus = "00000000-0000-4000-8000-000000000000"
    resp = client.get(f"/progress/{bogus}")

    assert resp.status_code == 404, (
        "Round 29 / L1: a well-formed but unknown analysis id must "
        "abort(404) so the branded handler renders templates/404.html. "
        "Got status %s." % resp.status_code
    )

    body = resp.get_data(as_text=True)

    assert 'data-bs-theme=' in body, (
        "Round 29 / L1: the 404 body for an unknown analysis id must "
        "inherit data-bs-theme from base.html (proves the branded "
        "template rendered, not the prior raw f-string)."
    )
    assert 'id="theme-toggle"' in body, (
        "Round 29 / L1: the 404 body for an unknown analysis id must "
        "include the navbar sun/moon toggle from base.html."
    )
    assert "Page Not Found" in body, (
        "Round 29 / L1: the 404 body must contain the 'Page Not "
        "Found' heading from templates/404.html."
    )

    # Negative assertion: the legacy f-string body must not appear.
    legacy_marker = "<h1>Analysis not found</h1>"
    assert legacy_marker not in body, (
        "Round 29 / L1: progress() still emits the legacy raw "
        "'<h1>Analysis not found</h1>' f-string; replace the "
        "return with ``abort(404)`` so the branded handler runs."
    )
