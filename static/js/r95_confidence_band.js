(function () {
    'use strict';

    function _className(level) {
        if (level === 'High') {
            return 'bg-success-subtle text-success border';
        }
        if (level === 'Low') {
            return 'bg-danger-subtle text-danger border';
        }
        return 'bg-warning-subtle text-warning border';
    }

    function _isLegacy(payload) {
        var diag = (payload && payload.retrieval_diag) || {};
        var mode = String((payload && payload.mode) || '').toLowerCase();
        var method = String(
            (payload && payload.retrieval_method) || diag.method || ''
        ).toLowerCase();
        return mode === 'legacy_ungrounded' || method === 'legacy_ungrounded';
    }

    function _serverConfidence(payload) {
        var diag = (payload && payload.retrieval_diag) || {};
        var value = (payload && payload.confidence) || diag.confidence;
        return value && typeof value === 'object' ? value : null;
    }

    function classifyConfidence(payload) {
        if (_isLegacy(payload)) {
            return {
                level: 'Unscored',
                className: 'bg-secondary-subtle text-secondary border',
                reason: 'Confidence unknown: legacy ungrounded responses are not server-scored.'
            };
        }
        var confidence = _serverConfidence(payload);
        var level = confidence && String(confidence.level || '');
        if (level !== 'High' && level !== 'Medium' && level !== 'Low') {
            return {
                level: 'Unscored',
                className: 'bg-secondary-subtle text-secondary border',
                reason: 'Confidence unknown: this response did not include a server trust score.'
            };
        }
        var reasons = Array.isArray(confidence.reasons)
            ? confidence.reasons.filter(function (reason) {
                return typeof reason === 'string' && reason.trim();
            })
            : [];
        var score = Number(confidence.score);
        var reasonText = reasons.join(' ');
        if (!reasonText) {
            reasonText = 'Server confidence: ' + level + '.';
        }
        if (Number.isFinite(score)) {
            reasonText = 'Server trust score ' + Math.max(0, Math.min(100, Math.round(score)))
                + '/100. ' + reasonText;
        }
        return {
            level: level,
            className: _className(level),
            reason: reasonText
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
