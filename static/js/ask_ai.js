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

    function formatAnswerInto(targetEl, text) {
        while (targetEl.firstChild) targetEl.removeChild(targetEl.firstChild);
        if (!text || typeof text !== 'string') return;
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

    function askAI(question, opts) {
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
                    askAI(question, nextOpts);
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
                formatAnswerInto(answerContent, (data.answer && data.answer.trim()) ? data.answer : 'No answer was returned. Please try rephrasing your question.');
                // Round 68 / Build 42 (C5): always render the debug
                // chip on a successful answer so an operator can
                // file a support ticket with the debug ID.
                _r68RenderDebugChip(data);
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
