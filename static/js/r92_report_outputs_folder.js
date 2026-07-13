/* Round 92: report-output folder Preferences card.
 *
 * XSS posture: all server-returned values are rendered with
 * textContent.  Mutating requests use CSRF headers.  The client never
 * supplies a path to the open-report endpoint; opening is keyed by
 * analysis_id + target only.
 */
(function () {
    'use strict';

    var ENDPOINT_URL = '/api/settings/report-outputs-folder';
    var CARD_SELECTOR = '[data-report-outputs-folder-card]';
    var INPUT_SELECTOR = '[data-report-outputs-folder-input]';
    var SAVE_SELECTOR = '[data-report-outputs-folder-save]';
    var CLEAR_SELECTOR = '[data-report-outputs-folder-clear]';
    var CURRENT_SELECTOR = '[data-report-outputs-folder-current]';
    var PERSISTED_SELECTOR = '[data-report-outputs-folder-persisted]';
    var EXISTS_SELECTOR = '[data-report-outputs-folder-exists]';
    var SOURCE_TEXT_SELECTOR = '[data-report-outputs-folder-source-text]';
    var SOURCE_PILL_SELECTOR = '[data-report-outputs-folder-source-pill]';
    var FEEDBACK_SELECTOR = '[data-report-outputs-folder-feedback]';

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

    function setSourcePill(source) {
        var pill = document.querySelector(SOURCE_PILL_SELECTOR);
        var txt = document.querySelector(SOURCE_TEXT_SELECTOR);
        if (txt) { txt.textContent = source || '-'; }
        if (!pill) { return; }
        pill.className = 'badge rounded-pill';
        if (source === 'settings.json') {
            pill.classList.add('bg-success');
        } else if (source === 'env') {
            pill.classList.add('bg-info', 'text-dark');
        } else if (source === 'default') {
            pill.classList.add('bg-primary');
        } else if (source === 'dev') {
            pill.classList.add('bg-secondary');
        } else {
            pill.classList.add('bg-warning', 'text-dark');
        }
    }

    function paintReportOutputsFolderCard(payload) {
        var current = document.querySelector(CURRENT_SELECTOR);
        if (current) { current.textContent = (payload && payload.folder_path) || '(no path resolved)'; }
        var persisted = document.querySelector(PERSISTED_SELECTOR);
        if (persisted) {
            persisted.textContent = (payload && payload.persisted_value) || '(none -- using env / default)';
        }
        var exists = document.querySelector(EXISTS_SELECTOR);
        if (exists) {
            exists.className = 'badge rounded-pill';
            if (payload && payload.path_exists === true) {
                exists.textContent = 'exists on disk';
                exists.classList.add('bg-success');
            } else if (payload && payload.path_exists === false) {
                exists.textContent = 'NOT found on disk';
                exists.classList.add('bg-danger');
            } else {
                exists.textContent = 'unknown';
                exists.classList.add('bg-secondary');
            }
        }
        setSourcePill(payload && payload.source);
    }

    function fetchReportOutputsFolderCard() {
        return fetch(ENDPOINT_URL, {
            method: 'GET',
            credentials: 'same-origin',
            headers: { 'Accept': 'application/json' }
        }).then(function (resp) {
            if (!resp.ok) {
                return resp.json().catch(function () { return null; }).then(function (data) {
                    throw new Error((data && data.error) || ('HTTP ' + resp.status));
                });
            }
            return resp.json();
        }).then(function (payload) {
            paintReportOutputsFolderCard(payload);
            return payload;
        }).catch(function (err) {
            setFeedback('error', 'Failed to load: ' + (err && err.message ? err.message : 'network error'));
        });
    }

    function postReportOutputsFolder(rawValue) {
        var token = getCsrfToken();
        return fetch(ENDPOINT_URL, {
            method: 'POST',
            credentials: 'same-origin',
            headers: {
                'Accept': 'application/json',
                'Content-Type': 'application/json',
                'X-CSRFToken': token,
                'X-CSRF-Token': token
            },
            body: JSON.stringify({ folder_path: rawValue == null ? '' : String(rawValue) })
        }).then(function (resp) {
            var ok = resp.ok;
            var status = resp.status;
            return resp.json().catch(function () { return null; }).then(function (data) {
                return { ok: ok, status: status, data: data };
            });
        });
    }

    function bindReportOutputsFolderSave() {
        var card = document.querySelector(CARD_SELECTOR);
        if (!card) { return; }
        var input = document.querySelector(INPUT_SELECTOR);
        var save = document.querySelector(SAVE_SELECTOR);
        var clear = document.querySelector(CLEAR_SELECTOR);

        function persist(value, button) {
            if (button) { button.disabled = true; }
            setFeedback('info', value ? 'Saving...' : 'Clearing...');
            postReportOutputsFolder(value).then(function (result) {
                if (!result.ok || !result.data || result.data.ok !== true) {
                    var detail = (result.data && (result.data.detail || result.data.error)) || ('HTTP ' + result.status);
                    setFeedback('error', 'Save failed: ' + detail);
                    return;
                }
                paintReportOutputsFolderCard(result.data);
                setFeedback('success', value ? 'Saved. Future reports use this folder.' : 'Override cleared.');
            }).catch(function () {
                setFeedback('error', 'Save failed: network error');
            }).then(function () {
                if (button) { button.disabled = false; }
            });
        }

        if (save && input) {
            save.addEventListener('click', function () {
                persist((input.value || '').trim(), save);
            });
        }
        if (clear) {
            clear.addEventListener('click', function () {
                if (input) { input.value = ''; }
                persist('', clear);
            });
        }
    }

    function init() {
        bindReportOutputsFolderSave();
        fetchReportOutputsFolderCard();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

    window.AdoptIQReportOutputsFolder = {
        bindReportOutputsFolderSave: bindReportOutputsFolderSave,
        paintReportOutputsFolderCard: paintReportOutputsFolderCard,
        fetchReportOutputsFolderCard: fetchReportOutputsFolderCard
    };
})();
