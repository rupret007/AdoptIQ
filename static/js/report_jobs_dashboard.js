/* Round 91: single-window report jobs dashboard.

   This module renders any [data-report-jobs-panel] surface from the
   existing status/cancel/download APIs so operators can start multiple
   reports from one browser window and watch them complete in real time.

   Security notes:
     * Server values land via textContent / attributes only.
     * Mutating calls use the page CSRF token from the existing meta tag.
     * Polling slows down while no jobs are active to avoid status-lock load.
*/
(function () {
    'use strict';

    if (window.__adoptiqReportJobsInit) { return; }
    window.__adoptiqReportJobsInit = true;

    var STATUS_URL = '/api/status/all?limit=50';
    var ACTIVE_STATUSES = { starting: true, initializing: true, running: true, cancelling: true };
    var FAST_MS = 3000;
    var SLOW_MS = 30000;
    var timer = null;
    var optimisticJobs = {};

    function getCsrfToken() {
        try {
            var meta = document.querySelector('meta[name="csrf-token"]');
            if (meta && meta.getAttribute) { return meta.getAttribute('content') || ''; }
            var input = document.querySelector('input[name="csrf_token"]');
            if (input && input.value) { return input.value; }
        } catch (_) { /* fall through */ }
        return '';
    }

    function normalizePayload(payload) {
        if (Array.isArray(payload)) { return payload; }
        if (payload && Array.isArray(payload.statuses)) { return payload.statuses; }
        return [];
    }

    function safeText(value, fallback) {
        if (value === null || value === undefined) { return fallback || ''; }
        var text = String(value).trim();
        return text || (fallback || '');
    }

    function reportLabel(job) {
        var type = safeText(job.report_type || job.renewal_type, 'report');
        if (type === 'customer_renewal') { type = safeText(job.renewal_type, 'renewal'); }
        return type.replace(/_/g, ' ').replace(/\b\w/g, function (c) { return c.toUpperCase(); });
    }

    function modelLabel(job) {
        return safeText(job.active_report_model || job.report_model_name, 'n/a');
    }

    function formatElapsed(seconds) {
        var n = Number(seconds || 0);
        if (!Number.isFinite(n) || n < 0) { n = 0; }
        var mins = Math.floor(n / 60);
        var secs = Math.floor(n % 60);
        if (mins >= 60) {
            var hrs = Math.floor(mins / 60);
            mins = mins % 60;
            return hrs + 'h ' + String(mins).padStart(2, '0') + 'm';
        }
        return mins + ':' + String(secs).padStart(2, '0');
    }

    function statusBadgeClass(status) {
        switch (status) {
            case 'completed': return 'bg-success';
            case 'error': return 'bg-danger';
            case 'cancelled': return 'bg-secondary';
            case 'cancelling': return 'bg-warning text-dark';
            case 'running': return 'bg-primary';
            case 'starting':
            case 'initializing': return 'bg-info text-dark';
            default: return 'bg-secondary';
        }
    }

    function isActiveJob(job) {
        return !!ACTIVE_STATUSES[String((job && job.status) || '').toLowerCase()];
    }

    function jobTimeValue(job) {
        var fields = ['updated_at', 'end_time', 'completed_at', 'start_time', 'created_at'];
        for (var i = 0; i < fields.length; i += 1) {
            var raw = job && job[fields[i]];
            if (!raw) { continue; }
            var parsed = Date.parse(String(raw));
            if (Number.isFinite(parsed)) { return parsed; }
        }
        return 0;
    }

    function sortJobsForDisplay(jobs) {
        return (jobs || []).slice().sort(function (a, b) {
            var activeDelta = (isActiveJob(b) ? 1 : 0) - (isActiveJob(a) ? 1 : 0);
            if (activeDelta) { return activeDelta; }
            return jobTimeValue(b) - jobTimeValue(a);
        });
    }

    function appendText(parent, tag, className, text) {
        var el = document.createElement(tag);
        if (className) { el.className = className; }
        el.textContent = text;
        parent.appendChild(el);
        return el;
    }

    function appendAction(parent, label, href, className) {
        var a = document.createElement('a');
        a.className = className || 'btn btn-sm btn-outline-primary';
        a.href = href;
        a.textContent = label;
        parent.appendChild(a);
        return a;
    }

    function appendOpenButton(parent, label, analysisId, target, className) {
        var btn = document.createElement('button');
        btn.type = 'button';
        btn.className = className || 'btn btn-sm btn-outline-primary';
        btn.textContent = label;
        btn.addEventListener('click', function () { openReportArtifact(analysisId, target, btn); });
        parent.appendChild(btn);
        return btn;
    }

    function markButtonFailure(button, message, logPrefix) {
        if (!button) { return; }
        // Round 94 source-shape pins: "report-jobs-open failed" /
        // "report-jobs-cancel failed" remain visible for audit grep.
        console.warn(logPrefix + ' failed: ' + message);
        button.textContent = message;
        button.classList.add('btn-outline-danger');
    }

    function renderRow(job) {
        var tr = document.createElement('tr');
        var aid = safeText(job.analysis_id);
        var status = safeText(job.status, 'unknown').toLowerCase();
        var progress = Math.max(0, Math.min(100, parseInt(job.progress || 0, 10) || 0));
        if (ACTIVE_STATUSES[status]) { tr.className = 'adoptiq-report-job-active'; }

        var reportCell = document.createElement('td');
        appendText(reportCell, 'div', 'fw-semibold', reportLabel(job));
        appendText(reportCell, 'div', 'small text-muted', safeText(job.manager || job.customer_name || job.subscription_id, 'Scope pending'));
        appendText(reportCell, 'div', 'small text-muted', safeText(job.tech || job.technology, 'Technology pending'));
        tr.appendChild(reportCell);

        var statusCell = document.createElement('td');
        appendText(statusCell, 'span', 'badge rounded-pill ' + statusBadgeClass(status), status || 'unknown');
        appendText(statusCell, 'div', 'small text-muted mt-1', safeText(job.current_step || job.message, 'Waiting for status'));
        tr.appendChild(statusCell);

        var progressCell = document.createElement('td');
        var barWrap = document.createElement('div');
        barWrap.className = 'progress';
        barWrap.style.height = '0.75rem';
        var bar = document.createElement('div');
        bar.className = 'progress-bar';
        bar.style.width = progress + '%';
        bar.setAttribute('role', 'progressbar');
        bar.setAttribute('aria-valuenow', String(progress));
        bar.setAttribute('aria-valuemin', '0');
        bar.setAttribute('aria-valuemax', '100');
        barWrap.appendChild(bar);
        progressCell.appendChild(barWrap);
        appendText(progressCell, 'div', 'small text-muted mt-1', progress + '% complete');
        tr.appendChild(progressCell);

        var runtimeCell = document.createElement('td');
        appendText(runtimeCell, 'div', 'small', 'Elapsed ' + formatElapsed(job.elapsed_seconds));
        appendText(runtimeCell, 'div', 'small text-muted', 'Model: ' + modelLabel(job));
        var corpusText = 'Corpus: ';
        if (job.corpus_eligible === true) {
            corpusText += 'eligible';
        } else if (job.corpus_eligible === false) {
            corpusText += 'not indexed';
        } else {
            corpusText += 'pending';
        }
        appendText(runtimeCell, 'div', 'small text-muted', corpusText);
        tr.appendChild(runtimeCell);

        var actionCell = document.createElement('td');
        actionCell.className = 'text-nowrap';
        if (aid) {
            appendAction(actionCell, 'Open progress', '/progress/' + encodeURIComponent(aid), 'btn btn-sm btn-outline-primary me-1 mb-1');
            if (job.word_available && status === 'completed') {
                appendOpenButton(actionCell, 'Open Word', aid, 'docx', 'btn btn-sm btn-outline-success me-1 mb-1');
                appendAction(actionCell, 'Download Word', '/download/' + encodeURIComponent(aid) + '/docx', 'btn btn-sm btn-outline-secondary me-1 mb-1');
            }
            if (job.excel_available && status === 'completed') {
                appendOpenButton(actionCell, 'Open Excel', aid, 'xlsx', 'btn btn-sm btn-outline-success me-1 mb-1');
                appendAction(actionCell, 'Download Excel', '/download/' + encodeURIComponent(aid) + '/xlsx', 'btn btn-sm btn-outline-secondary me-1 mb-1');
            }
            if (status === 'completed' && (job.word_available || job.excel_available)) {
                appendOpenButton(actionCell, 'Open Folder', aid, 'folder', 'btn btn-sm btn-outline-primary me-1 mb-1');
            }
            if (ACTIVE_STATUSES[status]) {
                var cancel = document.createElement('button');
                cancel.type = 'button';
                cancel.className = 'btn btn-sm btn-outline-danger mb-1';
                cancel.textContent = status === 'cancelling' ? 'Cancelling' : 'Cancel';
                cancel.disabled = status === 'cancelling';
                cancel.addEventListener('click', function () { cancelJob(aid, cancel); });
                actionCell.appendChild(cancel);
            }
        }
        tr.appendChild(actionCell);
        return tr;
    }

    function mergeOptimistic(serverJobs) {
        var seen = {};
        serverJobs.forEach(function (job) {
            if (job && job.analysis_id) { seen[String(job.analysis_id)] = true; }
        });
        Object.keys(optimisticJobs).forEach(function (aid) {
            if (!seen[aid]) { serverJobs.unshift(optimisticJobs[aid]); }
        });
        serverJobs.forEach(function (job) {
            if (job && job.analysis_id && !ACTIVE_STATUSES[String(job.status || '').toLowerCase()]) {
                delete optimisticJobs[String(job.analysis_id)];
            }
        });
        return serverJobs;
    }

    function setPanelPollError(message) {
        document.querySelectorAll('[data-report-jobs-panel]').forEach(function (panel) {
            var alert = panel.querySelector('[data-report-jobs-error]');
            if (!message) {
                if (alert) { alert.hidden = true; }
                return;
            }
            if (!alert) {
                alert = document.createElement('div');
                alert.className = 'alert alert-warning small m-3 mb-0';
                alert.setAttribute('role', 'status');
                alert.setAttribute('data-report-jobs-error', '');
                var body = panel.querySelector('.card-body') || panel;
                body.insertBefore(alert, body.firstChild);
            }
            alert.hidden = false;
            alert.textContent = message;
        });
    }

    function render(jobs) {
        var panels = document.querySelectorAll('[data-report-jobs-panel]');
        if (!panels.length) { return false; }
        jobs = sortJobsForDisplay(mergeOptimistic(jobs || []));
        var activeJobs = jobs.filter(isActiveJob);
        var historyJobs = jobs.filter(function (job) { return !isActiveJob(job); });
        var activeCount = activeJobs.length;
        panels.forEach(function (panel) {
            var body = panel.querySelector('[data-report-jobs-body]');
            var empty = panel.querySelector('[data-report-jobs-empty]');
            var count = panel.querySelector('[data-report-jobs-count]');
            var model = panel.querySelector('[data-report-model-current]');
            if (count) { count.textContent = String(activeCount); }
            if (model) {
                var firstModel = jobs.map(modelLabel).filter(function (v) { return v && v !== 'n/a'; })[0];
                model.textContent = firstModel || model.getAttribute('data-default-model') || 'n/a';
            }
            if (!body) { return; }
            body.textContent = '';
            var visible = panel.hasAttribute('data-report-jobs-current-only') ? activeJobs.slice(0, 6) : jobs.slice(0, 12);
            visible.forEach(function (job) { body.appendChild(renderRow(job)); });
            if (empty) { empty.hidden = visible.length > 0; }
        });
        document.querySelectorAll('[data-report-history-panel]').forEach(function (panel) {
            var body = panel.querySelector('[data-report-history-body]');
            var empty = panel.querySelector('[data-report-history-empty]');
            var count = panel.querySelector('[data-report-history-count]');
            if (count) { count.textContent = String(historyJobs.length); }
            if (!body) { return; }
            body.textContent = '';
            historyJobs.slice(0, 6).forEach(function (job) { body.appendChild(renderRow(job)); });
            if (empty) { empty.hidden = historyJobs.length > 0; }
        });
        return activeCount > 0;
    }

    async function refresh() {
        var panels = document.querySelectorAll('[data-report-jobs-panel]');
        if (!panels.length) { return; }
        var active = false;
        try {
            var response = await fetch(STATUS_URL, { headers: { 'Accept': 'application/json' } });
            if (!response.ok) { throw new Error('status ' + response.status); }
            var payload = await response.json();
            setPanelPollError('');
            active = render(normalizePayload(payload));
        } catch (err) {
            setPanelPollError('Unable to refresh report jobs; showing last local start state only.');
            active = render(Object.keys(optimisticJobs).map(function (aid) { return optimisticJobs[aid]; }));
        } finally {
            schedule(active ? FAST_MS : SLOW_MS);
        }
    }

    function schedule(ms) {
        if (timer) { window.clearTimeout(timer); }
        timer = window.setTimeout(refresh, document.hidden ? SLOW_MS : ms);
    }

    async function cancelJob(analysisId, button) {
        if (!analysisId) { return; }
        try {
            var response = await fetch('/cancel/' + encodeURIComponent(analysisId), {
                method: 'POST',
                headers: {
                    'X-CSRFToken': getCsrfToken(),
                    'X-Requested-With': 'XMLHttpRequest',
                    'Accept': 'application/json'
                }
            });
            if (response.ok) {
                await refresh();
            } else {
                markButtonFailure(button, 'Cancel failed', 'report-jobs-cancel');
            }
        } catch (err) {
            markButtonFailure(button, 'Cancel failed', 'report-jobs-cancel');
        }
    }

    async function openReportArtifact(analysisId, target, button) {
        if (!analysisId || !target) { return; }
        try {
            if (button) {
                button.disabled = true;
                button.setAttribute('aria-busy', 'true');
            }
            var response = await fetch('/open-report/' + encodeURIComponent(analysisId) + '/' + encodeURIComponent(target), {
                method: 'POST',
                headers: {
                    'X-CSRFToken': getCsrfToken(),
                    'X-Requested-With': 'XMLHttpRequest',
                    'Accept': 'application/json'
                }
            });
            if (!response.ok) {
                await response.json().catch(function () { return null; });
                markButtonFailure(button, 'Open failed', 'report-jobs-open');
            }
        } catch (err) {
            // The next click can retry; the server owns path validation.
            markButtonFailure(button, 'Open failed', 'report-jobs-open');
        } finally {
            if (button) {
                button.disabled = false;
                button.removeAttribute('aria-busy');
            }
        }
    }

    function recordStartedJob(job) {
        if (!job || !job.analysis_id) { return; }
        var aid = String(job.analysis_id);
        optimisticJobs[aid] = Object.assign({
            status: 'starting',
            progress: 0,
            current_step: 'Queued on server',
            start_time: new Date().toISOString(),
            elapsed_seconds: 0
        }, job);
        render(Object.keys(optimisticJobs).map(function (key) { return optimisticJobs[key]; }));
        refresh();
    }

    document.addEventListener('visibilitychange', function () {
        schedule(document.hidden ? SLOW_MS : FAST_MS);
    });
    document.addEventListener('DOMContentLoaded', function () { refresh(); });

    window.AdoptIQReportJobs = {
        recordStartedJob: recordStartedJob,
        refreshNow: refresh,
        openReportArtifact: openReportArtifact
    };
})();
