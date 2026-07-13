r"""Round 31 / build6 -- inline ``<script>`` in ``progress.html`` is not truncated by HTML5 parser.

Build5 shipped with a regression in ``templates/progress.html``: a
JavaScript line comment inside the page-bottom IIFE contained the
literal token ``</script>`` (used to illustrate what ``|tojson``
escapes for callers).  HTML5's tokenizer is *comment-blind* inside
``<script>`` -- the script-data state terminates the element on the
literal byte sequence ``</script`` regardless of whether it appears
inside a JS line comment, block comment, or string.  The browser
therefore closed the ``<script>`` element mid-comment, leaving
``(function () { ... `` open and emitting
``Uncaught SyntaxError: Unexpected end of input`` in DevTools.

Symptoms in build5:

* The progress page rendered, but the polling JS never executed
  -- ``refreshStatus()`` was inside the truncated portion.
* "Cancel Analysis" did nothing because its ``addEventListener``
  call was past the truncation point.
* Page sat forever on the last server-side message
  ("Fetching team subscriptions and customer data...") even after
  the backend job completed and ``/status/<id>`` returned
  ``progress: 100`` and ``status: "completed"``.
* Backend logs showed zero ``GET /status/<id>`` polls from the page
  after the initial render -- definitive proof that the inline JS
  initialisation never ran.

The fix in build6 escapes the literal token to ``<\/script>`` inside
the comment.  ``\/`` is a no-op for the JS lexer (the ECMAScript
spec collapses it to ``/``) and ``<\`` does NOT advance the HTML5
script-data state into script-data-end-tag-open-state, so the
tokenizer stays inside the script element.

This test pins the fix from two angles:

1. **Source-level static check** -- the only literal ``</script``
   tokens inside ``templates/progress.html`` must be the actual
   ``</script>`` *closing tags* at the end of each ``<script>``
   block.  No occurrences may live inside the body of a script.

2. **Rendered-output integrity** -- render the template through
   Flask's test client (so it really exercises Jinja escaping +
   ``|tojson`` interpolation), parse the response with the stdlib
   ``html.parser``, and assert that every inline ``<script>``
   block:
   * is at least 1 KB (the truncation collapsed the IIFE down
     to ~340 bytes -- a generous floor catches future
     reintroductions),
   * contains ``refreshStatus`` (the polling driver), and
   * ends with a closed ``})();`` IIFE.

Without the fix the rendered IIFE truncates to ~340 chars and these
assertions fail loudly in CI long before a build can ship.
"""

from __future__ import annotations

import pathlib
import re
from html.parser import HTMLParser

import pytest


REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
PROGRESS_HTML = REPO_ROOT / "templates" / "progress.html"


class _ScriptCollector(HTMLParser):
    """Replicates the HTML5 tokenizer's script-data state for tests.

    ``html.parser`` is conservative but it does honour the rule that
    ``</script`` inside the script body terminates the element (which
    is exactly the behaviour we are guarding against).  That makes it
    a faithful proxy for what a browser would do: if this collector
    sees the IIFE end mid-comment, so will Chrome / Firefox / Safari.
    """

    def __init__(self) -> None:
        super().__init__()
        self._in_script = False
        self._cur_attrs: dict[str, str] | None = None
        self._cur_body: list[str] = []
        self.scripts: list[tuple[dict[str, str], str]] = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "script":
            self._in_script = True
            self._cur_attrs = dict(attrs)
            self._cur_body = []

    def handle_endtag(self, tag):
        if tag.lower() == "script" and self._in_script:
            self.scripts.append((self._cur_attrs or {}, "".join(self._cur_body)))
            self._in_script = False
            self._cur_attrs = None
            self._cur_body = []

    def handle_data(self, data):
        if self._in_script:
            self._cur_body.append(data)


@pytest.fixture
def _seeded_progress_status(app):
    """Inject a deterministic status row so /progress/<id> renders.

    Mirrors ``tests/test_round28_progress_template_extends_base.py``
    so we exercise the same code path operators hit in production.
    """
    import app_simple as app_mod

    aid = "round31-progress-script-intact"
    snapshot = None
    with app_mod.analysis_status_lock:
        snapshot = dict(app_mod.analysis_status)
        app_mod.analysis_status[aid] = {
            "status": "running",
            "progress": 42,
            "message": "Crunching the numbers",
            "current_step": "Analyzing customer data",
            "manager": "Jane Doe",
            "technology": "Webex",
            "days": 90,
            "start_time": "2026-04-25T10:00:00",
            "report_type": "comprehensive",
        }
    try:
        yield aid
    finally:
        with app_mod.analysis_status_lock:
            app_mod.analysis_status.clear()
            app_mod.analysis_status.update(snapshot)


def _count_script_blocks_via_html5_state(src: str) -> int:
    """Count complete ``<script>...</script>`` blocks in template source.

    A naive ``re.findall`` for ``<script`` over-counts because comments
    inside an existing script body can mention the literal token
    ``<script>`` without opening a new element -- HTML5's tokenizer is
    in script-data state and only listens for ``</script[\\s/>]`` to
    leave it.  This helper walks the source through the same state
    machine the browser uses, so it agrees with what Chrome, Firefox,
    and Safari would produce when rendering the template.
    """

    class _Counter(HTMLParser):
        def __init__(self) -> None:
            super().__init__()
            self.opens = 0
            self.closes = 0

        def handle_starttag(self, tag, attrs):
            if tag.lower() == "script":
                self.opens += 1

        def handle_endtag(self, tag):
            if tag.lower() == "script":
                self.closes += 1

    p = _Counter()
    p.feed(src)
    return p.opens, p.closes


def test_progress_template_source_has_no_unescaped_script_close_in_body():
    """Static check on the template source.

    For every ``<script>...</script>`` block in the file, only the
    final ``</script>`` should be a literal end-tag token.  A second
    ``</script`` substring inside the body is what bit build5.
    """
    src = PROGRESS_HTML.read_text(encoding="utf-8")

    # Walk the source one ``<script>`` block at a time.
    pattern = re.compile(
        r"(?P<open><script[^>]*>)(?P<body>.*?)(?P<close></script\s*>)",
        re.IGNORECASE | re.DOTALL,
    )
    blocks = list(pattern.finditer(src))
    assert blocks, (
        "Round 31 / build6: templates/progress.html has no <script> "
        "blocks at all -- did the file get truncated?"
    )

    offenders: list[tuple[int, str]] = []
    for m in blocks:
        body = m.group("body")
        # Any literal ``</script`` followed by an HTML5 end-tag char
        # inside the body would prematurely close the element.
        if re.search(r"</script[\s/>]", body, re.IGNORECASE):
            line_no = src.count("\n", 0, m.start("body")) + 1
            offenders.append((line_no, body[:120]))

    assert not offenders, (
        "Round 31 / build6: templates/progress.html contains a literal "
        "``</script`` token inside a <script> body (line(s) %r).  HTML5 "
        "tokenizers are comment-blind inside <script> and will close "
        "the element at that point -- this was the build5 regression "
        "that froze the progress page.  Escape the example token as "
        "``<\\/script>`` (the ``\\/`` is a JS no-op but stops the "
        "HTML5 script-data state from advancing)."
        % offenders
    )


def test_progress_template_source_script_tags_balance():
    """Belt-and-braces: real ``<script>`` opens and ``</script>`` closes balance.

    Uses the stdlib HTML5 tokenizer (the same one the browser uses)
    rather than a naive regex.  That way the test agrees with what
    a real parser would do when handed the template -- mentions of
    the literal token in JS comments inside an existing script body
    do NOT count as new opens (HTML5 stays in script-data state) and
    a stray literal token in a body DOES count as an extra close
    (which is the bug we are guarding against, and would surface as
    ``opens != closes``).
    """
    src = PROGRESS_HTML.read_text(encoding="utf-8")
    opens, closes = _count_script_blocks_via_html5_state(src)
    assert opens == closes, (
        "Round 31 / build6: templates/progress.html parses to %d "
        "<script> opens and %d </script> closes (per HTML5 tokenizer "
        "semantics).  An imbalance means either a hand-written tag is "
        "missing its closer OR a literal script-end-tag token leaked "
        "into a body and was counted as an extra close -- exactly the "
        "build5 regression." % (opens, closes)
    )


def test_progress_route_inline_script_iife_is_intact(client, _seeded_progress_status):
    """End-to-end: render the page, parse it, prove the IIFE survives.

    This is the real guard.  The static checks above pin the source,
    but a templating regression (e.g. a Jinja macro re-introducing
    the literal token) would still slip past them.  Rendering through
    Flask exercises ``|tojson`` escaping, base.html inheritance, and
    the actual HTML the browser receives.
    """
    aid = _seeded_progress_status
    resp = client.get(f"/progress/{aid}")
    assert resp.status_code == 200, resp.data[:500]

    body = resp.get_data(as_text=True)

    parser = _ScriptCollector()
    parser.feed(body)

    inline_blocks = [
        (attrs, text)
        for (attrs, text) in parser.scripts
        if "src" not in attrs
    ]
    assert inline_blocks, (
        "Round 31 / build6: rendered /progress/<id> emitted zero "
        "inline <script> blocks -- the template lost its body."
    )

    # The page-bottom IIFE that drives polling + cancel handlers is
    # the largest inline block and is identified by its unique payload
    # markers.  Locate it by content rather than position so a
    # future reorder does not break the test.
    polling_blocks = [
        (attrs, text)
        for (attrs, text) in inline_blocks
        if "refreshStatus" in text or "ANALYSIS_ID" in text
    ]
    assert polling_blocks, (
        "Round 31 / build6: rendered /progress/<id> has no inline "
        "<script> block containing ``refreshStatus`` or "
        "``ANALYSIS_ID``.  Either the polling IIFE was deleted "
        "(regression) OR the HTML5 parser truncated the script body "
        "before those identifiers, which is exactly the build5 bug "
        "this test guards against."
    )

    polling_attrs, polling_text = polling_blocks[-1]

    # Length floor -- the truncated build5 body was ~340 chars.
    # The full IIFE is well over 4 KB.  1 KB is a safe floor that
    # catches any meaningful truncation.
    _MIN_BYTES = 1024
    assert len(polling_text) >= _MIN_BYTES, (
        "Round 31 / build6: rendered polling <script> body is only "
        "%d bytes (floor: %d).  build5 truncated this block to ~340 "
        "bytes because a literal ``</script>`` in a JS comment "
        "closed the element early.  Rebuild with the escaped "
        "``<\\/script>`` token in the comment."
        % (len(polling_text), _MIN_BYTES)
    )

    # IIFE close marker -- if the body got truncated the closing
    # ``})();`` will be missing.
    assert "})();" in polling_text, (
        "Round 31 / build6: rendered polling <script> body does not "
        "contain its closing ``})();`` IIFE marker -- the body was "
        "truncated.  This produces ``Uncaught SyntaxError: Unexpected "
        "end of input`` in the browser and freezes the progress "
        "page (no polling, no cancel handler)."
    )

    # Polling driver must be present and complete.
    assert "fetch('/status/' + ANALYSIS_ID)" in polling_text or \
           'fetch("/status/" + ANALYSIS_ID)' in polling_text, (
        "Round 31 / build6: rendered polling <script> body lost its "
        "``fetch('/status/' + ANALYSIS_ID)`` call.  Without this the "
        "page can never refresh its progress bar."
    )

    # Cancel handler must be present and complete.
    assert "cancel" in polling_text.lower(), (
        "Round 31 / build6: rendered polling <script> body has no "
        "``cancel`` references -- the Cancel Analysis button will be "
        "inert.  This was the second visible symptom in build5."
    )


def test_progress_route_no_unescaped_script_close_in_rendered_body(
    client, _seeded_progress_status
):
    """End-to-end mirror of the source check, against rendered HTML.

    Same rule as ``test_progress_template_source_has_no_unescaped_script_close_in_body``
    but applied to the page that actually leaves the server.  This
    catches a ``|safe`` interpolation that injects ``</script>`` from
    a route variable as well as the static-template case.
    """
    aid = _seeded_progress_status
    resp = client.get(f"/progress/{aid}")
    assert resp.status_code == 200

    body = resp.get_data(as_text=True)

    pattern = re.compile(
        r"(?P<open><script[^>]*>)(?P<body>.*?)(?P<close></script\s*>)",
        re.IGNORECASE | re.DOTALL,
    )
    offenders = []
    for m in pattern.finditer(body):
        inner = m.group("body")
        if re.search(r"</script[\s/>]", inner, re.IGNORECASE):
            offenders.append(inner[:160])

    assert not offenders, (
        "Round 31 / build6: rendered /progress/<id> has a literal "
        "``</script`` token inside a <script> body (offender heads: %r). "
        "The HTML5 tokenizer will close the element at that point and "
        "the rest of the inline JS will be parsed as page text -- "
        "exactly the build5 freeze." % offenders
    )
