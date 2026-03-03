/**
 * Subscription Search Functionality for AdoptIQ
 * Handles subscription search and selection
 */

function setupSubscriptionSearch() {
    const searchBtn = document.getElementById('search-subscriptions-btn');
    const searchInput = document.getElementById('subscription-search');
    const useBtn = document.getElementById('use-subscription-btn');
    const resultsDiv = document.getElementById('subscription-results');
    const selectedDiv = document.getElementById('selected-subscription');
    const subscriptionList = document.getElementById('subscription-list');
    const selectedDetails = document.getElementById('selected-subscription-details');
    
    if (!searchBtn || !searchInput || !useBtn || !resultsDiv || !selectedDiv || !subscriptionList || !selectedDetails) {
        console.warn('Subscription search elements not found');
        return;
    }
    
    let selectedSubscription = null;
    
    // Search button click handler
    searchBtn.addEventListener('click', async () => {
        const customerName = searchInput.value.trim();
        
        if (!customerName) {
            showNotification('Please enter a customer name to search', 'warning');
            return;
        }
        
        if (customerName.length < 2) {
            showNotification('Customer name must be at least 2 characters', 'warning');
            return;
        }
        
        try {
            searchBtn.disabled = true;
            searchBtn.innerHTML = '<i class="fas fa-spinner fa-spin me-1"></i>Searching...';
            
            const response = await fetch('/search_subscriptions', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    customer_name: customerName,
                    limit: 10
                })
            });
            
            const data = await response.json();
            
            if (data.success) {
                displaySearchResults(data.subscriptions);
            } else {
                showNotification(data.error || 'Search failed', 'error');
            }
            
        } catch (error) {
            console.error('Search error:', error);
            showNotification('Search failed. Please try again.', 'error');
        } finally {
            searchBtn.disabled = false;
            searchBtn.innerHTML = '<i class="fas fa-search me-1"></i>Search';
        }
    });
    
    // Enter key handler for search input
    searchInput.addEventListener('keypress', (e) => {
        if (e.key === 'Enter') {
            searchBtn.click();
        }
    });
    
    // Use subscription button handler
    useBtn.addEventListener('click', () => {
        if (selectedSubscription) {
            // Fill in the customer name field
            const customerNameInput = document.getElementById('customer_name');
            if (customerNameInput) {
                customerNameInput.value = selectedSubscription.BU_NAME;
            }
            
            // Store subscription ID in hidden field
            const subscriptionIdField = document.getElementById('subscription_id');
            if (subscriptionIdField) {
                subscriptionIdField.value = selectedSubscription.SUBSCRIPTION_ID;
            }
            
            // Show success message
            showNotification(`Using subscription ${selectedSubscription.SUBSCRIPTION_ID} for ${selectedSubscription.BU_NAME}`, 'success');
            
            // Hide search results
            resultsDiv.style.display = 'none';
            selectedDiv.style.display = 'none';
            searchInput.value = '';
            selectedSubscription = null;
            useBtn.disabled = true;
            
            // Trigger multiple events to ensure fields get disabled
            // Trigger input event on customer name
            if (customerNameInput) {
                customerNameInput.dispatchEvent(new Event('input', { bubbles: true }));
            }
            
            // Trigger change event on subscription_id field with a slight delay
            if (subscriptionIdField) {
                setTimeout(() => {
                    subscriptionIdField.dispatchEvent(new Event('change', { bubbles: true }));
                    subscriptionIdField.dispatchEvent(new Event('input', { bubbles: true }));
                }, 10);
            }
            
            // Also manually trigger the field state check if available
            setTimeout(() => {
                if (window.AnalysisFormManager && typeof window.AnalysisFormManager.checkSubscriptionFields === 'function') {
                    window.AnalysisFormManager.checkSubscriptionFields();
                }
            }, 50);
        }
    });
    
    // Display search results
    const displaySearchResults = (subscriptions) => {
        if (subscriptions.length === 0) {
            subscriptionList.innerHTML = '<div class="text-muted text-center py-3">No subscriptions found</div>';
        } else {
            subscriptionList.innerHTML = subscriptions.map(sub => `
                <div class="list-group-item list-group-item-action subscription-item" 
                     data-subscription='${JSON.stringify(sub)}'>
                    <div class="d-flex w-100 justify-content-between">
                        <h6 class="mb-1">${escapeHtml(sub.BU_NAME)}</h6>
                        <small>${escapeHtml(sub.SUBSCRIPTION_ID)}</small>
                    </div>
                    <p class="mb-1">
                        <span class="badge bg-primary me-2">${escapeHtml(sub.TECHNOLOGY_C || 'Unknown')}</span>
                        <span class="badge bg-secondary me-2">${escapeHtml(sub.SUB_TECHNOLOGY_C || 'Unknown')}</span>
                        <span class="badge bg-${sub.STATUS_C === 'Active' ? 'success' : 'warning'}">${escapeHtml(sub.STATUS_C || 'Unknown')}</span>
                    </p>
                    <small>CSSM: ${escapeHtml(sub.CSSM_NAME || 'Unknown')}</small>
                </div>
            `).join('');
            
            // Add click handlers to subscription items
            subscriptionList.querySelectorAll('.subscription-item').forEach(item => {
                item.addEventListener('click', () => {
                    // Remove previous selection
                    subscriptionList.querySelectorAll('.subscription-item').forEach(i => {
                        i.classList.remove('active');
                    });
                    
                    // Add selection to clicked item
                    item.classList.add('active');
                    
                    // Store selected subscription
                    selectedSubscription = JSON.parse(item.dataset.subscription);
                    
                    // Show selected subscription details
                    selectedDetails.innerHTML = `
                        <div class="row">
                            <div class="col-md-6">
                                <strong>Customer:</strong> ${escapeHtml(selectedSubscription.BU_NAME)}<br>
                                <strong>Subscription:</strong> ${escapeHtml(selectedSubscription.SUBSCRIPTION_ID)}<br>
                                <strong>Technology:</strong> ${escapeHtml(selectedSubscription.TECHNOLOGY_C || 'Unknown')}
                            </div>
                            <div class="col-md-6">
                                <strong>Sub-Technology:</strong> ${escapeHtml(selectedSubscription.SUB_TECHNOLOGY_C || 'Unknown')}<br>
                                <strong>Status:</strong> <span class="badge bg-${selectedSubscription.STATUS_C === 'Active' ? 'success' : 'warning'}">${escapeHtml(selectedSubscription.STATUS_C || 'Unknown')}</span><br>
                                <strong>CSSM:</strong> ${escapeHtml(selectedSubscription.CSSM_NAME || 'Unknown')}
                            </div>
                        </div>
                    `;
                    
                    selectedDiv.style.display = 'block';
                    useBtn.disabled = false;
                });
            });
        }
        
        resultsDiv.style.display = 'block';
    };
    
    // XSS protection helper
    const escapeHtml = (text) => {
        if (!text) return '';
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    };
}

// Initialize subscription search when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
    setupSubscriptionSearch();
});
