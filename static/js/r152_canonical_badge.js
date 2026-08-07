/*
 * Round 152 / B4 -- canonical cross-check visibility for Ask AI.
 *
 * ``ask_ai_grounded._r95_apply_canonical_corrections`` deletes any sentence
 * whose KPI contradicts ``canonical_metrics`` and appends a
 * "### Canonical Metrics" section carrying the true values.  It returns a
 * structured record of what was corrected and of what was checked and
 * agreed, and BOTH the sync route and the SSE route already forward those
 * fields -- but nothing in static/js read either of them.  A user therefore
 * saw a bare heading with no indication that a correction had occurred, and
 * no indication when the whole answer HAD been verified.  The single
 * strongest trust signal the product computes was transported to the browser
 * and discarded.
 *
 * This lives in its own module rather than inside r95_confidence_band.js so
 * the Round 95 / Phase D source-shape contract for the confidence band --
 * which requires that the band derive its level ONLY from the server trust
 * score, never from client-side heuristics over correction payloads --
 * stays literally true of that file.
 */
(function () {
    'use strict';

    function _label(item) {
        return String((item && (item.kpi || item.metric || item.key)) || '').trim();
    }

    function _stated(item) {
        if (!item) { return ''; }
        return item.llm_value !== undefined ? item.llm_value : item.stated_value;
    }

    function _canonical(item) {
        if (!item) { return ''; }
        return item.canonical_value !== undefined ? item.canonical_value : item.value;
    }

    function classifyCanonical(payload) {
        var corrections = (payload && payload.canonical_corrections) || [];
        var verified = (payload && payload.canonical_verified) || [];
        if (!Array.isArray(corrections)) { corrections = []; }
        if (!Array.isArray(verified)) { verified = []; }

        if (corrections.length) {
            // A correction is the signal that matters; never let a partial
            // verification mask it.
            var detail = corrections.map(function (item) {
                return (_label(item) || 'metric')
                    + ': answer said ' + String(_stated(item))
                    + ', canonical is ' + String(_canonical(item));
            }).join('; ');
            return {
                level: 'corrected',
                className: 'bg-warning-subtle text-warning border',
                text: 'canonical: ' + corrections.length + ' corrected',
                reason: 'These figures were replaced with canonical values because the '
                    + 'generated answer disagreed with them. ' + detail
            };
        }
        if (verified.length) {
            return {
                level: 'verified',
                className: 'bg-success-subtle text-success border',
                text: 'canonical: ' + verified.length + ' verified',
                reason: 'Every figure in this answer was checked against canonical metrics and matched.'
            };
        }
        // Neutral, not green: an answer with no cross-checkable KPI must not
        // read as verified, or the badge would fabricate a verification that
        // never happened.
        return {
            level: 'none',
            className: 'bg-secondary-subtle text-secondary border',
            text: 'canonical: not applicable',
            reason: 'This answer stated no KPI that could be cross-checked against canonical metrics.'
        };
    }

    function renderCanonicalBadge(payload) {
        var el = document.getElementById('r152CanonicalBadge');
        if (!el) { return; }
        var state = classifyCanonical(payload || {});
        el.className = 'badge ' + state.className;
        // textContent only -- corrections carry server-derived KPI labels.
        el.textContent = state.text;
        el.setAttribute('title', state.reason);
        el.style.display = '';
    }

    window.AdoptIQCanonicalBadge = {
        classifyCanonical: classifyCanonical,
        renderCanonicalBadge: renderCanonicalBadge
    };
}());
