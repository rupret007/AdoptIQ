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

    // Round 26 - review (R26-003): defensive double-init guard.  The
    // poller is included exactly once via base.html today, but a future
    // bundle accident or hot-reload would otherwise bind two click
    // handlers on [data-intel-run-now] and two visibilitychange
    // listeners, doubling traffic to /api/intel/{status,refresh}.
    // Idempotent across DOMContentLoaded -> immediate-init races and
    // across multi-script-include accidents.
    if (window.__adoptiqIntelStatusInit) { return; }
    window.__adoptiqIntelStatusInit = true;

    var STATUS_URL = '/api/intel/status';
    var REFRESH_URL = '/api/intel/refresh';
    // Round 32 / Phase 2.E: persistent on/off toggle for AdoptIQ
    // Intelligence.  POSTs JSON {"enabled": bool} to the server,
    // which writes ~/Library/Application Support/AdoptIQ/settings.json
    // and mutates Config in-process so the change survives restarts
    // without anyone touching env vars.
    var SETTINGS_URL = '/api/settings/intelligence';
    var ENABLE_TOGGLE = '[data-intel-enable-toggle]';
    var UPLOAD_FORM_ID = 'adoptiq-intel-upload-form';
    var UPLOAD_FEEDBACK = '[data-intel-upload-feedback]';
    // Round 33 / Build8: SharePoint connection panel selectors + URLs.
    // Persisting the URL goes through /api/settings/sharepoint_url
    // (CSRF + allow-list validated server-side); sign-in / sign-out
    // hit the existing /api/corpus/sharepoint/* routes.
    var SHAREPOINT_PANEL = '[data-sharepoint-panel]';
    var SHAREPOINT_URL_INPUT = '[data-sharepoint-url-input]';
    var SHAREPOINT_SAVE_URL = '[data-sharepoint-save-url]';
    var SHAREPOINT_SIGNIN = '[data-sharepoint-signin]';
    var SHAREPOINT_SIGNOUT = '[data-sharepoint-signout]';
    var SHAREPOINT_FEEDBACK = '[data-sharepoint-feedback]';
    var SHAREPOINT_STATE_PILL = '[data-sharepoint-state-pill]';
    var SHAREPOINT_STATE_TEXT = '[data-sharepoint-state-text]';
    var SHAREPOINT_ACCOUNT = '[data-sharepoint-account]';
    var SHAREPOINT_DEVICE = '[data-sharepoint-devicecode]';
    var SHAREPOINT_DEVICE_URI = '[data-sharepoint-devicecode-uri]';
    var SHAREPOINT_DEVICE_USER = '[data-sharepoint-devicecode-user]';
    var SHAREPOINT_DEVICE_STATUS = '[data-sharepoint-devicecode-status]';
    var SHAREPOINT_URL_SAVE_URL = '/api/settings/sharepoint_url';
    var SHAREPOINT_SIGNIN_URL = '/api/corpus/sharepoint/signin';
    var SHAREPOINT_SIGNOUT_URL = '/api/corpus/sharepoint/signout';
    // Mirrors adoptiq_settings._SHAREPOINT_URL_RE so the UI can refuse
    // to POST a clearly-bad URL before the round-trip; the server does
    // the authoritative check.
    var SHAREPOINT_URL_RE = /^https:\/\/[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.sharepoint\.com\/[A-Za-z0-9._~:\/?#\[\]@!$&'()*+,;=%-]+$/;
    var POLL_FAST_MS = 5000;
    var POLL_SLOW_MS = 60000;
    var REFRESH_DEBOUNCE_MS = 2000;
    // Round 33 / Build8: device-code prompts expire after ~15 minutes
    // (server-supplied ``expires_in``).  Cap our polling in case the
    // user closes the tab and reopens; we should never poll forever.
    var SHAREPOINT_SIGNIN_POLL_MS = 3000;
    var SHAREPOINT_SIGNIN_MAX_POLL_MS = 16 * 60 * 1000;

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

    // Round 27: explicit, user-visible feedback for the "Run now" click.
    // The earlier implementation only repainted the global state via
    // ``paint(data)``, which silently no-oped when the new index pass
    // finished within the 2 s debounce window (zero new files) or when
    // the server returned ``refresh_started=false`` / a non-2xx status.
    // ``setRefreshFeedback`` writes a short, human-readable line into
    // the existing ``[data-intel-banner-summary]`` element so the user
    // always knows what just happened.  All writes use ``textContent``
    // (never ``innerHTML``) per codeguard-0-client-side-web-security.
    var REFRESH_FEEDBACK_CLASSES = [
        'intel-refresh-pending',
        'intel-refresh-success',
        'intel-refresh-error',
    ];

    function setRefreshFeedback(state, message) {
        var summary = document.querySelector(
            '[data-intel-banner] [data-intel-banner-summary]'
        );
        if (!summary) { return; }
        var i;
        for (i = 0; i < REFRESH_FEEDBACK_CLASSES.length; i += 1) {
            summary.classList.remove(REFRESH_FEEDBACK_CLASSES[i]);
        }
        if (state === 'pending') {
            summary.classList.add('intel-refresh-pending');
        } else if (state === 'success') {
            summary.classList.add('intel-refresh-success');
        } else if (state === 'error') {
            summary.classList.add('intel-refresh-error');
        }
        summary.textContent = String(message == null ? '' : message);
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
            // Round 27: tell the user we received the click before
            // the server has a chance to respond.  Without this the
            // button just greyed out for ~2 s with no other signal.
            setRefreshFeedback('pending', 'Refresh requested\u2026');
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
                // Round 27: capture both the status and the (best-effort)
                // JSON body so the success / failure branches below can
                // report something meaningful.  Do NOT swallow non-2xx
                // into ``paint(data)`` -- a 403 with no ``enabled``/
                // ``boot`` keys would otherwise classify as ``idle``
                // and silently mutate the navbar badge.
                var ok = resp.ok;
                var status = resp.status;
                return resp.json().catch(function () { return null; }).then(
                    function (data) {
                        return { ok: ok, status: status, data: data };
                    }
                );
            }).then(function (result) {
                if (!result.ok) {
                    setRefreshFeedback(
                        'error',
                        'Refresh failed: HTTP ' + result.status
                    );
                    return;
                }
                var data = result.data || {};
                if (data.refresh_started === true) {
                    // Re-sync state first so the pill flips to
                    // "Indexing" instead of waiting the full debounce;
                    // ``paint()`` rewrites ``[data-intel-banner-summary]``
                    // so the explicit refresh feedback below MUST come
                    // after to avoid being clobbered.
                    paint(data);
                    setRefreshFeedback(
                        'success',
                        'Refresh started; indexing\u2026'
                    );
                    pollOnce();
                } else {
                    var reason = (
                        data.refresh_error
                        || data.error
                        || 'unknown reason'
                    );
                    // refresh_started=false is still a 200, so the
                    // payload is the canonical status snapshot --
                    // safe to paint.  Order matters here too (see the
                    // success branch above).
                    paint(data);
                    setRefreshFeedback(
                        'error',
                        'Refresh not started: ' + reason
                    );
                }
            }).catch(function () {
                setRefreshFeedback('error', 'Refresh failed: network error');
            }).then(function () {
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

    function bindEnableToggle() {
        // Round 32 / Phase 2.E: persistent on/off switch.
        var box = document.querySelector(ENABLE_TOGGLE);
        if (!box) { return; }
        box.addEventListener('change', function () {
            var desired = !!box.checked;
            box.disabled = true;
            var token = getCsrfToken();
            fetch(SETTINGS_URL, {
                method: 'POST',
                credentials: 'same-origin',
                headers: {
                    'Accept': 'application/json',
                    'Content-Type': 'application/json',
                    'X-CSRFToken': token,
                    'X-CSRF-Token': token
                },
                body: JSON.stringify({ enabled: desired })
            }).then(function (resp) {
                var ok = resp.ok;
                var status = resp.status;
                return resp.json().catch(function () { return null; }).then(
                    function (data) { return { ok: ok, status: status, data: data }; }
                );
            }).then(function (result) {
                if (!result.ok || !result.data || result.data.ok !== true) {
                    // Revert the checkbox on failure so the UI doesn't
                    // misrepresent the persisted state.
                    box.checked = !desired;
                    var msg = (result.data && (result.data.error || result.data.refresh_error)) || ('HTTP ' + result.status);
                    setRefreshFeedback('error', 'Toggle failed: ' + msg);
                    return;
                }
                if (desired) {
                    setRefreshFeedback(
                        'success',
                        result.data.refresh_started
                            ? 'Intelligence enabled; indexing\u2026'
                            : 'Intelligence enabled.'
                    );
                } else {
                    setRefreshFeedback('success', 'Intelligence disabled.');
                }
                pollOnce();
            }).catch(function () {
                box.checked = !desired;
                setRefreshFeedback('error', 'Toggle failed: network error');
            }).then(function () {
                box.disabled = false;
            });
        });
    }

    // -----------------------------------------------------------------
    // Round 33 / Build8: SharePoint connection panel
    // -----------------------------------------------------------------

    var sharepointSigninPollHandle = null;
    var sharepointSigninPollDeadline = 0;

    function setSharepointFeedback(state, message) {
        var el = document.querySelector(SHAREPOINT_FEEDBACK);
        if (!el) { return; }
        el.classList.remove('text-success', 'text-danger', 'text-muted');
        if (state === 'success') {
            el.classList.add('text-success');
        } else if (state === 'error') {
            el.classList.add('text-danger');
        } else {
            el.classList.add('text-muted');
        }
        el.textContent = String(message == null ? '' : message);
    }

    function paintSharepointPanel(payload) {
        var panel = document.querySelector(SHAREPOINT_PANEL);
        if (!panel) { return; }
        var sp = (payload && payload.boot && payload.boot.sharepoint) || null;

        var state = 'unknown';
        var label = 'checking\u2026';
        var pillClass = 'bg-secondary';
        var account = '';
        var signedIn = false;
        var configured = false;

        if (sp) {
            configured = !!sp.configured;
            signedIn = !!sp.signed_in;
            account = sp.account || '';
            if (!configured) {
                state = 'not_configured';
                label = 'Not configured';
                pillClass = 'bg-warning text-dark';
            } else if (signedIn) {
                state = 'signed_in';
                label = 'Signed in';
                pillClass = 'bg-success';
            } else if (sp.error_kind === 'auth_required') {
                state = 'auth_required';
                label = 'Sign-in required';
                pillClass = 'bg-warning text-dark';
            } else if (sp.error_kind) {
                state = sp.error_kind;
                label = String(sp.error_kind).replace(/_/g, ' ');
                pillClass = 'bg-danger';
            } else {
                state = 'unknown';
                label = 'Status unknown';
                pillClass = 'bg-secondary';
            }
        }

        panel.setAttribute('data-state', state);

        var pill = panel.querySelector(SHAREPOINT_STATE_PILL);
        var pillText = panel.querySelector(SHAREPOINT_STATE_TEXT);
        if (pill) {
            pill.className = 'badge rounded-pill ' + pillClass;
        }
        if (pillText) {
            pillText.textContent = label;
        }

        var acctEl = panel.querySelector(SHAREPOINT_ACCOUNT);
        if (acctEl) {
            acctEl.textContent = signedIn && account ? ('Connected as ' + account) : '';
        }

        var signinBtn = panel.querySelector(SHAREPOINT_SIGNIN);
        var signoutBtn = panel.querySelector(SHAREPOINT_SIGNOUT);
        if (signinBtn) {
            signinBtn.style.display = (configured && !signedIn) ? '' : 'none';
        }
        if (signoutBtn) {
            signoutBtn.style.display = signedIn ? '' : 'none';
        }

        // Hide the device-code modal as soon as we observe signed_in.
        if (signedIn) {
            hideSharepointDeviceCode();
            stopSharepointSigninPoll();
        }

        // Reflect the persisted folder URL into the input only when the
        // user is not currently editing it.  ``document.activeElement``
        // check avoids stomping on a value the user just typed.
        try {
            var input = panel.querySelector(SHAREPOINT_URL_INPUT);
            if (input && document.activeElement !== input && sp && typeof sp.folder_url === 'string') {
                if (!input.value && sp.folder_url) {
                    input.value = sp.folder_url;
                }
            }
        } catch (_) { /* non-fatal */ }
    }

    function hideSharepointDeviceCode() {
        var box = document.querySelector(SHAREPOINT_DEVICE);
        if (box) { box.style.display = 'none'; }
    }

    function showSharepointDeviceCode(envelope) {
        var box = document.querySelector(SHAREPOINT_DEVICE);
        if (!box) { return; }
        var uriEl = box.querySelector(SHAREPOINT_DEVICE_URI);
        var userEl = box.querySelector(SHAREPOINT_DEVICE_USER);
        var statusEl = box.querySelector(SHAREPOINT_DEVICE_STATUS);
        var verUri = envelope && envelope.verification_uri ? String(envelope.verification_uri) : '';
        var userCode = envelope && envelope.user_code ? String(envelope.user_code) : '';
        if (uriEl) {
            // Clamp to https only -- never accept a non-https
            // verification URI from the server.
            if (/^https:\/\//i.test(verUri)) {
                uriEl.setAttribute('href', verUri);
                uriEl.textContent = verUri;
            } else {
                uriEl.setAttribute('href', '#');
                uriEl.textContent = 'microsoft.com/devicelogin';
            }
        }
        if (userEl) {
            userEl.textContent = userCode;
        }
        if (statusEl) {
            statusEl.textContent = '';
        }
        box.style.display = '';
    }

    function setSharepointDeviceCodeStatus(text) {
        var statusEl = document.querySelector(SHAREPOINT_DEVICE_STATUS);
        if (statusEl) {
            statusEl.textContent = String(text == null ? '' : text);
        }
    }

    function stopSharepointSigninPoll() {
        if (sharepointSigninPollHandle) {
            window.clearTimeout(sharepointSigninPollHandle);
            sharepointSigninPollHandle = null;
        }
        sharepointSigninPollDeadline = 0;
    }

    function startSharepointSigninPoll() {
        stopSharepointSigninPoll();
        sharepointSigninPollDeadline = Date.now() + SHAREPOINT_SIGNIN_MAX_POLL_MS;
        var tick = function () {
            if (Date.now() > sharepointSigninPollDeadline) {
                setSharepointDeviceCodeStatus('Sign-in window expired \u2014 click Connect to try again.');
                stopSharepointSigninPoll();
                return;
            }
            fetch(STATUS_URL, {
                method: 'GET',
                credentials: 'same-origin',
                headers: { 'Accept': 'application/json' }
            }).then(function (resp) {
                if (!resp.ok) { throw new Error('HTTP ' + resp.status); }
                return resp.json();
            }).then(function (data) {
                paint(data);
                paintSharepointPanel(data);
                var sp = (data && data.boot && data.boot.sharepoint) || null;
                if (sp && sp.signed_in) {
                    setSharepointFeedback('success', 'Signed in to Microsoft.');
                    hideSharepointDeviceCode();
                    stopSharepointSigninPoll();
                    return;
                }
                sharepointSigninPollHandle = window.setTimeout(tick, SHAREPOINT_SIGNIN_POLL_MS);
            }).catch(function () {
                sharepointSigninPollHandle = window.setTimeout(tick, SHAREPOINT_SIGNIN_POLL_MS);
            });
        };
        sharepointSigninPollHandle = window.setTimeout(tick, SHAREPOINT_SIGNIN_POLL_MS);
    }

    function bindSharepointUrlSave() {
        var btn = document.querySelector(SHAREPOINT_SAVE_URL);
        if (!btn) { return; }
        btn.addEventListener('click', function (ev) {
            ev.preventDefault();
            if (btn.disabled) { return; }
            var input = document.querySelector(SHAREPOINT_URL_INPUT);
            var url = input ? String(input.value || '').trim() : '';
            if (url && !SHAREPOINT_URL_RE.test(url)) {
                setSharepointFeedback(
                    'error',
                    'Invalid URL \u2014 must be https://<tenant>.sharepoint.com/...'
                );
                return;
            }
            btn.disabled = true;
            setSharepointFeedback('pending', 'Saving\u2026');
            var token = getCsrfToken();
            fetch(SHAREPOINT_URL_SAVE_URL, {
                method: 'POST',
                credentials: 'same-origin',
                headers: {
                    'Accept': 'application/json',
                    'Content-Type': 'application/json',
                    'X-CSRFToken': token,
                    'X-CSRF-Token': token
                },
                body: JSON.stringify({ url: url })
            }).then(function (resp) {
                var ok = resp.ok;
                var status = resp.status;
                return resp.json().catch(function () { return null; }).then(function (data) {
                    return { ok: ok, status: status, data: data };
                });
            }).then(function (result) {
                if (!result.ok || !result.data || result.data.ok !== true) {
                    var msg = (result.data && result.data.error) || ('HTTP ' + result.status);
                    setSharepointFeedback('error', 'Save failed: ' + msg);
                    return;
                }
                setSharepointFeedback(
                    'success',
                    result.data.configured ? 'URL saved.' : 'URL cleared.'
                );
                pollOnce();
            }).catch(function () {
                setSharepointFeedback('error', 'Save failed: network error');
            }).then(function () {
                btn.disabled = false;
            });
        });
    }

    function bindSharepointSignin() {
        var btn = document.querySelector(SHAREPOINT_SIGNIN);
        if (!btn) { return; }
        btn.addEventListener('click', function (ev) {
            ev.preventDefault();
            if (btn.disabled) { return; }
            btn.disabled = true;
            setSharepointFeedback('pending', 'Starting Microsoft sign-in\u2026');
            var token = getCsrfToken();
            fetch(SHAREPOINT_SIGNIN_URL, {
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
                var ok = resp.ok;
                var status = resp.status;
                return resp.json().catch(function () { return null; }).then(function (data) {
                    return { ok: ok, status: status, data: data };
                });
            }).then(function (result) {
                if (!result.ok || !result.data || result.data.ok !== true) {
                    var msg = (result.data && result.data.error) || ('HTTP ' + result.status);
                    setSharepointFeedback('error', 'Sign-in failed: ' + msg);
                    return;
                }
                showSharepointDeviceCode(result.data);
                setSharepointFeedback('pending', 'Awaiting Microsoft sign-in\u2026');
                startSharepointSigninPoll();
            }).catch(function () {
                setSharepointFeedback('error', 'Sign-in failed: network error');
            }).then(function () {
                btn.disabled = false;
            });
        });
    }

    function bindSharepointSignout() {
        var btn = document.querySelector(SHAREPOINT_SIGNOUT);
        if (!btn) { return; }
        btn.addEventListener('click', function (ev) {
            ev.preventDefault();
            if (btn.disabled) { return; }
            // eslint-disable-next-line no-alert
            if (!window.confirm('Sign out of Microsoft?  Indexing will pause until you sign in again.')) {
                return;
            }
            btn.disabled = true;
            setSharepointFeedback('pending', 'Signing out\u2026');
            stopSharepointSigninPoll();
            hideSharepointDeviceCode();
            var token = getCsrfToken();
            fetch(SHAREPOINT_SIGNOUT_URL, {
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
                var ok = resp.ok;
                return resp.json().catch(function () { return null; }).then(function (data) {
                    return { ok: ok, data: data };
                });
            }).then(function (result) {
                if (!result.ok || !result.data || result.data.ok !== true) {
                    var msg = (result.data && result.data.error) || 'unknown error';
                    setSharepointFeedback('error', 'Sign-out failed: ' + msg);
                    return;
                }
                setSharepointFeedback('success', 'Signed out of Microsoft.');
                pollOnce();
            }).catch(function () {
                setSharepointFeedback('error', 'Sign-out failed: network error');
            }).then(function () {
                btn.disabled = false;
            });
        });
    }

    // Re-paint the SharePoint panel on every status poll.
    var _basePaint = paint;
    paint = function (payload) {
        var state = _basePaint(payload);
        try {
            paintSharepointPanel(payload);
        } catch (_) { /* never break the navbar badge on a panel error */ }
        return state;
    };

    function init() {
        bindRefreshButton();
        bindUploadForm();
        bindEnableToggle();
        bindSharepointUrlSave();
        bindSharepointSignin();
        bindSharepointSignout();
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
