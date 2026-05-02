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

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', check, { once: true });
    } else {
        check();
    }
})();
