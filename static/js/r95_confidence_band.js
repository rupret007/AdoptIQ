(function () {
    'use strict';

    function _array(value) {
        return Array.isArray(value) ? value : null;
    }

    function _coverage(payload) {
        var diag = (payload && payload.retrieval_diag) || {};
        var raw = diag.coverage || diag.risk_profiles_coverage || payload.risk_profiles_coverage || '';
        return String(raw || 'FULL').toUpperCase();
    }

    function classifyConfidence(payload) {
        if (!payload || !_array(payload.canonical_corrections)) {
            return {
                level: 'Medium',
                className: 'bg-warning-subtle text-warning border',
                reason: 'Legacy response without canonical correction diagnostics.'
            };
        }
        var corrections = payload.canonical_corrections.length;
        var diag = payload.retrieval_diag || {};
        var method = String(diag.method || payload.retrieval_method || '').toLowerCase();
        var rerank = String(diag.rerank || '').toLowerCase();
        var coverage = _coverage(payload);
        if (method === 'lexical' || coverage === 'STREAMING' || coverage === 'NONE' || corrections >= 3) {
            return {
                level: 'Low',
                className: 'bg-danger-subtle text-danger border',
                reason: 'Low confidence: lexical retrieval, incomplete coverage, or multiple canonical corrections.'
            };
        }
        if (corrections > 0 || coverage === 'PARTIAL' || rerank !== 'hybrid') {
            return {
                level: 'Medium',
                className: 'bg-warning-subtle text-warning border',
                reason: 'Medium confidence: answer rendered with limited rerank signal, partial coverage, or minor corrections.'
            };
        }
        return {
            level: 'High',
            className: 'bg-success-subtle text-success border',
            reason: 'High confidence: hybrid retrieval, reranked evidence, full coverage, and no canonical corrections.'
        };
    }

    function renderConfidenceBand(payload) {
        var el = document.getElementById('r95ConfidenceBand');
        if (!el) { return; }
        var state = classifyConfidence(payload || {});
        el.className = 'badge ' + state.className;
        el.textContent = 'confidence: ' + state.level;
        el.setAttribute('title', state.reason);
        el.style.display = '';
    }

    window.AdoptIQConfidenceBand = {
        classifyConfidence: classifyConfidence,
        renderConfidenceBand: renderConfidenceBand
    };
}());
