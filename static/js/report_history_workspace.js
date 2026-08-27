/* Round 146: filterable report history, canonical comparison, and safe reopen.
 * Server responses are always rendered with textContent and same-origin links.
 */
(function () {
    'use strict';

    var form = document.querySelector('[data-history-filter-form]');
    if (!form || window.__adoptiqReportHistoryWorkspace) { return; }
    window.__adoptiqReportHistoryWorkspace = true;

    var HISTORY_URL = '/api/decision-workspace/history';
    var COMPARE_URL = '/api/decision-workspace/compare';
    var REPORT_URL = '/api/decision-workspace/report/';
    var selected = {};
    var visibleReports = {};
    var allReports = [];
    var visibleLimit = 12;
    var activeController = null;

    function text(value, fallback) {
        if (value === null || value === undefined) { return fallback || ''; }
        var result = String(value).trim();
        return result || (fallback || '');
    }

    function element(tag, className, value) {
        var node = document.createElement(tag);
        if (className) { node.className = className; }
        if (value !== undefined && value !== null) { node.textContent = text(value); }
        return node;
    }

    function clear(node) {
        if (node) { node.replaceChildren(); }
    }

    function safeRelativeHref(value) {
        var href = text(value);
        return href.charAt(0) === '/' && href.charAt(1) !== '/' ? href : '';
    }

    function csrfToken() {
        var meta = document.querySelector('meta[name="csrf-token"]');
        return meta ? text(meta.getAttribute('content')) : '';
    }

    function displayDate(value) {
        var raw = text(value, 'Time unavailable');
        var parsed = Date.parse(raw);
        if (!Number.isFinite(parsed)) { return raw; }
        try {
            return new Intl.DateTimeFormat(undefined, {
                dateStyle: 'medium', timeStyle: 'short'
            }).format(new Date(parsed));
        } catch (_) {
            return raw;
        }
    }

    function appendLink(container, label, href, className) {
        var safeHref = safeRelativeHref(href);
        if (!safeHref) { return; }
        var link = element('a', className || 'btn btn-sm btn-outline-primary', label);
        link.href = safeHref;
        container.appendChild(link);
    }

    function setHistoryState(kind, message) {
        var status = document.querySelector('[data-history-status]');
        var results = document.querySelector('[data-history-results]');
        if (!status || !results) { return; }
        status.hidden = false;
        status.className = 'alert ' + (kind === 'error' ? 'alert-warning' : 'alert-info');
        status.textContent = message;
        if (kind !== 'ready') { results.hidden = true; }
    }

    function comparisonEligible(report) {
        return report && report.is_completed === true && report.excel_available === true;
    }

    function updateSelectionState(message) {
        var ids = Object.keys(selected);
        var count = document.querySelector('[data-compare-selection-count]');
        var button = document.querySelector('[data-compare-button]');
        if (count) { count.textContent = message || (ids.length + ' of 2 selected'); }
        if (button) { button.disabled = ids.length !== 2; }
    }

    function reportLabel(report) {
        return text(report.report_label, text(report.report_type, 'Report').replace(/_/g, ' '));
    }

    function scopeLabel(report) {
        if (text(report.customer)) { return text(report.customer); }
        if (text(report.scope_type) === 'team' && text(report.manager)) { return text(report.manager) + ' team'; }
        return text(report.manager, 'Portfolio scope');
    }

    function askReportUrl(analysisId) {
        var params = new URLSearchParams({ report_analysis_id: analysisId });
        return '/ask-ai?' + params.toString();
    }

    function handleSelection(report, checkbox) {
        var id = text(report.analysis_id);
        if (!id) { checkbox.checked = false; return; }
        if (checkbox.checked) {
            if (Object.keys(selected).length >= 2) {
                checkbox.checked = false;
                updateSelectionState('Two reports are already selected');
                return;
            }
            selected[id] = report;
        } else {
            delete selected[id];
        }
        updateSelectionState();
    }

    function renderHistoryCard(report) {
        var card = element('article', 'workspace-history-card');
        var selectRow = element('div', 'workspace-history-card__select');
        var checkbox = document.createElement('input');
        checkbox.type = 'checkbox';
        checkbox.className = 'form-check-input';
        checkbox.value = text(report.analysis_id);
        checkbox.disabled = !comparisonEligible(report);
        checkbox.checked = !!selected[text(report.analysis_id)];
        checkbox.setAttribute(
            'aria-label',
            'Select ' + reportLabel(report) + ' for ' + scopeLabel(report) + ' (' + text(report.scope_type, 'team') + ' scope), ' + text(report.technology, 'all technologies') + ', completed ' + displayDate(report.completed_at || report.started_at) + ', run ending ' + text(report.analysis_id).slice(-8) + ' for comparison'
        );
        checkbox.setAttribute('data-report-compare-id', text(report.analysis_id));
        if (!comparisonEligible(report)) {
            checkbox.title = 'Comparison requires a completed report with a Source Data workbook.';
        }
        checkbox.addEventListener('change', function () { handleSelection(report, checkbox); });
        selectRow.appendChild(checkbox);

        var body = element('div', 'workspace-history-card__body');
        var heading = element('div', 'd-flex flex-wrap align-items-start justify-content-between gap-2');
        var titleWrap = element('div');
        titleWrap.appendChild(element('h3', 'h6 mb-1', reportLabel(report)));
        titleWrap.appendChild(element('p', 'small text-muted mb-0', scopeLabel(report)));
        heading.appendChild(titleWrap);
        var status = text(report.status, 'unknown');
        var badgeClass = report.is_completed ? 'bg-success' : (status === 'error' ? 'bg-danger' : 'bg-secondary');
        heading.appendChild(element('span', 'badge rounded-pill ' + badgeClass, status.replace(/_/g, ' ')));
        body.appendChild(heading);

        var meta = element('div', 'workspace-history-card__meta');
        meta.appendChild(element('span', '', 'Completed: ' + displayDate(report.completed_at || report.started_at)));
        meta.appendChild(element('span', '', 'Manager: ' + text(report.manager, 'Not applicable')));
        meta.appendChild(element('span', '', 'Technology: ' + text(report.technology, 'All')));
        meta.appendChild(element('span', '', 'Window: ' + (report.days ? text(report.days) + ' days' : 'Unknown')));
        meta.appendChild(element('span', '', 'Scope: ' + text(report.scope_type, 'team')));
        body.appendChild(meta);

        var actions = element('div', 'd-flex flex-wrap gap-2 mt-3');
        if (text(report.analysis_id)) {
            var review = element('button', 'btn btn-sm btn-outline-primary', 'Review decision view');
            review.type = 'button';
            review.setAttribute('data-report-detail-id', text(report.analysis_id));
            review.addEventListener('click', function () { loadReportDetail(text(report.analysis_id)); });
            actions.appendChild(review);
            appendLink(actions, 'Open progress', report.progress_url, 'btn btn-sm btn-outline-secondary');
            if (report.is_completed) { appendLink(actions, 'Ask about this report', askReportUrl(text(report.analysis_id)), 'btn btn-sm btn-outline-primary'); }
        }
        appendLink(actions, 'Download Word', report.word_url, 'btn btn-sm btn-primary');
        appendLink(actions, 'Download Source Data', report.source_data_url, 'btn btn-sm btn-outline-success');
        body.appendChild(actions);
        selectRow.appendChild(body);
        card.appendChild(selectRow);
        return card;
    }

    function renderHistory(reports) {
        var results = document.querySelector('[data-history-results]');
        var status = document.querySelector('[data-history-status]');
        var count = document.querySelector('[data-history-count]');
        var fallback = document.querySelector('[data-history-fallback]');
        if (!results) { return; }
        allReports = Array.isArray(reports) ? reports.slice() : [];
        var renderedReports = allReports.slice(0, visibleLimit);
        clear(results);
        visibleReports = {};
        renderedReports.forEach(function (report) {
            var id = text(report.analysis_id);
            if (id) { visibleReports[id] = report; }
            results.appendChild(renderHistoryCard(report));
        });
        Object.keys(selected).forEach(function (id) {
            if (!visibleReports[id]) { delete selected[id]; }
        });
        updateSelectionState();
        if (count) { count.textContent = String(allReports.length); }
        results.hidden = false;
        if (status) {
            status.hidden = false;
            status.className = 'workspace-state';
            status.textContent = allReports.length
                ? 'Showing ' + renderedReports.length + ' of ' + allReports.length + ' server-recorded reports.'
                : 'No reports match these filters.';
        }
        var showMore = document.querySelector('[data-history-show-more]');
        if (showMore) {
            showMore.hidden = renderedReports.length >= allReports.length;
            showMore.textContent = 'Show ' + Math.min(12, Math.max(0, allReports.length - renderedReports.length)) + ' more reports';
        }
        if (fallback) { fallback.hidden = true; }
    }

    function filterQuery() {
        var params = new URLSearchParams();
        new FormData(form).forEach(function (value, key) {
            var normalized = text(value);
            if (normalized) { params.set(key, normalized); }
        });
        return params;
    }

    async function loadHistory() {
        if (activeController) { activeController.abort(); }
        activeController = new AbortController();
        setHistoryState('loading', 'Loading server-recorded report history…');
        var params = filterQuery();
        try {
            var response = await fetch(HISTORY_URL + (params.toString() ? '?' + params.toString() : ''), {
                headers: { 'Accept': 'application/json' },
                signal: activeController.signal
            });
            var payload = await response.json().catch(function () { return {}; });
            if (!response.ok || !payload.ok || !Array.isArray(payload.reports)) {
                throw new Error(text(payload.error, 'Report history could not be loaded.'));
            }
            renderHistory(payload.reports);
        } catch (error) {
            if (error && error.name === 'AbortError') { return; }
            setHistoryState('error', text(error && error.message, 'Report history is temporarily unavailable. The file list remains below.'));
            var fallback = document.querySelector('[data-history-fallback]');
            if (fallback) { fallback.hidden = false; }
        }
    }

    function appendDetailMetric(container, label, value) {
        var card = element('div', 'workspace-kpi');
        card.appendChild(element('div', 'workspace-kpi__label', label));
        card.appendChild(element('div', 'workspace-kpi__value', value));
        container.appendChild(card);
    }

    function numericValue(value) {
        var number = Number(value);
        return Number.isFinite(number) ? number : null;
    }

    function renderDetailCharts(report) {
        var section = element('section', 'mb-4');
        section.appendChild(element('h3', 'h6', 'Decision charts'));
        var grid = element('div', 'workspace-chart-grid');
        var charts = Array.isArray(report.charts) ? report.charts : [];
        if (!charts.length) {
            grid.appendChild(element('p', 'workspace-state mb-0', 'No canonical chart series were available in this artifact.'));
            section.appendChild(grid);
            return section;
        }
        charts.slice(0, 6).forEach(function (chart) {
            var series = Array.isArray(chart.series) ? chart.series : (Array.isArray(chart.points) ? chart.points : []);
            series = series.filter(function (item) { return item && typeof item === 'object'; }).slice(0, 12);
            var figure = element('figure', 'workspace-chart');
            var caption = element('figcaption');
            caption.appendChild(element('strong', '', text(chart.label || chart.title, 'Decision chart')));
            if (text(chart.description)) { caption.appendChild(element('p', 'small text-muted mb-0 mt-1', text(chart.description))); }
            figure.appendChild(caption);
            if (!series.length) {
                figure.appendChild(element('p', 'small text-muted mb-0 mt-2', 'No values were available for this series.'));
                grid.appendChild(figure);
                return;
            }
            var maximum = Math.max.apply(null, series.map(function (item) {
                return Math.abs(numericValue(item.value) || 0);
            }).concat([0]));
            var list = element('ul', 'workspace-chart__series');
            series.forEach(function (item) {
                var value = numericValue(item.value);
                var display = text(item.display_value);
                if (!display) { display = value === null ? 'Unknown' : text(value); }
                if (text(chart.unit) && display.indexOf(text(chart.unit)) === -1) { display += ' ' + text(chart.unit); }
                var point = element('li');
                var header = element('div', 'workspace-chart__series-header');
                header.appendChild(element('span', '', text(item.label || item.name, 'Series value')));
                header.appendChild(element('strong', '', display));
                point.appendChild(header);
                var track = element('div', 'workspace-chart__track');
                track.setAttribute('role', 'img');
                track.setAttribute('aria-label', text(item.label || item.name, 'Series value') + ': ' + display);
                var bar = element('span', 'workspace-chart__bar');
                bar.style.width = (maximum > 0 && value !== null ? Math.max(1, Math.min(100, Math.abs(value) / maximum * 100)) : 0) + '%';
                track.appendChild(bar);
                point.appendChild(track);
                list.appendChild(point);
            });
            figure.appendChild(list);
            grid.appendChild(figure);
        });
        section.appendChild(grid);
        return section;
    }

    function renderDetailReadiness(report) {
        var readiness = report.customer_share_readiness && typeof report.customer_share_readiness === 'object'
            ? report.customer_share_readiness
            : {};
        var ready = readiness.customer_shareable === true;
        var alert = element('div', 'alert customer-share-readiness mb-4 ' + (ready ? 'alert-success' : 'alert-warning'));
        alert.setAttribute('role', 'status');
        alert.appendChild(element(
            'strong',
            '',
            ready
                ? 'Customer-share checks passed for this exact report.'
                : 'Internal preview — not customer shareable.'
        ));
        if (!ready) {
            alert.appendChild(element(
                'p',
                'small mb-0 mt-1',
                'This view is for internal review only and does not claim production accuracy.'
            ));
        }
        return alert;
    }

    function renderDetailInsights(report) {
        var section = element('section', 'mb-4');
        section.appendChild(element('h3', 'h6', 'Evidence-backed insights'));
        var insights = Array.isArray(report.decision_insights) ? report.decision_insights : [];
        var list = element('div', 'workspace-insight-list');
        if (!insights.length) {
            list.appendChild(element('p', 'workspace-state mb-0', 'No supported evidence-backed insights were available in this artifact.'));
            section.appendChild(list);
            return section;
        }
        insights.slice(0, 4).forEach(function (insight) {
            var card = element('article', 'workspace-insight');
            card.appendChild(element('strong', '', text(insight.label, 'Executive insight')));
            card.appendChild(element('p', 'mb-2 mt-2', text(insight.claim, 'Claim unavailable')));
            if (text(insight.caveat)) {
                card.appendChild(element('p', 'small text-muted mb-2', text(insight.caveat)));
            }
            var sources = Array.isArray(insight.source_sheets) ? insight.source_sheets.filter(function (item) {
                return typeof item === 'string' && text(item);
            }) : [];
            if (sources.length) {
                card.appendChild(element('p', 'small text-muted mb-0', 'Sources: ' + sources.join(', ') + ' · State: ' + text(insight.source_state, 'unknown')));
            }
            if (text(insight.evidence_key)) {
                card.appendChild(element(
                    'p',
                    'small text-muted mb-0 mt-1',
                    'Evidence: ' + text(insight.evidence_key) + ' · ' + text(insight.evidence_count, '0') + ' linked source record(s)'
                ));
            }
            list.appendChild(card);
        });
        section.appendChild(list);
        return section;
    }

    function renderReportDetail(report) {
        var panel = document.querySelector('[data-history-detail-panel]');
        var title = document.querySelector('[data-history-detail-title]');
        var meta = document.querySelector('[data-history-detail-meta]');
        var body = document.querySelector('[data-history-detail-body]');
        var actions = document.querySelector('[data-history-detail-actions]');
        if (!panel || !body || !actions) { return; }
        panel.hidden = false;
        if (title) { title.textContent = text(report.scope_label, 'Portfolio') + ' decision view'; }
        if (meta) {
            meta.textContent = [text(report.report_type, 'report').replace(/_/g, ' '), text(report.manager), text(report.technology), report.days ? text(report.days) + ' days' : '', report.data_as_of_utc ? 'As of ' + text(report.data_as_of_utc) : '']
                .filter(Boolean).join(' · ');
        }
        clear(actions);
        var downloads = report.downloads || {};
        appendLink(actions, 'Download Word', downloads.word, 'btn btn-sm btn-primary');
        appendLink(actions, 'Download Source Data', downloads.source_data, 'btn btn-sm btn-outline-success');
        appendLink(actions, 'Ask about this report', report.ask_ai_url, 'btn btn-sm btn-outline-primary');

        clear(body);
        body.appendChild(renderDetailReadiness(report));
        body.appendChild(renderDetailInsights(report));
        var metrics = element('div', 'workspace-kpi-grid mb-4');
        var decisionMetrics = Array.isArray(report.decision_metrics) ? report.decision_metrics : [];
        decisionMetrics.slice(0, 9).forEach(function (metric) {
            var value = text(metric.display_value);
            if (!value && metric.value !== null && metric.value !== undefined) { value = text(metric.value); }
            appendDetailMetric(metrics, text(metric.label, metric.metric_key || 'Metric'), value || 'Unknown');
        });
        if (!decisionMetrics.length) { metrics.appendChild(element('p', 'workspace-state mb-0', 'No canonical KPIs were available.')); }
        body.appendChild(metrics);
        body.appendChild(renderDetailCharts(report));

        var row = element('div', 'row g-4');
        var risksColumn = element('section', 'col-lg-5');
        risksColumn.appendChild(element('h3', 'h6', 'Account exceptions'));
        var risks = element('div', 'workspace-account-list');
        var accounts = Array.isArray(report.top_accounts) ? report.top_accounts : [];
        accounts.forEach(function (account) {
            var card = element('article', 'workspace-account');
            card.appendChild(element('strong', '', text(account.customer, 'Unknown customer')));
            card.appendChild(element('p', 'small text-muted mb-0 mt-1', 'Risk: ' + text(account.risk_band, 'Unknown') + ' · Score: ' + text(account.risk_score_0_100, 'Unknown') + ' · Overdue actions: ' + text(account.overdue_action_plans, 'Unknown')));
            risks.appendChild(card);
        });
        if (!accounts.length) { risks.appendChild(element('p', 'workspace-state mb-0', 'No account exceptions were recorded.')); }
        risksColumn.appendChild(risks);
        row.appendChild(risksColumn);

        var actionColumn = element('section', 'col-lg-7');
        actionColumn.appendChild(element('h3', 'h6', 'Action plans requiring attention'));
        var actionList = element('div', 'workspace-action-list');
        var actionPlans = Array.isArray(report.top_action_plans) ? report.top_action_plans : [];
        actionPlans.forEach(function (action) {
            var card = element('article', 'workspace-action' + (action.is_overdue ? ' workspace-action--overdue' : ''));
            card.appendChild(element('strong', '', text(action.title, 'Action title unavailable')));
            card.appendChild(element('p', 'small text-muted mb-0 mt-1', 'ID: ' + text(action.record_id, 'Missing') + ' · ' + text(action.customer, 'Unknown customer') + ' · Owner: ' + text(action.owner, 'Unassigned') + ' · ' + text(action.status, 'Unknown') + ' · Due: ' + text(action.due_date, 'Not set')));
            if (text(action.next_action)) { card.appendChild(element('p', 'small mb-0 mt-2', 'Next: ' + text(action.next_action))); }
            actionList.appendChild(card);
        });
        if (!actionPlans.length) { actionList.appendChild(element('p', 'workspace-state mb-0', 'No action plans were available.')); }
        actionColumn.appendChild(actionList);
        row.appendChild(actionColumn);
        body.appendChild(row);

        var limitations = [];
        (Array.isArray(report.source_limitations) ? report.source_limitations : []).forEach(function (item) {
            limitations.push(text(item.source, 'Source') + ': ' + text(item.state, 'unknown'));
        });
        (Array.isArray(report.source_warnings) ? report.source_warnings : []).forEach(function (item) {
            if (typeof item === 'string' && text(item)) { limitations.push(text(item)); }
        });
        var limitationSection = element('section', 'mt-4');
        limitationSection.appendChild(element('h3', 'h6', 'Source coverage and limitations'));
        if (limitations.length) {
            var list = element('ul', 'workspace-limitation-list mb-0');
            limitations.forEach(function (item) { list.appendChild(element('li', '', item)); });
            limitationSection.appendChild(list);
        } else {
            limitationSection.appendChild(element('p', 'small text-muted mb-0', 'No source limitations were recorded in this artifact.'));
        }
        body.appendChild(limitationSection);
        panel.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }

    async function loadReportDetail(analysisId) {
        if (!/^[A-Za-z0-9._-]{1,200}$/.test(analysisId)) { return; }
        var panel = document.querySelector('[data-history-detail-panel]');
        var body = document.querySelector('[data-history-detail-body]');
        if (panel) { panel.hidden = false; }
        if (body) { clear(body); body.appendChild(element('p', 'workspace-state mb-0', 'Loading server-verified report facts…')); }
        try {
            var response = await fetch(REPORT_URL + encodeURIComponent(analysisId), { headers: { 'Accept': 'application/json' } });
            var payload = await response.json().catch(function () { return {}; });
            if (!response.ok || !payload.ok || !payload.report) {
                throw new Error(text(payload.error, 'This decision view could not be loaded.'));
            }
            renderReportDetail(payload.report);
        } catch (error) {
            if (body) { clear(body); body.appendChild(element('p', 'workspace-state text-warning mb-0', text(error && error.message, 'This decision view is unavailable.'))); }
        }
    }

    function appendCompareStat(container, label, value) {
        var card = element('div', 'workspace-compare-stat');
        card.appendChild(element('div', 'workspace-compare-stat__label', label));
        card.appendChild(element('div', 'workspace-compare-stat__value', value));
        container.appendChild(card);
    }

    function changeLabel(value) {
        var labels = {
            'new': 'New', 'absent': 'No longer present', 'completed': 'Completed',
            'reopened': 'Reopened', 'became_overdue': 'Became overdue',
            'owner_changed': 'Owner changed', 'due_date_changed': 'Due date changed',
            'status_changed': 'Status changed'
        };
        return labels[text(value)] || text(value, 'Changed').replace(/_/g, ' ');
    }

    function renderMetricChanges(changes) {
        var container = document.querySelector('[data-compare-metrics]');
        if (!container) { return; }
        clear(container);
        if (!changes.length) { container.appendChild(element('p', 'workspace-state mb-0', 'No KPI movement was detected.')); return; }
        var wrap = element('div', 'workspace-table-wrap');
        var table = element('table', 'table table-sm align-middle mb-0');
        var head = element('thead');
        var headRow = element('tr');
        ['Metric', 'Before', 'After', 'Change', 'Canonical source'].forEach(function (label) { headRow.appendChild(element('th', '', label)); });
        head.appendChild(headRow);
        table.appendChild(head);
        var body = element('tbody');
        changes.forEach(function (change) {
            var row = element('tr');
            row.appendChild(element('th', '', text(change.label, change.metric_key || 'Metric')));
            row.appendChild(element('td', '', text(change.before, 'Unknown')));
            row.appendChild(element('td', '', text(change.after, 'Unknown')));
            var delta = change.delta === null || change.delta === undefined ? 'Not comparable' : (Number(change.delta) > 0 ? '+' : '') + text(change.delta);
            row.appendChild(element('td', '', delta));
            row.appendChild(element('td', 'small text-muted', text(change.source_sheet, 'Source unavailable')));
            body.appendChild(row);
        });
        table.appendChild(body);
        wrap.appendChild(table);
        container.appendChild(wrap);
    }

    function renderActionChanges(changes) {
        var container = document.querySelector('[data-compare-actions]');
        if (!container) { return; }
        clear(container);
        if (!changes.length) { container.appendChild(element('p', 'workspace-state mb-0', 'No stable-ID Action Plan movement was detected.')); return; }
        var list = element('div', 'workspace-action-list');
        changes.forEach(function (change) {
            var card = element('article', 'workspace-action');
            var heading = element('div', 'd-flex flex-wrap justify-content-between gap-2');
            heading.appendChild(element('strong', '', text(change.record_id, 'Missing stable ID')));
            heading.appendChild(element('span', 'badge bg-primary', changeLabel(change.change)));
            card.appendChild(heading);
            if (text(change.customer) || text(change.title)) {
                card.appendChild(element('p', 'small mb-0 mt-2', [text(change.customer), text(change.title)].filter(Boolean).join(' · ')));
            }
            if (change.before !== undefined || change.after !== undefined) {
                card.appendChild(element('p', 'small text-muted mb-0 mt-1', 'Before: ' + text(change.before, 'Unknown') + ' → After: ' + text(change.after, 'Unknown')));
            }
            list.appendChild(card);
        });
        container.appendChild(list);
    }

    function renderSourceChanges(changes) {
        var container = document.querySelector('[data-compare-sources]');
        if (!container) { return; }
        clear(container);
        if (!changes.length) { container.appendChild(element('p', 'workspace-state mb-0', 'Source availability did not change.')); return; }
        var list = element('ul', 'workspace-plain-list');
        changes.forEach(function (change) {
            list.appendChild(element('li', '', text(change.source, 'Source') + ': ' + text(change.before, 'unknown') + ' → ' + text(change.after, 'unknown')));
        });
        container.appendChild(list);
    }

    function renderComparison(comparison) {
        var panel = document.querySelector('[data-compare-panel]');
        var status = document.querySelector('[data-compare-status]');
        var content = document.querySelector('[data-compare-content]');
        var summary = document.querySelector('[data-compare-summary]');
        var caveats = document.querySelector('[data-compare-caveats]');
        var scopeBadge = document.querySelector('[data-compare-scope-state]');
        var period = document.querySelector('[data-compare-period]');
        if (!panel || !content || !summary || !caveats) { return; }
        panel.hidden = false;
        if (status) { status.hidden = true; }
        content.hidden = false;
        if (scopeBadge) {
            scopeBadge.textContent = comparison.same_scope ? 'Like-for-like scope' : 'Different scopes';
            scopeBadge.className = 'badge rounded-pill ' + (comparison.same_scope ? 'bg-success' : 'bg-warning text-dark');
        }
        if (period) {
            period.textContent = text(comparison.before && comparison.before.scope_label, 'Earlier report') + ' → ' + text(comparison.after && comparison.after.scope_label, 'Later report');
        }
        clear(summary);
        appendCompareStat(summary, 'Business changes', text(comparison.business_change_count, '0'));
        appendCompareStat(summary, 'Source-state changes', text(comparison.source_change_count, '0'));
        appendCompareStat(summary, 'Scope match', comparison.same_scope ? 'Yes' : 'No');
        clear(caveats);
        var caveatValues = Array.isArray(comparison.caveats) ? comparison.caveats : [];
        if (caveatValues.length) {
            var alert = element('div', 'alert alert-warning mb-0');
            alert.appendChild(element('strong', '', 'Interpretation notes'));
            var list = element('ul', 'workspace-limitation-list mb-0 mt-2');
            caveatValues.forEach(function (item) { list.appendChild(element('li', '', item)); });
            alert.appendChild(list);
            caveats.appendChild(alert);
        }
        renderMetricChanges(Array.isArray(comparison.metric_changes) ? comparison.metric_changes : []);
        renderActionChanges(Array.isArray(comparison.action_plan_changes) ? comparison.action_plan_changes : []);
        renderSourceChanges(Array.isArray(comparison.source_state_changes) ? comparison.source_state_changes : []);
        panel.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }

    function comparisonOrder() {
        return Object.keys(selected).map(function (id) { return selected[id]; }).sort(function (a, b) {
            return Date.parse(text(a.completed_at || a.started_at)) - Date.parse(text(b.completed_at || b.started_at));
        });
    }

    async function compareSelected() {
        var reports = comparisonOrder();
        if (reports.length !== 2) { return; }
        var panel = document.querySelector('[data-compare-panel]');
        var status = document.querySelector('[data-compare-status]');
        var content = document.querySelector('[data-compare-content]');
        if (panel) { panel.hidden = false; }
        if (content) { content.hidden = true; }
        if (status) {
            status.hidden = false;
            status.className = 'alert alert-info mb-0';
            status.textContent = 'Comparing canonical Source Data snapshots…';
        }
        try {
            var response = await fetch(COMPARE_URL, {
                method: 'POST',
                credentials: 'same-origin',
                headers: {
                    'Accept': 'application/json',
                    'Content-Type': 'application/json',
                    'X-CSRFToken': csrfToken(),
                    'X-Requested-With': 'XMLHttpRequest'
                },
                body: JSON.stringify({
                    before_analysis_id: text(reports[0].analysis_id),
                    after_analysis_id: text(reports[1].analysis_id)
                })
            });
            var payload = await response.json().catch(function () { return {}; });
            if (!response.ok || !payload.ok || !payload.comparison) {
                throw new Error(text(payload.error, 'These reports could not be compared.'));
            }
            renderComparison(payload.comparison);
        } catch (error) {
            if (status) {
                status.hidden = false;
                status.className = 'alert alert-warning mb-0';
                status.textContent = text(error && error.message, 'Comparison is temporarily unavailable.');
            }
        }
    }

    form.addEventListener('submit', function (event) {
        event.preventDefault();
        selected = {};
        visibleLimit = 12;
        updateSelectionState();
        loadHistory();
    });
    form.addEventListener('reset', function () {
        window.setTimeout(function () {
            selected = {};
            visibleLimit = 12;
            updateSelectionState();
            loadHistory();
        }, 0);
    });
    var compareButton = document.querySelector('[data-compare-button]');
    if (compareButton) { compareButton.addEventListener('click', compareSelected); }
    var showMoreButton = document.querySelector('[data-history-show-more]');
    if (showMoreButton) {
        showMoreButton.addEventListener('click', function () {
            visibleLimit += 12;
            renderHistory(allReports);
        });
    }
    loadHistory();
}());
