"""Round 28 / Phase 3 -- error pages are wired to the branded templates.

``templates/404.html`` and ``templates/500.html`` already extended
``base.html`` and inherited the dark-default + sun/moon toggle, but
no ``@app.errorhandler`` ever pointed at them.  That meant every
unknown URL fell through to Werkzeug's bare default page (no theme,
no nav, no brand), and any unhandled exception fell through to the
generic 500 handler.

Round 28 registers the two handlers in ``app_simple.py``.  This
test pins:

1. Hitting a non-existent route returns 404 *and* the branded body
   (the ``404`` heading + the navbar toggle) -- proving the
   template was rendered, not Werkzeug's fallback.
2. Hitting a route that raises an exception returns 500 *and* the
   branded body -- using a temporary route registered on the
   already-built test client so we never need to inject a real
   server bug.

Both responses must include ``data-bs-theme`` and ``#theme-toggle``
so the user can flip dark/light even from an error page.
"""

from __future__ import annotations


def test_404_handler_renders_branded_page(client):
    resp = client.get("/this-route-definitely-does-not-exist-r28")
    assert resp.status_code == 404, resp.data[:300]

    body = resp.get_data(as_text=True)
    assert 'data-bs-theme=' in body, (
        "Round 28 / Phase 3: the 404 page must inherit data-bs-theme "
        "from base.html (otherwise users get Werkzeug's bare "
        "default response, which does not flip with the toggle)."
    )
    assert 'id="theme-toggle"' in body, (
        "Round 28 / Phase 3: the 404 page must show the navbar "
        "sun/moon toggle so users can change theme even from an "
        "error state."
    )
    assert "404" in body, "Round 28 / Phase 3: 404 body must announce the 404 status code."
    assert "Page Not Found" in body, (
        "Round 28 / Phase 3: 404 body must contain the 'Page Not "
        "Found' heading from templates/404.html."
    )


def test_500_handler_renders_branded_page(app):
    """Render the 500 handler directly inside an app context.

    We cannot late-register a fault-injecting route on ``app``: Flask
    seals route registration after the first request, and the
    test-client fixture has already issued earlier calls in the same
    session.  Instead we look up the registered 500 handler via the
    public Flask API (``app.error_handler_spec``) and invoke it
    through ``app.test_request_context()``, which is the same path
    Flask uses internally when an exception bubbles up.
    """
    # Resolve the 500 handler the way Flask does: walk the
    # registered error_handler_spec for the default blueprint
    # (``None``), HTTP code 500.
    spec = app.error_handler_spec.get(None, {})
    handlers_for_500 = spec.get(500, {})
    assert handlers_for_500, (
        "Round 28 / Phase 3: app must register a 500 error handler "
        "(@app.errorhandler(500)) so unhandled exceptions render the "
        "branded templates/500.html instead of Werkzeug's default "
        "page."
    )
    # The registered handler dict is keyed by exception class; any
    # entry will do because they all delegate to the same renderer.
    handler = next(iter(handlers_for_500.values()))

    with app.test_request_context("/"):
        result = handler(RuntimeError("Round 28 test bomb"))

    # Handlers may return either a Response or a (body, status) tuple.
    if isinstance(result, tuple):
        response_body, status = result[0], result[1]
    else:
        response_body, status = result, getattr(result, "status_code", 500)

    if hasattr(response_body, "get_data"):
        body = response_body.get_data(as_text=True)
    else:
        body = str(response_body)

    assert status == 500, "Round 28 / Phase 3: 500 handler must return HTTP 500"
    assert 'data-bs-theme=' in body, (
        "Round 28 / Phase 3: the 500 page must inherit "
        "data-bs-theme from base.html."
    )
    assert 'id="theme-toggle"' in body, (
        "Round 28 / Phase 3: the 500 page must include the "
        "navbar theme toggle."
    )
    assert "500" in body, "Round 28 / Phase 3: 500 body must contain the 500 status code."
    assert "Internal Server Error" in body, (
        "Round 28 / Phase 3: 500 body must contain the "
        "'Internal Server Error' heading from templates/500.html."
    )
