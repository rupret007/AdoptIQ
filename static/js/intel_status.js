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
    // Round 39 / corpus crypto self-heal, updated in Round 107 for the
    // prebaked corpus model.  Wired up to [data-intel-reset], which
    // the paint() wrapper unhides only when boot.last_error_kind ===
    // 'crypto'. Confirms via a native confirm() prompt before POSTing:
    // the current encrypted DB is preserved as <name>.broken-<utc>,
    // then the next refresh rebuilds from all available local sources
    // and OneDrive when present.
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

    // Round 113 / C4: only the analyze page carries the full intel
    // banner ([data-intel-banner]) and/or the corpus panel
    // ([data-sharepoint-panel]).  Pages like /preferences, /history,
    // /ask-ai load this same script (it's in base.html) but have
    // neither surface, so the recurring /api/intel/status poll there
    // was pure redundant traffic.  Gate the recurring poll on the
    // presence of one of those surfaces.  The navbar badge
    // ([data-intel-badge], present on every page) is rendered
    // server-side on load and only reflects boot state, which does
    // not change mid-session on a page without the corpus controls --
    // so a one-shot refresh (e.g. after the Preferences toggle) is
    // still allowed, but the recurring loop is not started.
    function _intelPollSurfacePresent() {
        try {
            return !!(
                document.querySelector('[data-intel-banner]')
                || document.querySelector('[data-sharepoint-panel]')
            );
        } catch (_) {
            return false;
        }
    }

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
        // Round 108 / Corpus Smoothness: legacy OneDrive source labels
        // are optional refresh context now, not an Ask AI hard-block.
        // Keep the global banner calm; the detailed corpus panel can
        // still explain how to connect the optional OneDrive source.
        if (payload.boot && (
            payload.boot.source === 'blocked_no_onedrive'
            || payload.boot.source === 'signed_in_no_corpus'
        )) {
            return payload.available === false ? 'unavailable' : 'idle';
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
            case 'blocked':     return 'Optional refresh setup';
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
            return 'Updating the local Ask AI corpus from generated reports, uploads, and OneDrive when available…';
        }
        if (state === 'blocked') {
            // Round 53.3: surface the actionable remediation message
            // produced by ``corpus_bootstrap`` (rather than a generic
            // "Error") so the global banner matches the corpus panel.
            // Round 80: default fallback now references the
            // SharePoint shortcut workflow rather than the
            // owner-only "AI Projects/AdoptIQ_CSOne_Reports" path.
            var blockedMsg = (payload.boot && payload.boot.last_error)
                || 'OneDrive is optional; connect it when you want shared-source refresh coverage.';
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
        // Round 87 / Phase 5: toggle the "Reports remain safe to run
        // during indexing" clarifier.  Visible only while the panel
        // is in the ``running`` state (i.e. ``boot.in_progress`` is
        // truthy).  Build 62 acceptance feedback flagged that the
        // operator saw "Indexing CSOne reports..." with no signal
        // that report generation was unaffected; this line surfaces
        // the SSoT contract that core reports (Comprehensive, Compact,
        // Renewal, Leader) do NOT depend on the corpus index pass --
        // only Ask AI grounding does.  Hidden in every other state so
        // the line never appears outside an active index pass.
        var clarifier = banner.querySelector(
            '[data-intel-indexing-clarifier]'
        );
        if (clarifier) {
            if (state === 'running') {
                clarifier.removeAttribute('hidden');
            } else {
                clarifier.setAttribute('hidden', '');
            }
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
        // Round 113 / C2: the Preferences page has no [data-intel-banner]
        // wrapper, so fall through to a standalone
        // [data-intel-banner-summary] feedback target there.  Without
        // this the POST /api/settings/intelligence result was silently
        // dropped on /preferences.
        if (!summary) {
            summary = document.querySelector('[data-intel-banner-summary]');
        }
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
        // Round 113 / C4: never start the recurring loop on a page
        // without the banner/panel.  A one-shot pollOnce (e.g. the
        // Preferences toggle's badge refresh) is still allowed -- it
        // just won't schedule a follow-up tick here.
        if (!_intelPollSurfacePresent()) { return; }
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
            // Round 108: reset is shown only for genuine crypto/index
            // corruption. Missing OneDrive is optional refresh context,
            // not a reset blocker.
            if (btn.disabled
                || btn.getAttribute('aria-disabled') === 'true') { return; }
            var ok = window.confirm(
                'Reset the local corpus?  Your current encrypted '
                + 'database will be preserved on disk as a .broken '
                + 'backup, then rebuilt from generated reports, uploads, '
                + 'and OneDrive when available.  Continue?'
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

    // Computes one of the runtime-only panel states from the status payload.
    // Exposed (via window.__adoptiqCorpusPanelState) so the unit
    // tests can pin the rendering logic without touching the DOM.
    //
    //   * runtime_synced       -- Active. Corpus indexed locally from
    //                             the prebaked bundle and updates.
    //   * fresh_indexing       -- Building local corpus...
    //   * fresh_not_synced     -- Local corpus pending.
    //   * refreshing           -- Daily refresh in progress AND no
    //                             prior corpus is yet usable.  Round
    //                             109 promotes the "in_progress over
    //                             an already-available corpus" path
    //                             to ``runtime_synced`` so users do
    //                             not see "Indexing" while Ask AI is
    //                             actually serving lexical answers.
    //   * refresh_failed       -- Last refresh raised an error
    //                             (last good local corpus still served).
    //   * blocked_no_onedrive  -- Legacy source label shown only as
    //                             optional OneDrive refresh guidance.
    //   * unknown              -- pre-poll / no payload yet.
    //
    // Round 109 / Fix Indexing Hang: the boot pass is bounded by
    // ``ask_ai_vector_store._runtime_max_chunks_default`` so the
    // ``in_progress`` window is short.  Even when it does run, an
    // already-available corpus (``payload.available=true`` with
    // chunks > 0) keeps serving lexical answers, so we promote that
    // case to ``runtime_synced`` and surface dense backfill as a
    // separate quality note via ``r108DenseStatus``.
    function classifyCorpusPanel(payload) {
        var boot = (payload && payload.boot) || null;
        if (!boot) { return 'unknown'; }
        var available = !!(payload && payload.available);
        var corpus = (payload && payload.corpus) || {};
        var hasChunks = false;
        try {
            hasChunks = (typeof corpus.chunks === 'number')
                ? corpus.chunks > 0
                : false;
        } catch (e) { hasChunks = false; }
        var inProgress = !!boot.in_progress;
        if (inProgress && !(available && hasChunks)) {
            return 'refreshing';
        }
        var source = (typeof boot.source === 'string') ? boot.source : '';
        var od = (typeof boot.onedrive_status === 'string') ? boot.onedrive_status : '';
        // Round 83 / Build 59, downgraded in Round 108: these source
        // labels need different optional refresh copy and CTAs:
        //   * signed_in_no_corpus -- user signed in to Cisco
        //     OneDrive, but the corpus share isn't in their tree.
        //     One-click "Add corpus share" button surfaces here.
        //   * blocked_no_onedrive -- user not signed in to OneDrive
        //     at all (or signed in to a non-Cisco account).  Legacy
        //     state now renders optional refresh copy.
        if (source === 'signed_in_no_corpus') { return 'signed_in_no_corpus'; }
        // Round 108: legacy blocked_no_onedrive is optional refresh
        // guidance. Keep the dedicated panel copy when old state
        // snapshots carry the source label.
        if (source === 'blocked_no_onedrive') { return 'blocked_no_onedrive'; }
        if (boot.last_refresh_error) {
            return 'refresh_failed';
        }
        // Round 107 / Build 76: baked/self-healed source labels mean
        // the bundled corpus is already usable; OneDrive status is only
        // refresh context.
        if ((source === 'self_healed_baked' || source === 'baked') && od === 'synced') {
            return 'runtime_synced';
        }
        if (source === 'self_healed_baked' || source === 'baked') {
            return 'runtime_synced';
        }
        // Round 106 / Build 75: completed local corpus indexes are active
        // even when OneDrive is absent. OneDrive is now one possible source,
        // not a prerequisite for Ask AI grounding.
        if (source === 'fresh' && boot.completed) {
            return 'runtime_synced';
        }
        if (source === 'fresh' && od === 'synced') { return 'fresh_indexing'; }
        if (source === 'fresh') { return 'fresh_not_synced'; }
        return 'unknown';
    }

    function corpusPanelLabel(state) {
        switch (state) {
            case 'runtime_synced':      return 'Active \u2022 local corpus';
            case 'fresh_indexing':      return 'Indexing local corpus\u2026';
            case 'fresh_not_synced':    return 'Local corpus pending';
            case 'refreshing':          return 'Building local corpus\u2026';
            case 'refresh_failed':      return 'Last refresh failed';
            // Round 83 / Build 59: distinct from blocked_no_onedrive
            // -- the user IS signed in to OneDrive, they just need
            // to add the corpus share to their tree.
            case 'signed_in_no_corpus': return 'Add corpus share to OneDrive';
            case 'blocked_no_onedrive': return 'Optional OneDrive refresh';
            default:                    return 'checking\u2026';
        }
    }

    function corpusPanelPillClass(state) {
        switch (state) {
            case 'runtime_synced':      return 'bg-success';
            case 'fresh_indexing':      return 'bg-primary';
            case 'fresh_not_synced':    return 'bg-warning text-dark';
            case 'refreshing':          return 'bg-primary';
            case 'refresh_failed':      return 'bg-danger';
            // Round 108: warning pill means optional refresh coverage
            // is incomplete, not that Ask AI is hard-blocked.
            case 'signed_in_no_corpus': return 'bg-warning text-dark';
            case 'blocked_no_onedrive': return 'bg-warning text-dark';
            default:                    return 'bg-secondary';
        }
    }

    function r108DenseStatus(payload) {
        var boot = (payload && payload.boot) || {};
        var method = boot.ask_ai_retrieval_method
            ? String(boot.ask_ai_retrieval_method) : '';
        if (boot.dense_retrieval_status === 'stale_or_lexical') {
            return ' Dense retrieval is degraded; lexical fallback is active'
                + (method ? ' (' + method + ').' : '.');
        }
        // Round 109 / Fix Indexing Hang: when the bounded runtime
        // upsert has more chunks to backfill, surface that as a
        // quality note instead of leaving the panel implying a hang.
        if (boot.dense_retrieval_status === 'partial') {
            var remaining = (typeof boot.dense_rows_remaining === 'number')
                ? boot.dense_rows_remaining
                : null;
            if (remaining && remaining > 0) {
                return ' Dense retrieval is warming \u2022 backfilling '
                    + String(remaining) + ' chunks'
                    + (method ? ' (' + method + ').' : '.');
            }
            return ' Dense retrieval is warming'
                + (method ? ' (' + method + ').' : '.');
        }
        if (boot.embedder_status === 'ready' || boot.dense_retrieval_status === 'ready') {
            return ' Dense retrieval is ready'
                + (method ? ' (' + method + ').' : '.');
        }
        return '';
    }

    function r108ProgressDetail(payload) {
        var boot = (payload && payload.boot) || {};
        var stats = boot.last_stats || {};
        var pieces = [];
        if (typeof stats.files_parsed === 'number' || typeof stats.files_seen === 'number') {
            pieces.push('parsed ' + (stats.files_parsed || 0) + ' of '
                + (stats.files_seen || 0) + ' files');
        }
        if (typeof stats.files_skipped === 'number') {
            pieces.push('skipped ' + stats.files_skipped);
        }
        if (typeof stats.files_failed === 'number' && stats.files_failed > 0) {
            pieces.push('failed ' + stats.files_failed);
        }
        if (typeof stats.chunks_added === 'number') {
            pieces.push('chunks added ' + stats.chunks_added);
        }
        if (typeof boot.dense_vectors_upserted === 'number') {
            pieces.push('dense vectors updated ' + boot.dense_vectors_upserted);
        }
        if (boot.last_successful_update_at) {
            pieces.push('last update ' + String(boot.last_successful_update_at));
        }
        return pieces.length ? ' Update details: ' + pieces.join(', ') + '.' : '';
    }

    function r177PeerGuidanceHonesty() {
        // Round 177: operator cannot over-claim live Cisco accuracy.
        return ' Peer guidance is local-corpus only and is not live Cisco validation.';
    }

    function corpusPanelDetail(state, payload) {
        var boot = (payload && payload.boot) || {};
        var fileCount = (typeof boot.onedrive_file_count === 'number')
            ? boot.onedrive_file_count : null;
        switch (state) {
            case 'runtime_synced':
                if (fileCount != null && fileCount > 0) {
                    return 'Corpus indexed locally from available AdoptIQ sources, including OneDrive when present (\u2265 ' + fileCount
                        + ' file' + (fileCount === 1 ? '' : 's')
                        + ').' + r108ProgressDetail(payload) + r108DenseStatus(payload)
                        + r177PeerGuidanceHonesty();
                }
                return 'Corpus indexed locally from generated AdoptIQ reports and Intelligence uploads. OneDrive sync is optional.'
                    + r108ProgressDetail(payload) + r108DenseStatus(payload)
                    + r177PeerGuidanceHonesty();
            // Round 80: panel messaging now points users at the
            // canonical SharePoint share + "Add shortcut to OneDrive"
            // workflow. Pre-R80 the message asked them to sync
            // "AI Projects/AdoptIQ_CSOne_Reports" -- a path only the
            // corpus owner can sync. The clickable SharePoint deep
            // link is rendered separately via [data-onedrive-deep-link]
            // (Round 53.4.1) backed by Config.ADOPTIQ_CORPUS_ONEDRIVE_DEEP_LINK.
            case 'fresh_indexing':
                return 'Indexing the local knowledge corpus for the first time\u2026';
            case 'fresh_not_synced':
                return 'The local corpus will build from generated AdoptIQ reports, Intelligence uploads, and OneDrive data if that folder is available. You can run reports now; Ask AI grounding activates after the first index pass.';
            case 'refreshing':
                return 'Building the local knowledge corpus from generated AdoptIQ reports, Intelligence uploads, and available OneDrive data. Reports remain safe to run while this finishes.'
                    + r108ProgressDetail(payload);
            case 'refresh_failed':
                var detail = boot.last_refresh_error
                    ? String(boot.last_refresh_error) : 'unknown';
                return 'Last refresh failed (' + detail
                    + ').  The last good local corpus is still being served.';
            // Round 83 / Build 59: signed in to OneDrive but the
            // corpus share isn't in the user's tree yet.  Distinct
            // from blocked_no_onedrive: the OneDrive client is
            // already authenticated, so clicking "Add corpus share"
            // opens the SharePoint URL in the browser, SSO completes
            // automatically (the user is already signed in), the
            // share lands as a shortcut, and the next daily refresh
            // tick picks it up. No need to mention OneDrive sign-in.
            case 'signed_in_no_corpus':
                return 'OneDrive is signed in, but the AdoptIQ corpus '
                    + 'share is not in your OneDrive tree yet.  Click '
                    + '\u201CAdd corpus share to my OneDrive\u201D to '
                    + 'open the share in your browser; OneDrive will '
                    + 'mirror it locally and AdoptIQ will pick it up '
                    + 'on the next refresh. The local corpus remains usable.';
            // Round 108: legacy blocked state is optional refresh-source
            // guidance only. The prebaked/local corpus remains available.
            case 'blocked_no_onedrive':
                return 'AdoptIQ ships with a prebaked local corpus. Add '
                    + 'the AdoptIQ corpus shortcut to OneDrive when you '
                    + 'want background refreshes from the shared source.  '
                    + 'Open the SharePoint folder and click '
                    + '\u201CAdd shortcut to OneDrive\u201D so the '
                    + 'OneDrive client syncs it to your Mac.';
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
    // to prevent click-spam while a pass is already running.
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

    // Round 53 / Phase 53.4.1 (extended Round 83 / Build 59):
    // render (or hide) the OneDrive deep-link anchor. Looks for a
    // ``[data-onedrive-deep-link]`` slot anywhere inside the panel
    // and writes the href + visibility. The slot is hidden in
    // every state where the corpus is healthy so it never confuses
    // users who already have OneDrive synced.
    //
    // Round 83 unhides the button on the new ``signed_in_no_corpus``
    // state in addition to the legacy ``blocked_no_onedrive`` state,
    // and rewrites the visible label so the user sees a clearer
    // CTA matching the panel copy. The href stays the same
    // (Config.ADOPTIQ_CORPUS_ONEDRIVE_DEEP_LINK -> SharePoint share
    // URL); only the visible text changes.
    function paintDeepLink(state, payload) {
        var anchors = document.querySelectorAll('[data-onedrive-deep-link]');
        if (!anchors || anchors.length === 0) { return; }
        var rawUrl = (payload && payload.boot)
            ? payload.boot.onedrive_deep_link : null;
        var safe = r53SafeDeepLink(rawUrl);
        var visible = (
            (state === 'blocked_no_onedrive' || state === 'signed_in_no_corpus')
            && safe
        );
        // Round 83: dynamic button label so the user sees a CTA
        // that matches the state. Pre-R83 every blocked state showed
        // the same "Open AdoptIQ corpus folder in OneDrive" text,
        // which was inappropriate for the new signed_in_no_corpus
        // state where the user has not yet added the share.
        var buttonText;
        if (state === 'signed_in_no_corpus') {
            buttonText = 'Add corpus share to my OneDrive';
        } else {
            buttonText = 'Open AdoptIQ corpus folder in OneDrive';
        }
        for (var i = 0; i < anchors.length; i += 1) {
            var a = anchors[i];
            if (visible) {
                a.setAttribute('href', safe);
                // codeguard-0-client-side-web-security: external link
                // hardening.  Always set noopener+noreferrer so the
                // OneDrive page cannot reach window.opener.
                a.setAttribute('target', '_blank');
                a.setAttribute('rel', 'noopener noreferrer');
                // textContent is XSS-safe (no innerHTML); the label
                // text comes from this module's source code, not
                // from the server payload.
                a.textContent = buttonText;
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
            if (state === 'runtime_synced' && (boot.last_successful_update_at || boot.indexed_at)) {
                acctEl.textContent = 'Last local update '
                    + String(boot.last_successful_update_at || boot.indexed_at);
            } else {
                acctEl.textContent = '';
            }
        }

        if (detail) {
            // Round 108: only true refresh failures use error styling;
            // optional OneDrive setup states use neutral/pending copy.
            var feedbackKind = (state === 'refresh_failed') ? 'error' : 'pending';
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
        r177Honesty: r177PeerGuidanceHonesty,  // Round 177
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
        // Round 113 / C4: only start the recurring poll (and the
        // visibility-change re-sync) on pages that actually render the
        // intel banner or corpus panel.  bindEnableToggle() above is
        // intentionally still wired on every page so the Preferences
        // toggle keeps working (and its post-toggle pollOnce refreshes
        // the navbar badge once without starting a loop).
        if (!_intelPollSurfacePresent()) { return; }
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
