/**
 * Round 134 — WxCC health check export (deterministic plain-text download).
 */
(function () {
    'use strict';

    function csrfToken() {
        var meta = document.querySelector('meta[name="csrf-token"]');
        return meta ? meta.getAttribute('content') : '';
    }

    function notify(message, isError) {
        if (typeof window.showNotification === 'function') {
            window.showNotification(message, isError ? 'danger' : 'success');
            return;
        }
        if (isError) {
            console.error(message);
        } else {
            console.log(message);
        }
    }

    function parseFilename(disposition) {
        if (!disposition) {
            return 'wxcc_health_input.txt';
        }
        var match = /filename="([^"]+)"/i.exec(disposition);
        return match ? match[1] : 'wxcc_health_input.txt';
    }

    function setBusy(button, busy) {
        if (!button) {
            return;
        }
        button.disabled = !!busy;
        if (busy) {
            button.setAttribute('data-wxcc-busy', '1');
        } else {
            button.removeAttribute('data-wxcc-busy');
        }
    }

    function exportWxccHealthInput(options) {
        var customerName = (options.customerName || '').trim();
        var subscriptionId = (options.subscriptionId || '').trim();
        var technology = (options.technology || 'Webex Contact Center').trim();
        var days = parseInt(options.days, 10);
        if (!customerName && !subscriptionId) {
            notify('Enter a customer name or subscription id first.', true);
            return Promise.resolve(false);
        }
        if (!days || days < 1 || days > 365) {
            days = 90;
        }

        var button = options.button || null;
        setBusy(button, true);

        return fetch('/api/export/wxcc-health-input', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': csrfToken(),
                'X-Requested-With': 'XMLHttpRequest',
            },
            body: JSON.stringify({
                customer_name: customerName,
                subscription_id: subscriptionId,
                technology: technology,
                days: days,
            }),
        })
            .then(function (response) {
                if (!response.ok) {
                    return response.json().catch(function () {
                        return { error: 'Export failed (' + response.status + ')' };
                    }).then(function (payload) {
                        throw new Error(payload.error || 'Export failed');
                    });
                }
                var disposition = response.headers.get('Content-Disposition') || '';
                return response.blob().then(function (blob) {
                    return { blob: blob, filename: parseFilename(disposition) };
                });
            })
            .then(function (result) {
                var url = URL.createObjectURL(result.blob);
                var link = document.createElement('a');
                link.href = url;
                link.download = result.filename;
                document.body.appendChild(link);
                link.click();
                link.remove();
                URL.revokeObjectURL(url);
                notify('WxCC health check file downloaded.', false);
                return true;
            })
            .catch(function (err) {
                notify(err && err.message ? err.message : 'Export failed', true);
                return false;
            })
            .finally(function () {
                setBusy(button, false);
            });
    }

    function bindAnalyzePage() {
        var btn = document.querySelector('[data-wxcc-export-btn]');
        if (!btn) {
            return;
        }
        var customerEl = document.getElementById('customer_name');
        var subEl = document.getElementById('subscription_id');
        var daysEl = document.querySelector('input[name="days"]');
        var techEl = document.querySelector('select[name="technology"]');

        function refreshEnabled() {
            var hasCustomer = customerEl && customerEl.value.trim().length > 0;
            var hasSub = subEl && subEl.value.trim().length > 0;
            btn.disabled = !(hasCustomer || hasSub);
        }

        if (customerEl) {
            customerEl.addEventListener('input', refreshEnabled);
        }
        if (subEl) {
            subEl.addEventListener('change', refreshEnabled);
        }
        refreshEnabled();

        btn.addEventListener('click', function () {
            exportWxccHealthInput({
                button: btn,
                customerName: customerEl ? customerEl.value : '',
                subscriptionId: subEl ? subEl.value : '',
                technology: techEl ? techEl.value : 'Webex Contact Center',
                days: daysEl ? daysEl.value : 90,
            });
        });
    }

    function bindCustomer360Page() {
        var btn = document.querySelector('[data-wxcc-export-customer360]');
        if (!btn) {
            return;
        }
        var customerName = btn.getAttribute('data-customer-name') || '';
        btn.addEventListener('click', function () {
            exportWxccHealthInput({
                button: btn,
                customerName: customerName,
                subscriptionId: '',
                technology: btn.getAttribute('data-technology') || 'Webex Contact Center',
                days: parseInt(btn.getAttribute('data-days') || '90', 10),
            });
        });
    }

    window.AdoptIQWxccExport = {
        exportWxccHealthInput: exportWxccHealthInput,
        bindAnalyzePage: bindAnalyzePage,
        bindCustomer360Page: bindCustomer360Page,
    };

    document.addEventListener('DOMContentLoaded', function () {
        bindAnalyzePage();
        bindCustomer360Page();
    });
})();
