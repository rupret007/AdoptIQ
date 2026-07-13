/*
 * AdoptIQ Theme Toggle  (Round 17.4 / Phase 6.4)
 *
 * Companion to the early-paint inline bootstrap in templates/base.html.
 * That inline <script> reads ``localStorage['adoptiq-theme']`` and sets
 * ``data-bs-theme`` on <html> BEFORE the page paints, so the user does
 * not see a flash of the wrong theme.  This file then runs after the
 * DOM is parsed and:
 *
 *   1) wires the click handler on #theme-toggle so users can flip
 *      between dark and light at runtime;
 *   2) keeps ``aria-pressed`` and ``aria-label`` in sync so screen
 *      readers correctly announce the current state and the action
 *      the click will perform;
 *   3) honours the OS-level ``prefers-color-scheme`` media query for
 *      first-time visitors who have NOT explicitly chosen a theme,
 *      while still defaulting to dark when the OS preference is also
 *      "no-preference" (matches the AdoptIQ design intent).
 *
 * Security / privacy notes:
 *   - Stored value is constrained to "dark" or "light"; everything
 *     else is treated as "dark" so a corrupted localStorage cannot
 *     inject arbitrary attribute values.
 *   - No analytics, no network calls, no PII -- the only side effect
 *     is one ``localStorage.setItem`` and one ``setAttribute`` per
 *     click.
 *   - No prototype mutation; module pattern with a single IIFE so
 *     nothing leaks onto the global ``window`` namespace.
 */
(function () {
    'use strict';

    var STORAGE_KEY = 'adoptiq-theme';
    var DEFAULT_THEME = 'dark';
    var VALID_THEMES = ['dark', 'light'];

    function isValidTheme(value) {
        return VALID_THEMES.indexOf(value) !== -1;
    }

    function safeReadStored() {
        try {
            var raw = window.localStorage.getItem(STORAGE_KEY);
            return isValidTheme(raw) ? raw : null;
        } catch (_) {
            return null;
        }
    }

    function safeWriteStored(value) {
        if (!isValidTheme(value)) {
            return;
        }
        try {
            window.localStorage.setItem(STORAGE_KEY, value);
        } catch (_) {
            /* private mode / SecurityError -- silently keep in-memory only */
        }
    }

    function getCurrentTheme() {
        var attr = document.documentElement.getAttribute('data-bs-theme');
        return isValidTheme(attr) ? attr : DEFAULT_THEME;
    }

    /*
     * Round 28 / Phase 4: deliver on the existing comment at the top
     * of this file ("honours the OS-level prefers-color-scheme media
     * query for first-time visitors who have NOT explicitly chosen a
     * theme").  Until now that behaviour was promised but never
     * implemented -- a first-time visitor on a light-mode OS still
     * landed in dark mode because ``init()`` only consulted
     * localStorage.  We only invoke this branch when ``localStorage``
     * has no persisted choice; once the user toggles even once, the
     * stored value wins on every subsequent visit so we never
     * "fight" their explicit preference.  The matchMedia call is
     * guarded so older browsers (or hardened private modes) that
     * lack the API silently fall back to the dark default.
     */
    function detectPreferredTheme() {
        try {
            if (typeof window.matchMedia === 'function') {
                var mql = window.matchMedia('(prefers-color-scheme: light)');
                if (mql && mql.matches) {
                    return 'light';
                }
            }
        } catch (_) {
            /* matchMedia unavailable / blocked -- fall through */
        }
        return DEFAULT_THEME;
    }

    function applyTheme(theme) {
        var resolved = isValidTheme(theme) ? theme : DEFAULT_THEME;
        document.documentElement.setAttribute('data-bs-theme', resolved);
        updateToggleAria(resolved);
    }

    function updateToggleAria(theme) {
        var btn = document.getElementById('theme-toggle');
        if (!btn) {
            return;
        }
        var isDark = theme === 'dark';
        btn.setAttribute('aria-pressed', isDark ? 'true' : 'false');
        btn.setAttribute(
            'aria-label',
            isDark ? 'Switch to light theme' : 'Switch to dark theme'
        );
        btn.setAttribute(
            'title',
            isDark ? 'Switch to light theme' : 'Switch to dark theme'
        );
    }

    function toggleTheme() {
        var next = getCurrentTheme() === 'dark' ? 'light' : 'dark';
        applyTheme(next);
        safeWriteStored(next);
    }

    function init() {
        var stored = safeReadStored();
        if (stored !== null) {
            applyTheme(stored);
        } else {
            // Round 28 / Phase 4: respect the OS-level
            // ``prefers-color-scheme`` on first visit.  We do NOT
            // persist the OS-derived choice -- so if the user later
            // changes their OS preference, we re-detect on the next
            // first-run rather than locking them into the previous
            // OS state.  Once they explicitly click the toggle the
            // ``safeWriteStored`` call inside ``toggleTheme()``
            // upgrades the page to the persisted-preference branch.
            applyTheme(detectPreferredTheme());
        }

        var btn = document.getElementById('theme-toggle');
        if (btn) {
            btn.addEventListener('click', function (ev) {
                ev.preventDefault();
                toggleTheme();
            });
        }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
