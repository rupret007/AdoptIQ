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
    // Round 39 / corpus crypto self-heal: manual escape hatch endpoint.
    // Wired up to [data-intel-reset], which the paint() wrapper unhides
    // only when boot.last_error_kind === 'crypto'.  Confirms via a
    // native confirm() prompt before POSTing -- the action preserves
    // the user's current encrypted DB as <name>.broken-<utc> and
    // reinstalls the bundled baked snapshot.
    var RESET_URL = '/api/intel/reset';
    // Round 32 / Phase 2.E: persistent on/off toggle for AdoptIQ
    // Intelligence.  POSTs JSON {"enabled": bool} to the server,
    // which writes ~/Library/Application Support/AdoptIQ/settings.json
    // and mutates Config in-process so the change survives restarts
    // without anyone touching env vars.
    var SETTINGS_URL = '/api/settings/intelligence';
    var ENABLE_TOGGLE = '[data-intel-enable-toggle]';
    var UPLOAD_FORM_ID = 'adoptiq-intel-upload-form';
    var UPLOAD_FEEDBACK = '[data-intel-upload-feedback]';
    // Round 33 / Build8 -> Round 36: AdoptIQ Knowledge Corpus panel.
    // The MSAL/Graph signin/signout flow was removed in Round 36 --
    // the OneDrive desktop client now mirrors the canonical AdoptIQ
    // folder under Config.CSONE_ONEDRIVE_FOLDER and the daily refresh
    // worker re-indexes from that path.  The selectors retain their
    // ``data-sharepoint-*`` names purely for back-compat with existing
    // CSS / templates -- the implementation is OneDrive-sync-only.
    var SHAREPOINT_PANEL = '[data-sharepoint-panel]';
    var SHAREPOINT_FEEDBACK = '[data-sharepoint-feedback]';
    var SHAREPOINT_STATE_PILL = '[data-sharepoint-state-pill]';
    var SHAREPOINT_STATE_TEXT = '[data-sharepoint-state-text]';
    var SHAREPOINT_ACCOUNT = '[data-sharepoint-account]';
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
        // Round 53.3: ``blocked_no_onedrive`` is an actionable
        // "sign in to OneDrive" state, not a hard failure -- the
        // corpus panel already classifies it as a warning. The global
        // navbar badge and analyze banner used to flash red ``Error``
        // for the same payload because ``last_error`` was non-null,
        // contradicting the corpus panel sitting just below it.
        // Map the blocked source to a dedicated state so the badge,
        // banner, and panel all agree.
        if (payload.boot && payload.boot.source === 'blocked_no_onedrive') {
            return 'blocked';
        }
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
            case 'blocked':     return 'bg-warning text-dark';
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
            case 'blocked':     return 'Sign in to OneDrive';
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
        if (state === 'blocked') {
            // Round 53.3: surface the actionable remediation message
            // produced by ``corpus_bootstrap`` (rather than a generic
            // "Error") so the global banner matches the corpus panel.
            var blockedMsg = (payload.boot && payload.boot.last_error)
                || 'OneDrive sync of AI Projects/AdoptIQ_CSOne_Reports is required to unlock the corpus.';
            return blockedMsg;
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
        } else if (state === 'blocked' && payload && payload.boot && payload.boot.last_error) {
            title += ' — ' + payload.boot.last_error;
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
            // Round 53 / Phase 53.4: ``data-disabled-when`` gating.
            // ``btn.disabled`` covers both the ``disabled`` attribute
            // (set by paintGatedButtons) AND the in-flight debounce
            // below, so a single check guards both paths.
            if (btn.disabled
                || btn.getAttribute('aria-disabled') === 'true') { return; }
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

    // Round 39 / corpus crypto self-heal -- bind the manual "Reset
    // corpus" button.  The button stays hidden in healthy states; the
    // paint() wrapper below unhides it ONLY when the boot payload has
    // last_error_kind === 'crypto', so a healthy install never sees a
    // destructive control.  The click handler confirms before POSTing.
    function bindResetButton() {
        var btn = document.querySelector('[data-intel-reset]');
        if (!btn) { return; }
        btn.addEventListener('click', function (ev) {
            ev.preventDefault();
            // Round 53 / Phase 53.4: same gating applies to Reset --
            // a reset under blocked_no_onedrive would land the same
            // .enc on disk and we'd be back to "needs OneDrive" so
            // the click is intentionally a no-op while blocked.
            if (btn.disabled
                || btn.getAttribute('aria-disabled') === 'true') { return; }
            var ok = window.confirm(
                'Reset the local corpus?  Your current encrypted '
                + 'database will be preserved on disk as a .broken '
                + 'backup, then replaced from the bundled snapshot.  '
                + 'The next refresh will pick up any newer OneDrive '
                + 'files.  Continue?'
            );
            if (!ok) { return; }
            btn.disabled = true;
            setRefreshFeedback('pending', 'Resetting corpus\u2026');
            var token = getCsrfToken();
            fetch(RESET_URL, {
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
                var ok2 = resp.ok;
                var status = resp.status;
                return resp.json().catch(function () { return null; }).then(
                    function (data) {
                        return { ok: ok2, status: status, data: data };
                    }
                );
            }).then(function (result) {
                if (!result.ok) {
                    setRefreshFeedback(
                        'error', 'Reset failed: HTTP ' + result.status
                    );
                    return;
                }
                var data = result.data || {};
                if (data.ok === false) {
                    setRefreshFeedback(
                        'error',
                        'Reset failed: ' + (data.reason || 'unknown reason')
                    );
                    return;
                }
                var preserved = (typeof data.preserved_count === 'number')
                    ? data.preserved_count : 0;
                if (data.refresh_started === true) {
                    setRefreshFeedback(
                        'success',
                        'Corpus reset (preserved ' + preserved
                        + ' file' + (preserved === 1 ? '' : 's')
                        + '); reindexing\u2026'
                    );
                    pollOnce();
                } else {
                    setRefreshFeedback(
                        'success',
                        'Corpus reset (preserved ' + preserved
                        + ' file' + (preserved === 1 ? '' : 's')
                        + ').'
                    );
                }
            }).catch(function () {
                setRefreshFeedback('error', 'Reset failed: network error');
            }).then(function () {
                window.setTimeout(function () {
                    btn.disabled = false;
                    pollOnce();
                }, REFRESH_DEBOUNCE_MS);
            });
        });
    }

    // Round 39: visibility toggle for [data-intel-reset].  Only shown
    // when boot.last_error_kind === 'crypto'; healthy installs and
    // non-crypto error states keep the button hidden so a careless
    // click cannot wipe a working corpus.
    function paintResetButtonVisibility(payload) {
        var btn = document.querySelector('[data-intel-reset]');
        if (!btn) { return; }
        var kind = (payload && payload.boot && payload.boot.last_error_kind)
            ? String(payload.boot.last_error_kind) : '';
        if (kind === 'crypto') {
            btn.hidden = false;
            btn.removeAttribute('hidden');
        } else {
            btn.hidden = true;
            btn.setAttribute('hidden', '');
        }
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
    // Round 36 / onedrive-sync-auth: AdoptIQ Knowledge Corpus panel
    //
    // Renders the corpus state from ``boot.source`` x
    // ``boot.onedrive_status`` -- no buttons, no MSAL prompts.  The
    // OneDrive desktop client handles auth/MFA/admin-consent and
    // mirrors the canonical AdoptIQ folder to disk; we just stat()
    // it and surface the result.
    //
    // Selectors retain their ``data-sharepoint-*`` names for
    // back-compat with the existing analyze.html template.
    // -----------------------------------------------------------------

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

    // Computes one of eight panel states from the status payload.
    // Exposed (via window.__adoptiqCorpusPanelState) so the unit
    // tests can pin the rendering logic without touching the DOM.
    //
    //   * baked_synced         -- Active. OneDrive synced (N files).
    //                             Daily refresh enabled.
    //   * baked_not_synced     -- Active (baked snapshot).  OneDrive
    //                             folder not detected.
    //   * fresh_indexing       -- Indexing OneDrive folder...
    //   * fresh_not_synced     -- OneDrive sync required.
    //   * refreshing           -- Daily refresh in progress.
    //   * refresh_failed       -- Last refresh raised an error
    //                             (baked snapshot still served).
    //   * blocked_no_onedrive  -- Round 53 / Phase 53.4: corpus
    //                             unlock requires the canonical
    //                             OneDrive sentinel which is not
    //                             yet synced.  Buttons disabled;
    //                             clickable deep-link offered.
    //   * unknown              -- pre-poll / no payload yet.
    function classifyCorpusPanel(payload) {
        var boot = (payload && payload.boot) || null;
        if (!boot) { return 'unknown'; }
        if (boot.in_progress) { return 'refreshing'; }
        var source = (typeof boot.source === 'string') ? boot.source : '';
        var od = (typeof boot.onedrive_status === 'string') ? boot.onedrive_status : '';
        // Round 53 / Phase 53.4: blocked_no_onedrive takes precedence
        // over refresh_failed because a "no OneDrive sentinel" error
        // is the *cause* of the open failure -- showing
        // "refresh failed" instead would mislead users into clicking
        // the (disabled) Re-index button.
        if (source === 'blocked_no_onedrive') { return 'blocked_no_onedrive'; }
        if (boot.last_refresh_error) {
            return 'refresh_failed';
        }
        if (source === 'baked' && od === 'synced') { return 'baked_synced'; }
        if (source === 'baked') { return 'baked_not_synced'; }
        if (source === 'fresh' && od === 'synced') { return 'fresh_indexing'; }
        if (source === 'fresh') { return 'fresh_not_synced'; }
        return 'unknown';
    }

    function corpusPanelLabel(state) {
        switch (state) {
            case 'baked_synced':        return 'Active \u2022 OneDrive synced';
            case 'baked_not_synced':    return 'Active \u2022 baked snapshot';
            case 'fresh_indexing':      return 'Indexing OneDrive\u2026';
            case 'fresh_not_synced':    return 'OneDrive sync required';
            case 'refreshing':          return 'Refreshing\u2026';
            case 'refresh_failed':      return 'Last refresh failed';
            case 'blocked_no_onedrive': return 'Sign in to OneDrive';
            default:                    return 'checking\u2026';
        }
    }

    function corpusPanelPillClass(state) {
        switch (state) {
            case 'baked_synced':        return 'bg-success';
            case 'baked_not_synced':    return 'bg-info text-dark';
            case 'fresh_indexing':      return 'bg-primary';
            case 'fresh_not_synced':    return 'bg-warning text-dark';
            case 'refreshing':          return 'bg-primary';
            case 'refresh_failed':      return 'bg-danger';
            case 'blocked_no_onedrive': return 'bg-warning text-dark';
            default:                    return 'bg-secondary';
        }
    }

    function corpusPanelDetail(state, payload) {
        var boot = (payload && payload.boot) || {};
        var fileCount = (typeof boot.onedrive_file_count === 'number')
            ? boot.onedrive_file_count : null;
        switch (state) {
            case 'baked_synced':
                if (fileCount != null && fileCount > 0) {
                    return 'OneDrive synced (\u2265 ' + fileCount
                        + ' file' + (fileCount === 1 ? '' : 's')
                        + ').  Daily refresh enabled.';
                }
                return 'OneDrive synced.  Daily refresh enabled.';
            case 'baked_not_synced':
                return 'OneDrive folder not detected.  '
                    + 'Sign in to OneDrive and sync '
                    + '\u201CAI Projects/AdoptIQ_CSOne_Reports\u201D '
                    + 'to enable daily refresh.';
            case 'fresh_indexing':
                return 'Indexing OneDrive folder for the first time\u2026';
            case 'fresh_not_synced':
                return 'No baked snapshot is bundled and OneDrive sync '
                    + 'is not detected.  Sign in to OneDrive and sync '
                    + '\u201CAI Projects/AdoptIQ_CSOne_Reports\u201D '
                    + 'to populate the corpus.';
            case 'refreshing':
                return 'Refreshing knowledge corpus from OneDrive\u2026';
            case 'refresh_failed':
                var detail = boot.last_refresh_error
                    ? String(boot.last_refresh_error) : 'unknown';
                return 'Last refresh failed (' + detail
                    + ').  The baked snapshot is still being served.';
            case 'blocked_no_onedrive':
                return 'AdoptIQ needs you to sign in to OneDrive and '
                    + 'sync \u201CAI Projects/AdoptIQ_CSOne_Reports\u201D '
                    + 'to unlock the corpus.  The corpus is encrypted '
                    + 'against a key that lives in that OneDrive '
                    + 'folder, so until it is synced AdoptIQ cannot '
                    + 'decrypt the bundled snapshot.';
            default:
                return '';
        }
    }

    // Round 53 / Phase 53.4.1: scheme allow-list mirroring the
    // server-side ``_R53_ONEDRIVE_DEEP_LINK_SCHEMES`` in
    // ``app_simple._r53_safe_onedrive_deep_link``.  Defense in depth:
    // even if the server payload smuggles a hostile URL through (env
    // override, supply-chain), the renderer refuses to use it for an
    // ``href``.  No anchor click can result in JS execution this way.
    var R53_DEEP_LINK_SCHEMES = ['http://', 'https://', 'odopen:', 'ms-onedrive:'];

    // Round 54 / F2 -- length cap mirroring the server-side
    // ``_R53_ONEDRIVE_DEEP_LINK_MAX_BYTES`` (2048).  A hostile env
    // override could pass an oversized URL through the server cap on
    // a stale build; the JS-side cap is defense in depth so a long
    // string never lands in an anchor href the user could fat-finger
    // a copy of.  Counts UTF-16 code units which is a conservative
    // overestimate of the UTF-8 byte count -- if it passes here it is
    // also under the server cap.
    var R53_DEEP_LINK_MAX_LEN = 2048;

    function r53SafeDeepLink(url) {
        if (typeof url !== 'string') { return null; }
        var trimmed = url.trim();
        if (!trimmed) { return null; }
        if (trimmed.length > R53_DEEP_LINK_MAX_LEN) { return null; }
        var lower = trimmed.toLowerCase();
        for (var i = 0; i < R53_DEEP_LINK_SCHEMES.length; i += 1) {
            if (lower.indexOf(R53_DEEP_LINK_SCHEMES[i]) === 0) {
                return trimmed;
            }
        }
        return null;
    }

    // Round 53 / Phase 53.4: button gating.  Buttons annotated with
    // ``data-disabled-when="<state>"`` are hard-disabled (and the
    // corresponding listener short-circuits) while the panel sits in
    // that state.  Used by the analyze-page Re-index / Reset buttons
    // to prevent click-spam during ``blocked_no_onedrive`` (where the
    // refresh would just immediately fail again with the same error).
    function paintGatedButtons(state) {
        var nodes = document.querySelectorAll('[data-disabled-when]');
        for (var i = 0; i < nodes.length; i += 1) {
            var node = nodes[i];
            var blocking = String(node.getAttribute('data-disabled-when') || '');
            // Comma-separated list lets a single button gate on
            // multiple states (e.g. "blocked_no_onedrive,refreshing").
            var parts = blocking.split(',');
            var disabled = false;
            for (var j = 0; j < parts.length; j += 1) {
                if (parts[j].trim() === state) { disabled = true; break; }
            }
            if (disabled) {
                node.setAttribute('disabled', 'disabled');
                node.setAttribute('aria-disabled', 'true');
                // Bootstrap pill-button styling: dim visually so the
                // user sees the gate.  ``disabled`` alone is enough
                // for accessibility / form behavior.
                node.classList.add('disabled');
            } else {
                node.removeAttribute('disabled');
                node.removeAttribute('aria-disabled');
                node.classList.remove('disabled');
            }
        }
    }

    // Round 53 / Phase 53.4.1: render (or hide) the OneDrive deep-
    // link anchor.  Looks for a ``[data-onedrive-deep-link]`` slot
    // anywhere inside the panel and writes the href + visibility.
    // The slot is hidden in every non-blocked state so it never
    // confuses users who already have OneDrive synced.
    function paintDeepLink(state, payload) {
        var anchors = document.querySelectorAll('[data-onedrive-deep-link]');
        if (!anchors || anchors.length === 0) { return; }
        var rawUrl = (payload && payload.boot)
            ? payload.boot.onedrive_deep_link : null;
        var safe = r53SafeDeepLink(rawUrl);
        var visible = (state === 'blocked_no_onedrive' && safe);
        for (var i = 0; i < anchors.length; i += 1) {
            var a = anchors[i];
            if (visible) {
                a.setAttribute('href', safe);
                // codeguard-0-client-side-web-security: external link
                // hardening.  Always set noopener+noreferrer so the
                // OneDrive page cannot reach window.opener.
                a.setAttribute('target', '_blank');
                a.setAttribute('rel', 'noopener noreferrer');
                a.hidden = false;
                a.removeAttribute('hidden');
            } else {
                a.hidden = true;
                a.setAttribute('hidden', '');
                a.removeAttribute('href');
            }
        }
    }

    function paintSharepointPanel(payload) {
        var panel = document.querySelector(SHAREPOINT_PANEL);
        var state = classifyCorpusPanel(payload);
        // Button gating + deep-link rendering work even when the
        // SharePoint panel itself is absent (e.g. a future template
        // moves the buttons elsewhere), so we run them BEFORE the
        // early-return so they always paint per poll.
        try { paintGatedButtons(state); } catch (_) { /* ignore */ }
        try { paintDeepLink(state, payload); } catch (_) { /* ignore */ }
        if (!panel) { return; }
        var label = corpusPanelLabel(state);
        var pillClass = corpusPanelPillClass(state);
        var detail = corpusPanelDetail(state, payload);

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
            var boot = (payload && payload.boot) || {};
            if (boot.source === 'baked' && boot.indexed_at) {
                acctEl.textContent = 'Last bake ' + String(boot.indexed_at);
            } else {
                acctEl.textContent = '';
            }
        }

        if (detail) {
            // Round 53 / Phase 53.4: blocked_no_onedrive uses error
            // styling so the WARN-orange pill + the red feedback
            // text reinforce that this is an action required by the
            // user, not a transient hiccup.
            var feedbackKind = (
                state === 'fresh_not_synced'
                || state === 'refresh_failed'
                || state === 'blocked_no_onedrive'
            ) ? 'error' : 'pending';
            setSharepointFeedback(feedbackKind, detail);
        } else {
            setSharepointFeedback(null, '');
        }
    }

    // Exposed for unit tests (the panel rendering decisions are pure
    // functions of the payload; tests should not need to mount a DOM
    // to verify the eight-state matrix).
    // Round 53 / Phase 53.4 -- adds ``safeDeepLink`` so the URL
    // allow-list can be pinned by JS-level tests without spinning
    // up a Flask client to hit ``_r53_safe_onedrive_deep_link``.
    window.__adoptiqCorpusPanelState = {
        classify: classifyCorpusPanel,
        label: corpusPanelLabel,
        pillClass: corpusPanelPillClass,
        detail: corpusPanelDetail,
        safeDeepLink: r53SafeDeepLink,
    };

    // Re-paint the corpus panel on every status poll.
    var _basePaint = paint;
    paint = function (payload) {
        var state = _basePaint(payload);
        try {
            paintSharepointPanel(payload);
        } catch (_) { /* never break the navbar badge on a panel error */ }
        try {
            // Round 39 / corpus crypto self-heal: toggle the Reset
            // corpus button visibility per status payload.
            paintResetButtonVisibility(payload);
        } catch (_) { /* never break the badge on a button-toggle error */ }
        return state;
    };

    function init() {
        bindRefreshButton();
        bindResetButton();
        bindUploadForm();
        bindEnableToggle();
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
