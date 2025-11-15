(() => {
    function refreshCartCount() {
        fetch('/api/cart/count', {
            credentials: 'same-origin',
            headers: {
                'Accept': 'application/json'
            }
        })
            .then(response => response.json())
            .then(data => {
                if (data.status === 'success') {
                    const summary = data.cart || {};
                    const count = data.count ?? data.cart_count ?? summary.count ?? summary.cart_count ?? 0;

                    if (typeof window.updateCartBadgeCount === 'function') {
                        window.updateCartBadgeCount(count);
                    } else {
                        const cartCountElements = document.querySelectorAll('.cart-count');
                        cartCountElements.forEach(el => {
                            el.textContent = count || '0';
                        });
                    }
                }
            })
            .catch(error => console.error('Error updating cart count:', error));
    }

    function finalizeButtonState(form, { disabled, text, loadingHidden }) {
        const submitBtn = form.querySelector('button[type="submit"]');
        const addToCartText = form.querySelector('.add-to-cart-text');
        const addToCartLoader = form.querySelector('.add-to-cart-loader');

        if (submitBtn) submitBtn.disabled = disabled;
        if (addToCartText && typeof text === 'string') addToCartText.textContent = text;
        if (addToCartLoader) {
            if (loadingHidden) {
                addToCartLoader.classList.add('hidden');
            } else {
                addToCartLoader.classList.remove('hidden');
            }
        }
    }

    function handleAddToCartSubmit(event) {
        event.preventDefault();
        event.stopPropagation();

        const form = event.currentTarget;
        if (form.dataset.loading === 'true') {
            return;
        }

        form.dataset.loading = 'true';

        const addToCartText = form.querySelector('.add-to-cart-text');
        if (addToCartText && !form.dataset.originalText) {
            form.dataset.originalText = addToCartText.textContent;
        }

        finalizeButtonState(form, { disabled: true, text: 'Adding...', loadingHidden: false });

        const formData = new FormData(form);
        const url = form.getAttribute('action');

        fetch(url, {
            method: 'POST',
            body: formData,
            headers: {
                'X-Requested-With': 'XMLHttpRequest',
                'X-CSRFToken': formData.get('csrf_token')
            }
        })
            .then(response => {
                if (!response.ok) {
                    return response.json().then(err => {
                        throw err;
                    });
                }
                return response.json();
            })
            .then(data => {
                if (data.status !== 'success') {
                    throw data;
                }

                const summary = data.cart || {};
                const count = data.count ?? data.cart_count ?? summary.count ?? summary.cart_count ?? 0;

                if (typeof window.updateCartBadgeCount === 'function') {
                    window.updateCartBadgeCount(count);
                }

                window.dispatchEvent(new CustomEvent('cart:updated', { detail: summary }));
                showToast('success', data.message || 'Item added to cart');

                finalizeButtonState(form, {
                    disabled: false,
                    text: 'Added to Cart',
                    loadingHidden: true
                });

                setTimeout(() => {
                    const defaultText = form.dataset.originalText || 'Add to Cart';
                    finalizeButtonState(form, {
                        disabled: false,
                        text: defaultText,
                        loadingHidden: true
                    });
                }, 2000);
            })
            .catch(error => {
                console.error('Error:', error);
                showToast('error', error.message || 'Failed to add item to cart');

                const defaultText = form.dataset.originalText || 'Add to Cart';
                finalizeButtonState(form, {
                    disabled: false,
                    text: defaultText,
                    loadingHidden: true
                });

                if (error.redirect) {
                    window.location.href = error.redirect;
                }
            })
            .finally(() => {
                delete form.dataset.loading;
            });
    }

    function bindAddToCartForms(scope = document) {
        scope.querySelectorAll('.add-to-cart-form').forEach(form => {
            if (form.dataset.cartBound === 'true') {
                return;
            }

            form.dataset.cartBound = 'true';
            form.addEventListener('submit', handleAddToCartSubmit, { passive: false });
        });
    }

    document.addEventListener('DOMContentLoaded', () => {
        refreshCartCount();
        bindAddToCartForms();
    });

    window.refreshCartCount = refreshCartCount;
    window.bindAddToCartForms = bindAddToCartForms;
})();

// Helper function to show toast messages
function showToast(type, message) {
    // Create toast element if it doesn't exist
    let toastContainer = document.getElementById('toast-container');
    if (!toastContainer) {
        toastContainer = document.createElement('div');
        toastContainer.id = 'toast-container';
        toastContainer.className = 'fixed bottom-4 right-4 z-50 space-y-2';
        document.body.appendChild(toastContainer);
    }
    
    const toast = document.createElement('div');
    const bgColor = type === 'success' ? 'bg-green-500' : 'bg-red-500';
    
    toast.className = `${bgColor} text-white px-4 py-2 rounded-md shadow-lg flex items-center`;
    toast.innerHTML = `
        <span>${message}</span>
        <button class="ml-4 text-white hover:text-gray-200" onclick="this.parentElement.remove()">
            <i class="fas fa-times"></i>
        </button>
    `;
    
    toastContainer.appendChild(toast);
    
    // Auto-remove toast after 5 seconds
    setTimeout(() => {
        toast.remove();
    }, 5000);
}
