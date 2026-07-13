/* Round 113 / C3: persisted default analysis scope Preferences card.
 *
 * Reads GET /api/settings/report-defaults to paint the three selects
 * (manager / technology / days) with the EFFECTIVE persisted values
 * (the server already drops stale manager/tech values that are no
 * longer on the live roster / TECH_CHOICES).  Save POSTs the chosen
 * scope; Clear resets all three to "unset".
 *
 * XSS posture: option values are server-rendered Jinja text and are
 * matched by value (select.value = ...), never injected as HTML.  The
 * only dynamic text written is the feedback line, set via textContent.
 * Mutating requests carry the CSRF token.
 */
(function () {
    'use strict';

    var ENDPOINT_URL = '/api/settings/report-defaults';
    var CARD_SELECTOR = '[data-report-defaults-card]';
    var MANAGER_SELECTOR = '[data-report-defaults-manager]';
    var TECH_SELECTOR = '[data-report-defaults-technology]';
    var DAYS_SELECTOR = '[data-report-defaults-days]';
    var SAVE_SELECTOR = '[data-report-defaults-save]';
    var CLEAR_SELECTOR = '[data-report-defaults-clear]';
    var FEEDBACK_SELECTOR = '[data-report-defaults-feedback]';

    function getCsrfToken() {
        try {
            var meta = document.querySelector('meta[name="csrf-token"]');
            if (meta && meta.getAttribute) { return meta.getAttribute('content') || ''; }
            var input = document.querySelector('input[name="csrf_token"]');
            if (input && input.value) { return input.value; }
        } catch (_e) { /* fall through */ }
        return '';
    }

    function setFeedback(level, msg) {
        var el = document.querySelector(FEEDBACK_SELECTOR);
        if (!el) { return; }
        el.textContent = msg || '';
        el.className = 'small ms-2';
        if (level === 'error') {
            el.classList.add('text-danger');
        } else if (level === 'success') {
            el.classList.add('text-success');
        } else {
            el.classList.add('text-muted');
        }
    }

    // Set a <select> to ``value`` only if that option exists; otherwise
    // leave it on its first ("unset") option so a stale persisted value
    // never selects a phantom option.
    function setSelectValue(sel, value) {
        if (!sel) { return; }
        var target = (value == null) ? '' : String(value);
        var found = false;
        for (var i = 0; i < sel.options.length; i += 1) {
            if (sel.options[i].value === target) { found = true; break; }
        }
        sel.value = found ? target : '';
    }

    function paint(payload) {
        var mgr = document.querySelector(MANAGER_SELECTOR);
        var tech = document.querySelector(TECH_SELECTOR);
        var days = document.querySelector(DAYS_SELECTOR);
        if (!payload || payload.ok !== true) { return; }
        setSelectValue(mgr, payload.default_manager || '');
        setSelectValue(tech, payload.default_technology || '');
        var d = (typeof payload.default_days === 'number') ? payload.default_days : 0;
        setSelectValue(days, String(d || 0));
        // When the persisted value was dropped as stale, surface a hint.
        try {
            var persisted = payload.persisted || {};
            var droppedMgr = persisted.default_manager
                && persisted.default_manager !== (payload.default_manager || '');
            var droppedTech = persisted.default_technology
                && persisted.default_technology !== (payload.default_technology || '');
            if (droppedMgr || droppedTech) {
                setFeedback('muted',
                    'A previously-saved value is no longer on the roster and was reset.');
            }
        } catch (_e) { /* noop */ }
    }

    function fetchDefaults() {
        fetch(ENDPOINT_URL, {
            method: 'GET',
            credentials: 'same-origin',
            headers: { 'Accept': 'application/json' }
        }).then(function (resp) {
            if (!resp.ok) { throw new Error('HTTP ' + resp.status); }
            return resp.json();
        }).then(function (data) {
            paint(data);
        }).catch(function () {
            setFeedback('error', 'Could not load saved defaults.');
        });
    }

    function save(clear) {
        var mgr = document.querySelector(MANAGER_SELECTOR);
        var tech = document.querySelector(TECH_SELECTOR);
        var days = document.querySelector(DAYS_SELECTOR);
        var body;
        if (clear) {
            body = { default_days: 0, default_manager: '', default_technology: '' };
        } else {
            var dval = days ? parseInt(days.value, 10) : 0;
            if (isNaN(dval)) { dval = 0; }
            body = {
                default_days: dval,
                default_manager: mgr ? mgr.value : '',
                default_technology: tech ? tech.value : ''
            };
        }
        var token = getCsrfToken();
        setFeedback('muted', clear ? 'Clearing\u2026' : 'Saving\u2026');
        fetch(ENDPOINT_URL, {
            method: 'POST',
            credentials: 'same-origin',
            headers: {
                'Accept': 'application/json',
                'Content-Type': 'application/json',
                'X-CSRFToken': token,
                'X-CSRF-Token': token
            },
            body: JSON.stringify(body)
        }).then(function (resp) {
            var ok = resp.ok;
            return resp.json().catch(function () { return null; }).then(function (data) {
                return { ok: ok, data: data };
            });
        }).then(function (result) {
            if (!result.ok || !result.data || result.data.ok !== true) {
                var msg = (result.data && result.data.error) || 'save failed';
                setFeedback('error', 'Could not save: ' + msg);
                return;
            }
            paint(result.data);
            setFeedback('success', clear ? 'Defaults cleared.' : 'Default scope saved.');
        }).catch(function () {
            setFeedback('error', 'Could not save: network error');
        });
    }

    function init() {
        var card = document.querySelector(CARD_SELECTOR);
        if (!card) { return; }
        var saveBtn = document.querySelector(SAVE_SELECTOR);
        var clearBtn = document.querySelector(CLEAR_SELECTOR);
        if (saveBtn) {
            saveBtn.addEventListener('click', function () { save(false); });
        }
        if (clearBtn) {
            clearBtn.addEventListener('click', function () { save(true); });
        }
        fetchDefaults();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
