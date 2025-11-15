/**
 * Payment processing for Gambian Robotics Store
 * Handles payment form submission and processing
 */

document.addEventListener('DOMContentLoaded', function() {
    const paymentForm = document.getElementById('payment-form');
    
    if (paymentForm) {
        paymentForm.addEventListener('submit', handlePaymentSubmit);
    }
});

/**
 * Handle payment form submission
 * @param {Event} e - Form submit event
 */
async function handlePaymentSubmit(e) {
    e.preventDefault();
    
    const form = e.target;
    const submitBtn = form.querySelector('button[type="submit"]');
    const orderId = form.dataset.orderId;
    const paymentMethod = form.querySelector('input[name="payment_method"]:checked');
    const paymentReference = form.querySelector('input[name="payment_reference"]');
    
    // Validate form
    if (!paymentMethod || !paymentReference || !paymentReference.value.trim()) {
        showAlert('Please fill in all required fields', 'error');
        return;
    }
    
    // Disable submit button and show loading state
    const originalBtnText = submitBtn.innerHTML;
    submitBtn.disabled = true;
    submitBtn.innerHTML = '<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span> Processing...';
    
    try {
        // Simulate payment processing with the server
        const response = await fetch('/api/payment/process', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': getCSRFToken()
            },
            body: JSON.stringify({
                order_id: parseInt(orderId),
                payment_method: paymentMethod.value,
                reference: paymentReference.value.trim(),
                amount: parseFloat(form.dataset.orderTotal)
            })
        });
        
        const data = await response.json();
        
        if (response.ok && data.success) {
            // Payment successful
            showAlert('Payment processed successfully! Redirecting to order details...', 'success');
            
            // Redirect to order confirmation after a short delay
            setTimeout(() => {
                window.location.href = `/order-confirmation/${orderId}`;
            }, 2000);
        } else {
            // Payment failed
            showAlert(data.message || 'Payment processing failed. Please try again.', 'error');
            submitBtn.disabled = false;
            submitBtn.innerHTML = originalBtnText;
        }
    } catch (error) {
        console.error('Payment error:', error);
        showAlert('An error occurred while processing your payment. Please try again.', 'error');
        submitBtn.disabled = false;
        submitBtn.innerHTML = originalBtnText;
    }
}

/**
 * Show alert message to user
 * @param {string} message - The message to display
 * @param {string} type - The type of alert (success, error, warning, info)
 */
function showAlert(message, type = 'info') {
    // Remove any existing alerts
    const existingAlert = document.querySelector('.payment-alert');
    if (existingAlert) {
        existingAlert.remove();
    }
    
    // Create alert element
    const alertDiv = document.createElement('div');
    alertDiv.className = `alert alert-${type} payment-alert mt-3`;
    alertDiv.role = 'alert';
    alertDiv.textContent = message;
    
    // Add close button
    const closeBtn = document.createElement('button');
    closeBtn.type = 'button';
    closeBtn.className = 'btn-close';
    closeBtn.setAttribute('data-bs-dismiss', 'alert');
    closeBtn.setAttribute('aria-label', 'Close');
    closeBtn.addEventListener('click', () => alertDiv.remove());
    
    alertDiv.appendChild(closeBtn);
    
    // Insert after the form
    const form = document.getElementById('payment-form');
    if (form) {
        form.parentNode.insertBefore(alertDiv, form.nextSibling);
    } else {
        document.body.prepend(alertDiv);
    }
    
    // Auto-remove after 5 seconds
    setTimeout(() => {
        if (alertDiv.parentNode) {
            alertDiv.remove();
        }
    }, 5000);
}

/**
 * Get CSRF token from meta tag
 * @returns {string} CSRF token
 */
function getCSRFToken() {
    return document.querySelector('meta[name="csrf-token"]')?.content || '';
}

/**
 * Format currency
 * @param {number} amount - The amount to format
 * @returns {string} Formatted currency string
 */
function formatCurrency(amount) {
    return new Intl.NumberFormat('en-GM', {
        style: 'currency',
        currency: 'GMD',
        minimumFractionDigits: 2
    }).format(amount);
}
