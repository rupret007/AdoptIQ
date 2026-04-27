/* Round 26 / Phase C — AdoptIQ Intelligence status poller.

   Drives two UI surfaces:
     1. The compact navbar badge ([data-intel-badge]) defined in
        templates/base.html.  Always present once the page loads.
     2. The larger banner on / ([data-intel-banner]) defined in
        templates/analyze.html.  Optional; only present on the
        analysis page.

   Polls /api/intel/status (GET, no auth required beyond session)
   and rewrites both surfaces in lockstep so the badge and banner
   never disagree.  The "Run now" button POSTs to /api/intel/refresh
   with the page CSRF token.

   Polling cadence:
     * 5 s while the indexer reports in_progress
     * 60 s while idle / completed / errored
   Hidden tabs are throttled to 60 s regardless to avoid wasting
   server cycles on background tabs.

   Security notes (rules: codeguard-0-client-side-web-security):
     * No innerHTML usage with untrusted data — every server value
       lands in textContent or class lists.
     * CSRF token comes from <meta name="csrf-token"> only; we do
       not accept it from query string or postMessage.
     * Refresh button is debounced for 2 s after a successful
       POST so a wedged user double-click cannot DoS the indexer.
*/

(function () {
    'use strict';

    var STATUS_URL = '/api/intel/status';
    var REFRESH_URL = '/api/intel/refresh';
    var UPLOAD_FORM_ID = 'adoptiq-intel-upload-form';
    var UPLOAD_FEEDBACK = '[data-intel-upload-feedback]';
    var POLL_FAST_MS = 5000;
    var POLL_SLOW_MS = 60000;
    var REFRESH_DEBOUNCE_MS = 2000;

    function getCsrfToken() {
        try {
            var meta = document.querySelector('meta[name="csrf-token"]');
            if (meta && meta.getAttribute) {
                return meta.getAttribute('content') || '';
            }
            var input = document.querySelector('input[name="csrf_token"]');
            if (input && input.value) { return input.value; }
        } catch (_) { /* fall through */ }
        return '';
    }

    function classifyState(payload) {
        if (!payload) { return 'unknown'; }
        if (payload.enabled === false) { return 'disabled'; }
        if (payload.boot && payload.boot.in_progress) { return 'running'; }
        if (payload.boot && payload.boot.last_error) { return 'error'; }
        if (payload.available === false) { return 'unavailable'; }
        if (payload.boot && payload.boot.last_finished_at) { return 'idle'; }
        return 'idle';
    }

    function stateToBadgeClass(state) {
        switch (state) {
            case 'running':     return 'bg-primary';
            case 'idle':        return 'bg-success';
            case 'error':       return 'bg-danger';
            case 'unavailable': return 'bg-warning text-dark';
            case 'disabled':    return 'bg-secondary';
            default:            return 'bg-secondary';
        }
    }

    function stateToLabel(state) {
        switch (state) {
            case 'running':     return 'Indexing';
            case 'idle':        return 'Idle';
            case 'error':       return 'Error';
            case 'unavailable': return 'Unavailable';
            case 'disabled':    return 'Disabled';
            default:            return 'Intelligence';
        }
    }

    function buildSummary(payload, state) {
        if (!payload) { return 'Status unavailable.'; }
        if (state === 'disabled') {
            return 'Idle — intelligence indexing is disabled.';
        }
        if (state === 'running') {
            return 'Indexing CSOne reports…';
        }
        if (state === 'error') {
            var kind = (payload.boot && payload.boot.last_error_kind) || 'error';
            var msg = (payload.boot && payload.boot.last_error) || 'Unknown error.';
            return 'Last run failed (' + kind + ') — ' + msg;
        }
        if (state === 'unavailable') {
            return 'Indexer unavailable — ' + (payload.reason || 'no details');
        }
        if (payload.boot && payload.boot.last_finished_at) {
            return 'Last run finished ' + payload.boot.last_finished_at + '.';
        }
        return 'Idle — no run yet this session.';
    }

    function paintBadge(state, payload) {
        var badge = document.querySelector('[data-intel-badge]');
        if (!badge) { return; }
        var pill = badge.querySelector('.badge');
        var label = badge.querySelector('[data-intel-badge-text]');
        var pillClass = stateToBadgeClass(state);
        if (pill) {
            pill.className = 'badge rounded-pill ' + pillClass;
            pill.style.fontWeight = '500';
        }
        if (label) {
            label.textContent = stateToLabel(state);
        }
        badge.setAttribute('data-state', state);
        var title = 'AdoptIQ Intelligence: ' + stateToLabel(state).toLowerCase();
        if (state === 'running' && payload && payload.boot && payload.boot.last_started_at) {
            title += ' (started ' + payload.boot.last_started_at + ')';
        } else if (state === 'idle' && payload && payload.boot && payload.boot.last_finished_at) {
            title += ' (last finished ' + payload.boot.last_finished_at + ')';
        } else if (state === 'error' && payload && payload.boot && payload.boot.last_error) {
            title += ' — ' + payload.boot.last_error;
        }
        badge.setAttribute('title', title);
    }

    function paintBanner(state, payload) {
        var banner = document.querySelector('[data-intel-banner]');
        if (!banner) { return; }
        banner.setAttribute('data-state', state);
        var pill = banner.querySelector('[data-intel-banner-state-pill]');
        var pillText = banner.querySelector('[data-intel-banner-state-text]');
        var summary = banner.querySelector('[data-intel-banner-summary]');
        var pillClass = stateToBadgeClass(state);
        if (pill) {
            pill.className = 'badge rounded-pill ' + pillClass;
        }
        if (pillText) {
            pillText.textContent = stateToLabel(state);
        }
        if (summary) {
            summary.textContent = buildSummary(payload, state);
        }
    }

    function paint(payload) {
        var state = classifyState(payload);
        paintBadge(state, payload);
        paintBanner(state, payload);
        return state;
    }

    var pollHandle = null;

    function scheduleNextPoll(state) {
        if (pollHandle) {
            window.clearTimeout(pollHandle);
            pollHandle = null;
        }
        var hidden = (typeof document.hidden === 'boolean') ? document.hidden : false;
        var delay = (state === 'running' && !hidden) ? POLL_FAST_MS : POLL_SLOW_MS;
        pollHandle = window.setTimeout(pollOnce, delay);
    }

    function pollOnce() {
        fetch(STATUS_URL, {
            method: 'GET',
            credentials: 'same-origin',
            headers: { 'Accept': 'application/json' }
        }).then(function (resp) {
            if (!resp.ok) { throw new Error('HTTP ' + resp.status); }
            return resp.json();
        }).then(function (data) {
            var state = paint(data);
            scheduleNextPoll(state);
        }).catch(function () {
            // Network / parse error — slow down polling but keep going.
            scheduleNextPoll('error');
        });
    }

    function bindRefreshButton() {
        var btn = document.querySelector('[data-intel-run-now]');
        if (!btn) { return; }
        btn.addEventListener('click', function (ev) {
            ev.preventDefault();
            if (btn.disabled) { return; }
            btn.disabled = true;
            var token = getCsrfToken();
            fetch(REFRESH_URL, {
                method: 'POST',
                credentials: 'same-origin',
                headers: {
                    'Accept': 'application/json',
                    'X-CSRFToken': token,
                    'X-CSRF-Token': token,
                    'Content-Type': 'application/x-www-form-urlencoded'
                },
                body: ''
            }).then(function (resp) {
                return resp.json().catch(function () { return null; });
            }).then(function (data) {
                if (data) { paint(data); }
            }).catch(function () { /* swallow */ })
              .then(function () {
                  window.setTimeout(function () {
                      btn.disabled = false;
                      pollOnce();
                  }, REFRESH_DEBOUNCE_MS);
              });
        });
    }

    function bindUploadForm() {
        var form = document.getElementById(UPLOAD_FORM_ID);
        if (!form) { return; }
        var feedback = form.querySelector(UPLOAD_FEEDBACK);
        var submit = form.querySelector('button[type="submit"]');
        form.addEventListener('submit', function (ev) {
            ev.preventDefault();
            if (submit) { submit.disabled = true; }
            if (feedback) { feedback.textContent = 'Uploading…'; }
            var formData = new FormData(form);
            var token = getCsrfToken();
            fetch(form.action, {
                method: 'POST',
                credentials: 'same-origin',
                headers: {
                    'Accept': 'application/json',
                    'X-CSRFToken': token,
                    'X-CSRF-Token': token
                },
                body: formData
            }).then(function (resp) {
                return resp.json().catch(function () { return null; }).then(function (data) {
                    return { ok: resp.ok, data: data };
                });
            }).then(function (result) {
                if (result.ok && result.data && result.data.ok !== false) {
                    if (feedback) {
                        feedback.textContent = 'Uploaded — indexer will pick it up on the next pass.';
                    }
                    form.reset();
                    pollOnce();
                } else {
                    var msg = (result.data && (result.data.error || result.data.reason)) || 'Upload failed.';
                    if (feedback) { feedback.textContent = msg; }
                }
            }).catch(function () {
                if (feedback) { feedback.textContent = 'Upload failed (network error).'; }
            }).then(function () {
                if (submit) { submit.disabled = false; }
            });
        });
    }

    function init() {
        bindRefreshButton();
        bindUploadForm();
        if (typeof document.addEventListener === 'function') {
            document.addEventListener('visibilitychange', function () {
                // When the tab becomes visible again, re-sync state
                // immediately rather than waiting for the slow tick.
                if (!document.hidden) { pollOnce(); }
            });
        }
        pollOnce();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
