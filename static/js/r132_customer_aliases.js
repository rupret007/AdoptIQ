/* Round 132 / Build 102: customer alias groups Preferences card.
 *
 * Endpoints:
 *   GET  /api/settings/customer-aliases
 *   POST /api/settings/customer-aliases  { groups: [...] } | { clear: true }
 *
 * XSS posture: bundled summary + paths via textContent only.
 */

/* eslint-disable no-var */
(function () {
    'use strict';

    var ENDPOINT_URL = '/api/settings/customer-aliases';

    var CARD_SELECTOR = '[data-customer-aliases-card]';
    var BUNDLED_SELECTOR = '[data-customer-aliases-bundled]';
    var PATH_SELECTOR = '[data-customer-aliases-path]';
    var OVERRIDE_PILL_SELECTOR = '[data-customer-aliases-override-pill]';
    var OVERRIDE_TEXT_SELECTOR = '[data-customer-aliases-override-text]';
    var COUNT_SELECTOR = '[data-customer-aliases-effective-count]';
    var TEXTAREA_SELECTOR = '[data-customer-aliases-input]';
    var SAVE_SELECTOR = '[data-customer-aliases-save]';
    var CLEAR_SELECTOR = '[data-customer-aliases-clear]';
    var FEEDBACK_SELECTOR = '[data-customer-aliases-feedback]';

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

    function formatBundledSummary(groups) {
        if (!groups || !groups.length) {
            return '(no bundled alias groups)';
        }
        var parts = [];
        for (var i = 0; i < groups.length; i += 1) {
            var g = groups[i];
            if (!g || !g.group_id) { continue; }
            var aliasCount = (g.aliases && g.aliases.length) ? g.aliases.length : 0;
            parts.push(g.group_id + ' (' + aliasCount + ' aliases)');
        }
        return parts.join('; ') || '(no bundled alias groups)';
    }

    function groupsToTextarea(groups) {
        if (!groups || !groups.length) {
            return '{\n  "groups": []\n}';
        }
        try {
            return JSON.stringify({ groups: groups }, null, 2);
        } catch (_e) {
            return '{\n  "groups": []\n}';
        }
    }

    function paintCustomerAliasesCard(payload) {
        var bundled = document.querySelector(BUNDLED_SELECTOR);
        if (bundled) {
            bundled.textContent = formatBundledSummary(payload && payload.bundled_groups);
        }
        var pathEl = document.querySelector(PATH_SELECTOR);
        if (pathEl) {
            pathEl.textContent = (payload && payload.user_file_path) || '(not configured)';
        }
        var countEl = document.querySelector(COUNT_SELECTOR);
        if (countEl) {
            var n = (payload && payload.effective_group_count);
            countEl.textContent = (typeof n === 'number') ? String(n) : '\u2014';
        }
        var pill = document.querySelector(OVERRIDE_PILL_SELECTOR);
        var pillText = document.querySelector(OVERRIDE_TEXT_SELECTOR);
        var hasOverride = !!(payload && payload.has_operator_override);
        if (pillText) {
            pillText.textContent = hasOverride ? 'operator override active' : 'bundled only';
        }
        if (pill) {
            pill.className = 'badge rounded-pill';
            if (hasOverride) {
                pill.classList.add('bg-warning', 'text-dark');
            } else {
                pill.classList.add('bg-success');
            }
        }
        var textarea = document.querySelector(TEXTAREA_SELECTOR);
        if (textarea && payload) {
            var opGroups = payload.operator_groups || [];
            textarea.value = groupsToTextarea(opGroups);
        }
    }

    function fetchCustomerAliasesCard() {
        return fetch(ENDPOINT_URL, {
            method: 'GET',
            headers: { 'Accept': 'application/json' },
            credentials: 'same-origin',
        }).then(function (response) {
            if (!response.ok) {
                throw new Error('GET failed: ' + response.status);
            }
            return response.json();
        }).then(function (payload) {
            if (!payload || !payload.ok) {
                throw new Error('GET payload not ok');
            }
            paintCustomerAliasesCard(payload);
            setFeedback('info', '');
            return payload;
        }).catch(function (err) {
            setFeedback('error', 'Could not load customer aliases: ' + (err.message || String(err)));
        });
    }

    function parseTextareaJson(text) {
        var parsed;
        try {
            parsed = JSON.parse(text);
        } catch (e) {
            return { error: 'invalid_json: ' + e.message };
        }
        if (!parsed || typeof parsed !== 'object') {
            return { error: 'invalid_json_payload' };
        }
        if (!Array.isArray(parsed.groups)) {
            return { error: 'groups_must_be_list' };
        }
        return { groups: parsed.groups };
    }

    function postCustomerAliases(body) {
        var headers = {
            'Accept': 'application/json',
            'Content-Type': 'application/json',
        };
        var token = getCsrfToken();
        if (token) {
            headers['X-CSRFToken'] = token;
        }
        return fetch(ENDPOINT_URL, {
            method: 'POST',
            headers: headers,
            credentials: 'same-origin',
            body: JSON.stringify(body),
        }).then(function (response) {
            return response.json().then(function (data) {
                return { response: response, data: data };
            });
        });
    }

    function bindCustomerAliasesCard() {
        var card = document.querySelector(CARD_SELECTOR);
        if (!card) { return; }

        var saveBtn = document.querySelector(SAVE_SELECTOR);
        if (saveBtn) {
            saveBtn.addEventListener('click', function () {
                var textarea = document.querySelector(TEXTAREA_SELECTOR);
                var raw = textarea ? textarea.value : '';
                var parsed = parseTextareaJson(raw);
                if (parsed.error) {
                    setFeedback('error', 'Invalid JSON: ' + parsed.error);
                    return;
                }
                setFeedback('info', 'Saving…');
                postCustomerAliases({ groups: parsed.groups }).then(function (result) {
                    if (!result.response.ok || !result.data || !result.data.ok) {
                        var err = (result.data && result.data.error) || ('HTTP ' + result.response.status);
                        setFeedback('error', 'Save failed: ' + err);
                        return;
                    }
                    paintCustomerAliasesCard(result.data);
                    setFeedback('success', 'Operator alias groups saved.');
                }).catch(function (err) {
                    setFeedback('error', 'Save failed: ' + (err.message || String(err)));
                });
            });
        }

        var clearBtn = document.querySelector(CLEAR_SELECTOR);
        if (clearBtn) {
            clearBtn.addEventListener('click', function () {
                if (!window.confirm('Clear operator customer alias overrides and use bundled defaults only?')) {
                    return;
                }
                setFeedback('info', 'Clearing…');
                postCustomerAliases({ clear: true }).then(function (result) {
                    if (!result.response.ok || !result.data || !result.data.ok) {
                        var err = (result.data && result.data.error) || ('HTTP ' + result.response.status);
                        setFeedback('error', 'Clear failed: ' + err);
                        return;
                    }
                    paintCustomerAliasesCard(result.data);
                    var textarea = document.querySelector(TEXTAREA_SELECTOR);
                    if (textarea) {
                        textarea.value = groupsToTextarea([]);
                    }
                    setFeedback('success', 'Operator override cleared.');
                }).catch(function (err) {
                    setFeedback('error', 'Clear failed: ' + (err.message || String(err)));
                });
            });
        }

        fetchCustomerAliasesCard();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', bindCustomerAliasesCard);
    } else {
        bindCustomerAliasesCard();
    }

    window.AdoptIQCustomerAliases = {
        bindCustomerAliasesCard: bindCustomerAliasesCard,
        fetchCustomerAliasesCard: fetchCustomerAliasesCard,
        paintCustomerAliasesCard: paintCustomerAliasesCard,
    };
}());
