/* Round 84 / Build 60: corpus share URL settings card.
 *
 * Wires the analyze-page card that lets the operator rotate the
 * SharePoint share URL used by the bootstrap-shortcut button on the
 * AdoptIQ Knowledge Corpus panel without rebuilding the DMG.
 *
 * Endpoints:
 *   GET  /api/settings/corpus-share-url -> {share_url, source,
 *                                           persisted_value,
 *                                           env_var, env_value_set}
 *   POST /api/settings/corpus-share-url -> {url: <https or empty>}
 *
 * Threat model: this URL is NOT a secret. The Cisco-tenant
 * SharePoint ACL gates the share, OneDrive auth gates the corpus
 * decryption (R83 contract preserved by
 * ``corpus_bootstrap._run_index_pass`` calling
 * ``open_corpus_for_user(..., allow_local_sentinel=False)``). The
 * server enforces an https-only + ``*.sharepoint.com`` host
 * allow-list before persistence; this module's role is purely UX.
 *
 * XSS posture: every value the server echoes back (URL, source
 * label, persisted value, error message) is rendered via
 * ``textContent``, never ``innerHTML``. The ``Test`` button uses
 * ``window.open(url)`` which the browser treats as a navigation
 * target -- if a future server bug let a non-https URL slip past
 * the validator, the browser still prevents script execution
 * because the fetched response is rendered in a fresh tab.
 *
 * Source-shape pinned by
 * ``tests/test_round84_corpus_share_url_ui_source_shape.py``.
 */

/* eslint-disable no-var */
(function () {
    'use strict';

    var ENDPOINT_URL = '/api/settings/corpus-share-url';
    var SHORTCUT_URL = '/api/corpus/bootstrap-shortcut';

    var CARD_SELECTOR = '[data-corpus-share-url-card]';
    var INPUT_SELECTOR = '[data-corpus-share-url-input]';
    var SAVE_SELECTOR = '[data-corpus-share-url-save]';
    var TEST_SELECTOR = '[data-corpus-share-url-test]';
    var CLEAR_SELECTOR = '[data-corpus-share-url-clear]';
    var CURRENT_SELECTOR = '[data-corpus-share-url-current]';
    var SOURCE_PILL_SELECTOR = '[data-corpus-share-url-source-pill]';
    var SOURCE_TEXT_SELECTOR = '[data-corpus-share-url-source-text]';
    var FEEDBACK_SELECTOR = '[data-corpus-share-url-feedback]';

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
        // textContent (NOT innerHTML) -- defense-in-depth XSS guard.
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
            // textContent only -- the source label is server-controlled
            // (one of "settings.json"/"env"/"config.py") but we still
            // treat it as untrusted so a future server bug cannot inject
            // markup via this code path.
            txt.textContent = source || '\u2014';
        }
        if (!pill) { return; }
        // Reset pill colour each render so an old class does not stick
        // when the source label flips between tiers.
        pill.className = 'badge rounded-pill';
        if (source === 'settings.json') {
            pill.classList.add('bg-success');
        } else if (source === 'env') {
            pill.classList.add('bg-info', 'text-dark');
        } else {
            // "config.py" or unknown -- neutral.
            pill.classList.add('bg-secondary');
        }
    }

    function paintCorpusShareUrlCard(payload) {
        var current = document.querySelector(CURRENT_SELECTOR);
        if (current) {
            // textContent only -- the URL came from settings.json /
            // env / config and is server-validated (https-only,
            // sharepoint.com host) before reaching us, but the browser
            // treats ``code`` as text-content anyway so this is the
            // canonical XSS-safe rendering.
            if (payload && payload.share_url) {
                current.textContent = payload.share_url;
            } else {
                current.textContent = '(none configured)';
            }
        }
        if (payload && payload.source) {
            setSourcePill(payload.source);
        }
    }

    function fetchCorpusShareUrlCard() {
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
            paintCorpusShareUrlCard(payload);
            return payload;
        }).catch(function (err) {
            setFeedback('error', 'Failed to load: ' + (err && err.message ? err.message : 'network error'));
        });
    }

    function postCorpusShareUrl(rawValue) {
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
            body: JSON.stringify({ url: rawValue == null ? '' : String(rawValue) })
        }).then(function (resp) {
            var ok = resp.ok;
            var status = resp.status;
            return resp.json().catch(function () { return null; }).then(function (data) {
                return { ok: ok, status: status, data: data };
            });
        });
    }

    function bindCorpusShareUrlSave() {
        var card = document.querySelector(CARD_SELECTOR);
        if (!card) { return; }
        var input = document.querySelector(INPUT_SELECTOR);
        var save = document.querySelector(SAVE_SELECTOR);
        var test = document.querySelector(TEST_SELECTOR);
        var clear = document.querySelector(CLEAR_SELECTOR);

        if (save && input) {
            save.addEventListener('click', function () {
                var value = (input.value || '').trim();
                save.disabled = true;
                setFeedback('info', 'Saving\u2026');
                postCorpusShareUrl(value).then(function (result) {
                    if (!result.ok || !result.data || result.data.ok !== true) {
                        var detail = (result.data && (result.data.detail || result.data.error)) || ('HTTP ' + result.status);
                        setFeedback('error', 'Save failed: ' + detail);
                        return;
                    }
                    paintCorpusShareUrlCard(result.data);
                    if (value) {
                        setFeedback('success', 'Saved. Active source: ' + (result.data.source || 'unknown') + '.');
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
                postCorpusShareUrl('').then(function (result) {
                    if (!result.ok || !result.data || result.data.ok !== true) {
                        var detail = (result.data && (result.data.detail || result.data.error)) || ('HTTP ' + result.status);
                        setFeedback('error', 'Clear failed: ' + detail);
                        return;
                    }
                    paintCorpusShareUrlCard(result.data);
                    setFeedback('success', 'Override cleared. Active source: ' + (result.data.source || 'unknown') + '.');
                }).catch(function () {
                    setFeedback('error', 'Clear failed: network error');
                }).then(function () {
                    clear.disabled = false;
                });
            });
        }

        if (test) {
            // Test reads the SAVED URL (server-validated) via the
            // existing R83 bootstrap-shortcut endpoint -- intentionally
            // NOT the raw input value, so an unsaved typo cannot be
            // launched by the browser. ``window.open`` happens
            // synchronously inside the click handler so the popup
            // blocker does not eat the navigation.
            test.addEventListener('click', function () {
                test.disabled = true;
                setFeedback('info', 'Loading saved URL\u2026');
                fetch(SHORTCUT_URL, {
                    method: 'GET',
                    credentials: 'same-origin',
                    headers: { 'Accept': 'application/json' }
                }).then(function (resp) {
                    return resp.json().catch(function () { return null; }).then(function (data) {
                        return { ok: resp.ok, data: data };
                    });
                }).then(function (result) {
                    if (!result.ok || !result.data || result.data.ok !== true || !result.data.share_url) {
                        var msg = (result.data && (result.data.message || result.data.error)) || 'no URL configured';
                        setFeedback('error', 'Test failed: ' + msg);
                        return;
                    }
                    var w = window.open(result.data.share_url, '_blank', 'noopener,noreferrer');
                    if (!w) {
                        setFeedback('error', 'Browser blocked the popup. Allow popups and try again.');
                        return;
                    }
                    setFeedback('success', 'Opened the saved URL in a new tab.');
                }).catch(function () {
                    setFeedback('error', 'Test failed: network error');
                }).then(function () {
                    test.disabled = false;
                });
            });
        }
    }

    function init() {
        bindCorpusShareUrlSave();
        fetchCorpusShareUrlCard();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

    // Export for tests / future composition. Keeps the module
    // self-contained so a future page that reuses the card markup
    // can call ``window.AdoptIQCorpusShareUrl.paint(payload)``
    // directly without re-binding handlers.
    window.AdoptIQCorpusShareUrl = {
        bindCorpusShareUrlSave: bindCorpusShareUrlSave,
        paintCorpusShareUrlCard: paintCorpusShareUrlCard,
        fetchCorpusShareUrlCard: fetchCorpusShareUrlCard
    };
})();
