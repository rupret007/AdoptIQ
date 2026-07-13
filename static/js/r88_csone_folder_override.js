/* Round 88 / Build 64: CSOne OneDrive folder override settings card.
 *
 * Wires the Preferences-page card that lets the operator override
 * the auto-discovered CSOne OneDrive folder path WITHOUT a DMG
 * rebuild.  Closes Brian's Build 61 acceptance gap where his
 * sharer-prefixed sync folder
 *
 *     /Users/brfrazie/Library/CloudStorage/OneDrive-Cisco/
 *     Jeffrey Story (jestory) - AdoptIQ_CSOne_Reports
 *
 * was not picked up by the auto-discovery candidates list (which
 * looks for the canonical ``AI Projects/AdoptIQ_CSOne_Reports``
 * shape).
 *
 * Endpoints:
 *   GET  /api/settings/csone-onedrive-folder
 *        -> {active_path, source, persisted_value, env_var,
 *            env_value_set, path_exists, ok}
 *   POST /api/settings/csone-onedrive-folder
 *        -> {folder_path: <abs path or empty>}
 *
 * Threat model: the folder PATH is not a secret -- it points at
 * the operator's own filesystem.  The OneDrive desktop client
 * still gates content access (only files the operator's own
 * Microsoft account is authorised to see land in the local
 * sync mirror).  The server enforces an absolute-or-tilde +
 * shell-meta-free allow-list (``adoptiq_settings.
 * is_valid_csone_folder_path``) before persistence; this
 * module's role is purely UX.
 *
 * XSS posture: every value the server echoes back (active path,
 * source label, persisted value, error message) is rendered via
 * ``textContent``, never ``innerHTML``.
 *
 * Source-shape pinned by
 * ``tests/test_round88_csone_folder_override_ui.py``.
 */

/* eslint-disable no-var */
(function () {
    'use strict';

    var ENDPOINT_URL = '/api/settings/csone-onedrive-folder';

    var CARD_SELECTOR = '[data-csone-folder-card]';
    var INPUT_SELECTOR = '[data-csone-folder-input]';
    var SAVE_SELECTOR = '[data-csone-folder-save]';
    var CLEAR_SELECTOR = '[data-csone-folder-clear]';
    var CURRENT_SELECTOR = '[data-csone-folder-current]';
    var PERSISTED_SELECTOR = '[data-csone-folder-persisted]';
    var EXISTS_SELECTOR = '[data-csone-folder-exists]';
    var SOURCE_PILL_SELECTOR = '[data-csone-folder-source-pill]';
    var SOURCE_TEXT_SELECTOR = '[data-csone-folder-source-text]';
    var FEEDBACK_SELECTOR = '[data-csone-folder-feedback]';

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

    function setSourcePill(source) {
        var pill = document.querySelector(SOURCE_PILL_SELECTOR);
        var txt = document.querySelector(SOURCE_TEXT_SELECTOR);
        if (txt) {
            txt.textContent = source || '\u2014';
        }
        if (!pill) { return; }
        pill.className = 'badge rounded-pill';
        if (source === 'settings.json') {
            pill.classList.add('bg-success');
        } else if (source === 'env') {
            pill.classList.add('bg-info', 'text-dark');
        } else if (source === 'auto-discovery') {
            pill.classList.add('bg-secondary');
        } else {
            pill.classList.add('bg-warning', 'text-dark');
        }
    }

    function paintCsoneFolderCard(payload) {
        var current = document.querySelector(CURRENT_SELECTOR);
        if (current) {
            // Round 113 / C1: the GET /api/settings/csone-onedrive-folder
            // endpoint returns the active path under ``folder_path``
            // (not ``active_path``).  Pre-R113 this read the wrong key,
            // so the card always showed "(no path resolved)" even when
            // a path WAS resolved.  Accept both for back-compat.
            var resolvedPath = (payload && (payload.folder_path || payload.active_path)) || '';
            if (resolvedPath) {
                current.textContent = resolvedPath;
            } else {
                current.textContent = '(no path resolved)';
            }
        }
        var persisted = document.querySelector(PERSISTED_SELECTOR);
        if (persisted) {
            if (payload && payload.persisted_value) {
                persisted.textContent = payload.persisted_value;
            } else {
                persisted.textContent = '(none -- using env / auto-discovery)';
            }
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
        if (payload && payload.source) {
            setSourcePill(payload.source);
        }
    }

    function fetchCsoneFolderCard() {
        return fetch(ENDPOINT_URL, {
            method: 'GET',
            credentials: 'same-origin',
            headers: { 'Accept': 'application/json' }
        }).then(function (resp) {
            if (!resp.ok) {
                return resp.json().catch(function () { return null; }).then(function (data) {
                    var detail = (data && data.error) || ('HTTP ' + resp.status);
                    throw new Error(detail);
                });
            }
            return resp.json();
        }).then(function (payload) {
            paintCsoneFolderCard(payload);
            return payload;
        }).catch(function (err) {
            setFeedback('error', 'Failed to load: ' + (err && err.message ? err.message : 'network error'));
        });
    }

    function postCsoneFolder(rawValue) {
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

    function bindCsoneFolderSave() {
        var card = document.querySelector(CARD_SELECTOR);
        if (!card) { return; }
        var input = document.querySelector(INPUT_SELECTOR);
        var save = document.querySelector(SAVE_SELECTOR);
        var clear = document.querySelector(CLEAR_SELECTOR);

        if (save && input) {
            save.addEventListener('click', function () {
                var value = (input.value || '').trim();
                save.disabled = true;
                setFeedback('info', 'Saving\u2026');
                postCsoneFolder(value).then(function (result) {
                    if (!result.ok || !result.data || result.data.ok !== true) {
                        var detail = (result.data && (result.data.detail || result.data.error)) || ('HTTP ' + result.status);
                        setFeedback('error', 'Save failed: ' + detail);
                        return;
                    }
                    paintCsoneFolderCard(result.data);
                    if (value) {
                        setFeedback('success', 'Saved. Active source: ' + (result.data.source || 'unknown') + '. Restart not required.');
                    } else {
                        setFeedback('success', 'Override cleared. Active source: ' + (result.data.source || 'unknown') + '.');
                    }
                }).catch(function () {
                    setFeedback('error', 'Save failed: network error');
                }).then(function () {
                    save.disabled = false;
                });
            });
        }

        if (clear) {
            clear.addEventListener('click', function () {
                if (input) { input.value = ''; }
                clear.disabled = true;
                setFeedback('info', 'Clearing\u2026');
                postCsoneFolder('').then(function (result) {
                    if (!result.ok || !result.data || result.data.ok !== true) {
                        var detail = (result.data && (result.data.detail || result.data.error)) || ('HTTP ' + result.status);
                        setFeedback('error', 'Clear failed: ' + detail);
                        return;
                    }
                    paintCsoneFolderCard(result.data);
                    setFeedback('success', 'Override cleared. Active source: ' + (result.data.source || 'unknown') + '.');
                }).catch(function () {
                    setFeedback('error', 'Clear failed: network error');
                }).then(function () {
                    clear.disabled = false;
                });
            });
        }
    }

    function init() {
        bindCsoneFolderSave();
        fetchCsoneFolderCard();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

    window.AdoptIQCsoneFolderOverride = {
        bindCsoneFolderSave: bindCsoneFolderSave,
        paintCsoneFolderCard: paintCsoneFolderCard,
        fetchCsoneFolderCard: fetchCsoneFolderCard
    };
})();
