/*
 * AdoptIQ Quit / Shutdown button  (Round 60)
 *
 * Wires the navbar Quit button (#adoptiq-quit-btn) to the
 * server-side ``POST /api/shutdown`` endpoint.  See the Round 60
 * comment block in app_simple.py for the auth contract and the
 * SIGTERM-based shutdown sequencing.
 *
 * UX flow:
 *   1) User clicks #adoptiq-quit-btn.
 *   2) ``window.confirm`` for an explicit "are you sure?".
 *   3) POST /api/shutdown with the page CSRF token.
 *   4a) On 202 -> swap the page to the pre-rendered
 *       #adoptiq-shutdown-overlay so the user knows the local
 *       server has stopped and the tab is safe to close.
 *   4b) On 409 (analyses still running) -> open the pre-rendered
 *       Bootstrap modal #adoptiq-quit-confirm-modal listing the
 *       jobs in progress; the modal's "Force quit anyway" button
 *       re-POSTs with force=1 and behaves like 4a on success.
 *   4c) On 403 / network error -> ephemeral toast + re-enable the
 *       button so the user can retry or copy the error.
 *
 * Security / robustness notes:
 *   - All HTML for overlay + modal lives in templates/base.html
 *     (rendered server-side); this file only reads-and-mutates
 *     existing nodes' textContent / hidden / classList.  No
 *     innerHTML construction with user data, no eval, no inline
 *     event handlers.
 *   - CSRF token is read from <meta name="csrf-token">.  When the
 *     meta tag is missing (shouldn't happen on any rendered page,
 *     but defensive), we fall back to no header and the server
 *     will respond 403.
 *   - The button is disabled while the request is in flight so
 *     impatient double-clicks don't queue up duplicate SIGTERMs.
 *   - Listed in-progress entries are escaped via textContent so a
 *     malicious manager / customer name from a corrupted
 *     analysis_status.json cannot inject HTML into the modal.
 *   - IIFE-wrapped, no globals.
 */
(function () {
    'use strict';

    var QUIT_URL = '/api/shutdown';
    var BTN_ID = 'adoptiq-quit-btn';
    var OVERLAY_ID = 'adoptiq-shutdown-overlay';
    var MODAL_ID = 'adoptiq-quit-confirm-modal';
    var FORCE_BTN_ID = 'adoptiq-quit-force-btn';
    var LIST_ID = 'adoptiq-quit-confirm-list';
    var SUMMARY_ID = 'adoptiq-quit-confirm-summary';

    function readCsrfToken() {
        try {
            var meta = document.querySelector('meta[name="csrf-token"]');
            if (meta && typeof meta.getAttribute === 'function') {
                return meta.getAttribute('content') || '';
            }
        } catch (_) {
            /* defensive -- fall through */
        }
        return '';
    }

    function disableButton(btn) {
        if (!btn) { return; }
        btn.disabled = true;
        btn.setAttribute('aria-busy', 'true');
    }

    function enableButton(btn) {
        if (!btn) { return; }
        btn.disabled = false;
        btn.removeAttribute('aria-busy');
    }

    function showOverlay() {
        var overlay = document.getElementById(OVERLAY_ID);
        if (!overlay) { return; }
        overlay.hidden = false;
        // Round 60: park keyboard focus inside the overlay so screen
        // readers immediately announce the shutdown state and the
        // user cannot tab back into a now-defunct UI.
        try { overlay.focus(); } catch (_) { /* ignore */ }
    }

    function showToast(message) {
        // Round 60: minimal Bootstrap-style toast that does NOT
        // depend on Bootstrap's toast container being present.  We
        // just inject a transient alert next to the navbar so the
        // user sees the error.  Auto-removes after 6 seconds.
        try {
            var existing = document.getElementById('adoptiq-quit-toast');
            if (existing && existing.parentNode) {
                existing.parentNode.removeChild(existing);
            }
            var toast = document.createElement('div');
            toast.id = 'adoptiq-quit-toast';
            toast.setAttribute('role', 'alert');
            toast.style.position = 'fixed';
            toast.style.top = '4rem';
            toast.style.right = '1rem';
            toast.style.zIndex = '2147483647';
            toast.style.maxWidth = '24rem';
            toast.style.padding = '0.75rem 1rem';
            toast.style.background = '#e31c3d';
            toast.style.color = '#fff';
            toast.style.borderRadius = '0.5rem';
            toast.style.boxShadow = '0 4px 16px rgba(0,0,0,0.25)';
            toast.style.fontSize = '0.95rem';
            toast.textContent = String(message || 'Quit failed.');
            document.body.appendChild(toast);
            window.setTimeout(function () {
                if (toast && toast.parentNode) {
                    toast.parentNode.removeChild(toast);
                }
            }, 6000);
        } catch (_) {
            /* last resort */
            try { window.alert(String(message || 'Quit failed.')); } catch (__) { /* ignore */ }
        }
    }

    function renderInProgressList(payload) {
        var list = document.getElementById(LIST_ID);
        var summary = document.getElementById(SUMMARY_ID);
        if (!list) { return; }
        // Clear any prior contents safely (no innerHTML wipe to
        // avoid CSP / sanitizer churn).
        while (list.firstChild) {
            list.removeChild(list.firstChild);
        }
        var entries = (payload && Array.isArray(payload.in_progress)) ? payload.in_progress : [];
        var count = (payload && typeof payload.in_progress_count === 'number') ? payload.in_progress_count : entries.length;
        if (summary) {
            if (count === 1) {
                summary.textContent = 'One analysis is still running. Quitting now will stop it.';
            } else {
                summary.textContent = count + ' analyses are still running. Quitting now will stop them.';
            }
        }
        for (var i = 0; i < entries.length; i++) {
            var entry = entries[i] || {};
            var li = document.createElement('li');
            // textContent escapes, so manager / type / id can never
            // inject HTML into the modal.
            var label = (entry.type || 'analysis') + ' \u2014 ' + (entry.manager || '\u2014');
            if (typeof entry.progress === 'number' && entry.progress > 0) {
                label += ' (' + entry.progress + '%)';
            }
            if (entry.current_step) {
                label += ' \u2014 ' + entry.current_step;
            }
            li.textContent = label;
            list.appendChild(li);
        }
    }

    function showForceModal(payload, btn) {
        renderInProgressList(payload);
        var modalEl = document.getElementById(MODAL_ID);
        if (!modalEl) { return; }
        var bs = (window && window.bootstrap) ? window.bootstrap : null;
        var modal = null;
        if (bs && typeof bs.Modal === 'function') {
            try {
                modal = bs.Modal.getOrCreateInstance(modalEl);
            } catch (_) {
                modal = null;
            }
        }
        if (modal && typeof modal.show === 'function') {
            modal.show();
        } else {
            // Bootstrap missing (very unlikely) -- fall back to confirm.
            var ok = window.confirm('Analyses still running. Force quit anyway?');
            if (ok) { sendShutdown(true, btn); }
            return;
        }
        var forceBtn = document.getElementById(FORCE_BTN_ID);
        if (forceBtn) {
            // Replace any previous handler so we don't stack
            // listeners across repeated 409 retries.
            var clone = forceBtn.cloneNode(true);
            forceBtn.parentNode.replaceChild(clone, forceBtn);
            clone.addEventListener('click', function (ev) {
                ev.preventDefault();
                if (modal && typeof modal.hide === 'function') {
                    modal.hide();
                }
                sendShutdown(true, btn);
            });
        }
    }

    function sendShutdown(force, btn) {
        var token = readCsrfToken();
        var headers = { 'X-Requested-With': 'XMLHttpRequest' };
        if (token) {
            headers['X-CSRFToken'] = token;
        }
        var formBody = force ? 'force=1' : '';
        if (formBody) {
            headers['Content-Type'] = 'application/x-www-form-urlencoded';
        }
        disableButton(btn);
        window.fetch(QUIT_URL, {
            method: 'POST',
            credentials: 'same-origin',
            headers: headers,
            body: formBody
        }).then(function (resp) {
            // Round 60: branch on status code first; the body
            // shape is consistent across 202 / 409 / 403.
            return resp.json().catch(function () { return {}; }).then(function (payload) {
                return { status: resp.status, payload: payload };
            });
        }).then(function (result) {
            if (result.status === 202 && result.payload && result.payload.ok) {
                showOverlay();
                return;
            }
            if (result.status === 409 && result.payload && result.payload.needs_force) {
                enableButton(btn);
                showForceModal(result.payload, btn);
                return;
            }
            enableButton(btn);
            var msg = (result.payload && result.payload.error)
                ? result.payload.error
                : 'Quit failed (HTTP ' + result.status + ').';
            showToast(msg);
        }).catch(function (err) {
            enableButton(btn);
            showToast('Quit failed: ' + ((err && err.message) ? err.message : 'network error'));
        });
    }

    function onQuitClick(ev) {
        ev.preventDefault();
        var btn = ev.currentTarget || document.getElementById(BTN_ID);
        var ok = window.confirm(
            "Quit AdoptIQ?\n\n" +
            "The local server will stop and you'll need to reopen the .app to use it again."
        );
        if (!ok) { return; }
        sendShutdown(false, btn);
    }

    function init() {
        var btn = document.getElementById(BTN_ID);
        if (!btn) { return; }
        btn.addEventListener('click', onQuitClick);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
