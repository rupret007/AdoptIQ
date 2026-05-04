"""Round 74 / Build 48 / Phase 3 (P3): Ask AI streaming tests.

Pin the SSE streaming endpoint shape, the chunking helper, the
client-side dispatcher / fallback contract, and the conversation
history payload pass-through.  These tests do NOT exercise the
LLM end-to-end; they pin the contract so a future refactor of
``run_portfolio_grounded_ask_ai`` cannot silently break the SSE
shape that the client (and ultimately the operator) depends on.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parent.parent
_APP_SIMPLE = _REPO_ROOT / "app_simple.py"
_ASK_AI_JS = _REPO_ROOT / "static" / "js" / "ask_ai.js"
_ASK_AI_HTML = _REPO_ROOT / "templates" / "ask_ai.html"


# ---------------------------------------------------------------------------
# Backend tests
# ---------------------------------------------------------------------------


def test_streaming_endpoint_route_exists():
    """``POST /api/ask-ai-portfolio/stream`` must be registered with the
    streaming MIME / CSRF / throttle contract.
    """
    src = _APP_SIMPLE.read_text(encoding="utf-8")
    assert "/api/ask-ai-portfolio/stream" in src, (
        "Round 74 / P3: streaming route URL missing -- the dispatcher "
        "in static/js/ask_ai.js POSTs to this exact path."
    )
    # Route decorator + POST method
    pattern = (
        r"@app\.route\(\s*['\"]/api/ask-ai-portfolio/stream['\"][\s\S]*?"
        r"methods\s*=\s*\[\s*['\"]POST['\"]\s*\]"
    )
    assert re.search(pattern, src), (
        "Round 74 / P3: streaming route must be a POST endpoint "
        "(EventSource doesn't support POST + custom CSRF headers, "
        "so the client uses fetch+ReadableStream)."
    )


def test_streaming_endpoint_emits_text_event_stream_mime():
    """Response must carry ``mimetype='text/event-stream'``."""
    src = _APP_SIMPLE.read_text(encoding="utf-8")
    # The Response() call near the end of ask_ai_portfolio_stream
    # must specify the streaming MIME type.
    assert "mimetype='text/event-stream'" in src or 'mimetype="text/event-stream"' in src, (
        "Round 74 / P3: streaming endpoint must return Response with "
        "mimetype='text/event-stream' so the client's ReadableStream "
        "can decode SSE frames."
    )


def test_streaming_endpoint_disables_proxy_buffering():
    """``X-Accel-Buffering: no`` must be set so a reverse proxy (e.g.
    nginx) doesn't accumulate the entire response before flushing.
    """
    src = _APP_SIMPLE.read_text(encoding="utf-8")
    assert "X-Accel-Buffering" in src, (
        "Round 74 / P3: streaming response must set X-Accel-Buffering: no "
        "so a reverse proxy doesn't defeat the point of streaming."
    )


def test_chunk_text_for_sse_yields_word_boundary_chunks():
    """``_r74_chunk_text_for_sse`` must split at word boundaries when
    the buffer reaches the chunk size limit.
    """
    import sys
    sys.path.insert(0, str(_REPO_ROOT))
    from app_simple import _r74_chunk_text_for_sse

    text = "The quick brown fox jumps over the lazy dog. " * 5
    chunks = list(_r74_chunk_text_for_sse(text, chunk_size=20))
    assert len(chunks) > 1, "long text should yield multiple chunks"
    # No chunk should END mid-word (the trailing char is whitespace OR
    # punctuation OR the chunk is the final tail).
    for i, ch in enumerate(chunks[:-1]):
        # Allow trailing whitespace / period after a word boundary.
        last_meaningful = ch.rstrip()
        if last_meaningful:
            # The split should land after a complete word + punct.
            tail_char = ch[-1]
            assert tail_char in (' ', '\t', '\n', '\r') or tail_char.isalnum() or tail_char in '.,;:!?', (
                f"chunk {i} ended mid-word at unexpected char: {tail_char!r}"
            )
    # All chunks concatenated must equal the original text.
    assert ''.join(chunks) == text


def test_chunk_text_for_sse_handles_newlines_as_boundaries():
    """A newline inside the source must always be a chunk boundary so
    markdown block elements emit cleanly between chunks.
    """
    import sys
    sys.path.insert(0, str(_REPO_ROOT))
    from app_simple import _r74_chunk_text_for_sse

    text = "Line 1\nLine 2\nLine 3"
    chunks = list(_r74_chunk_text_for_sse(text, chunk_size=64))
    # Every newline-separated segment should appear at a chunk
    # boundary -- joined the chunks must equal the source.
    assert ''.join(chunks) == text
    # And we should have at least 2 chunks because of the explicit newlines.
    assert len(chunks) >= 2


def test_chunk_text_for_sse_handles_empty_input():
    """Empty / non-string inputs must yield no chunks."""
    import sys
    sys.path.insert(0, str(_REPO_ROOT))
    from app_simple import _r74_chunk_text_for_sse

    assert list(_r74_chunk_text_for_sse('')) == []
    assert list(_r74_chunk_text_for_sse(None)) == []
    assert list(_r74_chunk_text_for_sse(123)) == []  # type: ignore[arg-type]


def test_format_sse_event_produces_valid_sse_frame():
    """``_r74_format_sse_event`` output must follow the SSE spec:
    ``event: <name>\\ndata: <json>\\n\\n``.
    """
    import sys
    sys.path.insert(0, str(_REPO_ROOT))
    from app_simple import _r74_format_sse_event

    frame = _r74_format_sse_event('meta', {'foo': 'bar', 'n': 1})
    assert frame.startswith('event: meta\n'), (
        "SSE frame must start with the event line"
    )
    assert frame.endswith('\n\n'), (
        "SSE frame must end with a blank line (\\n\\n) "
        "to delimit frames"
    )
    assert 'data: {"foo": "bar"' in frame or 'data: {"n":' in frame, (
        "data line must carry JSON-serialised payload"
    )


def test_format_sse_event_sanitises_event_name():
    """The event name must be alphanumeric+dashes only -- a hostile
    payload name could otherwise inject a CR/LF and corrupt the frame.
    """
    import sys
    sys.path.insert(0, str(_REPO_ROOT))
    from app_simple import _r74_format_sse_event

    # Hostile event name with control chars
    frame = _r74_format_sse_event("meta\ndata: hostile", {"x": 1})
    # The hostile newline + 'data:' injection must not appear in the event line
    event_line = frame.split('\n')[0]
    assert event_line.startswith('event: '), "event line must be first"
    assert '\n' not in event_line, "event line must not carry CR/LF"


def test_streaming_endpoint_csrf_protected():
    """Same CSRF protection as the synchronous endpoint."""
    src = _APP_SIMPLE.read_text(encoding="utf-8")
    # Find the body of ask_ai_portfolio_stream
    m = re.search(
        r"def ask_ai_portfolio_stream\([^)]*\):[\s\S]*?(?=\n@app\.route|\ndef\s)",
        src,
    )
    assert m, "could not locate streaming endpoint body"
    body = m.group(0)
    assert 'validate_csrf' in body, (
        "streaming endpoint must call validate_csrf -- same posture as the sync path"
    )


def test_streaming_endpoint_uses_throttle():
    """Same per-IP throttle as the synchronous endpoint."""
    src = _APP_SIMPLE.read_text(encoding="utf-8")
    m = re.search(
        r"def ask_ai_portfolio_stream\([^)]*\):[\s\S]*?(?=\n@app\.route|\ndef\s)",
        src,
    )
    assert m, "could not locate streaming endpoint body"
    body = m.group(0)
    assert '_check_ask_ai_throttle' in body, (
        "streaming endpoint must inherit the ask-ai throttle so a runaway "
        "client cannot bypass the rate limit by switching endpoints."
    )


def test_streaming_endpoint_accepts_conversation_history():
    """Round 74 / Phase 5 (P5): the endpoint must read
    ``conversation_history`` from the request body and prepend it to
    the question via ``_r74_apply_conversation_history``.
    """
    src = _APP_SIMPLE.read_text(encoding="utf-8")
    m = re.search(
        r"def ask_ai_portfolio_stream\([^)]*\):[\s\S]*?(?=\n@app\.route|\ndef\s)",
        src,
    )
    assert m, "could not locate streaming endpoint body"
    body = m.group(0)
    assert 'conversation_history' in body, (
        "streaming endpoint must accept conversation_history in the payload"
    )
    assert '_r74_apply_conversation_history' in body, (
        "streaming endpoint must funnel history through the canonical helper"
    )


# ---------------------------------------------------------------------------
# Client-side tests (source-shape pins)
# ---------------------------------------------------------------------------


def test_ask_ai_js_defines_ask_streaming_function():
    """``_r74AskStreaming`` must exist as a callable function."""
    src = _ASK_AI_JS.read_text(encoding="utf-8")
    assert 'function _r74AskStreaming(' in src, (
        "Round 74 / P3: client must define _r74AskStreaming(question, opts)"
    )


def test_ask_ai_js_dispatcher_falls_back_to_sync():
    """``askAI`` must dispatch streaming first, then fall back to
    ``_r74AskSync`` on rejection.
    """
    src = _ASK_AI_JS.read_text(encoding="utf-8")
    # The new askAI should call _r74AskStreaming and chain a fallback.
    assert '_r74AskStreaming(question, opts).then(' in src, (
        "askAI dispatcher must invoke _r74AskStreaming and chain a fallback"
    )
    assert '_r74AskSync(question, nextOpts)' in src, (
        "askAI dispatcher must fall back to _r74AskSync on streaming failure"
    )


def test_ask_ai_js_keeps_legacy_sync_path_intact():
    """``_r74AskSync`` must still be defined so the dispatcher's
    fallback target exists.
    """
    src = _ASK_AI_JS.read_text(encoding="utf-8")
    assert 'function _r74AskSync(question, opts)' in src, (
        "Round 74 / P3: synchronous path must be preserved verbatim "
        "as _r74AskSync so the streaming fallback is always available."
    )


def test_ask_ai_js_streaming_uses_fetch_and_reader():
    """Streaming client must use fetch + ReadableStream.getReader (NOT
    EventSource, which doesn't support POST + custom headers).
    """
    src = _ASK_AI_JS.read_text(encoding="utf-8")
    assert "fetch('/api/ask-ai-portfolio/stream'" in src, (
        "streaming client must POST to /api/ask-ai-portfolio/stream via fetch"
    )
    assert 'response.body.getReader()' in src, (
        "streaming client must use ReadableStream.getReader to consume SSE"
    )
    assert 'TextDecoder' in src, (
        "streaming client must use TextDecoder to convert byte chunks to UTF-8"
    )


def test_ask_ai_js_streaming_parses_event_and_data_frames():
    """The SSE frame parser must split on ``event:`` and ``data:`` lines."""
    src = _ASK_AI_JS.read_text(encoding="utf-8")
    assert "line.indexOf('event:')" in src, "must parse 'event:' lines"
    assert "line.indexOf('data:')" in src, "must parse 'data:' lines"


def test_ask_ai_js_streaming_handles_meta_data_done_error_events():
    """The four canonical SSE events must be handled."""
    src = _ASK_AI_JS.read_text(encoding="utf-8")
    for event in ("'meta'", "'data'", "'done'", "'error'"):
        assert event in src, (
            f"streaming client must handle the {event} SSE event "
            "(server emits all four)."
        )


def test_ask_ai_js_streaming_pill_in_template():
    """The streaming-mode pill must be present in ask_ai.html so the
    client can show / hide it.
    """
    html = _ASK_AI_HTML.read_text(encoding="utf-8")
    assert 'r74StreamingPill' in html, (
        "Round 74 / P3: ask_ai.html must include the #r74StreamingPill "
        "badge so the client can show whether the answer streamed."
    )
