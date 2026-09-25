(function () {
    'use strict';

    // Round 177: XSS-safe Observed-in-peers card for Ask AI.
    // textContent only. Never honor ready_for_live_cisco=true.
    var HONESTY = 'Local encrypted corpus only. Not live Cisco validation.';
    // Round 184: peer-guidance Source IDs are content-addressed and
    // should be shown only when they match the published CORPUS:PG shape.
    var SOURCE_ID_RE = /^CORPUS:PG-[A-Z0-9]+$/;

    function _el(id) {
        return document.getElementById(id);
    }

    function _setText(node, value) {
        if (!node) { return; }
        node.textContent = value == null ? '' : String(value);
    }

    function _show(node, visible) {
        if (!node) { return; }
        if (visible) {
            node.style.display = '';
            node.removeAttribute('hidden');
        } else {
            node.style.display = 'none';
            node.setAttribute('hidden', '');
        }
    }

    function hidePeerGuidance() {
        var card = _el('r177PeerGuidanceCard');
        _show(card, false);
        if (card) {
            card.removeAttribute('data-r177-peer-insufficient');
            card.removeAttribute('data-r178-peer-caution');
            card.removeAttribute('data-r179-peer-already-lived');
        }
    }

    function _viewFromPayload(payload) {
        var corpus = (payload && payload.corpus) || {};
        var view = corpus.peer_guidance;
        if (!view || typeof view !== 'object') { return null; }
        if (!view.status) { return null; }
        return view;
    }

    function renderPeerGuidance(payload) {
        var card = _el('r177PeerGuidanceCard');
        if (!card) { return; }
        var view = _viewFromPayload(payload);
        if (!view) {
            hidePeerGuidance();
            return;
        }
        var status = String(view.status || 'insufficient');
        if (status !== 'actionable' && status !== 'method_only') {
            status = 'insufficient';
        }
        var honesty = String(view.honesty_label || HONESTY);
        if (view.ready_for_live_cisco === true) {
            honesty = HONESTY;
        }
        _setText(_el('r177PeerGuidanceHonesty'), honesty);

        var nextWrap = _el('r177PeerGuidanceNextStepWrap');
        var nextEl = _el('r177PeerGuidanceNextStep');
        var methodEl = _el('r177PeerGuidanceMethod');
        var likelyEl = _el('r177PeerGuidanceLikely');
        var evidenceEl = _el('r177PeerGuidanceEvidence');
        var copyEl = _el('r177PeerGuidanceCopy');
        var sourceEl = _el('r177PeerGuidanceSource');
        var statusEl = _el('r177PeerGuidanceStatus');
        var likelyKey = String(view.likely_next || '');
        var reason = String(view.insufficient_reason || '');
        var alreadyLived = reason === 'already_lived';
        var incomparable = reason === 'incomparable_severity' || reason === 'incomparable_case_severity';
        var sourceId = String(view.source_id || '');
        var hasSourceId = SOURCE_ID_RE.test(sourceId);
        var caution = likelyKey === 'remains_open' || likelyKey === 'pulse_worsening';
        if (caution) {
            card.setAttribute('data-r178-peer-caution', '');
        } else {
            card.removeAttribute('data-r178-peer-caution');
        }
        if (alreadyLived) {
            card.setAttribute('data-r179-peer-already-lived', '');
        } else {
            card.removeAttribute('data-r179-peer-already-lived');
        }

        if (status === 'insufficient') {
            card.setAttribute('data-r177-peer-insufficient', '');
            _setText(statusEl, 'Insufficient evidence');
            _show(nextWrap, false);
            _show(methodEl, false);
            _show(likelyEl, false);
            _show(evidenceEl, false);
            _show(sourceEl, false);
            _setText(
                copyEl,
                view.insufficient_copy
                    || 'Not enough similar accounts in this local corpus to suggest a next step.'
            );
            _show(copyEl, true);
        } else if (status === 'method_only') {
            card.removeAttribute('data-r177-peer-insufficient');
            _setText(
                statusEl,
                alreadyLived
                    ? 'Peer path already completed'
                    : (incomparable ? 'No comparable-severity evidence' : 'Not enough outcome evidence')
            );
            _show(nextWrap, false);
            var method = String(view.method || '');
            _setText(methodEl, method ? ('Observed peer method (not a recommendation): ' + method) : '');
            _show(methodEl, Boolean(method));
            _show(likelyEl, false);
            var evidence = String(view.evidence_line || '');
            _setText(evidenceEl, evidence);
            _show(evidenceEl, Boolean(evidence));
            var copy = String(view.insufficient_copy || '');
            _setText(copyEl, copy);
            _show(copyEl, Boolean(copy));
            _setText(sourceEl, hasSourceId ? ('Source ID: ' + sourceId) : '');
            _show(sourceEl, hasSourceId);
        } else {
            card.removeAttribute('data-r177-peer-insufficient');
            _setText(statusEl, caution ? 'Caution: peer path stalled' : 'Peer-backed next step');
            var nextStep = String(view.next_step || '');
            _setText(nextEl, nextStep);
            _show(nextWrap, Boolean(nextStep));
            var methodA = String(view.method || '');
            var methodLabel = caution
                ? 'Peer method not to repeat unchanged: '
                : 'Peer method to test: ';
            _setText(methodEl, methodA ? (methodLabel + methodA) : '');
            _show(methodEl, Boolean(methodA));
            var likely = String(view.likely_next_label || '');
            _setText(likelyEl, likely);
            _show(likelyEl, Boolean(likely));
            var evidenceA = String(view.evidence_line || '');
            _setText(evidenceEl, evidenceA);
            _show(evidenceEl, Boolean(evidenceA));
            _setText(sourceEl, hasSourceId ? ('Source ID: ' + sourceId) : '');
            _show(sourceEl, hasSourceId);
            _show(copyEl, false);
        }
        _show(card, true);
    }

    window.AdoptIQPeerGuidanceCard = {
        renderPeerGuidance: renderPeerGuidance,
        hidePeerGuidance: hidePeerGuidance
    };
}());
