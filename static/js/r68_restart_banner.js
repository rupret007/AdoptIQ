/* Round 68 / Build 42 (A2): restart-required banner client.
 *
 * Calls /api/version exactly once per page load.  When the server
 * reports ``restart_required: true`` (a newer .app is installed than
 * the currently-running process), unhide the pre-rendered banner near
 * the top of <main> so the operator cannot miss the trap that
 * produced Build 41 acceptance reports from a pre-Build-41 binary.
 *
 * Failure modes are deliberately silent: a 4xx/5xx, a network error,
 * or a missing banner element MUST NOT break the page or surface a
 * console error to the user.  The only side effect on success is the
 * banner becoming visible.
 */

(function () {
    'use strict';

    function fmtIso(value) {
        if (!value || typeof value !== 'string') {
            return '';
        }
        try {
            // Best-effort: parse ISO -> show local date/time so the
            // operator can match it to their own timeline ("install
            // 4:50 PM, started 1:24 AM" is much more legible than
            // raw UTC strings side by side).
            var d = new Date(value);
            if (Number.isNaN(d.getTime())) {
                return value;
            }
            return d.toLocaleString();
        } catch (err) {
            return value;
        }
    }

    function showBanner(payload) {
        var banner = document.getElementById('r68-restart-required-banner');
        if (!banner) {
            return;
        }
        var detail = document.getElementById('r68-restart-required-detail');
        if (detail) {
            var v = payload && payload.version ? String(payload.version) : '?';
            var b = payload && payload.build ? String(payload.build) : '?';
            var procStarted = fmtIso(payload && payload.process_started_at_utc);
            var dmgInstall = fmtIso(payload && payload.dmg_install_at_utc);
            var msg = 'Installed build: v' + v + ' build ' + b + '.';
            if (procStarted) {
                msg += '  Running process started at ' + procStarted + '.';
            }
            if (dmgInstall) {
                msg += '  New install detected at ' + dmgInstall + '.';
            }
            detail.textContent = msg;
        }
        if (banner.dataset) {
            banner.dataset.r68Version = (payload && payload.version) || '';
            banner.dataset.r68Build = (payload && payload.build) || '';
        }
        banner.removeAttribute('hidden');
    }

    function check() {
        try {
            var ctrl = (typeof AbortController !== 'undefined') ? new AbortController() : null;
            var opts = { credentials: 'same-origin' };
            if (ctrl) {
                opts.signal = ctrl.signal;
                // Hard 4s timeout -- this is a UX nicety, never block.
                window.setTimeout(function () {
                    try { ctrl.abort(); } catch (_) { /* noop */ }
                }, 4000);
            }
            fetch('/api/version', opts)
                .then(function (resp) {
                    if (!resp || !resp.ok) {
                        return null;
                    }
                    return resp.json();
                })
                .then(function (data) {
                    if (data && data.restart_required === true) {
                        showBanner(data);
                    }
                })
                .catch(function () { /* silent */ });
        } catch (_err) { /* silent */ }
    }

    // -----------------------------------------------------------------
    // Round 119 / Build 88: auto-update banner.
    //
    // Polls /api/update/status once per page load.  When a newer build is
    // available it unhides #r119-update-available-banner.  In ``auto``
    // mode the worker applies the update on its own (banner reads
    // "updating…"); in ``notify`` mode an "Install now" button POSTs to
    // /api/update/apply with the CSRF header.  All rendering is
    // textContent-only; failures are silent (never break the page).
    // -----------------------------------------------------------------
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

    function setUpdateFeedback(msg) {
        var el = document.getElementById('r119-update-feedback');
        if (el) { el.textContent = msg || ''; }
    }

    function installUpdate(btn) {
        if (btn) { btn.disabled = true; }
        setUpdateFeedback('Starting update\u2026');
        try {
            fetch('/api/update/apply', {
                method: 'POST',
                credentials: 'same-origin',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': getCsrfToken(),
                    'X-Requested-With': 'XMLHttpRequest'
                },
                body: '{}'
            })
                .then(function (resp) {
                    return resp.json().then(function (data) {
                        return { status: resp.status, ok: resp.ok, data: data };
                    });
                })
                .then(function (res) {
                    var data = res.data || {};
                    if (res.status === 409 || data.needs_force) {
                        setUpdateFeedback('An analysis is running; update deferred until idle.');
                        if (btn) { btn.disabled = false; }
                        return;
                    }
                    if (res.ok && (data.ok || data.would_update || data.state === 'applying')) {
                        setUpdateFeedback('Update starting; the app will relaunch.');
                    } else {
                        setUpdateFeedback('Update could not start; try again later.');
                        if (btn) { btn.disabled = false; }
                    }
                })
                .catch(function () {
                    setUpdateFeedback('Network error starting update.');
                    if (btn) { btn.disabled = false; }
                });
        } catch (_e) {
            if (btn) { btn.disabled = false; }
        }
    }

    function showUpdateBanner(status) {
        var banner = document.getElementById('r119-update-available-banner');
        if (!banner) { return; }
        var mode = status && status.update_mode ? String(status.update_mode) : 'auto';
        var headline = document.getElementById('r119-update-available-headline');
        var detail = document.getElementById('r119-update-available-detail');
        var btn = document.getElementById('r119-update-install-btn');
        var v = (status && status.latest_version) ? ('v' + status.latest_version) : '';
        var b = (status && status.latest_build !== null && status.latest_build !== undefined)
            ? ('build ' + status.latest_build) : '';
        if (banner.dataset) {
            banner.dataset.r119Build = (status && status.latest_build) || '';
        }
        if (headline) {
            headline.textContent = ('A newer AdoptIQ build is available: ' + v + ' ' + b + '.').replace(/\s+/g, ' ').trim();
        }
        if (mode === 'auto') {
            if (detail) {
                detail.textContent = 'It will install automatically when no analysis is running, then relaunch.';
            }
            if (btn) { btn.classList.add('d-none'); }
        } else {
            if (detail) {
                detail.textContent = 'Click to download, verify, and install it now.';
            }
            if (btn) {
                btn.classList.remove('d-none');
                btn.addEventListener('click', function () { installUpdate(btn); }, { once: false });
            }
        }
        banner.removeAttribute('hidden');
    }

    function checkUpdate() {
        try {
            fetch('/api/update/status', { credentials: 'same-origin', headers: { 'Accept': 'application/json' } })
                .then(function (resp) {
                    if (!resp || !resp.ok) { return null; }
                    return resp.json();
                })
                .then(function (data) {
                    if (data && data.update_available === true && data.update_mode !== 'off') {
                        showUpdateBanner(data);
                    }
                })
                .catch(function () { /* silent */ });
        } catch (_err) { /* silent */ }
    }

    function runChecks() {
        check();
        checkUpdate();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', runChecks, { once: true });
    } else {
        runChecks();
    }
})();
