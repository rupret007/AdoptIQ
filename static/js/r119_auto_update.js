/* Round 119 / Build 88: automatic-update mode card (Preferences hub).
 *
 * Wires the Preferences card that lets the operator choose how the
 * cross-platform Tier-C auto-updater behaves on this machine, and shows
 * the current update status.
 *
 * Endpoints:
 *   GET  /api/update/status -> {update_available, latest_build,
 *                               latest_version, artifact, update_mode,
 *                               releases_folder_found, last_error,
 *                               current_build}
 *   POST /api/settings/auto-update-mode -> {mode: off|notify|auto}
 *   POST /api/update/apply -> triggers a verified self-replace (idle-gated)
 *
 * XSS posture: every server-echoed value (mode, version, error) is
 * rendered via textContent, never innerHTML. No eval, no inline handlers.
 *
 * Source-shape pinned by tests/test_round119_auto_update_ui.py.
 */

/* eslint-disable no-var */
(function () {
    'use strict';

    var STATUS_URL = '/api/update/status';
    var MODE_URL = '/api/settings/auto-update-mode';
    var APPLY_URL = '/api/update/apply';

    var CARD_SELECTOR = '[data-auto-update-card]';
    var SELECT_SELECTOR = '[data-auto-update-mode-select]';
    var SAVE_SELECTOR = '[data-auto-update-mode-save]';
    var INSTALL_SELECTOR = '[data-auto-update-install-now]';
    var FEEDBACK_SELECTOR = '[data-auto-update-feedback]';
    var STATUS_PILL_SELECTOR = '[data-auto-update-status-pill]';
    var STATUS_TEXT_SELECTOR = '[data-auto-update-status-text]';

    var ALLOWED_MODES = ['off', 'notify', 'auto'];

    function getCsrfToken() {
        try {
            var meta = document.querySelector('meta[name="csrf-token"]');
            if (meta && meta.getAttribute) {
                return meta.getAttribute('content') || '';
            }
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

    function setStatusPill(text, level) {
        var pill = document.querySelector(STATUS_PILL_SELECTOR);
        var txt = document.querySelector(STATUS_TEXT_SELECTOR);
        if (txt) { txt.textContent = text || '\u2014'; }
        if (!pill) { return; }
        pill.className = 'badge rounded-pill';
        if (level === 'available') {
            pill.classList.add('bg-warning', 'text-dark');
        } else if (level === 'current') {
            pill.classList.add('bg-success');
        } else if (level === 'error') {
            pill.classList.add('bg-danger');
        } else {
            pill.classList.add('bg-secondary');
        }
    }

    function paint(status) {
        if (!status || typeof status !== 'object') {
            setStatusPill('Status unavailable', 'muted');
            return;
        }
        var mode = ALLOWED_MODES.indexOf(status.update_mode) >= 0
            ? status.update_mode : 'auto';
        var sel = document.querySelector(SELECT_SELECTOR);
        if (sel) { sel.value = mode; }

        var installBtn = document.querySelector(INSTALL_SELECTOR);
        if (status.update_available === true) {
            var v = status.latest_version ? ('v' + status.latest_version) : '';
            var b = (status.latest_build !== null && status.latest_build !== undefined)
                ? ('build ' + status.latest_build) : '';
            setStatusPill(('Update available ' + v + ' ' + b).trim(), 'available');
            // Only offer the manual install button when not fully automatic
            // (auto mode applies on its own when idle).
            if (installBtn) {
                if (mode === 'off') {
                    installBtn.classList.add('d-none');
                } else {
                    installBtn.classList.remove('d-none');
                }
            }
        } else if (status.releases_folder_found === false) {
            setStatusPill('Releases folder not synced', 'muted');
            if (installBtn) { installBtn.classList.add('d-none'); }
        } else if (status.last_error) {
            setStatusPill('Last check: ' + String(status.last_error), 'error');
            if (installBtn) { installBtn.classList.add('d-none'); }
        } else {
            var cb = (status.current_build !== null && status.current_build !== undefined)
                ? (' (build ' + status.current_build + ')') : '';
            setStatusPill('Up to date' + cb, 'current');
            if (installBtn) { installBtn.classList.add('d-none'); }
        }
    }

    function fetchStatus() {
        fetch(STATUS_URL, { headers: { 'Accept': 'application/json' } })
            .then(function (resp) {
                if (!resp.ok) { throw new Error('status ' + resp.status); }
                return resp.json();
            })
            .then(paint)
            .catch(function () {
                setStatusPill('Status unavailable', 'muted');
            });
    }

    function saveMode() {
        var sel = document.querySelector(SELECT_SELECTOR);
        if (!sel) { return; }
        var mode = sel.value;
        if (ALLOWED_MODES.indexOf(mode) < 0) {
            setFeedback('error', 'Invalid mode.');
            return;
        }
        setFeedback('muted', 'Saving\u2026');
        fetch(MODE_URL, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': getCsrfToken(),
                'X-Requested-With': 'XMLHttpRequest'
            },
            body: JSON.stringify({ mode: mode })
        })
            .then(function (resp) {
                return resp.json().then(function (data) {
                    return { ok: resp.ok, data: data };
                });
            })
            .then(function (res) {
                if (res.ok && res.data && res.data.ok) {
                    setFeedback('success', 'Saved: ' + mode);
                    fetchStatus();
                } else {
                    var msg = (res.data && res.data.error) || 'Save failed.';
                    setFeedback('error', String(msg));
                }
            })
            .catch(function () {
                setFeedback('error', 'Network error saving update mode.');
            });
    }

    function installNow() {
        setFeedback('muted', 'Starting update\u2026');
        fetch(APPLY_URL, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': getCsrfToken(),
                'X-Requested-With': 'XMLHttpRequest'
            },
            body: JSON.stringify({})
        })
            .then(function (resp) {
                return resp.json().then(function (data) {
                    return { ok: resp.ok, status: resp.status, data: data };
                });
            })
            .then(function (res) {
                var data = res.data || {};
                if (res.status === 409 || data.needs_force) {
                    setFeedback('error', 'An analysis is running; update deferred until idle.');
                    return;
                }
                if (res.ok && (data.ok || data.would_update)) {
                    setFeedback('success', 'Update starting; the app will relaunch.');
                } else {
                    var msg = data.reason || data.error || 'Update could not start.';
                    setFeedback('error', String(msg));
                }
            })
            .catch(function () {
                setFeedback('error', 'Network error starting update.');
            });
    }

    function init() {
        if (!document.querySelector(CARD_SELECTOR)) { return; }
        var saveBtn = document.querySelector(SAVE_SELECTOR);
        if (saveBtn) { saveBtn.addEventListener('click', saveMode); }
        var installBtn = document.querySelector(INSTALL_SELECTOR);
        if (installBtn) { installBtn.addEventListener('click', installNow); }
        fetchStatus();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

    // Expose for tests / programmatic refresh.
    window.AdoptIQAutoUpdate = {
        fetchStatus: fetchStatus,
        saveMode: saveMode,
        installNow: installNow,
        paint: paint
    };
})();
