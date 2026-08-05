/* Round 146: Manager Decision Workspace.
 *
 * This module owns only presentation and orchestration. Scope authorization,
 * artifact facts, and report bindings stay server-side. All API-provided text
 * is written with textContent; no response value is interpreted as markup.
 */
(function () {
    'use strict';

    if (window.__adoptiqManagerDecisionWorkspace) { return; }
    window.__adoptiqManagerDecisionWorkspace = true;

    var PREVIEW_URL = '/api/decision-workspace/scope-preview';
    var REPORT_URL = '/api/decision-workspace/report/';
    var LEADER_OPTIONS_URL = '/api/leader_scope_options';
    var ACTIVE_STATUSES = { starting: true, initializing: true, running: true, queued: true };
    var previewTimer = null;
    var previewController = null;
    var reportTimer = null;
    var latestReportId = '';
    var activeEvidenceReportId = '';
    var evidenceController = null;
    var evidenceReturnFocus = null;
    var STORAGE_KEY = 'adoptiq.latestDecisionReportId';
    var EVIDENCE_LIMIT = 25;

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

    function selectedReportType() {
        var selected = document.querySelector('input[name="report_type"]:checked');
        return selected ? text(selected.value, 'comprehensive') : 'comprehensive';
    }

    function selectedLeaderScope() {
        var selected = document.querySelector('input[name="workspace_scope_type"]:checked');
        return selected ? text(selected.value, 'team') : 'team';
    }

    function fieldValue(selector, fallback) {
        var field = document.querySelector(selector);
        return field ? text(field.value, fallback) : (fallback || '');
    }

    function subscriptionValue() {
        var canonical = fieldValue('#subscription_id');
        var typed = fieldValue('#subscription-search');
        if (canonical) { return canonical; }
        if (selectedReportType() === 'subscription' && /^[A-Za-z0-9_-]+$/.test(typed)) { return typed; }
        return /^Sub[0-9_-]+$/i.test(typed) ? typed : '';
    }

    function customerValue() {
        var named = fieldValue('#customer_name');
        var typed = fieldValue('#subscription-search');
        return named || (/^Sub[0-9_-]+$/i.test(typed) ? '' : typed);
    }

    function currentSelection() {
        var reportType = selectedReportType();
        var manager = fieldValue('select[name="manager"]');
        var technology = fieldValue('select[name="technology"]', 'All') || 'All';
        var days = fieldValue('[name="days"]', '90') || '90';
        var scopeType = 'team';
        var scopeValue = '';
        var memberEmail = '';
        var subscriptionId = subscriptionValue();

        if (reportType === 'leader') {
            scopeType = selectedLeaderScope();
            memberEmail = fieldValue('#workspaceMember');
            if (scopeType === 'member') { scopeValue = memberEmail; }
            if (scopeType === 'customer') { scopeValue = fieldValue('#workspaceCustomer'); }
        } else if (reportType === 'renewal') {
            scopeType = 'customer';
            scopeValue = customerValue() || subscriptionId;
        } else if (reportType === 'subscription') {
            scopeType = 'subscription';
        } else if ((reportType === 'comprehensive' || reportType === 'compact') && (customerValue() || subscriptionId)) {
            scopeType = 'customer';
            scopeValue = customerValue() || subscriptionId;
        }

        return {
            reportType: reportType,
            manager: manager,
            technology: technology,
            days: days,
            scopeType: scopeType,
            scopeValue: scopeValue,
            memberEmail: memberEmail,
            subscriptionId: subscriptionId
        };
    }

    function syncCanonicalScope() {
        var selection = currentSelection();
        var scopeType = document.getElementById('workspaceScopeType');
        var scopeValue = document.getElementById('workspaceScopeValue');
        if (scopeType) { scopeType.value = selection.scopeType; }
        if (scopeValue) { scopeValue.value = selection.scopeValue; }
        return selection;
    }

    function validSelection(selection) {
        var days = Number(selection.days);
        if (!Number.isInteger(days) || days < 1 || days > 365) {
            return 'Enter a window between 1 and 365 days.';
        }
        if (selection.reportType !== 'renewal' && selection.reportType !== 'subscription' && !selection.manager) {
            return 'Select a manager to define the authorized portfolio.';
        }
        if (selection.scopeType === 'member' && !selection.scopeValue) {
            return 'Select the individual team member to include.';
        }
        if (selection.scopeType === 'customer' && !selection.scopeValue) {
            return 'Select or enter the individual customer to include.';
        }
        if (selection.scopeType === 'subscription' && !selection.subscriptionId) {
            return 'Enter a subscription ID to preview this deep dive.';
        }
        return '';
    }

    function setPreviewState(kind, label, message) {
        var root = document.querySelector('[data-workspace-preview]');
        var badge = document.querySelector('[data-workspace-preview-state]');
        var body = document.querySelector('[data-workspace-preview-body]');
        if (!root || !body) { return; }
        root.setAttribute('data-state', kind);
        if (badge) {
            badge.textContent = label;
            badge.className = 'badge rounded-pill ' + (
                kind === 'ready' ? 'bg-success' :
                kind === 'loading' ? 'bg-primary' :
                kind === 'error' || kind === 'partial' ? 'bg-warning text-dark' : 'bg-secondary'
            );
        }
        clear(body);
        body.appendChild(element('p', kind === 'error' ? 'text-warning mb-0' : 'text-muted mb-0', message));
    }

    function appendSummaryItem(list, label, value) {
        var item = element('div', 'workspace-summary-item');
        item.appendChild(element('dt', '', label));
        item.appendChild(element('dd', '', value));
        list.appendChild(item);
    }

    function sourceModeLabel(preview) {
        var mode = text(preview.source_mode, 'unknown').toLowerCase();
        if (mode.indexOf('fixture') !== -1 || mode.indexOf('local') !== -1) {
            return 'Controlled local fixture';
        }
        if (preview.live_validation_performed === true) { return 'Live sources validated'; }
        return 'Coverage confirmed during generation';
    }

    function friendlyScopeLabel(preview) {
        if (text(preview.scope_type) === 'member') {
            var member = document.getElementById('workspaceMember');
            if (member && member.selectedIndex >= 0) {
                var option = member.options[member.selectedIndex];
                if (option && text(option.value) && text(option.textContent)) { return text(option.textContent); }
            }
        }
        return text(preview.scope_label, 'Selected portfolio');
    }

    function renderPreview(preview) {
        var root = document.querySelector('[data-workspace-preview]');
        var body = document.querySelector('[data-workspace-preview-body]');
        var badge = document.querySelector('[data-workspace-preview-state]');
        if (!root || !body) { return; }

        var limitations = Array.isArray(preview.limitations) ? preview.limitations : [];
        var partial = text(preview.source_state, 'unknown') !== 'available'
            || preview.live_validation_performed === false;
        root.setAttribute('data-state', partial ? 'partial' : 'ready');
        if (badge) {
            badge.textContent = sourceModeLabel(preview);
            badge.className = 'badge rounded-pill ' + (partial ? 'bg-warning text-dark' : 'bg-success');
        }

        clear(body);
        var lead = element('p', 'mb-3');
        lead.appendChild(element('strong', '', text(preview.report_label, 'Report') + ': '));
        lead.appendChild(document.createTextNode(text(preview.purpose, 'Decision-focused report.')));
        body.appendChild(lead);

        var summary = element('dl', 'workspace-summary-grid mb-3');
        appendSummaryItem(summary, 'Scope', friendlyScopeLabel(preview));
        appendSummaryItem(summary, 'Window', text(preview.days, '90') + ' days');
        appendSummaryItem(summary, 'Technology', text(preview.technology, 'All'));
        appendSummaryItem(summary, 'Deliverables', 'Word decision report + Source Data workbook');
        body.appendChild(summary);

        var sources = Array.isArray(preview.expected_sources) ? preview.expected_sources : [];
        body.appendChild(element('h4', 'h6 mb-1', 'Expected sources'));
        if (sources.length) {
            var sourceList = element('ul', 'workspace-source-list mb-3');
            sources.forEach(function (source) { sourceList.appendChild(element('li', '', source)); });
            body.appendChild(sourceList);
        } else {
            body.appendChild(element('p', 'small text-muted mb-3', 'Source coverage will be named in the generated report.'));
        }

        body.appendChild(element('h4', 'h6 mb-1', 'What to know'));
        var limitationList = element('ul', 'workspace-limitation-list mb-0');
        (limitations.length ? limitations : ['Source coverage will be confirmed during generation.'])
            .forEach(function (limitation) { limitationList.appendChild(element('li', '', limitation)); });
        body.appendChild(limitationList);

        var askLink = document.querySelector('[data-workspace-ask-scope]');
        if (askLink) {
            askLink.textContent = 'Ask AI will use this exact scope after the report is generated.';
            askLink.hidden = false;
        }
    }

    async function requestPreview() {
        var root = document.querySelector('[data-workspace-preview]');
        if (!root) { return; }
        var selection = syncCanonicalScope();
        var problem = validSelection(selection);
        if (problem) {
            if (previewController) { previewController.abort(); }
            setPreviewState('waiting', 'Needs selection', problem);
            return;
        }

        if (previewController) { previewController.abort(); }
        previewController = new AbortController();
        setPreviewState('loading', 'Checking scope', 'Confirming the selected scope and expected source coverage…');
        var query = new URLSearchParams({
            report_type: selection.reportType,
            manager: selection.manager,
            technology: selection.technology,
            days: selection.days,
            scope_type: selection.scopeType,
            scope_value: selection.scopeValue,
            member_email: selection.memberEmail,
            subscription_id: selection.subscriptionId
        });
        try {
            var response = await fetch(PREVIEW_URL + '?' + query.toString(), {
                headers: { 'Accept': 'application/json' },
                signal: previewController.signal
            });
            var payload = await response.json().catch(function () { return {}; });
            if (!response.ok || !payload.ok || !payload.preview) {
                throw new Error(text(payload.error, 'The selected scope could not be confirmed.'));
            }
            renderPreview(payload.preview);
        } catch (error) {
            if (error && error.name === 'AbortError') { return; }
            setPreviewState('error', 'Preview unavailable', text(error && error.message, 'The scope preview is temporarily unavailable.'));
        }
    }

    function schedulePreview() {
        if (previewTimer) { window.clearTimeout(previewTimer); }
        previewTimer = window.setTimeout(requestPreview, 260);
    }

    function optionValue(option) {
        return text(option && (option.email || option.value || option.name));
    }

    function optionLabel(option) {
        return text(option && (option.label || option.name || option.email || option.value), 'Unnamed option');
    }

    function replaceOptions(select, options, placeholder) {
        if (!select) { return; }
        var previous = text(select.value);
        clear(select);
        var initial = element('option', '', placeholder);
        initial.value = '';
        select.appendChild(initial);
        (Array.isArray(options) ? options : []).forEach(function (item) {
            var value = optionValue(item);
            if (!value) { return; }
            var option = element('option', '', optionLabel(item));
            option.value = value;
            select.appendChild(option);
        });
        if (previous && Array.prototype.some.call(select.options, function (item) { return item.value === previous; })) {
            select.value = previous;
        }
    }

    function paintLeaderScopeControls() {
        var reportType = selectedReportType();
        var scopeType = selectedLeaderScope();
        var memberGroup = document.querySelector('[data-workspace-member-group]');
        var customerGroup = document.querySelector('[data-workspace-customer-group]');
        var member = document.getElementById('workspaceMember');
        var customer = document.getElementById('workspaceCustomer');
        var showMember = reportType === 'leader' && (scopeType === 'member' || scopeType === 'customer');
        var showCustomer = reportType === 'leader' && scopeType === 'customer';
        if (memberGroup) { memberGroup.hidden = !showMember; }
        if (customerGroup) { customerGroup.hidden = !showCustomer; }
        if (member) {
            member.disabled = !showMember;
            member.required = scopeType === 'member' && showMember;
        }
        if (customer) {
            customer.disabled = !showCustomer;
            customer.required = showCustomer;
        }
        syncCanonicalScope();
    }

    async function loadLeaderOptions() {
        if (selectedReportType() !== 'leader') { return; }
        var manager = fieldValue('select[name="manager"]');
        var member = document.getElementById('workspaceMember');
        var customer = document.getElementById('workspaceCustomer');
        var warning = document.querySelector('[data-workspace-options-warning]');
        var scopeType = selectedLeaderScope();
        var selectedMember = fieldValue('#workspaceMember');
        if (warning) { warning.hidden = true; warning.textContent = ''; }
        if (!manager) {
            replaceOptions(member, [], 'Select a manager first');
            replaceOptions(customer, [], 'Select a manager first');
            schedulePreview();
            return;
        }
        if (member) { member.disabled = true; }
        if (customer) { customer.disabled = true; }
        replaceOptions(member, [], 'Loading authorized members…');
        if (scopeType === 'customer') { replaceOptions(customer, [], 'Loading authorized customers…'); }
        var params = new URLSearchParams({ manager: manager });
        if (scopeType === 'customer') {
            params.set('include_customers', 'true');
            if (selectedMember) { params.set('member_email', selectedMember); }
        }
        try {
            var response = await fetch(LEADER_OPTIONS_URL + '?' + params.toString(), { headers: { 'Accept': 'application/json' } });
            var payload = await response.json().catch(function () { return {}; });
            if (!response.ok || !payload.success) {
                throw new Error(text(payload.error, 'Authorized scope options are unavailable.'));
            }
            replaceOptions(member, payload.members, scopeType === 'member' ? 'Choose a team member' : 'All team members');
            if (member && selectedMember && Array.prototype.some.call(member.options, function (item) { return item.value === selectedMember; })) {
                member.value = selectedMember;
            }
            if (scopeType === 'customer') {
                replaceOptions(customer, payload.customers, 'Choose a customer');
            }
            if (warning && text(payload.warning)) {
                warning.textContent = text(payload.warning);
                warning.hidden = false;
            }
        } catch (error) {
            replaceOptions(member, [], 'Members unavailable');
            replaceOptions(customer, [], 'Customers unavailable');
            if (warning) {
                warning.textContent = text(error && error.message, 'Scope options are temporarily unavailable.');
                warning.hidden = false;
            }
        } finally {
            paintLeaderScopeControls();
            schedulePreview();
        }
    }

    function displayMetric(metric) {
        var shown = text(metric && metric.display_value);
        if (!shown && metric && metric.value !== null && metric.value !== undefined) { shown = text(metric.value); }
        if (!shown) { shown = 'Unknown'; }
        var unit = text(metric && metric.unit);
        return unit && shown.indexOf(unit) === -1 ? shown + ' ' + unit : shown;
    }

    function safeRelativeHref(value) {
        var href = text(value);
        return href.charAt(0) === '/' && href.charAt(1) !== '/' ? href : '';
    }

    function safeAnalysisId(value) {
        var analysisId = text(value);
        return /^[A-Za-z0-9._-]{1,200}$/.test(analysisId) ? analysisId : '';
    }

    function safeEvidenceKey(value) {
        var evidenceKey = text(value);
        if (!evidenceKey || evidenceKey.length > 300 || /[\u0000-\u001F\u007F]/.test(evidenceKey)) { return ''; }
        return evidenceKey;
    }

    function evidenceDialog() {
        return document.querySelector('[data-evidence-dialog]');
    }

    function setEvidenceState(kind, message, retry) {
        var state = document.querySelector('[data-evidence-state]');
        var content = document.querySelector('[data-evidence-content]');
        if (!state) { return; }
        state.hidden = false;
        state.className = 'workspace-evidence-state workspace-evidence-state--' + kind;
        clear(state);
        state.appendChild(document.createTextNode(message));
        if (typeof retry === 'function') {
            var retryButton = element('button', 'btn btn-sm btn-outline-primary mt-3', 'Try again');
            retryButton.type = 'button';
            retryButton.addEventListener('click', retry);
            state.appendChild(retryButton);
        }
        if (content) { content.hidden = kind === 'loading' || kind === 'error'; }
    }

    function hideEvidenceState() {
        var state = document.querySelector('[data-evidence-state]');
        if (!state) { return; }
        clear(state);
        state.hidden = true;
    }

    function restoreEvidenceFocus() {
        var returnTarget = evidenceReturnFocus;
        evidenceReturnFocus = null;
        if (returnTarget && document.contains(returnTarget) && typeof returnTarget.focus === 'function') {
            returnTarget.focus();
        }
    }

    function closeEvidenceDialog() {
        if (evidenceController) {
            evidenceController.abort();
            evidenceController = null;
        }
        var dialog = evidenceDialog();
        if (!dialog) { restoreEvidenceFocus(); return; }
        if (dialog.open && typeof dialog.close === 'function') {
            dialog.close();
        } else {
            dialog.removeAttribute('open');
            restoreEvidenceFocus();
        }
    }

    function openEvidenceDialog(trigger, label) {
        var dialog = evidenceDialog();
        if (!dialog) { return false; }
        evidenceReturnFocus = trigger || document.activeElement;
        var title = document.querySelector('[data-evidence-title]');
        var description = document.querySelector('[data-evidence-description]');
        if (title) { title.textContent = 'Source evidence'; }
        if (description) {
            description.textContent = label
                ? 'Loading the exact source records for ' + label + '.'
                : 'Loading exact source records from this report.';
        }
        setEvidenceState('loading', 'Loading server-verified source records…');
        if (!dialog.open) {
            if (typeof dialog.showModal === 'function') { dialog.showModal(); }
            else { dialog.setAttribute('open', ''); }
        }
        window.requestAnimationFrame(function () {
            var focusTarget = document.querySelector('[data-evidence-title]');
            if (focusTarget && typeof focusTarget.focus === 'function') { focusTarget.focus(); }
        });
        return true;
    }

    function appendEvidenceDefinition(list, label, value) {
        list.appendChild(element('dt', '', label));
        list.appendChild(element('dd', '', text(value, 'Not provided')));
    }

    function renderEvidenceRecord(record, index) {
        var card = element('article', 'workspace-evidence-record');
        var heading = element('div', 'workspace-evidence-record__heading');
        heading.appendChild(element('strong', '', text(record.title, 'Source record ' + text(index + 1))));
        heading.appendChild(element('span', 'badge bg-secondary', text(record.source_sheet, 'Source sheet unavailable')));
        card.appendChild(heading);

        var details = element('dl', 'workspace-evidence-record__details');
        appendEvidenceDefinition(details, 'Source sheet', record.source_sheet);
        appendEvidenceDefinition(details, 'Source row', record.source_row_number);
        appendEvidenceDefinition(details, 'Record ID', record.record_id);
        appendEvidenceDefinition(details, 'ID quality', record.record_id_quality);
        appendEvidenceDefinition(details, 'Customer', record.customer);
        appendEvidenceDefinition(details, 'Title', record.title);
        appendEvidenceDefinition(details, 'Status', record.status);
        appendEvidenceDefinition(details, 'Date', record.date);
        appendEvidenceDefinition(details, 'Owner', record.owner);
        appendEvidenceDefinition(details, 'Summary', record.summary);
        card.appendChild(details);
        return card;
    }

    function renderEvidence(evidence) {
        var title = document.querySelector('[data-evidence-title]');
        var description = document.querySelector('[data-evidence-description]');
        var summary = document.querySelector('[data-evidence-summary]');
        var recordsRoot = document.querySelector('[data-evidence-records]');
        var recordsSection = document.querySelector('[data-evidence-records-section]');
        var limitationsRoot = document.querySelector('[data-evidence-limitations]');
        var limitationsSection = document.querySelector('[data-evidence-limitations-section]');
        var download = document.querySelector('[data-evidence-source-data]');
        var content = document.querySelector('[data-evidence-content]');
        var records = Array.isArray(evidence.records)
            ? evidence.records.filter(function (record) { return record && typeof record === 'object'; }).slice(0, EVIDENCE_LIMIT)
            : [];
        var limitations = Array.isArray(evidence.limitations)
            ? evidence.limitations.filter(function (limitation) {
                return typeof limitation === 'string' && text(limitation);
            })
            : [];

        if (title) { title.textContent = 'Evidence for ' + text(evidence.label, text(evidence.evidence_key, 'selected finding')); }
        if (description) {
            description.textContent = records.length
                ? 'Exact records returned by the report evidence contract.'
                : 'The report evidence contract returned no source records for this item.';
        }
        if (summary) {
            clear(summary);
            appendEvidenceDefinition(summary, 'Evidence key', evidence.evidence_key);
            appendEvidenceDefinition(summary, 'Source state', evidence.source_state);
            appendEvidenceDefinition(summary, 'Total records', evidence.total_records);
            appendEvidenceDefinition(summary, 'Scope', evidence.scope_label);
            appendEvidenceDefinition(summary, 'Data as of', evidence.data_as_of_utc);
        }
        if (recordsRoot) {
            clear(recordsRoot);
            records.forEach(function (record, index) { recordsRoot.appendChild(renderEvidenceRecord(record, index)); });
        }
        if (recordsSection) { recordsSection.hidden = records.length === 0; }
        if (limitationsRoot) {
            clear(limitationsRoot);
            limitations.forEach(function (limitation) { limitationsRoot.appendChild(element('li', '', limitation)); });
        }
        if (limitationsSection) { limitationsSection.hidden = limitations.length === 0; }
        if (download) {
            var sourceDataHref = safeRelativeHref(evidence.source_data_url);
            download.hidden = !sourceDataHref;
            if (sourceDataHref) { download.href = sourceDataHref; }
            else { download.removeAttribute('href'); }
        }
        if (content) { content.hidden = false; }

        if (!records.length) {
            setEvidenceState('empty', 'No source records were returned. Review the source state and limitations below.');
        } else {
            hideEvidenceState();
        }
    }

    async function requestEvidence(evidenceKey, label, trigger) {
        var reportId = safeAnalysisId(activeEvidenceReportId);
        var key = safeEvidenceKey(evidenceKey);
        if (!reportId || !key || !openEvidenceDialog(trigger, label)) { return; }
        if (evidenceController) { evidenceController.abort(); }
        var controller = new AbortController();
        evidenceController = controller;
        var query = new URLSearchParams({ evidence_key: key, limit: text(EVIDENCE_LIMIT) });
        try {
            var response = await fetch(
                REPORT_URL + encodeURIComponent(reportId) + '/evidence?' + query.toString(),
                { headers: { 'Accept': 'application/json' }, signal: controller.signal }
            );
            var payload = await response.json().catch(function () { return {}; });
            if (!response.ok || !payload.ok || !payload.evidence) {
                throw new Error(text(payload.error, 'Source evidence is unavailable for this item.'));
            }
            if (evidenceController !== controller) { return; }
            renderEvidence(payload.evidence);
        } catch (error) {
            if (error && error.name === 'AbortError') { return; }
            if (evidenceController !== controller) { return; }
            setEvidenceState(
                'error',
                text(error && error.message, 'Source evidence could not be loaded.'),
                function () { requestEvidence(key, label, trigger); }
            );
        } finally {
            if (evidenceController === controller) { evidenceController = null; }
        }
    }

    function appendEvidenceButton(container, report, evidenceKey, label) {
        var key = safeEvidenceKey(evidenceKey);
        if (!container || report.evidence_available !== true || !key || !safeAnalysisId(activeEvidenceReportId)) { return; }
        var button = element('button', 'btn btn-sm btn-outline-primary workspace-evidence-button', 'View evidence');
        button.type = 'button';
        button.setAttribute('aria-label', 'View evidence for ' + text(label, 'this report item'));
        button.addEventListener('click', function () { requestEvidence(key, label, button); });
        container.appendChild(button);
    }

    function renderEvidenceAvailability(report) {
        var notice = document.querySelector('[data-decision-evidence-notice]');
        if (!notice) { return; }
        var available = report.evidence_available === true;
        var message = text(report.evidence_notice);
        notice.hidden = available && !message;
        notice.className = 'alert py-2 mb-4 ' + (available ? 'alert-info' : 'alert-warning');
        notice.textContent = message || 'Record-level evidence is unavailable for this report. Use the Source Data workbook for the available supporting data.';
    }

    function appendEmpty(container, message) {
        clear(container);
        container.appendChild(element('p', 'workspace-state mb-0', message));
    }

    function renderKpis(report) {
        var container = document.querySelector('[data-decision-kpis]');
        if (!container) { return; }
        clear(container);
        var metrics = Array.isArray(report.decision_metrics) ? report.decision_metrics : [];
        if (!metrics.length) {
            container.appendChild(element('p', 'workspace-state mb-0', 'No canonical KPI values were available in this artifact.'));
            return;
        }
        metrics.slice(0, 9).forEach(function (metric) {
            var card = element('div', 'workspace-kpi');
            card.appendChild(element('div', 'workspace-kpi__label', text(metric.label, metric.metric_key || 'Metric')));
            card.appendChild(element('div', 'workspace-kpi__value', displayMetric(metric)));
            if (text(metric.source_state) && text(metric.source_state) !== 'available') {
                card.appendChild(element('div', 'small text-warning mt-1', 'Source: ' + text(metric.source_state)));
            }
            appendEvidenceButton(card, report, metric.evidence_key || metric.metric_key, metric.label || metric.metric_key);
            container.appendChild(card);
        });
    }

    function numericValue(value) {
        var number = Number(value);
        return Number.isFinite(number) ? number : null;
    }

    function chartSeries(chart) {
        var values = Array.isArray(chart && chart.series)
            ? chart.series
            : (Array.isArray(chart && chart.points) ? chart.points : []);
        return values.filter(function (item) { return item && typeof item === 'object'; }).slice(0, 12);
    }

    function renderCharts(report) {
        var container = document.querySelector('[data-decision-charts]');
        if (!container) { return; }
        clear(container);
        var charts = Array.isArray(report.charts) ? report.charts : [];
        if (!charts.length) {
            container.appendChild(element('p', 'workspace-state mb-0', 'No canonical chart series were available in this artifact.'));
            return;
        }
        charts.slice(0, 6).forEach(function (chart) {
            var series = chartSeries(chart);
            var figure = element('figure', 'workspace-chart');
            var caption = element('figcaption');
            caption.appendChild(element('strong', '', text(chart.label || chart.title, 'Decision chart')));
            if (text(chart.description)) { caption.appendChild(element('p', 'small text-muted mb-0 mt-1', text(chart.description))); }
            if (text(chart.source_state) && text(chart.source_state).toLowerCase() !== 'available') {
                caption.appendChild(element('p', 'small text-warning mb-0 mt-1', 'Source: ' + text(chart.source_state)));
            }
            figure.appendChild(caption);
            if (!series.length) {
                figure.appendChild(element('p', 'small text-muted mb-0 mt-2', 'No values were available for this series.'));
                container.appendChild(figure);
                return;
            }
            var magnitudes = series.map(function (item) { return Math.abs(numericValue(item.value) || 0); });
            var maximum = Math.max.apply(null, magnitudes.concat([0]));
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
                var width = maximum > 0 && value !== null ? Math.max(1, Math.min(100, Math.abs(value) / maximum * 100)) : 0;
                bar.style.width = width + '%';
                track.appendChild(bar);
                point.appendChild(track);
                appendEvidenceButton(point, report, item.metric_key, item.label || item.name);
                list.appendChild(point);
            });
            figure.appendChild(list);
            container.appendChild(figure);
        });
    }

    function sourceStateLabel(value) {
        var state = text(value, 'unknown').toLowerCase();
        return state.charAt(0).toUpperCase() + state.slice(1);
    }

    function accountFieldDisplay(account, field, value) {
        var states = account && account.field_states && typeof account.field_states === 'object' ? account.field_states : {};
        var state = text(states[field], 'unknown').toLowerCase();
        if (state === 'available' || state === 'zero') {
            return text(value, 'Unknown');
        }
        return 'Unavailable (' + sourceStateLabel(state) + ')';
    }

    function renderAccounts(report) {
        var container = document.querySelector('[data-decision-accounts]');
        if (!container) { return; }
        clear(container);
        var accounts = Array.isArray(report.top_accounts) ? report.top_accounts : [];
        if (!accounts.length) { appendEmpty(container, 'No account exceptions were recorded in the canonical workbook.'); return; }
        var list = element('div', 'workspace-account-list');
        accounts.forEach(function (account) {
            var card = element('article', 'workspace-account');
            var heading = element('div', 'd-flex flex-wrap align-items-center justify-content-between gap-2');
            heading.appendChild(element('strong', '', text(account.customer, 'Customer unavailable')));
            var riskState = text(account.field_states && account.field_states.risk_band, 'unknown').toLowerCase();
            if ((riskState === 'available' || riskState === 'zero') && text(account.risk_band)) {
                heading.appendChild(element('span', 'badge bg-warning text-dark', text(account.risk_band) + ' risk'));
            } else {
                heading.appendChild(element('span', 'badge bg-secondary', 'Risk unavailable (' + sourceStateLabel(riskState) + ')'));
            }
            card.appendChild(heading);
            var meta = element('div', 'workspace-account__meta');
            meta.appendChild(element('span', '', 'Risk score: ' + accountFieldDisplay(account, 'risk_score_0_100', account.risk_score_0_100)));
            meta.appendChild(element('span', '', 'Open actions: ' + accountFieldDisplay(account, 'open_action_plans', account.open_action_plans)));
            meta.appendChild(element('span', '', 'Overdue: ' + accountFieldDisplay(account, 'overdue_action_plans', account.overdue_action_plans)));
            meta.appendChild(element('span', '', 'Critical/high barriers: ' + accountFieldDisplay(account, 'critical_high_barriers', account.critical_high_barriers)));
            meta.appendChild(element('span', '', 'TAC cases: ' + accountFieldDisplay(account, 'tac_cases', account.tac_cases)));
            card.appendChild(meta);
            if (text(account.customer)) {
                var deepDive = element('a', 'btn btn-sm btn-outline-secondary mt-2', 'Open Customer 360');
                deepDive.href = '/customer/' + encodeURIComponent(text(account.customer));
                deepDive.target = '_blank';
                deepDive.rel = 'noopener noreferrer';
                deepDive.setAttribute(
                    'aria-label',
                    'Open Customer 360 deep dive for ' + text(account.customer)
                );
                card.appendChild(deepDive);
            }
            appendEvidenceButton(card, report, account.evidence_key, account.customer);
            list.appendChild(card);
        });
        container.appendChild(list);
    }

    function renderActions(report) {
        var container = document.querySelector('[data-decision-actions]');
        if (!container) { return; }
        clear(container);
        var actions = Array.isArray(report.top_action_plans) ? report.top_action_plans : [];
        if (!actions.length) { appendEmpty(container, 'No action plans were available for this scope.'); return; }
        var list = element('div', 'workspace-action-list');
        actions.forEach(function (action) {
            var card = element('article', 'workspace-action' + (action.is_overdue ? ' workspace-action--overdue' : ''));
            card.appendChild(element('strong', '', text(action.title, 'Action title unavailable')));
            var meta = element('div', 'workspace-action__meta');
            meta.appendChild(element('span', '', 'ID: ' + text(action.record_id, 'Missing stable ID')));
            meta.appendChild(element('span', '', 'Customer: ' + text(action.customer, 'Unknown')));
            meta.appendChild(element('span', '', 'Owner: ' + text(action.owner, 'Unassigned')));
            meta.appendChild(element('span', '', 'Status: ' + text(action.status, 'Unknown')));
            if (text(action.priority)) { meta.appendChild(element('span', '', 'Priority: ' + text(action.priority))); }
            meta.appendChild(element('span', '', 'Due: ' + text(action.due_date, 'Not set')));
            if (action.is_overdue) { meta.appendChild(element('span', 'fw-semibold text-danger', 'Overdue')); }
            card.appendChild(meta);
            if (text(action.next_action)) { card.appendChild(element('p', 'small mb-0 mt-2', 'Next: ' + text(action.next_action))); }
            appendEvidenceButton(card, report, action.evidence_key, action.title);
            list.appendChild(card);
        });
        container.appendChild(list);
    }

    function renderLimitations(report) {
        var container = document.querySelector('[data-decision-limitations]');
        if (!container) { return; }
        clear(container);
        var items = [];
        (Array.isArray(report.source_limitations) ? report.source_limitations : []).forEach(function (entry) {
            items.push(text(entry.source, 'Source') + ': ' + text(entry.state, 'unknown'));
        });
        (Array.isArray(report.source_warnings) ? report.source_warnings : []).forEach(function (warning) {
            if (typeof warning === 'string' && text(warning)) { items.push(text(warning)); }
        });
        if (!items.length) {
            container.appendChild(element('p', 'small text-muted mb-0', 'No source limitations were recorded in this artifact.'));
            return;
        }
        var list = element('ul', 'workspace-limitation-list mb-0');
        items.forEach(function (item) { list.appendChild(element('li', '', item)); });
        container.appendChild(list);
    }

    function appendDecisionLink(container, label, href, className) {
        var safeHref = safeRelativeHref(href);
        if (!safeHref) { return; }
        var link = element('a', className || 'btn btn-outline-primary', label);
        link.href = safeHref;
        container.appendChild(link);
    }

    function renderDownloads(report) {
        var container = document.querySelector('[data-decision-downloads]');
        if (!container) { return; }
        clear(container);
        var downloads = report.downloads || {};
        appendDecisionLink(container, 'Download Word decision report', downloads.word, 'btn btn-primary');
        appendDecisionLink(container, 'Download Source Data workbook', downloads.source_data, 'btn btn-outline-success');
        appendDecisionLink(container, 'Ask about this report', report.ask_ai_url, 'btn btn-outline-primary');
        if (!container.childNodes.length) {
            container.appendChild(element('p', 'small text-muted mb-0', 'Artifacts are not available for download yet.'));
        }
    }

    function renderReport(report) {
        var panel = document.querySelector('[data-decision-report-panel]');
        var loading = document.querySelector('[data-decision-report-loading]');
        var content = document.querySelector('[data-decision-report-content]');
        var title = document.querySelector('[data-decision-report-title]');
        var meta = document.querySelector('[data-decision-report-meta]');
        var badge = document.querySelector('[data-decision-report-state]');
        if (!panel || !content) { return; }
        activeEvidenceReportId = safeAnalysisId(report.analysis_id) || safeAnalysisId(latestReportId);
        panel.hidden = false;
        if (loading) { loading.hidden = true; }
        content.hidden = false;
        if (title) { title.textContent = text(report.scope_label, 'Portfolio') + ' decision view'; }
        if (meta) {
            var parts = [text(report.report_type, 'report').replace(/_/g, ' '), text(report.manager), text(report.technology), report.days ? text(report.days) + ' days' : '', report.data_as_of_utc ? 'As of ' + text(report.data_as_of_utc) : ''];
            meta.textContent = parts.filter(Boolean).join(' · ');
        }
        if (badge) {
            badge.textContent = 'Completed';
            badge.className = 'badge rounded-pill bg-success';
        }
        renderEvidenceAvailability(report);
        renderKpis(report);
        renderCharts(report);
        renderAccounts(report);
        renderActions(report);
        renderLimitations(report);
        renderDownloads(report);
    }

    function showReportState(kind, message, analysisId) {
        var panel = document.querySelector('[data-decision-report-panel]');
        var loading = document.querySelector('[data-decision-report-loading]');
        var content = document.querySelector('[data-decision-report-content]');
        var badge = document.querySelector('[data-decision-report-state]');
        if (!panel || !loading) { return; }
        panel.hidden = false;
        content.hidden = true;
        loading.hidden = false;
        loading.className = 'alert mb-0 ' + (kind === 'error' ? 'alert-warning' : 'alert-info');
        clear(loading);
        loading.appendChild(document.createTextNode(message));
        if (kind === 'error' && analysisId) {
            var retry = element('button', 'btn btn-sm btn-outline-primary ms-2', 'Try again');
            retry.type = 'button';
            retry.addEventListener('click', function () { loadReport(analysisId, true); });
            loading.appendChild(retry);
        }
        if (badge) {
            badge.textContent = kind === 'error' ? 'Needs attention' : 'Generating';
            badge.className = 'badge rounded-pill ' + (kind === 'error' ? 'bg-warning text-dark' : 'bg-primary');
        }
    }

    async function loadReport(analysisId, userInitiated) {
        var safeId = safeAnalysisId(analysisId);
        if (!safeId) { return; }
        latestReportId = safeId;
        activeEvidenceReportId = '';
        closeEvidenceDialog();
        if (reportTimer) { window.clearTimeout(reportTimer); }
        showReportState('loading', 'Loading the server-verified decision view…', safeId);
        try {
            var response = await fetch(REPORT_URL + encodeURIComponent(safeId), { headers: { 'Accept': 'application/json' } });
            var payload = await response.json().catch(function () { return {}; });
            if (!response.ok || !payload.ok || !payload.report) {
                throw new Error(text(payload.error, 'The decision view is not available yet.'));
            }
            var report = payload.report;
            var status = text(report.status, 'unknown').toLowerCase();
            if (status === 'completed' || status === 'success' || status === 'done') {
                renderReport(report);
                return;
            }
            if (status === 'error' || status === 'failed' || status === 'cancelled') {
                showReportState('error', status === 'cancelled' ? 'This report was cancelled.' : 'This report did not complete. Open progress for details.', safeId);
                return;
            }
            showReportState('loading', 'The report is ' + (ACTIVE_STATUSES[status] ? status : 'being prepared') + '. This view will update automatically.', safeId);
            reportTimer = window.setTimeout(function () { loadReport(safeId, false); }, 5000);
        } catch (error) {
            var message = text(error && error.message, 'The decision view could not be loaded.');
            showReportState('error', message, safeId);
            if (!userInitiated) {
                reportTimer = window.setTimeout(function () { loadReport(safeId, false); }, 8000);
            }
        }
    }

    function rememberReport(analysisId) {
        var safeId = safeAnalysisId(analysisId);
        if (!safeId) { return; }
        try { window.sessionStorage.setItem(STORAGE_KEY, safeId); } catch (_) { /* optional */ }
        loadReport(safeId, false);
    }

    function wireReportCards() {
        document.querySelectorAll('.workspace-report-card').forEach(function (card) {
            card.addEventListener('click', function (event) {
                if (event.target.closest('input, label, button, a, select')) { return; }
                var radio = card.querySelector('input[type="radio"]');
                if (!radio) { return; }
                radio.checked = true;
                radio.dispatchEvent(new Event('change', { bubbles: true }));
                radio.focus();
            });
        });
    }

    function prioritizeWorkspace() {
        var configuration = document.querySelector('[data-workspace-config-card]');
        var intelligence = document.querySelector('[data-intel-banner]');
        if (configuration && intelligence && intelligence.parentNode === configuration.parentNode) {
            intelligence.parentNode.insertBefore(configuration, intelligence);
        }
    }

    function syncSubmitCopy() {
        var submit = document.getElementById('submitBtn');
        if (!submit || submit.getAttribute('aria-busy') === 'true') { return; }
        var labels = {
            leader: 'Generate Leader decision report',
            comprehensive: 'Generate Comprehensive report',
            compact: 'Generate Compact briefing',
            renewal: 'Generate Customer renewal report',
            renewal_portfolio: 'Generate Portfolio renewal report',
            subscription: 'Generate Subscription deep dive'
        };
        submit.textContent = labels[selectedReportType()] || 'Generate report';
    }

    function handleReportTypeChange() {
        paintLeaderScopeControls();
        syncSubmitCopy();
        if (selectedReportType() === 'leader') { loadLeaderOptions(); }
        schedulePreview();
    }

    function wireEvidenceDialog() {
        var dialog = evidenceDialog();
        if (!dialog) { return; }
        document.querySelectorAll('[data-evidence-close]').forEach(function (button) {
            button.addEventListener('click', closeEvidenceDialog);
        });
        dialog.addEventListener('cancel', function (event) {
            event.preventDefault();
            closeEvidenceDialog();
        });
        dialog.addEventListener('close', restoreEvidenceFocus);
        dialog.addEventListener('click', function (event) {
            if (event.target === dialog) { closeEvidenceDialog(); }
        });
        dialog.addEventListener('keydown', function (event) {
            if (event.key === 'Escape' && typeof dialog.showModal !== 'function') {
                event.preventDefault();
                closeEvidenceDialog();
                return;
            }
            if (event.key !== 'Tab' || typeof dialog.showModal === 'function') { return; }
            var focusable = Array.prototype.slice.call(dialog.querySelectorAll('a[href], button:not([disabled]), [tabindex]:not([tabindex="-1"])'));
            if (!focusable.length) { event.preventDefault(); return; }
            var first = focusable[0];
            var last = focusable[focusable.length - 1];
            if (event.shiftKey && document.activeElement === first) {
                event.preventDefault();
                last.focus();
            } else if (!event.shiftKey && document.activeElement === last) {
                event.preventDefault();
                first.focus();
            }
        });
    }

    function initialize() {
        if (!document.querySelector('[data-workspace-preview]') && !document.querySelector('[data-decision-report-panel]')) { return; }
        prioritizeWorkspace();
        wireReportCards();
        wireEvidenceDialog();
        document.querySelectorAll('input[name="report_type"]').forEach(function (radio) {
            radio.addEventListener('change', handleReportTypeChange);
        });
        document.querySelectorAll('input[name="workspace_scope_type"]').forEach(function (radio) {
            radio.addEventListener('change', function () {
                paintLeaderScopeControls();
                loadLeaderOptions();
            });
        });
        var manager = document.querySelector('select[name="manager"]');
        if (manager) {
            manager.addEventListener('change', function () {
                if (selectedReportType() === 'leader') { loadLeaderOptions(); }
                schedulePreview();
            });
        }
        var member = document.getElementById('workspaceMember');
        if (member) {
            member.addEventListener('change', function () {
                syncCanonicalScope();
                if (selectedLeaderScope() === 'customer') { loadLeaderOptions(); }
                schedulePreview();
            });
        }
        var customer = document.getElementById('workspaceCustomer');
        if (customer) { customer.addEventListener('change', function () { syncCanonicalScope(); schedulePreview(); }); }
        ['select[name="technology"]', '[name="days"]', '#subscription-search', '#subscription_id', '#customer_name'].forEach(function (selector) {
            var field = document.querySelector(selector);
            if (!field) { return; }
            field.addEventListener(field.tagName === 'SELECT' ? 'change' : 'input', schedulePreview);
            field.addEventListener('change', schedulePreview);
        });
        document.addEventListener('adoptiq:report-started', function (event) {
            rememberReport(event && event.detail && event.detail.analysisId);
        });
        document.addEventListener('adoptiq:workspace-reset', function () {
            paintLeaderScopeControls();
            syncSubmitCopy();
            schedulePreview();
        });
        paintLeaderScopeControls();
        syncSubmitCopy();
        schedulePreview();

        var queryId = new URLSearchParams(window.location.search).get('analysis_id');
        var storedId = '';
        try { storedId = window.sessionStorage.getItem(STORAGE_KEY) || ''; } catch (_) { /* optional */ }
        if (queryId || storedId) { loadReport(queryId || storedId, false); }
    }

    document.addEventListener('DOMContentLoaded', initialize);
    window.AdoptIQDecisionWorkspace = {
        refreshPreview: requestPreview,
        loadReport: loadReport,
        renderReport: renderReport,
        currentSelection: currentSelection
    };
}());
