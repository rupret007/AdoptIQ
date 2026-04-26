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
    // Round 6 / Phase 2.2: build DOM via createElement / textContent
    // instead of escape-then-rewrite-as-HTML.  The previous escape-then-
    // .replace pipeline was correct, but a single mis-ordered line in a
    // future change would re-introduce XSS.  This implementation only
    // EVER sets text via .textContent and creates element nodes
    // explicitly, so user/answer text can never become live HTML.
    function appendInline(parent, text) {
        if (text === '' || text == null) return;
        var idx = 0;
        var re = /(\*\*([^*]+?)\*\*|`([^`]+)`)/g;
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
    var lastAskedQuestion = '';

    function hideAllBanners() {
        ungroundedBanner.style.display = 'none';
        groundedFailedBanner.style.display = 'none';
        truncationBanner.style.display = 'none';
        partialDataBanner.style.display = 'none';
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
        fetch('/api/ask-ai-portfolio', _fetchOpts)
        .then(_askAiJson)
        .then(function(data) {
            _clearStepTimer();
            _clearAbortTimer();
            loadingState.style.display = 'none';
            askBtn.disabled = false;
            if (data.ok) {
                formatAnswerInto(answerContent, (data.answer && data.answer.trim()) ? data.answer : 'No answer was returned. Please try rephrasing your question.');
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
            var isAbort = err && (err.name === 'AbortError' || /aborted/i.test(String(err.message || '')));
            var msg = isAbort
                ? 'Request timed out after 90 seconds. The Snowflake fetch or Circuit AI call did not respond. Please try again or narrow the time range.'
                : 'Network error: ' + String(err);
            formatAnswerInto(answerContent, msg);
        })
        .finally(function() {
            _clearStepTimer();
            _clearAbortTimer();
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

    document.querySelectorAll('.suggestion-btn').forEach(function(btn) {
        btn.addEventListener('click', function() {
            questionInput.value = this.textContent;
            askAI(this.textContent);
        });
    });
});
