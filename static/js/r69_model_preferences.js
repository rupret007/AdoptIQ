/* Round 69 / Build 43: shared model-preferences UI module.
 *
 * Wires up any [data-r69-model-card="ask_ai"] or
 * [data-r69-model-card="report"] container with a Test-gates-Save
 * UX so a typo'd model name or unprovisioned model cannot land in
 * settings.json.
 *
 * Required descendants of the card element:
 *   * [data-r69-model-input]   - text input for the model name
 *   * [data-r69-test-btn]      - "Test" button (clicks /api/llm/ping)
 *   * [data-r69-save-btn]      - "Save" button (clicks /api/settings/{ask-ai,report}-model)
 *   * [data-r69-result]        - aria-live region for status messages
 *   * [data-r69-active-badge]  - small badge that displays "active: <model>"
 *
 * Contract:
 *   * Save is initially disabled.
 *   * Any input change locks Save again so a previously-passed Test
 *     cannot be promoted with a new (untested) value.
 *   * Test enables Save IFF the server returns {ok: true} OR the input
 *     is empty (empty == clear override).
 *   * Save POSTs the trimmed value and refreshes the active badge from
 *     the server's response so the operator sees which precedence
 *     layer is currently winning (settings > env > config default).
 *
 * Loaded by templates/ask_ai.html (Ask AI surface) and
 * templates/analyze.html (Report surface).  The admin console
 * (enhanced_admin_dashboard_v2.py) carries its own inline copy of the
 * same logic because the admin template renders inline (no static
 * asset chain).
 */

(function () {
    'use strict';

    var ENDPOINTS = {
        ask_ai: {get: '/api/settings/ask-ai-model', post: '/api/settings/ask-ai-model'},
        report: {get: '/api/settings/report-model', post: '/api/settings/report-model'}
    };
    var PING_URL = '/api/llm/ping';

    function getCsrfToken() {
        var meta = document.querySelector('meta[name="csrf-token"]');
        return meta ? (meta.getAttribute('content') || '') : '';
    }

    function setResult(card, msg, kind) {
        var el = card.querySelector('[data-r69-result]');
        if (!el) return;
        el.textContent = String(msg || '');
        if (kind === 'ok') {
            el.style.color = '#198754';
        } else if (kind === 'err') {
            el.style.color = '#dc3545';
        } else if (kind === 'pending') {
            el.style.color = '#0d6efd';
        } else {
            el.style.color = ''; // inherit muted default
        }
    }

    function setActiveBadge(card, value) {
        var badge = card.querySelector('[data-r69-active-badge]');
        if (!badge) return;
        badge.textContent = 'active: ' + (value || '-');
    }

    function loadActive(card, getUrl) {
        fetch(getUrl, {method: 'GET', headers: {'Accept': 'application/json'}, credentials: 'same-origin'})
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (j) {
                if (!j || !j.ok) return;
                var input = card.querySelector('[data-r69-model-input]');
                if (input) input.value = j.persisted_value || '';
                setActiveBadge(card, j.active_value);
            })
            .catch(function () { /* silent: read path is best-effort */ });
    }

    function bindCard(card) {
        var key = card.getAttribute('data-r69-model-card');
        var conf = ENDPOINTS[key];
        if (!conf) return;

        var input = card.querySelector('[data-r69-model-input]');
        var testBtn = card.querySelector('[data-r69-test-btn]');
        var saveBtn = card.querySelector('[data-r69-save-btn]');
        if (!input || !testBtn || !saveBtn) return;

        // Any input change locks Save until next successful Test.  This is
        // the regression guard against "tested 'gpt-5-nano' then typo'd
        // and saved" scenarios.
        input.addEventListener('input', function () {
            saveBtn.disabled = true;
            setResult(card, '', 'neutral');
        });

        testBtn.addEventListener('click', function () {
            var value = (input.value || '').trim();
            if (!value) {
                // Empty == clear override, no ping needed.
                saveBtn.disabled = false;
                setResult(card, 'Empty value will clear the override (falls back to env / config default). Click Save to confirm.', 'neutral');
                return;
            }
            setResult(card, 'Pinging CircuIT...', 'pending');
            testBtn.disabled = true;
            var headers = {'Content-Type': 'application/json'};
            var csrf = getCsrfToken();
            if (csrf) headers['X-CSRFToken'] = csrf;
            fetch(PING_URL, {
                method: 'POST',
                headers: headers,
                credentials: 'same-origin',
                body: JSON.stringify({model_name: value})
            })
                .then(function (r) {
                    return r.json().then(function (j) { return [r.status, j]; })
                        .catch(function () { return [r.status, {ok: false, error: 'non-JSON response'}]; });
                })
                .then(function (pair) {
                    var status = pair[0], j = pair[1] || {};
                    if (status === 200 && j.ok) {
                        saveBtn.disabled = false;
                        setResult(card, 'OK (' + (j.latency_ms || '?') + 'ms). You may save.', 'ok');
                    } else {
                        saveBtn.disabled = true;
                        setResult(card, 'Test failed: ' + (j.error || ('HTTP ' + status)), 'err');
                    }
                })
                .catch(function () {
                    saveBtn.disabled = true;
                    setResult(card, 'Test failed: network error.', 'err');
                })
                .then(function () { testBtn.disabled = false; });
        });

        saveBtn.addEventListener('click', function () {
            var value = (input.value || '').trim();
            saveBtn.disabled = true;
            setResult(card, 'Saving...', 'pending');
            var headers = {'Content-Type': 'application/json'};
            var csrf = getCsrfToken();
            if (csrf) headers['X-CSRFToken'] = csrf;
            fetch(conf.post, {
                method: 'POST',
                headers: headers,
                credentials: 'same-origin',
                body: JSON.stringify({model_name: value})
            })
                .then(function (r) {
                    return r.json().then(function (j) { return [r.status, j]; })
                        .catch(function () { return [r.status, {ok: false, error: 'non-JSON response'}]; });
                })
                .then(function (pair) {
                    var status = pair[0], j = pair[1] || {};
                    if (status === 200 && j.ok) {
                        setActiveBadge(card, j.active_value);
                        setResult(card, 'Saved. Active model: ' + (j.active_value || '-'), 'ok');
                    } else {
                        // Re-enable Save so the operator can retry.
                        saveBtn.disabled = false;
                        setResult(card, 'Save failed: ' + (j.error || ('HTTP ' + status)), 'err');
                    }
                })
                .catch(function () {
                    saveBtn.disabled = false;
                    setResult(card, 'Save failed: network error.', 'err');
                });
        });

        loadActive(card, conf.get);
    }

    function init() {
        var cards = document.querySelectorAll('[data-r69-model-card]');
        for (var i = 0; i < cards.length; i++) bindCard(cards[i]);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
