(function () {
    'use strict';

    // Round 177: XSS-safe Observed-in-peers card for Ask AI.
    // textContent only. Never honor ready_for_live_cisco=true.
    var HONESTY = 'Local encrypted corpus only. Not live Cisco validation.';

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
        var statusEl = _el('r177PeerGuidanceStatus');

        if (status === 'insufficient') {
            card.setAttribute('data-r177-peer-insufficient', '');
            _setText(statusEl, 'Insufficient evidence');
            _show(nextWrap, false);
            _show(methodEl, false);
            _show(likelyEl, false);
            _show(evidenceEl, false);
            _setText(
                copyEl,
                view.insufficient_copy
                    || 'Not enough similar accounts in this local corpus to suggest a next step.'
            );
            _show(copyEl, true);
        } else if (status === 'method_only') {
            card.removeAttribute('data-r177-peer-insufficient');
            _setText(statusEl, 'Method observed; no shared outcome');
            _show(nextWrap, false);
            var method = String(view.method || '');
            _setText(methodEl, method ? ('Observed method: ' + method) : '');
            _show(methodEl, Boolean(method));
            _show(likelyEl, false);
            var evidence = String(view.evidence_line || '');
            _setText(evidenceEl, evidence);
            _show(evidenceEl, Boolean(evidence));
            var copy = String(view.insufficient_copy || '');
            _setText(copyEl, copy);
            _show(copyEl, Boolean(copy));
        } else {
            card.removeAttribute('data-r177-peer-insufficient');
            _setText(statusEl, 'Actionable');
            var nextStep = String(view.next_step || '');
            _setText(nextEl, nextStep);
            _show(nextWrap, Boolean(nextStep));
            var methodA = String(view.method || '');
            _setText(methodEl, methodA ? ('Observed method: ' + methodA) : '');
            _show(methodEl, Boolean(methodA));
            var likely = String(view.likely_next_label || '');
            _setText(likelyEl, likely);
            _show(likelyEl, Boolean(likely));
            var evidenceA = String(view.evidence_line || '');
            _setText(evidenceEl, evidenceA);
            _show(evidenceEl, Boolean(evidenceA));
            _show(copyEl, false);
        }
        _show(card, true);
    }

    window.AdoptIQPeerGuidanceCard = {
        renderPeerGuidance: renderPeerGuidance,
        hidePeerGuidance: hidePeerGuidance
    };
}());
