/* AdoptIQ - Ask AI portfolio page client.
 *
 * Round 8 / Phase 5.2: extracted from templates/ask_ai.html so the
 * site-wide CSP no longer needs ``script-src 'unsafe-inline'`` to
 * load this page.  The page-level CSP override in ask_ai.html drops
 * ``'unsafe-inline'`` from script-src and serves this file from
 * ``'self'`` instead.  No Jinja substitution needed here -- the
 * CSRF token is read from the ``<meta name="csrf-token">`` element
 * already present in the page head.
 */
'use strict';

document.addEventListener('DOMContentLoaded', function() {
    // Round 68 / Build 42 (C7): module-level evidence index for the
    // current answer.  Populated from ``data.evidence_index`` when
    // an answer arrives; consumed by ``_r68RenderCitationBadge`` to
    // render clickable badges with snippet popovers for each
    // ``[Source: <ID>]`` marker in the answer text.
    var _r68EvidenceIndex = {};

    function _r68SetEvidenceIndex(records) {
        _r68EvidenceIndex = {};
        if (!Array.isArray(records)) { return; }
        for (var i = 0; i < records.length; i++) {
            var rec = records[i];
            if (!rec || typeof rec !== 'object') { continue; }
            var sid = (rec.source_id != null) ? String(rec.source_id) : '';
            if (!sid) { continue; }
            _r68EvidenceIndex[sid] = rec;
        }
    }

    // Round 68 / Build 42 (C7): build a clickable citation badge for
    // a single source ID.  The badge shows the ID; on click it
    // toggles an inline popover containing the snippet, source
    // type, and customer.  Falls through to a plain "[Source: ID]"
    // text node when the index has no entry (e.g. corpus citation
    // that the LLM produced but the validator rejected from
    // appearing in the index).  Always XSS-safe -- every node uses
    // textContent / dataset.
    function _r68BuildCitationBadge(sid, originalText) {
        var rec = _r68EvidenceIndex[sid];
        var span = document.createElement('span');
        span.className = 'r68-citation-wrapper d-inline-block';
        var badge = document.createElement('button');
        badge.type = 'button';
        badge.className = 'r68-citation-badge badge bg-light text-primary border'
            + ' me-1 align-baseline';
        badge.style.cssText = 'cursor:pointer;font-size:0.78em;padding:0.2em 0.45em;'
            + 'font-family:inherit;font-weight:600;';
        badge.textContent = sid;
        badge.title = rec
            ? (rec.source_type || 'evidence') + ': click to view snippet'
            : (originalText + ' (no snippet -- evidence record outside the index)');
        var popover = document.createElement('span');
        popover.className = 'r68-citation-popover bg-light border rounded shadow-sm small';
        popover.style.cssText = 'display:none;margin-left:0.5em;padding:0.5em 0.75em;'
            + 'max-width:520px;font-style:normal;white-space:normal;';
        if (rec) {
            var headerRow = document.createElement('div');
            headerRow.className = 'd-flex flex-wrap gap-2 mb-1 small text-muted';
            var typeBadge = document.createElement('span');
            typeBadge.className = 'badge bg-secondary text-light';
            typeBadge.textContent = String(rec.source_type || 'evidence');
            headerRow.appendChild(typeBadge);
            if (rec.customer) {
                var custBadge = document.createElement('span');
                custBadge.className = 'badge bg-info text-dark';
                custBadge.textContent = String(rec.customer);
                headerRow.appendChild(custBadge);
            }
            if (rec.timestamp) {
                var tsBadge = document.createElement('span');
                tsBadge.className = 'badge bg-light text-muted border';
                tsBadge.textContent = String(rec.timestamp);
                headerRow.appendChild(tsBadge);
            }
            popover.appendChild(headerRow);
            var snip = document.createElement('div');
            snip.className = 'text-body small';
            snip.textContent = String(rec.snippet || '(no snippet available)');
            popover.appendChild(snip);
        } else {
            popover.textContent = 'Evidence record not in this answer\u2019s index.  The LLM may have'
                + ' produced this citation from corpus knowledge that was rejected by the validator.';
        }
        badge.addEventListener('click', function () {
            var open = popover.style.display !== 'none';
            // Close any other open popovers in the document so only
            // one shows at a time.
            try {
                document.querySelectorAll('.r68-citation-popover').forEach(function (other) {
                    if (other !== popover) { other.style.display = 'none'; }
                });
            } catch (_) { /* noop */ }
            popover.style.display = open ? 'none' : 'inline-block';
        });
        span.appendChild(badge);
        span.appendChild(popover);
        return span;
    }

    // Round 6 / Phase 2.2: build DOM via createElement / textContent
    // instead of escape-then-rewrite-as-HTML.  The previous escape-then-
    // .replace pipeline was correct, but a single mis-ordered line in a
    // future change would re-introduce XSS.  This implementation only
    // EVER sets text via .textContent and creates element nodes
    // explicitly, so user/answer text can never become live HTML.
    //
    // Round 68 / Build 42 (C7): the regex now also matches
    // ``[Source: <IDs>]`` markers and replaces them with clickable
    // citation badges.  IDs may be comma-separated (the LLM emits
    // ``[Source: A, B, C]`` for compound claims).
    function appendInline(parent, text) {
        if (text === '' || text == null) return;
        var idx = 0;
        // Triple-arm regex: bold | code | source citation.  Citation
        // is greedy on the inner ``[Source: ...]`` content so a
        // multi-id citation gets one badge per id.
        var re = /(\*\*([^*]+?)\*\*|`([^`]+)`|\[Source:\s*([^\]]+)\])/g;
        var m;
        while ((m = re.exec(text)) !== null) {
            if (m.index > idx) {
                parent.appendChild(document.createTextNode(text.slice(idx, m.index)));
            }
            if (m[2] !== undefined) {
                var strong = document.createElement('strong');
                strong.textContent = m[2];
                parent.appendChild(strong);
            } else if (m[3] !== undefined) {
                var code = document.createElement('code');
                code.className = 'bg-light px-1 rounded';
                code.textContent = m[3];
                parent.appendChild(code);
            } else if (m[4] !== undefined) {
                // Citation marker -- split on comma + render one
                // badge per ID.  Preserve a leading '[' / trailing
                // ']' as plain text for visual continuity.
                parent.appendChild(document.createTextNode('['));
                var ids = String(m[4]).split(',');
                for (var bi = 0; bi < ids.length; bi++) {
                    var sid = String(ids[bi] || '').trim();
                    if (!sid) { continue; }
                    if (bi > 0) {
                        parent.appendChild(document.createTextNode(', '));
                    }
                    parent.appendChild(_r68BuildCitationBadge(sid, m[0]));
                }
                parent.appendChild(document.createTextNode(']'));
            }
            idx = m.index + m[0].length;
        }
        if (idx < text.length) {
            parent.appendChild(document.createTextNode(text.slice(idx)));
        }
    }

    // Round 74 / Phase 2 (P2): allow-list of HTML tags + attributes
    // we permit through DOMPurify when rendering an LLM-generated
    // markdown answer.  The list is intentionally tight: every entry
    // is what marked@11 emits for the markdown subset our prompt
    // template asks the LLM to use (headings, paragraphs, lists,
    // tables, fenced code, blockquotes, inline emphasis, links).
    // We deliberately omit ``img``, ``iframe``, ``style``, ``script``,
    // ``svg``, ``form``, etc. so a hypothetical model jailbreak that
    // injected raw HTML cannot smuggle privileged surface area into
    // the page even if the bare markdown survives the sanitizer.
    var _R74_MD_ALLOWED_TAGS = [
        'a', 'b', 'blockquote', 'br', 'code', 'em', 'h1', 'h2', 'h3',
        'h4', 'h5', 'h6', 'hr', 'i', 'li', 'ol', 'p', 'pre', 'span',
        'strong', 'table', 'tbody', 'td', 'th', 'thead', 'tr', 'ul'
    ];
    var _R74_MD_ALLOWED_ATTRS = [
        'href', 'target', 'rel', 'title', 'class', 'tabindex', 'role',
        'data-source-id'
    ];

    // Round 74 / Phase 2 (P2): DOMPurify config object cached once so
    // we do not allocate it per-render.  ``USE_PROFILES`` is omitted
    // intentionally -- we want the explicit allow-list above, not the
    // bundled HTML profile (which lets through ``form`` and ``input``).
    var _R74_DOMPURIFY_CONFIG = {
        ALLOWED_TAGS: _R74_MD_ALLOWED_TAGS,
        ALLOWED_ATTR: _R74_MD_ALLOWED_ATTRS,
        ALLOW_DATA_ATTR: false,
        FORBID_TAGS: ['style', 'script', 'iframe', 'object', 'embed', 'form'],
        FORBID_ATTR: ['style', 'onerror', 'onload', 'onclick']
    };

    // Round 74 / Phase 2 (P2): convert raw markdown text into a
    // sanitised HTML string.  Returns ``null`` if either marked or
    // DOMPurify is unavailable so the caller can fall back to the
    // line-by-line ``formatAnswerInto`` renderer (CDN blocked,
    // offline DMG run, future CSP tightening, etc.).
    function _r74RenderMarkdownSafe(rawText) {
        if (!rawText || typeof rawText !== 'string') { return ''; }
        if (typeof window.marked === 'undefined' || typeof window.DOMPurify === 'undefined') {
            return null;
        }
        try {
            // marked@11: ``parse`` returns the rendered HTML string.
            // ``gfm: true`` (default) gives us markdown tables; we
            // also enable ``breaks: false`` so soft newlines stay as
            // inline whitespace (matches typical markdown semantics).
            // ``mangle: false`` and ``headerIds: false`` are R74-safe
            // defaults that prevent marked from emitting auto-generated
            // ids that would clash with our own DOM ids.
            var html = window.marked.parse(rawText, {
                gfm: true,
                breaks: false,
                mangle: false,
                headerIds: false,
                pedantic: false
            });
            return window.DOMPurify.sanitize(html, _R74_DOMPURIFY_CONFIG);
        } catch (mdErr) {
            // Defensive: any error in marked or DOMPurify must NOT
            // crash the answer pipeline -- fall back to the legacy
            // renderer.  Logged at debug so the operator can spot it
            // in devtools without surfacing in the user UI.
            try {
                if (window.console && window.console.debug) {
                    window.console.debug('[R74] markdown render failed, falling back', mdErr);
                }
            } catch (_) { /* noop */ }
            return null;
        }
    }

    // Round 74 / Phase 2 (P2): build the canonical R74 source-badge
    // span for a single citation ID.  The badge carries:
    //
    //   - ``r74-source-badge`` class (CSS hook for the clickable look)
    //   - ``data-source-id`` attribute (read by Phase 4's drawer)
    //   - ``tabindex="0"`` + ``role="button"`` so keyboard users can
    //     focus + Enter/Space to activate the badge
    //
    // Phase 4 wires the click + keydown handlers; Phase 2 just renders
    // the badge so the markup is in place when the drawer module loads.
    function _r74BuildSourceBadge(sid, originalText) {
        var span = document.createElement('span');
        span.className = 'r74-source-badge';
        span.setAttribute('data-source-id', String(sid));
        span.setAttribute('tabindex', '0');
        span.setAttribute('role', 'button');
        span.setAttribute(
            'aria-label',
            'Show evidence record for source ' + String(sid)
        );
        // textContent is XSS-safe; preserves the original ``[Source: ID]``
        // appearance so a user reading the answer sees the same
        // citation literal they would in plain markdown.
        span.textContent = String(originalText || ('[Source: ' + sid + ']'));
        return span;
    }

    // Round 74 / Phase 2 (P2): walk a freshly-rendered DOM subtree and
    // replace ``[Source: ID]`` (or ``[Source: A, B, C]``) text-node
    // matches with the new R74 source-badge spans.  Citations may
    // appear inside paragraphs, list items, table cells, blockquotes,
    // etc. -- we recursively walk every text node and split it on the
    // citation pattern.  Element nodes are visited recursively but
    // their attribute values are NOT touched (only the visible text
    // content of text nodes).
    //
    // Idempotent: running the walker twice on the same subtree is a
    // no-op (badges are <span> elements so subsequent walks see them
    // as element nodes, not text nodes -- they get descended into and
    // their textContent is examined, but the badge's textContent
    // already starts with ``[Source:`` so the regex would re-match.
    // To prevent double-wrapping we skip any text node whose parent
    // already carries the ``r74-source-badge`` class).
    function _r74PostProcessSourceBadges(rootEl) {
        if (!rootEl) { return; }
        var citationRe = /\[Source:\s*([^\]]+)\]/g;
        function visit(node) {
            if (!node) { return; }
            if (node.nodeType === 3) { // Node.TEXT_NODE
                // Idempotency guard: don't re-wrap text inside an
                // already-built badge.
                if (node.parentNode &&
                    node.parentNode.classList &&
                    node.parentNode.classList.contains('r74-source-badge')) {
                    return;
                }
                var text = node.nodeValue;
                if (!text || text.indexOf('[Source:') < 0) { return; }
                var idx = 0;
                var frag = document.createDocumentFragment();
                var m;
                citationRe.lastIndex = 0;
                while ((m = citationRe.exec(text)) !== null) {
                    if (m.index > idx) {
                        frag.appendChild(document.createTextNode(text.slice(idx, m.index)));
                    }
                    var ids = String(m[1]).split(',');
                    var badgeIds = [];
                    for (var bi = 0; bi < ids.length; bi++) {
                        var sid = String(ids[bi] || '').trim();
                        if (sid) { badgeIds.push(sid); }
                    }
                    if (badgeIds.length === 0) {
                        // Empty citation -- preserve the literal so
                        // the user sees the malformed marker rather
                        // than swallowing it silently.
                        frag.appendChild(document.createTextNode(m[0]));
                    } else {
                        // ONE badge spans the whole ``[Source: A, B]``
                        // marker; data-source-id carries the comma-
                        // separated list so the drawer can fan out.
                        frag.appendChild(_r74BuildSourceBadge(
                            badgeIds.join(','), m[0]
                        ));
                    }
                    idx = m.index + m[0].length;
                }
                if (idx < text.length) {
                    frag.appendChild(document.createTextNode(text.slice(idx)));
                }
                if (node.parentNode) {
                    node.parentNode.replaceChild(frag, node);
                }
                return;
            }
            if (node.nodeType !== 1) { return; } // ELEMENT_NODE only
            if (node.classList && node.classList.contains('r74-source-badge')) {
                // Don't descend into our own badges (idempotency).
                return;
            }
            // Snapshot childNodes because the walker mutates the tree.
            var children = Array.prototype.slice.call(node.childNodes);
            for (var ci = 0; ci < children.length; ci++) {
                visit(children[ci]);
            }
        }
        visit(rootEl);
    }

    function formatAnswerInto(targetEl, text) {
        while (targetEl.firstChild) targetEl.removeChild(targetEl.firstChild);
        if (!text || typeof text !== 'string') return;
        // Round 74 / Phase 2 (P2): try the marked + DOMPurify render
        // path first.  If either library is unavailable (CDN blocked,
        // offline DMG run, etc.) ``_r74RenderMarkdownSafe`` returns
        // ``null`` and we fall back to the pre-R74 line-by-line
        // renderer which is XSS-safe by construction (every text
        // sink is .textContent / createTextNode).
        var sanitisedHtml = _r74RenderMarkdownSafe(text);
        if (sanitisedHtml !== null) {
            // ``innerHTML = sanitised`` is safe because DOMPurify
            // stripped every script / event-handler / forbidden tag
            // BEFORE it landed here.  We then walk the DOM to swap
            // ``[Source: ID]`` literals for clickable badges (which
            // are themselves XSS-safe -- the ID is set via dataset /
            // textContent, never innerHTML).
            targetEl.innerHTML = sanitisedHtml;
            try {
                _r74PostProcessSourceBadges(targetEl);
            } catch (_) { /* never fail the answer on badge wiring */ }
            return;
        }
        // Fallback: pre-R74 line-by-line renderer.  Preserved
        // verbatim so a CDN outage / offline run still ships a
        // readable answer.
        var lines = text.split(/\r?\n/);
        for (var i = 0; i < lines.length; i++) {
            var line = lines[i];
            var heading = null;
            var m;
            if ((m = /^#### (.+)$/.exec(line)) !== null) {
                heading = document.createElement('h6');
                heading.className = 'mb-1 mt-3 text-muted';
                heading.textContent = m[1];
            } else if ((m = /^### (.+)$/.exec(line)) !== null) {
                heading = document.createElement('h6');
                heading.className = 'mb-2 mt-3';
                heading.textContent = m[1];
            } else if ((m = /^## (.+)$/.exec(line)) !== null) {
                heading = document.createElement('h5');
                heading.className = 'mb-2 mt-3 text-primary';
                heading.textContent = m[1];
            } else if ((m = /^# (.+)$/.exec(line)) !== null) {
                heading = document.createElement('h4');
                heading.className = 'mb-2 mt-4 text-primary';
                heading.textContent = m[1];
            } else if (/^---$/.test(line)) {
                var hr = document.createElement('hr');
                hr.className = 'my-3';
                targetEl.appendChild(hr);
                continue;
            }
            if (heading) {
                targetEl.appendChild(heading);
                continue;
            }
            var prefixSpan = null;
            var rest = line;
            var num;
            if ((num = /^(\d+\.\s+)/.exec(line)) !== null) {
                prefixSpan = document.createElement('span');
                prefixSpan.className = 'text-primary fw-bold';
                prefixSpan.textContent = num[1].trim();
                rest = line.slice(num[1].length);
            } else if (/^[\-\*]\s+/.test(line)) {
                prefixSpan = document.createElement('span');
                prefixSpan.className = 'text-primary me-1';
                prefixSpan.textContent = '\u25CF';
                rest = line.replace(/^[\-\*]\s+/, '');
            }
            if (prefixSpan) {
                targetEl.appendChild(prefixSpan);
                targetEl.appendChild(document.createTextNode(' '));
            }
            appendInline(targetEl, rest);
            if (i < lines.length - 1) {
                targetEl.appendChild(document.createElement('br'));
            }
        }
    }

    var questionInput = document.getElementById('aiQuestion');
    var askBtn = document.getElementById('askBtn');
    var answerArea = document.getElementById('answerArea');
    var loadingState = document.getElementById('loadingState');
    var loadingDetail = document.getElementById('loadingDetail');
    var answerContent = document.getElementById('answerContent');
    var contextInfo = document.getElementById('contextInfo');
    var contextDetail = document.getElementById('contextDetail');
    var ungroundedBanner = document.getElementById('ungroundedBanner');
    var ungroundedDetail = document.getElementById('ungroundedDetail');
    var groundedFailedBanner = document.getElementById('groundedFailedBanner');
    var groundedFailedDetail = document.getElementById('groundedFailedDetail');
    var legacyOptInBtn = document.getElementById('legacyOptInBtn');
    var truncationBanner = document.getElementById('truncationBanner');
    var truncationDetail = document.getElementById('truncationDetail');
    var partialDataBanner = document.getElementById('partialDataBanner');
    var partialDataList = document.getElementById('partialDataList');
    // Round 68 / Build 42 (C5): debug chip elements (may be null on
    // older base.html bundles -- handle gracefully).
    var r68DebugChip = document.getElementById('r68DebugChip');
    var r68DebugChipMethod = document.getElementById('r68DebugChipMethod');
    var r68DebugChipId = document.getElementById('r68DebugChipId');
    var r68DebugChipCopyBtn = document.getElementById('r68DebugChipCopyBtn');
    // Round 69 / Build 43: model-name pill (may be null on older
    // ask_ai.html bundles -- handle gracefully like the rest of the chip).
    var r69DebugChipModel = document.getElementById('r69DebugChipModel');
    var r68LastQueryId = '';
    var lastAskedQuestion = '';

    function hideAllBanners() {
        ungroundedBanner.style.display = 'none';
        groundedFailedBanner.style.display = 'none';
        truncationBanner.style.display = 'none';
        partialDataBanner.style.display = 'none';
        // Round 68 / Build 42 (C5): also hide the debug chip on
        // every new question; it'll be re-shown when the answer
        // lands.  Otherwise a stale debug ID from the previous
        // answer would briefly show during the loading spinner.
        if (r68DebugChip) { r68DebugChip.style.display = 'none'; }
    }

    // Round 68 / Build 42 (C5): render the debug-chip footer when
    // an answer lands.  ``query_id`` and ``retrieval_method`` come
    // from the server payload (populated by ``app_simple.py`` via
    // ``_record_ask_ai_query_diag``).  We use textContent (XSS-safe)
    // throughout because both fields originate from server data
    // and we don't want any HTML interpretation.
    function _r68RenderDebugChip(data) {
        if (!r68DebugChip || !r68DebugChipMethod || !r68DebugChipId) { return; }
        var qid = (data && typeof data.query_id === 'string') ? data.query_id : '';
        var method = (data && typeof data.retrieval_method === 'string')
            ? data.retrieval_method : '';
        if (!qid && !method) {
            r68DebugChip.style.display = 'none';
            return;
        }
        r68LastQueryId = qid;
        r68DebugChipMethod.textContent = 'retrieval: ' + (method || 'unknown');
        // Truncate the visible debug ID for chrome reasons but keep
        // the full value in r68LastQueryId for the copy button.
        var displayId = qid.length > 18 ? (qid.slice(0, 8) + '...' + qid.slice(-6)) : qid;
        r68DebugChipId.textContent = 'debug: ' + (displayId || '-');
        r68DebugChipId.title = qid || '';
        // Round 69 / Build 43: surface the active Ask AI model name.
        // ``model_name`` is populated by the server (Round 69 / Build 43);
        // when missing (older server builds) the pill stays hidden.
        if (r69DebugChipModel) {
            var modelName = (data && typeof data.model_name === 'string')
                ? data.model_name : '';
            if (modelName) {
                r69DebugChipModel.textContent = 'model: ' + modelName;
                r69DebugChipModel.title = modelName;
                r69DebugChipModel.style.display = '';
            } else {
                r69DebugChipModel.style.display = 'none';
            }
        }
        r68DebugChip.style.display = '';
    }

    // Round 68 / Build 42 (C5): copy-to-clipboard for the debug ID.
    // Falls back to a synchronous selection method when the async
    // ``navigator.clipboard`` API is unavailable (e.g. when the page
    // is loaded over a non-secure context, or in older browsers).
    if (r68DebugChipCopyBtn) {
        r68DebugChipCopyBtn.addEventListener('click', function () {
            var qid = r68LastQueryId || '';
            if (!qid) {
                _r68ShowToast('No debug ID to copy yet.', 'error');
                return;
            }
            try {
                if (navigator.clipboard && navigator.clipboard.writeText) {
                    navigator.clipboard.writeText(qid).then(function () {
                        _r68ShowToast('Debug ID copied to clipboard.', 'success');
                    }, function () {
                        _r68ShowToast('Could not copy debug ID -- copy manually: ' + qid, 'error');
                    });
                } else {
                    var ta = document.createElement('textarea');
                    ta.value = qid;
                    ta.style.position = 'fixed';
                    ta.style.opacity = '0';
                    document.body.appendChild(ta);
                    ta.select();
                    try { document.execCommand('copy'); } catch (_) { /* noop */ }
                    document.body.removeChild(ta);
                    _r68ShowToast('Debug ID copied to clipboard.', 'success');
                }
            } catch (_e) {
                _r68ShowToast('Could not copy debug ID -- copy manually: ' + qid, 'error');
            }
        });
    }

    // Round 68 / Build 42 (C3): client-side error classification for
    // the Ask AI flow.  Pre-R68 every failure surfaced as the same
    // "Error: <data.error>" line which collapsed transient,
    // operator-actionable, and hard-failure cases into one
    // unhelpful message.  This map drives:
    //   * the retry-eligible decision below (transient = retryable),
    //   * the operator-facing toast per kind, and
    //   * the structured ``error_kind`` field of any
    //     diag log we surface.
    var R68_ERROR_KINDS = {
        rate_limit_429: { retryable: true,  toast: 'AI service throttled (HTTP 429); retrying...' },
        llm_timeout:    { retryable: true,  toast: 'AI service timed out; retrying...' },
        server_5xx:     { retryable: true,  toast: 'AI service returned a server error; retrying...' },
        network:        { retryable: true,  toast: 'Network error; retrying...' },
        client_abort:   { retryable: false, toast: 'Request was cancelled.' },
        content_filter: { retryable: false, toast: 'AI service blocked the question for content policy.  Rephrase the question.' },
        invalid_input:  { retryable: false, toast: 'Question rejected as invalid input.  Try a more specific phrasing.' },
        no_evidence:    { retryable: false, toast: 'No evidence records were available for this question.' },
        unknown:        { retryable: false, toast: 'AI service returned an unexpected error.' }
    };

    function _r68ClassifyError(httpStatus, data, fetchErr) {
        // Classify in priority order: explicit server-side hint
        // beats inferred kind beats HTTP status beats fetch error.
        if (data && typeof data.error_kind === 'string' && R68_ERROR_KINDS[data.error_kind]) {
            return data.error_kind;
        }
        if (fetchErr) {
            if (fetchErr.name === 'AbortError' || /aborted/i.test(String(fetchErr.message || ''))) {
                return 'client_abort';
            }
            return 'network';
        }
        if (typeof httpStatus === 'number') {
            if (httpStatus === 429) { return 'rate_limit_429'; }
            if (httpStatus === 408) { return 'llm_timeout'; }
            if (httpStatus >= 500 && httpStatus < 600) { return 'server_5xx'; }
            if (httpStatus === 422) { return 'invalid_input'; }
        }
        // Heuristic on server-side ``error`` / ``reason`` strings.
        var blob = ((data && (data.error || data.reason)) || '').toString().toLowerCase();
        if (/timeout|timed.?out/.test(blob)) { return 'llm_timeout'; }
        if (/rate.?limit|429/.test(blob)) { return 'rate_limit_429'; }
        if (/content.?filter|policy/.test(blob)) { return 'content_filter'; }
        if (/no evidence|no verifiable/.test(blob)) { return 'no_evidence'; }
        return 'unknown';
    }

    function _r68ShowToast(message, kind) {
        // Lightweight toast: append a transient bootstrap-style alert
        // to a fixed slot so we don't depend on a third-party toast lib.
        try {
            var slot = document.getElementById('r68-toast-slot');
            if (!slot) {
                slot = document.createElement('div');
                slot.id = 'r68-toast-slot';
                slot.style.cssText = 'position:fixed;bottom:20px;right:20px;z-index:9999;max-width:360px;';
                document.body.appendChild(slot);
            }
            var alert = document.createElement('div');
            var bsKind = (kind === 'retryable') ? 'alert-warning'
                       : (kind === 'success')   ? 'alert-success'
                       : 'alert-danger';
            alert.className = 'alert ' + bsKind + ' shadow-sm';
            alert.style.cssText = 'margin-top:8px;font-size:0.9em;';
            alert.textContent = String(message || '');
            slot.appendChild(alert);
            window.setTimeout(function () {
                try { alert.remove(); } catch (_) { /* noop */ }
            }, 6000);
        } catch (_) { /* never break Ask AI on toast failure */ }
    }

    // Round 74 / Phase 3 (P3): pre-R74 ``askAI`` was the single
    // entry point.  R74 splits it into:
    //
    //   * ``_r74AskSync(question, opts)``   -- the original
    //     synchronous endpoint flow, preserved verbatim so a
    //     streaming failure / disabled streaming still works.
    //   * ``_r74AskStreaming(question, opts)`` -- the new SSE
    //     flow that gives instant first-token feedback.
    //   * ``askAI(question, opts)`` -- the dispatcher.  Calls
    //     ``_r74AskStreaming`` first; on a hard failure (network
    //     error before any data lands, ``error`` event in the
    //     stream, etc.) falls back to ``_r74AskSync`` transparently
    //     so the operator never sees a streaming bug as a
    //     user-visible failure.
    //
    // ``_r74StreamingEnabled`` is a feature flag the operator can
    // flip via window.console for debugging.  Default ON.
    var _r74StreamingEnabled = true;
    try {
        if (window.localStorage &&
            window.localStorage.getItem('r74_streaming_disabled') === '1') {
            _r74StreamingEnabled = false;
        }
    } catch (_) { /* localStorage may be disabled */ }

    function _r74AskSync(question, opts) {
        opts = opts || {};
        if (!question.trim()) return;
        lastAskedQuestion = question.trim();
        answerArea.style.display = '';
        loadingState.style.display = '';
        answerContent.textContent = '';
        contextInfo.style.display = 'none';
        hideAllBanners();
        askBtn.disabled = true;
        // Round 68 / Build 42 (C3): bounded retry counter (max 3
        // attempts: original + 2 retries).  Tracked via opts so
        // recursion-style retries can carry the count without
        // closure leakage.
        opts._r68_attempt = (typeof opts._r68_attempt === 'number') ? opts._r68_attempt : 1;
        opts._r68_max_attempts = (typeof opts._r68_max_attempts === 'number') ? opts._r68_max_attempts : 3;

        // Round 6 / Phase 2.5: ``parseInt(...) || 90`` mishandles a
        // legitimate ``0`` (truthy-falsy collapse).  Use
        // ``Number.isFinite`` so any non-finite parse falls back, but
        // a real ``0`` (a zero-day window, edge case) is preserved.
        var _aiDaysRaw = parseInt(document.getElementById('aiDays').value, 10);
        var payload = {
            question: question.trim(),
            manager: document.getElementById('aiManager').value,
            technology: document.getElementById('aiTech').value,
            days: Number.isFinite(_aiDaysRaw) ? _aiDaysRaw : 90
        };
        if (opts.allow_legacy_fallback) {
            payload.allow_legacy_fallback = true;
        }

        var steps = [
            'Connecting to Snowflake...',
            'Fetching team subscriptions...',
            'Loading adoption barriers and cases...',
            'Building briefing for AI...',
            'Waiting for Circuit AI response...'
        ];
        var stepIdx = 0;
        var stepTimer = setInterval(function() {
            stepIdx++;
            if (stepIdx < steps.length) {
                loadingDetail.textContent = steps[stepIdx];
            }
        }, 4000);

        // Round 68 / Build 42 (C9): drive the 3-step indicator from
        // the same timer.  Steps map to the canonical pipeline:
        //   step 1 (fetch) -- starts immediately when the request fires
        //   step 2 (retrieve) -- after ~5s (the rolling step timer
        //     reaches step 2 of the legacy 5-step rotation)
        //   step 3 (synthesize) -- after ~12s (LLM call typically dominates)
        // The step pills are hard-coded HTML; we just toggle their
        // CSS classes here.  The progressbar role's aria-valuenow is
        // also kept in sync for accessibility.
        try {
            _r68SetStep(1);
            window.setTimeout(function () { _r68SetStep(2); }, 5000);
            window.setTimeout(function () { _r68SetStep(3); }, 12000);
        } catch (_) { /* never fail Ask AI on step ui */ }

        var csrfToken = (document.querySelector('meta[name="csrf-token"]') || {}).getAttribute('content') || '';
        var _stepTimerCleared = false;
        function _clearStepTimer() {
            if (!_stepTimerCleared) {
                _stepTimerCleared = true;
                try { clearInterval(stepTimer); } catch (_) { /* noop */ }
            }
        }
        // Round 8 / Phase 5.1: bound the request to 90 s using
        // AbortController so a stuck Snowflake / Circuit call
        // surfaces as an actionable error instead of an infinite
        // spinner.
        var _abortCtl = (typeof AbortController === 'function') ? new AbortController() : null;
        // Round 68 / Build 42 (C9): register the active controller
        // on the namespaced runtime slot so the Cancel button can
        // call .abort() on it.  Reset on response receipt below.
        try {
            if (window._R68_ASK_AI_RUNTIME) {
                window._R68_ASK_AI_RUNTIME.activeAbort = _abortCtl;
            }
        } catch (_) { /* noop */ }
        var _abortTimer = null;
        if (_abortCtl) {
            _abortTimer = setTimeout(function() {
                try { _abortCtl.abort(); } catch (_) { /* noop */ }
            }, 90000);
        }
        function _clearAbortTimer() {
            if (_abortTimer) {
                try { clearTimeout(_abortTimer); } catch (_) { /* noop */ }
                _abortTimer = null;
            }
        }
        var _fetchOpts = {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
            body: JSON.stringify(payload)
        };
        if (_abortCtl) { _fetchOpts.signal = _abortCtl.signal; }
        // Round 9 / Phase 5.2: previously this called ``r.json()`` with
        // only a ``.catch`` fallback, which masked HTTP 4xx/5xx
        // responses (a JSON error body would still be parsed and
        // dropped into the success branch as a falsy ``data.ok``) *and*
        // an HTML 200 response (e.g. a stray static-file redirect)
        // would surface as the opaque "Invalid response from server"
        // message.  Mirror the ``_intelJson`` guard in
        // ``templates/external_intelligence.html`` -- check ``r.ok``
        // first and assert the ``Content-Type`` header is JSON before
        // attempting to decode -- so the operator-facing error tells
        // them what actually went wrong.
        function _askAiJson(r) {
            var ctype = ((r.headers && r.headers.get('content-type')) || '').toLowerCase();
            if (!r.ok) {
                // Try to decode JSON error envelope when present;
                // otherwise surface a status-only message so the UI
                // still renders.
                if (ctype.indexOf('application/json') !== -1) {
                    return r.json().catch(function () {
                        return { ok: false, error: 'Server returned HTTP ' + r.status + ' (unparseable body)' };
                    });
                }
                return { ok: false, error: 'Server returned HTTP ' + r.status + ' (' + (ctype || 'unknown content-type') + ')' };
            }
            if (ctype.indexOf('application/json') === -1) {
                return { ok: false, error: 'Server returned non-JSON response (content-type=' + (ctype || 'unknown') + ')' };
            }
            return r.json().catch(function () {
                return { ok: false, error: 'Invalid JSON in response from server' };
            });
        }
        // Round 68 / Build 42 (C3): capture the HTTP status before
        // body parse so the classifier can distinguish 429/timeout/5xx
        // from semantic failures.
        var _r68LastStatus = 0;
        function _askAiJsonWithStatus(r) {
            _r68LastStatus = r.status || 0;
            return _askAiJson(r);
        }

        // Round 68 / Build 42 (C3): retryable-error dispatcher.  When
        // the response fails with a retryable kind AND we have
        // attempts left, fire a toast + schedule the next attempt
        // with exponential backoff; otherwise let the failure
        // surface to the user.  Backoff: 1s, 2s after the first and
        // second failure respectively (matches the server-side
        // ``_r64_call_llm_with_retry`` cadence).
        function _r68MaybeRetry(httpStatus, data, fetchErr) {
            var kind = _r68ClassifyError(httpStatus, data, fetchErr);
            var spec = R68_ERROR_KINDS[kind] || R68_ERROR_KINDS.unknown;
            var attemptsLeft = opts._r68_max_attempts - opts._r68_attempt;
            if (spec.retryable && attemptsLeft > 0 && kind !== 'client_abort') {
                var backoffMs = Math.pow(2, opts._r68_attempt - 1) * 1000;
                _r68ShowToast(spec.toast + ' (attempt ' + opts._r68_attempt
                    + '/' + opts._r68_max_attempts + ', retrying in '
                    + (backoffMs / 1000) + 's)', 'retryable');
                window.setTimeout(function () {
                    var nextOpts = {};
                    for (var k in opts) {
                        if (Object.prototype.hasOwnProperty.call(opts, k)) {
                            nextOpts[k] = opts[k];
                        }
                    }
                    nextOpts._r68_attempt = opts._r68_attempt + 1;
                    // Round 74 / Phase 3 (P3): retries stay on the
                    // sync path so we don't oscillate between
                    // streaming and sync mid-recovery.
                    _r74AskSync(question, nextOpts);
                }, backoffMs);
                return true;
            }
            // Non-retryable OR out of attempts: surface the toast at
            // 'error' severity so the operator sees a tailored message.
            _r68ShowToast(spec.toast, 'error');
            return false;
        }

        fetch('/api/ask-ai-portfolio', _fetchOpts)
        .then(_askAiJsonWithStatus)
        .then(function(data) {
            _clearStepTimer();
            _clearAbortTimer();
            loadingState.style.display = 'none';
            askBtn.disabled = false;
            if (data.ok) {
                // Round 68 / Build 42 (C7): populate the evidence
                // index BEFORE we format the answer, so the
                // citation badge renderer (called from
                // ``appendInline``) has the snippet data ready
                // when it builds each ``[Source: ID]`` badge.
                _r68SetEvidenceIndex(data.evidence_index);
                // Round 74 / Phase 4 (P4): also seed the new R74
                // evidence-record index so the offcanvas drawer
                // can render the underlying record on click.
                try {
                    _r74SetEvidenceRecords(
                        data.evidence_records,
                        data.query_id
                    );
                } catch (_) { /* noop */ }
                formatAnswerInto(answerContent, (data.answer && data.answer.trim()) ? data.answer : 'No answer was returned. Please try rephrasing your question.');
                // Round 68 / Build 42 (C5): always render the debug
                // chip on a successful answer so an operator can
                // file a support ticket with the debug ID.
                _r68RenderDebugChip(data);
                // Round 74 / Phase 5 (P5): record the conversation
                // turn AFTER we know the request succeeded.  When
                // the conversation toggle is OFF this is a no-op.
                try {
                    if (_r74ConversationActive() && data.answer) {
                        _r74ConversationPushTurn(
                            lastAskedQuestion, data.answer, data.query_id
                        );
                    }
                } catch (_) { /* noop */ }
                // Round 74 / Phase 6 (P6): follow-up chips when the
                // synchronous endpoint surfaces them.  Hidden when
                // the response carries an empty / missing list.
                try {
                    _r74RenderFollowUpChips(data.follow_up_suggestions || []);
                } catch (_) { /* noop */ }
                if (data.context_summary) {
                    contextInfo.style.display = '';
                    contextDetail.textContent = data.context_summary;
                }
                if (data.mode === 'legacy_ungrounded') {
                    ungroundedBanner.style.display = '';
                    if (data.ungrounded_warning) {
                        ungroundedDetail.textContent = data.ungrounded_warning;
                    }
                }
                var truncationBits = [];
                if (data.evidence_truncated) {
                    truncationBits.push('Evidence truncated to '
                        + (data.evidence_records_used || '?')
                        + ' of ' + (data.evidence_records_total || '?')
                        + ' records.');
                }
                if (data.account_batch_truncated) {
                    truncationBits.push('Account-level evidence covers '
                        + (data.account_batch_size || '?')
                        + ' of ' + (data.account_total || '?')
                        + ' accounts.');
                }
                if (truncationBits.length > 0) {
                    truncationDetail.textContent = truncationBits.join(' ')
                        + ' Headline numbers come from the canonical metrics block (full population).';
                    truncationBanner.style.display = '';
                }
                if (data.partial_data_warnings && data.partial_data_warnings.length > 0) {
                    while (partialDataList.firstChild) partialDataList.removeChild(partialDataList.firstChild);
                    data.partial_data_warnings.forEach(function(w) {
                        var li = document.createElement('li');
                        li.textContent = (w.dataset || 'unknown') + ': ' + (w.error || 'unknown error');
                        partialDataList.appendChild(li);
                    });
                    partialDataBanner.style.display = '';
                }
            } else {
                // Round 68 / Build 42 (C3): try retrying on
                // retryable error kinds before showing the legacy
                // banner.  ``_r68MaybeRetry`` returns true when it
                // schedules a retry (we suppress the legacy banner
                // for that case so the user sees only the toast).
                if (_r68MaybeRetry(_r68LastStatus, data, null)) {
                    return;
                }
                if (data.mode === 'grounded' && data.fallback_available) {
                    groundedFailedBanner.style.display = '';
                    groundedFailedDetail.textContent = ' Reason: '
                        + (data.reason || 'unspecified')
                        + '. You can retry, or accept an ungrounded answer.';
                    while (answerContent.firstChild) answerContent.removeChild(answerContent.firstChild);
                } else {
                    formatAnswerInto(answerContent, 'Error: ' + (data.error || 'Unknown error. Check that Snowflake and Circuit credentials are configured.'));
                }
            }
        })
        .catch(function(err) {
            _clearStepTimer();
            _clearAbortTimer();
            loadingState.style.display = 'none';
            askBtn.disabled = false;
            // Round 68 / Build 42 (C3): retry on transient network /
            // timeout errors before the user sees the bland
            // "Network error" line.
            if (_r68MaybeRetry(_r68LastStatus, null, err)) {
                return;
            }
            var isAbort = err && (err.name === 'AbortError' || /aborted/i.test(String(err.message || '')));
            var msg = isAbort
                ? 'Request timed out after 90 seconds. The Snowflake fetch or Circuit AI call did not respond. Please try again or narrow the time range.'
                : 'Network error: ' + String(err);
            formatAnswerInto(answerContent, msg);
        })
        .finally(function() {
            _clearStepTimer();
            _clearAbortTimer();
            // Round 68 / Build 42 (C9): clear the namespaced abort
            // slot so the Cancel button doesn't try to abort a
            // completed request.
            try {
                if (window._R68_ASK_AI_RUNTIME && window._R68_ASK_AI_RUNTIME.activeAbort === _abortCtl) {
                    window._R68_ASK_AI_RUNTIME.activeAbort = null;
                }
            } catch (_) { /* noop */ }
        });
    }

    // -----------------------------------------------------------------
    // Round 74 / Phase 4 (P4): in-memory evidence record index used by
    // the offcanvas drawer.  Keyed by string source_id (the badge's
    // ``data-source-id`` attribute).  Populated when an answer lands
    // (sync OR streaming) and consulted on every badge click.
    // -----------------------------------------------------------------
    var _r74EvidenceRecords = {};
    var _r74CurrentQueryId = '';

    function _r74SetEvidenceRecords(records, queryId) {
        _r74EvidenceRecords = {};
        _r74CurrentQueryId = String(queryId || '');
        if (!Array.isArray(records)) { return; }
        for (var i = 0; i < records.length; i++) {
            var rec = records[i];
            if (!rec || typeof rec !== 'object') { continue; }
            var sid = (rec.source_id != null) ? String(rec.source_id)
                    : (rec.id != null) ? String(rec.id) : '';
            if (!sid) { continue; }
            _r74EvidenceRecords[sid] = rec;
        }
    }

    // -----------------------------------------------------------------
    // Round 74 / Phase 5 (P5): in-memory conversation history (capped
    // at last 5 turns, sent in the next request payload when the
    // conversation toggle is ON).  We KEEP the answer text trimmed to
    // ~1500 chars per turn so the prompt stays bounded.
    // -----------------------------------------------------------------
    var _R74_CONVO_MAX_TURNS = 5;
    var _r74ConversationHistory = [];
    var r74ConversationToggle = document.getElementById('r74ConversationToggle');
    var r74ConversationCount = document.getElementById('r74ConversationCount');
    var r74ConversationResetBtn = document.getElementById('r74ConversationResetBtn');

    function _r74ConversationActive() {
        return !!(r74ConversationToggle && r74ConversationToggle.checked);
    }

    function _r74ConversationPushTurn(question, answer, queryId) {
        if (!question || !answer) { return; }
        _r74ConversationHistory.push({
            q: String(question),
            a: String(answer).slice(0, 1500),
            query_id: String(queryId || '')
        });
        while (_r74ConversationHistory.length > _R74_CONVO_MAX_TURNS) {
            _r74ConversationHistory.shift();
        }
        _r74RenderConversationCount();
    }

    function _r74RenderConversationCount() {
        var n = _r74ConversationHistory.length;
        if (r74ConversationCount) {
            if (n > 0) {
                r74ConversationCount.textContent = n + ' turn' + (n === 1 ? '' : 's');
                r74ConversationCount.style.display = '';
            } else {
                r74ConversationCount.style.display = 'none';
            }
        }
        if (r74ConversationResetBtn) {
            r74ConversationResetBtn.style.display = n > 0 ? '' : 'none';
        }
    }

    function _r74ResetConversation() {
        _r74ConversationHistory = [];
        _r74RenderConversationCount();
        _r68ShowToast('Conversation reset.', 'success');
    }

    if (r74ConversationResetBtn) {
        r74ConversationResetBtn.addEventListener('click', _r74ResetConversation);
    }
    if (r74ConversationToggle) {
        r74ConversationToggle.addEventListener('change', function () {
            // When toggling OFF, clear the in-memory history so a
            // future toggle-ON starts fresh.  This matches operator
            // expectation: toggle ON = "I'm having a conversation",
            // toggle OFF = "I'm done, forget the context".
            if (!this.checked) {
                _r74ConversationHistory = [];
                _r74RenderConversationCount();
            }
        });
    }

    // -----------------------------------------------------------------
    // Round 74 / Phase 6 (P6): follow-up chip rendering.
    // -----------------------------------------------------------------
    var r74FollowUpChipsContainer = document.getElementById('r74FollowUpChipsContainer');
    var r74FollowUpChips = document.getElementById('r74FollowUpChips');

    function _r74RenderFollowUpChips(suggestions) {
        if (!r74FollowUpChipsContainer || !r74FollowUpChips) { return; }
        while (r74FollowUpChips.firstChild) {
            r74FollowUpChips.removeChild(r74FollowUpChips.firstChild);
        }
        if (!Array.isArray(suggestions) || suggestions.length === 0) {
            r74FollowUpChipsContainer.style.display = 'none';
            return;
        }
        suggestions.forEach(function (s) {
            var qText = (typeof s === 'string') ? s
                : (s && typeof s.question === 'string') ? s.question
                : '';
            if (!qText.trim()) { return; }
            var btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'btn btn-outline-warning btn-sm';
            btn.textContent = qText;
            btn.addEventListener('click', function () {
                questionInput.value = qText;
                if (_r74ConversationActive()) {
                    askAI(qText);
                } else {
                    try { questionInput.focus(); } catch (_) { /* noop */ }
                }
            });
            r74FollowUpChips.appendChild(btn);
        });
        r74FollowUpChipsContainer.style.display = '';
    }

    function _r74HideFollowUpChips() {
        if (r74FollowUpChipsContainer) {
            r74FollowUpChipsContainer.style.display = 'none';
        }
    }

    // -----------------------------------------------------------------
    // Round 74 / Phase 4 (P4): offcanvas evidence drawer wiring.
    // -----------------------------------------------------------------
    var r74EvidenceDrawerEl = document.getElementById('r74EvidenceDrawer');
    var r74EvidenceDrawerBody = document.getElementById('r74EvidenceDrawerBody');
    var r74EvidenceDrawerStatus = document.getElementById('r74EvidenceDrawerStatus');
    var r74EvidenceDrawerLabel = document.getElementById('r74EvidenceDrawerLabel');
    var _r74EvidenceDrawerInstance = null;

    function _r74OpenEvidenceDrawer() {
        if (!r74EvidenceDrawerEl) { return; }
        try {
            if (window.bootstrap && window.bootstrap.Offcanvas) {
                if (!_r74EvidenceDrawerInstance) {
                    _r74EvidenceDrawerInstance = new window.bootstrap.Offcanvas(
                        r74EvidenceDrawerEl
                    );
                }
                _r74EvidenceDrawerInstance.show();
            } else {
                // Fallback when Bootstrap JS is not loaded -- toggle
                // the .show class manually so the drawer is still
                // visible (drawer styling comes from Bootstrap CSS
                // which IS loaded).
                r74EvidenceDrawerEl.classList.add('show');
                r74EvidenceDrawerEl.style.visibility = 'visible';
            }
        } catch (err) {
            try {
                if (window.console && window.console.debug) {
                    window.console.debug('[R74] drawer open failed', err);
                }
            } catch (_) { /* noop */ }
        }
    }

    function _r74RenderEvidenceRecord(record, sourceId) {
        if (!r74EvidenceDrawerBody) { return; }
        while (r74EvidenceDrawerBody.firstChild) {
            r74EvidenceDrawerBody.removeChild(r74EvidenceDrawerBody.firstChild);
        }
        if (!record || typeof record !== 'object') {
            var emptyP = document.createElement('p');
            emptyP.className = 'text-muted mb-0';
            emptyP.textContent = 'No evidence record found for source "' + String(sourceId) + '".';
            r74EvidenceDrawerBody.appendChild(emptyP);
            return;
        }
        // Header pills.
        var headerRow = document.createElement('div');
        headerRow.className = 'd-flex flex-wrap gap-1 mb-3';
        var typePill = document.createElement('span');
        typePill.className = 'badge bg-primary text-light';
        typePill.textContent = String(record.source_type || record.record_type || 'evidence');
        headerRow.appendChild(typePill);
        if (record.customer || record.customer_name || record.bu_name) {
            var custPill = document.createElement('span');
            custPill.className = 'badge bg-info text-dark';
            custPill.textContent = String(
                record.customer || record.customer_name || record.bu_name
            );
            headerRow.appendChild(custPill);
        }
        if (record.timestamp || record.recorded_at) {
            var tsPill = document.createElement('span');
            tsPill.className = 'badge bg-light text-muted border';
            tsPill.textContent = String(record.timestamp || record.recorded_at);
            headerRow.appendChild(tsPill);
        }
        r74EvidenceDrawerBody.appendChild(headerRow);
        // Headline / snippet.
        if (record.headline || record.title) {
            var h = document.createElement('div');
            h.className = 'fw-semibold mb-2';
            h.textContent = String(record.headline || record.title);
            r74EvidenceDrawerBody.appendChild(h);
        }
        if (record.snippet || record.summary) {
            var s = document.createElement('p');
            s.className = 'text-body mb-3';
            s.textContent = String(record.snippet || record.summary);
            r74EvidenceDrawerBody.appendChild(s);
        }
        // Definition list of every other field.
        var dl = document.createElement('dl');
        dl.className = 'row mb-3 small';
        var skipKeys = {
            'source_id': true, 'source_type': true, 'record_type': true,
            'customer': true, 'customer_name': true, 'bu_name': true,
            'timestamp': true, 'recorded_at': true,
            'headline': true, 'title': true,
            'snippet': true, 'summary': true,
            'id': true
        };
        var keys = Object.keys(record).sort();
        for (var i = 0; i < keys.length; i++) {
            var k = keys[i];
            if (skipKeys[k]) { continue; }
            var v = record[k];
            if (v == null || v === '') { continue; }
            var dt = document.createElement('dt');
            dt.className = 'col-sm-4 text-muted text-truncate';
            dt.textContent = k;
            dt.title = k;
            var dd = document.createElement('dd');
            dd.className = 'col-sm-8';
            // Render arrays / objects as JSON so the user sees the shape
            // without surrendering XSS safety -- still textContent.
            var displayVal;
            if (typeof v === 'object') {
                try { displayVal = JSON.stringify(v, null, 2); }
                catch (_) { displayVal = String(v); }
                var pre = document.createElement('pre');
                pre.className = 'small mb-0 bg-light p-2 rounded';
                pre.style.cssText = 'white-space:pre-wrap;word-break:break-word;';
                pre.textContent = displayVal;
                dd.appendChild(pre);
            } else {
                dd.textContent = String(v);
            }
            dl.appendChild(dt);
            dl.appendChild(dd);
        }
        if (dl.firstChild) {
            r74EvidenceDrawerBody.appendChild(dl);
        }
        // Optional Snowflake deep-link footer.
        if (record.snowflake_table) {
            var footer = document.createElement('div');
            footer.className = 'pt-2 mt-3 border-top text-muted small';
            footer.textContent = 'Source table: ' + String(record.snowflake_table);
            r74EvidenceDrawerBody.appendChild(footer);
        }
    }

    function _r74SetDrawerLabel(sourceId, recordType) {
        if (!r74EvidenceDrawerLabel) { return; }
        while (r74EvidenceDrawerLabel.firstChild) {
            r74EvidenceDrawerLabel.removeChild(r74EvidenceDrawerLabel.firstChild);
        }
        var icon = document.createElement('i');
        icon.className = 'fas fa-quote-left text-primary me-2';
        r74EvidenceDrawerLabel.appendChild(icon);
        var span = document.createElement('span');
        span.textContent = recordType
            ? (String(recordType) + ' [' + String(sourceId) + ']')
            : ('Evidence [' + String(sourceId) + ']');
        r74EvidenceDrawerLabel.appendChild(span);
    }

    function _r74SetDrawerStatus(message, kind) {
        if (!r74EvidenceDrawerStatus) { return; }
        if (!message) {
            r74EvidenceDrawerStatus.style.display = 'none';
            r74EvidenceDrawerStatus.textContent = '';
            return;
        }
        var bsKind = (kind === 'error') ? 'alert-danger' : 'alert-info';
        r74EvidenceDrawerStatus.className = 'alert ' + bsKind + ' small mb-3';
        r74EvidenceDrawerStatus.textContent = String(message);
        r74EvidenceDrawerStatus.style.display = '';
    }

    function _r74OnSourceBadgeActivate(sourceId) {
        var sid = String(sourceId || '').trim();
        if (!sid) { return; }
        // ``data-source-id`` may carry a comma-separated list when
        // the original [Source: A, B] marker was multi-id.  For the
        // drawer we just take the FIRST id (the others can be
        // navigated via subsequent badge clicks).
        var firstSid = sid.split(',')[0].trim();
        if (!firstSid) { return; }
        _r74SetDrawerLabel(firstSid);
        _r74SetDrawerStatus('', null);
        // Try in-memory index first.
        var rec = _r74EvidenceRecords[firstSid];
        if (rec) {
            _r74SetDrawerLabel(firstSid, rec.source_type || rec.record_type);
            _r74RenderEvidenceRecord(rec, firstSid);
            _r74OpenEvidenceDrawer();
            return;
        }
        // Fallback: ask the server.  This handles the post-reload
        // case AND the case where the index dropped a record because
        // it was too large to ship in the initial response.
        if (!_r74CurrentQueryId) {
            _r74RenderEvidenceRecord(null, firstSid);
            _r74SetDrawerStatus(
                'No evidence records available for this answer (the page may have been reloaded).',
                'error'
            );
            _r74OpenEvidenceDrawer();
            return;
        }
        _r74SetDrawerStatus('Loading evidence record...', null);
        _r74OpenEvidenceDrawer();
        var url = '/api/ask-ai/evidence/'
            + encodeURIComponent(_r74CurrentQueryId)
            + '/' + encodeURIComponent(firstSid);
        fetch(url, { method: 'GET', credentials: 'same-origin',
            headers: { 'Accept': 'application/json' } })
            .then(function (r) {
                if (!r.ok) {
                    throw new Error('lookup failed: HTTP ' + r.status);
                }
                return r.json();
            })
            .then(function (data) {
                if (data && data.ok && data.record) {
                    _r74SetDrawerLabel(firstSid,
                        data.record.source_type || data.record.record_type);
                    _r74RenderEvidenceRecord(data.record, firstSid);
                    _r74SetDrawerStatus('', null);
                } else {
                    _r74RenderEvidenceRecord(null, firstSid);
                    _r74SetDrawerStatus(
                        'Evidence record not available: ' + ((data && data.error) || 'unknown error'),
                        'error'
                    );
                }
            })
            .catch(function (err) {
                _r74RenderEvidenceRecord(null, firstSid);
                _r74SetDrawerStatus(
                    'Could not load evidence: ' + String(err && err.message || err),
                    'error'
                );
            });
    }

    // Delegated click + keydown handler for source badges (works for
    // every badge regardless of when it was rendered into the DOM).
    if (answerContent) {
        answerContent.addEventListener('click', function (evt) {
            var t = evt.target;
            while (t && t !== answerContent) {
                if (t.classList && t.classList.contains('r74-source-badge')) {
                    var sid = t.getAttribute('data-source-id') || '';
                    _r74OnSourceBadgeActivate(sid);
                    evt.preventDefault();
                    return;
                }
                t = t.parentNode;
            }
        });
        answerContent.addEventListener('keydown', function (evt) {
            if (evt.key !== 'Enter' && evt.key !== ' ') { return; }
            var t = evt.target;
            if (t && t.classList && t.classList.contains('r74-source-badge')) {
                var sid = t.getAttribute('data-source-id') || '';
                _r74OnSourceBadgeActivate(sid);
                evt.preventDefault();
            }
        });
    }

    // -----------------------------------------------------------------
    // Round 74 / Phase 3 (P3): SSE streaming client.
    // -----------------------------------------------------------------
    //
    // Uses fetch + ReadableStream to consume the SSE response (we
    // can't use EventSource because EventSource doesn't support POST
    // + custom headers, and we need both for CSRF).  The parser is
    // a minimal SSE frame splitter (event: + data: + blank line).
    //
    // On a hard failure (network error before any data lands, ``error``
    // event in the stream, or HTTP 4xx/5xx) the function REJECTS its
    // promise so the dispatcher (askAI) can fall back to _r74AskSync
    // transparently.  Once the answer area has been written to we
    // RESOLVE so a mid-stream blip doesn't fire a duplicate sync
    // request that would clobber the partial answer.
    function _r74AskStreaming(question, opts) {
        opts = opts || {};
        return new Promise(function (resolve, reject) {
            if (!question.trim()) { reject(new Error('empty question')); return; }
            var trimmed = question.trim();
            lastAskedQuestion = trimmed;
            answerArea.style.display = '';
            loadingState.style.display = '';
            answerContent.textContent = '';
            contextInfo.style.display = 'none';
            hideAllBanners();
            _r74HideFollowUpChips();
            askBtn.disabled = true;

            var _aiDaysRaw = parseInt(document.getElementById('aiDays').value, 10);
            var payload = {
                question: trimmed,
                manager: document.getElementById('aiManager').value,
                technology: document.getElementById('aiTech').value,
                days: Number.isFinite(_aiDaysRaw) ? _aiDaysRaw : 90
            };
            // Round 74 / Phase 5 (P5): conversation context.
            if (_r74ConversationActive() && _r74ConversationHistory.length) {
                payload.conversation_history = _r74ConversationHistory.slice();
            }

            try { _r68SetStep(1); } catch (_) { /* noop */ }
            window.setTimeout(function () { try { _r68SetStep(2); } catch (_) { } }, 3000);
            window.setTimeout(function () { try { _r68SetStep(3); } catch (_) { } }, 6000);

            var csrfToken = (document.querySelector('meta[name="csrf-token"]') || {})
                .getAttribute('content') || '';
            var abortCtl = (typeof AbortController === 'function')
                ? new AbortController() : null;
            try {
                if (window._R68_ASK_AI_RUNTIME) {
                    window._R68_ASK_AI_RUNTIME.activeAbort = abortCtl;
                }
            } catch (_) { /* noop */ }
            var abortTimer = null;
            if (abortCtl) {
                abortTimer = setTimeout(function () {
                    try { abortCtl.abort(); } catch (_) { /* noop */ }
                }, 90000);
            }

            var fetchOpts = {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': csrfToken,
                    'Accept': 'text/event-stream'
                },
                body: JSON.stringify(payload),
                credentials: 'same-origin'
            };
            if (abortCtl) { fetchOpts.signal = abortCtl.signal; }

            // Buffer for chunked answer text -- re-rendered on every
            // data event so the user sees the answer materialise.
            var bufferedAnswer = '';
            var metaPayload = null;
            var donePayload = null;
            var anyChunkLanded = false;
            var streamFailed = false;
            var streamFailReason = '';
            var firstChunkRendered = false;

            function _cleanup() {
                if (abortTimer) {
                    try { clearTimeout(abortTimer); } catch (_) { /* noop */ }
                    abortTimer = null;
                }
                askBtn.disabled = false;
                loadingState.style.display = 'none';
                try {
                    if (window._R68_ASK_AI_RUNTIME &&
                        window._R68_ASK_AI_RUNTIME.activeAbort === abortCtl) {
                        window._R68_ASK_AI_RUNTIME.activeAbort = null;
                    }
                } catch (_) { /* noop */ }
            }

            function _processFrame(frame) {
                if (!frame) { return; }
                var lines = frame.split(/\r?\n/);
                var event = 'message';
                var dataLines = [];
                for (var li = 0; li < lines.length; li++) {
                    var line = lines[li];
                    if (line.indexOf('event:') === 0) {
                        event = line.slice(6).trim();
                    } else if (line.indexOf('data:') === 0) {
                        dataLines.push(line.slice(5).trimStart());
                    }
                }
                if (dataLines.length === 0) { return; }
                var raw = dataLines.join('\n');
                var data;
                try { data = JSON.parse(raw); }
                catch (_) { data = { _raw: raw }; }
                if (event === 'meta') {
                    metaPayload = data;
                    try {
                        // R68 evidence index (citation badge popovers)
                        // is still wired off ``evidence_index`` -- preserve.
                        _r68SetEvidenceIndex(data.evidence_index);
                        _r74SetEvidenceRecords(
                            data.evidence_records,
                            data.query_id
                        );
                    } catch (_) { /* noop */ }
                } else if (event === 'data') {
                    var chunk = (data && typeof data.chunk === 'string')
                        ? data.chunk : '';
                    if (chunk) {
                        bufferedAnswer += chunk;
                        anyChunkLanded = true;
                        if (!firstChunkRendered) {
                            loadingState.style.display = 'none';
                            firstChunkRendered = true;
                        }
                        try {
                            formatAnswerInto(answerContent, bufferedAnswer);
                        } catch (_) { /* noop */ }
                    }
                } else if (event === 'done') {
                    donePayload = data;
                } else if (event === 'error') {
                    streamFailed = true;
                    streamFailReason = (data && data.error)
                        ? String(data.error) : 'stream error';
                }
            }

            fetch('/api/ask-ai-portfolio/stream', fetchOpts)
                .then(function (response) {
                    if (!response.ok) {
                        throw new Error('HTTP ' + response.status);
                    }
                    var ctype = (response.headers.get('content-type') || '').toLowerCase();
                    if (ctype.indexOf('text/event-stream') < 0) {
                        throw new Error('non-SSE response: ' + (ctype || 'unknown'));
                    }
                    if (!response.body || typeof response.body.getReader !== 'function') {
                        throw new Error('streaming unsupported in this browser');
                    }
                    var reader = response.body.getReader();
                    var decoder = new TextDecoder('utf-8');
                    var buffer = '';
                    function pump() {
                        return reader.read().then(function (chunk) {
                            if (chunk.done) {
                                if (buffer.trim()) { _processFrame(buffer); }
                                return;
                            }
                            buffer += decoder.decode(chunk.value, { stream: true });
                            // Frames are separated by a blank line
                            // (\n\n).  Drain complete frames out of
                            // the buffer; the trailing partial stays.
                            var splitIdx;
                            while ((splitIdx = buffer.indexOf('\n\n')) !== -1) {
                                var frame = buffer.slice(0, splitIdx);
                                buffer = buffer.slice(splitIdx + 2);
                                _processFrame(frame);
                            }
                            return pump();
                        });
                    }
                    return pump();
                })
                .then(function () {
                    _cleanup();
                    if (streamFailed) {
                        // Mid-stream error event -- reject so the
                        // dispatcher can fall back IF nothing landed
                        // yet.  When chunks already rendered we
                        // resolve to avoid clobbering the partial.
                        if (anyChunkLanded) {
                            _r68ShowToast(
                                'Streaming finished early: ' + streamFailReason,
                                'error'
                            );
                            _r74OnStreamComplete(metaPayload, bufferedAnswer, donePayload);
                            resolve({ partial: true, reason: streamFailReason });
                        } else {
                            reject(new Error(streamFailReason));
                        }
                        return;
                    }
                    if (!anyChunkLanded) {
                        reject(new Error('no chunks received from stream'));
                        return;
                    }
                    _r74OnStreamComplete(metaPayload, bufferedAnswer, donePayload);
                    resolve({ partial: false });
                })
                .catch(function (err) {
                    _cleanup();
                    if (anyChunkLanded) {
                        // A network blip mid-stream is annoying but
                        // we already painted SOMETHING -- don't
                        // double-fire the sync fallback.
                        _r74OnStreamComplete(metaPayload, bufferedAnswer, donePayload);
                        resolve({ partial: true, reason: String(err) });
                    } else {
                        reject(err);
                    }
                });
        });
    }

    function _r74OnStreamComplete(metaPayload, answerText, donePayload) {
        // Render the same chrome the sync path renders so the user
        // sees identical UX whether streaming worked or fell back.
        try {
            if (metaPayload) {
                _r68RenderDebugChip({
                    query_id: metaPayload.query_id || '',
                    retrieval_method: metaPayload.retrieval_method || '',
                    model_name: metaPayload.model_name || ''
                });
                if (metaPayload.context_summary) {
                    contextInfo.style.display = '';
                    contextDetail.textContent = metaPayload.context_summary;
                }
                var bits = [];
                if (metaPayload.evidence_truncated) {
                    bits.push('Evidence truncated to '
                        + (metaPayload.evidence_records_used || '?')
                        + ' of ' + (metaPayload.evidence_records_total || '?') + ' records.');
                }
                if (metaPayload.account_batch_truncated) {
                    bits.push('Account-level evidence covers '
                        + (metaPayload.account_batch_size || '?')
                        + ' of ' + (metaPayload.account_total || '?') + ' accounts.');
                }
                if (bits.length) {
                    truncationDetail.textContent = bits.join(' ')
                        + ' Headline numbers come from the canonical metrics block (full population).';
                    truncationBanner.style.display = '';
                }
                if (metaPayload.partial_data_warnings && metaPayload.partial_data_warnings.length) {
                    while (partialDataList.firstChild) {
                        partialDataList.removeChild(partialDataList.firstChild);
                    }
                    metaPayload.partial_data_warnings.forEach(function (w) {
                        var li = document.createElement('li');
                        li.textContent = (w.dataset || 'unknown') + ': ' + (w.error || 'unknown error');
                        partialDataList.appendChild(li);
                    });
                    partialDataBanner.style.display = '';
                }
            }
        } catch (_) { /* never fail on chrome rendering */ }
        // Streaming pill on.
        try {
            var pill = document.getElementById('r74StreamingPill');
            if (pill) { pill.style.display = ''; }
        } catch (_) { /* noop */ }
        // History + conversation tracking.
        try {
            var qid = (metaPayload && metaPayload.query_id) || '';
            if (lastAskedQuestion && answerText) {
                _r68RecordHistoryEntry(lastAskedQuestion, answerText, {
                    query_id: qid,
                    retrieval_method: (metaPayload && metaPayload.retrieval_method) || ''
                });
                if (_r74ConversationActive()) {
                    _r74ConversationPushTurn(lastAskedQuestion, answerText, qid);
                }
            }
        } catch (_) { /* noop */ }
        // Follow-up chips from the done event.
        try {
            var suggestions = (donePayload && donePayload.follow_up_suggestions) || [];
            _r74RenderFollowUpChips(suggestions);
        } catch (_) { /* noop */ }
    }

    // Round 74 / Phase 3 (P3): public dispatcher.  Tries streaming
    // first when enabled; falls back to the synchronous path on
    // failure (network error before any data, HTTP error, browser
    // doesn't support ReadableStream, etc.).
    function askAI(question, opts) {
        opts = opts || {};
        // Hide streaming-only chrome on each new request.
        try {
            _r74HideFollowUpChips();
            var pill = document.getElementById('r74StreamingPill');
            if (pill) { pill.style.display = 'none'; }
        } catch (_) { /* noop */ }
        if (!_r74StreamingEnabled || opts._r74_force_sync) {
            return _r74AskSync(question, opts);
        }
        // Streaming path.  On a hard failure transparently fall
        // back to the sync path.
        _r74AskStreaming(question, opts).then(
            function (_result) { /* success: no fallback needed */ },
            function (err) {
                try {
                    if (window.console && window.console.debug) {
                        window.console.debug('[R74] streaming fell back to sync:', err);
                    }
                } catch (_) { /* noop */ }
                // Inline toast so the operator sees that streaming
                // didn't work but the answer is still on the way.
                try {
                    _r68ShowToast(
                        'Streaming unavailable; falling back to standard request...',
                        'retryable'
                    );
                } catch (_) { /* noop */ }
                var nextOpts = {};
                for (var k in opts) {
                    if (Object.prototype.hasOwnProperty.call(opts, k)) {
                        nextOpts[k] = opts[k];
                    }
                }
                nextOpts._r74_force_sync = true;
                _r74AskSync(question, nextOpts);
            }
        );
    }

    askBtn.addEventListener('click', function() { askAI(questionInput.value); });
    questionInput.addEventListener('keydown', function(e) {
        if (e.key === 'Enter') { e.preventDefault(); askAI(this.value); }
    });
    if (legacyOptInBtn) {
        legacyOptInBtn.addEventListener('click', function() {
            if (lastAskedQuestion) {
                askAI(lastAskedQuestion, { allow_legacy_fallback: true });
            }
        });
    }

    // Round 68 / Build 42 (C6): personalised suggestion chips.  The
    // initial SSR render carries the static fallback set so the
    // chip strip is non-blank even when JS is disabled / slow.
    // ``_r68FetchAndRenderChips`` replaces the static chips with
    // personalised ones on first paint AND on every selector change
    // (manager / tech / days).  The endpoint is read-only and
    // never calls the LLM, so this is cheap to refresh.
    var R68_SUGGESTIONS_CONTAINER = document.getElementById('r68SuggestionChipsContainer');

    function _r68WireChipClicks() {
        if (!R68_SUGGESTIONS_CONTAINER) { return; }
        R68_SUGGESTIONS_CONTAINER.querySelectorAll('.suggestion-btn').forEach(function (btn) {
            // Defensive: avoid double-wiring on refresh by checking a
            // marker attribute.
            if (btn.dataset.r68Wired === '1') { return; }
            btn.dataset.r68Wired = '1';
            btn.addEventListener('click', function () {
                var qText = this.dataset.r68Question || this.textContent;
                questionInput.value = qText;
                askAI(qText);
            });
        });
    }

    function _r68RenderSuggestions(suggestions) {
        if (!R68_SUGGESTIONS_CONTAINER) { return; }
        // Preserve the leading "Try:" small caption (always the first
        // child).  Replace everything after it with the new chip set.
        var leading = R68_SUGGESTIONS_CONTAINER.querySelector('small');
        // Remove all children (including any old chips).
        while (R68_SUGGESTIONS_CONTAINER.firstChild) {
            R68_SUGGESTIONS_CONTAINER.removeChild(R68_SUGGESTIONS_CONTAINER.firstChild);
        }
        if (leading) {
            R68_SUGGESTIONS_CONTAINER.appendChild(leading);
        } else {
            var smallEl = document.createElement('small');
            smallEl.className = 'text-muted me-1 align-self-center';
            smallEl.textContent = 'Try:';
            R68_SUGGESTIONS_CONTAINER.appendChild(smallEl);
        }
        if (!Array.isArray(suggestions) || !suggestions.length) {
            return;
        }
        // Group label colours per category to give the operator a
        // visual cue.  Bootstrap utility classes only.
        var categoryColours = {
            top_risk:        'btn-outline-danger',
            stale_barriers:  'btn-outline-warning',
            recent_renewal:  'btn-outline-primary',
            last_report:     'btn-outline-info',
            general:         'btn-outline-secondary'
        };
        suggestions.forEach(function (s) {
            if (!s || typeof s.question !== 'string' || !s.question.trim()) {
                return;
            }
            var btn = document.createElement('button');
            var category = (typeof s.category === 'string') ? s.category : 'general';
            var colourClass = categoryColours[category] || categoryColours.general;
            btn.type = 'button';
            btn.className = 'btn ' + colourClass + ' btn-sm suggestion-btn';
            // Use textContent (XSS-safe).  The question payload comes
            // from server data and may include the customer name.
            btn.textContent = s.question;
            btn.dataset.r68Question = s.question;
            btn.dataset.category = category;
            if (s.label) {
                btn.title = String(s.label) + ': ' + s.question;
            }
            R68_SUGGESTIONS_CONTAINER.appendChild(btn);
        });
        _r68WireChipClicks();
    }

    function _r68FetchAndRenderChips() {
        if (!R68_SUGGESTIONS_CONTAINER) { return; }
        var managerEl = document.getElementById('aiManager');
        var techEl = document.getElementById('aiTech');
        var daysEl = document.getElementById('aiDays');
        var manager = managerEl ? managerEl.value : '';
        var tech = techEl ? techEl.value : '';
        var days = daysEl ? daysEl.value : '90';
        var qs = new URLSearchParams({
            manager: manager || '',
            technology: tech || '',
            days: days || '90'
        }).toString();
        fetch('/api/ask-ai/suggestions?' + qs, {
            method: 'GET',
            credentials: 'same-origin',
            headers: { 'Accept': 'application/json' }
        })
        .then(function (r) {
            // Always 200 from the server, but be defensive about
            // intermediate proxies returning HTML error pages.
            if (!r.ok) { throw new Error('suggestions fetch failed: ' + r.status); }
            var ctype = (r.headers.get('content-type') || '').toLowerCase();
            if (ctype.indexOf('application/json') === -1) {
                throw new Error('suggestions: non-JSON response');
            }
            return r.json();
        })
        .then(function (data) {
            if (data && data.ok && Array.isArray(data.suggestions)) {
                _r68RenderSuggestions(data.suggestions);
            }
        })
        .catch(function (err) {
            // Silent failure -- the SSR fallback chips already rendered,
            // so the user sees suggestions either way.  We log to the
            // console for the operator who has DevTools open.
            try {
                if (window.console && console.warn) {
                    console.warn('Round 68 / C6: suggestion chip refresh failed:', err);
                }
            } catch (_) { /* noop */ }
        });
    }

    // Wire selector changes to re-fetch chips.  Bounded: each call
    // is ~100ms read-only on the server side; no LLM cost.
    ['aiManager', 'aiTech', 'aiDays'].forEach(function (id) {
        var el = document.getElementById(id);
        if (el) {
            el.addEventListener('change', _r68FetchAndRenderChips);
        }
    });

    // Initial wiring of the SSR fallback chips, then refresh.
    _r68WireChipClicks();
    _r68FetchAndRenderChips();

    // -----------------------------------------------------------------
    // Round 68 / Build 42 (C8): localStorage chat history (last 20 Q/A)
    // -----------------------------------------------------------------
    //
    // The history lives ENTIRELY in localStorage so the user's
    // questions / answers never leave the browser (the server side
    // already persists a sanitised retrieval diag via R68/C2 keyed
    // by query_id; that's a different surface and never carries
    // raw answer text).
    //
    // Schema (key = ``r68_ask_ai_history``):
    //   [{q, a, ts, manager, technology, days, query_id, retrieval_method}]
    //
    // Cap: ``R68_HISTORY_MAX = 20`` entries, FIFO eviction.

    var R68_HISTORY_KEY = 'r68_ask_ai_history';
    var R68_HISTORY_MAX = 20;
    var r68HistoryCard = document.getElementById('r68ChatHistoryCard');
    var r68HistoryList = document.getElementById('r68ChatHistoryList');
    var r68HistoryCount = document.getElementById('r68ChatHistoryCount');
    var r68HistoryClearBtn = document.getElementById('r68ChatHistoryClearBtn');
    var r68HistoryToggleBtn = document.getElementById('r68ChatHistoryToggleBtn');
    var r68HistoryToggleIcon = document.getElementById('r68ChatHistoryToggleIcon');

    function _r68LoadHistory() {
        try {
            var raw = window.localStorage.getItem(R68_HISTORY_KEY);
            if (!raw) { return []; }
            var parsed = JSON.parse(raw);
            return Array.isArray(parsed) ? parsed : [];
        } catch (_) {
            return [];
        }
    }

    function _r68SaveHistory(entries) {
        try {
            window.localStorage.setItem(
                R68_HISTORY_KEY,
                JSON.stringify(entries.slice(-R68_HISTORY_MAX))
            );
        } catch (_) { /* localStorage may be disabled / full */ }
    }

    function _r68RecordHistoryEntry(question, answer, data) {
        if (!question || typeof question !== 'string') { return; }
        var entries = _r68LoadHistory();
        // De-dupe: if the most-recent entry has the same question,
        // overwrite it instead of stacking.
        if (entries.length && entries[entries.length - 1].q === question) {
            entries.pop();
        }
        var managerEl = document.getElementById('aiManager');
        var techEl = document.getElementById('aiTech');
        var daysEl = document.getElementById('aiDays');
        var entry = {
            q: String(question).slice(0, 600),
            a: String(answer || '').slice(0, 1200),
            ts: new Date().toISOString(),
            manager: managerEl ? managerEl.value : '',
            technology: techEl ? techEl.value : '',
            days: daysEl ? daysEl.value : '',
            query_id: (data && data.query_id) || '',
            retrieval_method: (data && data.retrieval_method) || ''
        };
        entries.push(entry);
        // FIFO trim.
        while (entries.length > R68_HISTORY_MAX) {
            entries.shift();
        }
        _r68SaveHistory(entries);
        _r68RenderHistory();
    }

    function _r68RenderHistory() {
        if (!r68HistoryCard || !r68HistoryList) { return; }
        var entries = _r68LoadHistory();
        if (!entries.length) {
            r68HistoryCard.style.display = 'none';
            return;
        }
        r68HistoryCard.style.display = '';
        if (r68HistoryCount) {
            r68HistoryCount.textContent = String(entries.length);
        }
        // Clear and re-render.
        while (r68HistoryList.firstChild) {
            r68HistoryList.removeChild(r68HistoryList.firstChild);
        }
        // Render newest-first for convenience.
        for (var i = entries.length - 1; i >= 0; i--) {
            r68HistoryList.appendChild(_r68BuildHistoryRow(entries[i], i));
        }
    }

    function _r68BuildHistoryRow(entry, index) {
        var li = document.createElement('li');
        li.className = 'list-group-item px-0 py-2';
        li.dataset.r68HistoryIndex = String(index);
        var row = document.createElement('div');
        row.className = 'd-flex flex-column';
        // Header: timestamp + scope tags.
        var header = document.createElement('div');
        header.className = 'd-flex flex-wrap align-items-center gap-1 mb-1 small text-muted';
        var ts = document.createElement('span');
        try {
            ts.textContent = new Date(entry.ts).toLocaleString();
        } catch (_) {
            ts.textContent = String(entry.ts || '');
        }
        header.appendChild(ts);
        if (entry.manager) {
            var mgrBadge = document.createElement('span');
            mgrBadge.className = 'badge bg-light text-muted border';
            mgrBadge.textContent = entry.manager;
            header.appendChild(mgrBadge);
        }
        if (entry.technology) {
            var techBadge = document.createElement('span');
            techBadge.className = 'badge bg-light text-muted border';
            techBadge.textContent = entry.technology;
            header.appendChild(techBadge);
        }
        if (entry.days) {
            var daysBadge = document.createElement('span');
            daysBadge.className = 'badge bg-light text-muted border';
            daysBadge.textContent = entry.days + 'd';
            header.appendChild(daysBadge);
        }
        row.appendChild(header);
        // Question text.
        var qDiv = document.createElement('div');
        qDiv.className = 'fw-semibold small text-body mb-1';
        qDiv.textContent = entry.q || '(empty question)';
        row.appendChild(qDiv);
        // Answer snippet (collapsed).
        var aDiv = document.createElement('div');
        aDiv.className = 'text-muted small mb-2';
        aDiv.style.cssText = 'max-height:3.6em;overflow:hidden;text-overflow:ellipsis;';
        aDiv.textContent = entry.a || '(no answer captured)';
        row.appendChild(aDiv);
        // Action row.
        var actions = document.createElement('div');
        actions.className = 'd-flex flex-wrap gap-2';
        actions.appendChild(_r68HistoryActionButton('Re-ask', 'fa-rotate-right', function () {
            questionInput.value = entry.q;
            askAI(entry.q);
        }));
        actions.appendChild(_r68HistoryActionButton('Refine', 'fa-pen-to-square', function () {
            questionInput.value = entry.q;
            try { questionInput.focus(); } catch (_) { /* noop */ }
        }));
        actions.appendChild(_r68HistoryActionButton('Copy', 'fa-copy', function () {
            var combined = 'Q: ' + (entry.q || '') + '\n\nA: ' + (entry.a || '');
            try {
                if (navigator.clipboard && navigator.clipboard.writeText) {
                    navigator.clipboard.writeText(combined).then(function () {
                        _r68ShowToast('Q+A copied to clipboard.', 'success');
                    }, function () {
                        _r68ShowToast('Could not copy Q+A to clipboard.', 'error');
                    });
                } else {
                    var ta = document.createElement('textarea');
                    ta.value = combined;
                    ta.style.position = 'fixed';
                    ta.style.opacity = '0';
                    document.body.appendChild(ta);
                    ta.select();
                    try { document.execCommand('copy'); } catch (_) { /* noop */ }
                    document.body.removeChild(ta);
                    _r68ShowToast('Q+A copied to clipboard.', 'success');
                }
            } catch (_e) {
                _r68ShowToast('Could not copy Q+A to clipboard.', 'error');
            }
        }));
        actions.appendChild(_r68HistoryActionButton('Delete', 'fa-trash', function () {
            var entries = _r68LoadHistory();
            if (index >= 0 && index < entries.length) {
                entries.splice(index, 1);
                _r68SaveHistory(entries);
                _r68RenderHistory();
                _r68ShowToast('Removed from history.', 'success');
            }
        }, 'btn-outline-danger'));
        row.appendChild(actions);
        li.appendChild(row);
        return li;
    }

    function _r68HistoryActionButton(label, icon, onClick, extraClass) {
        var btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'btn btn-sm ' + (extraClass || 'btn-outline-secondary');
        var iEl = document.createElement('i');
        iEl.className = 'fas ' + icon + ' me-1';
        btn.appendChild(iEl);
        var labelEl = document.createElement('span');
        labelEl.textContent = label;
        btn.appendChild(labelEl);
        btn.addEventListener('click', onClick);
        return btn;
    }

    if (r68HistoryClearBtn) {
        r68HistoryClearBtn.addEventListener('click', function () {
            try {
                window.localStorage.removeItem(R68_HISTORY_KEY);
            } catch (_) { /* noop */ }
            _r68RenderHistory();
            _r68ShowToast('History cleared.', 'success');
        });
    }

    if (r68HistoryToggleBtn && r68HistoryList && r68HistoryToggleIcon) {
        r68HistoryToggleBtn.addEventListener('click', function () {
            var isOpen = r68HistoryList.style.display !== 'none';
            r68HistoryList.style.display = isOpen ? 'none' : '';
            r68HistoryToggleIcon.className = isOpen
                ? 'fas fa-chevron-down'
                : 'fas fa-chevron-up';
        });
    }

    // Patch askAI's success path to record into history.  We do this
    // via a wrapper on _r68RenderDebugChip so the recording happens
    // on every successful answer with the same data shape we already
    // hand the chip renderer.
    var _origRenderDebugChip = _r68RenderDebugChip;
    _r68RenderDebugChip = function (data) {
        try {
            _origRenderDebugChip(data);
        } catch (_) { /* noop */ }
        try {
            // ``answerContent.textContent`` carries the rendered
            // answer text (sans HTML chrome like badges).  Use it
            // as the canonical answer for the history record.
            var answerText = answerContent ? (answerContent.textContent || '') : '';
            _r68RecordHistoryEntry(lastAskedQuestion, answerText, data || {});
        } catch (_) { /* noop */ }
    };

    // Initial render in case there's already history from a prior
    // session.
    _r68RenderHistory();

    // -----------------------------------------------------------------
    // Round 68 / Build 42 (C9): 3-step progress indicator + cancel
    // button + first-time empty-state tip.
    // -----------------------------------------------------------------

    var R68_STEP_IDS = ['r68StepFetch', 'r68StepRetrieve', 'r68StepSynthesize'];
    var r68LoadingStepsContainer = document.getElementById('r68LoadingSteps');
    var r68CancelBtn = document.getElementById('r68CancelBtn');

    // Track the active AbortController so the Cancel button can
    // abort the in-flight request.  ``askAI`` writes to this
    // module-level var on every request; the button reads it.
    var _r68ActiveAbortController = null;

    function _r68SetStep(stepNumber) {
        if (!r68LoadingStepsContainer) { return; }
        for (var i = 0; i < R68_STEP_IDS.length; i++) {
            var li = document.getElementById(R68_STEP_IDS[i]);
            if (!li) { continue; }
            var badge = li.querySelector('span.badge');
            var label = li.querySelector('span:not(.badge)');
            if (i + 1 < stepNumber) {
                // Done.
                if (badge) {
                    badge.className = 'badge bg-success text-light me-2';
                    badge.textContent = '\u2713';
                }
                if (label) {
                    label.classList.remove('text-muted');
                    label.classList.add('text-success');
                }
            } else if (i + 1 === stepNumber) {
                // Active.
                if (badge) {
                    badge.className = 'badge bg-primary text-light me-2';
                    badge.textContent = String(stepNumber);
                }
                if (label) {
                    label.classList.remove('text-muted');
                    label.classList.remove('text-success');
                    label.classList.add('text-body', 'fw-semibold');
                }
            } else {
                // Pending.
                if (badge) {
                    badge.className = 'badge bg-light text-muted border me-2';
                    badge.textContent = String(i + 1);
                }
                if (label) {
                    label.classList.add('text-muted');
                    label.classList.remove('text-body', 'fw-semibold', 'text-success');
                }
            }
        }
        try {
            r68LoadingStepsContainer.setAttribute('aria-valuenow', String(stepNumber));
        } catch (_) { /* noop */ }
    }

    if (r68CancelBtn) {
        r68CancelBtn.addEventListener('click', function () {
            var ctl = (window._R68_ASK_AI_RUNTIME || {}).activeAbort;
            if (ctl) {
                try {
                    ctl.abort();
                    _r68ShowToast('Request cancelled.', 'success');
                } catch (_) {
                    _r68ShowToast('Could not cancel request.', 'error');
                }
            } else {
                _r68ShowToast('No in-flight request to cancel.', 'error');
            }
        });
    }

    // Patch askAI to register the abort controller on the module-level
    // slot before each fetch.  We do this by monkey-patching
    // window.fetch -- but that's too invasive.  Instead, we expose
    // the active controller via a setter the askAI body already
    // populates.  The simplest path is to read _abortCtl from the
    // closure -- which we can't.  So we hook into the loading state
    // visibility transition: when loadingState becomes visible we
    // assume a new request started.  This keeps the cancel wiring
    // independent of askAI's internals.
    //
    // Lower-friction alternative: expose a globally-visible
    // _r68RegisterAbort(ctl) hook that askAI calls.  The askAI
    // body above sets ``_abortCtl`` -- we mirror that into our
    // module-level slot via an interval-based observer on the
    // loading state.
    //
    // Concretely: when the loading state becomes visible, we hook
    // into the next animation frame and try to find the active
    // controller via a global the askAI body sets.  Since askAI is
    // already inside our DOMContentLoaded scope, we can just expose
    // a setter and let askAI call it.  See the patch above askAI
    // for the actual hook.

    // Empty-state tip card.  Dismissed permanently via localStorage.
    var R68_TIP_DISMISSED_KEY = 'r68_ask_ai_tip_dismissed';
    var r68EmptyStateTip = document.getElementById('r68EmptyStateTip');
    var r68EmptyStateTipDismissBtn = document.getElementById('r68EmptyStateTipDismissBtn');

    function _r68ShouldShowTip() {
        try {
            return window.localStorage.getItem(R68_TIP_DISMISSED_KEY) !== '1';
        } catch (_) {
            return true;
        }
    }

    function _r68DismissTip() {
        try {
            window.localStorage.setItem(R68_TIP_DISMISSED_KEY, '1');
        } catch (_) { /* noop */ }
        if (r68EmptyStateTip) {
            r68EmptyStateTip.style.display = 'none';
        }
    }

    if (r68EmptyStateTipDismissBtn) {
        r68EmptyStateTipDismissBtn.addEventListener('click', _r68DismissTip);
    }

    if (r68EmptyStateTip && _r68ShouldShowTip()) {
        r68EmptyStateTip.style.display = '';
    }
});

// -----------------------------------------------------------------
// Round 68 / Build 42 (C9): module-level abort hook.
// -----------------------------------------------------------------
//
// ``askAI`` (defined inside the DOMContentLoaded scope above) needs
// a way to register its AbortController so the Cancel button (also
// inside the same scope) can call ``.abort()`` on it.  The simplest
// portable option is to store it on a window-namespaced slot that
// both can see.  We do NOT use window.* directly to avoid polluting
// the global namespace; instead we assign to a single namespaced
// object.
//
// askAI populates ``_R68_ASK_AI_RUNTIME.activeAbort`` whenever it
// starts a new fetch; the Cancel button reads it.  Both code paths
// live in the same closure-scoped DOMContentLoaded handler so a
// consistent reference is guaranteed.
window._R68_ASK_AI_RUNTIME = window._R68_ASK_AI_RUNTIME || { activeAbort: null };
