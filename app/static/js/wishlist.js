// Wishlist functionality (global)
function initializeWishlist() {
    const body = document.body;
    if (!body) return;

    const loginUrl = body.getAttribute('data-login-url') || '/login';
    const isAuthenticated = body.getAttribute('data-authenticated') === 'true';

    const initialIds = (body.dataset.wishlistIds || '')
        .split(',')
        .map(value => parseInt(value, 10))
        .filter(Number.isFinite);

    const wishlistState = new Set(initialIds);
    const processedButtons = new WeakSet();
    const processedCards = new WeakSet();
    const pendingProducts = new Set();

    const localShowToast = (type, message) => {
        if (!message) {
            return;
        }
        let toastContainer = document.getElementById('toast-container');
        if (!toastContainer) {
            toastContainer = document.createElement('div');
            toastContainer.id = 'toast-container';
            toastContainer.className = 'fixed bottom-4 right-4 z-50 space-y-2';
            document.body.appendChild(toastContainer);
        }

        const toast = document.createElement('div');
        let bgClass = 'bg-slate-700';
        if (type === 'success') {
            bgClass = 'bg-green-500';
        } else if (type === 'error') {
            bgClass = 'bg-red-500';
        } else if (type === 'info') {
            bgClass = 'bg-blue-500';
        }
        toast.className = `${bgClass} text-white px-4 py-2 rounded-md shadow-lg flex items-center gap-3 animate-in-custom`;
        toast.innerHTML = `
            <span>${message}</span>
            <button type="button" class="ml-2 text-white/80 hover:text-white focus:outline-none">
                <i class="fas fa-times"></i>
            </button>
        `;
        toast.querySelector('button').addEventListener('click', () => toast.remove());
        toastContainer.appendChild(toast);
        setTimeout(() => toast.remove(), 4000);
    };

    const emitToast = (type, message) => {
        if (type === 'info') {
            localShowToast(type, message);
            return;
        }
        if (typeof window.showToast === 'function') {
            window.showToast(type, message);
        } else {
            localShowToast(type, message);
        }
    };

    const updateBodyDataset = () => {
        body.dataset.wishlistIds = Array.from(wishlistState).join(',');
    };

    const updateWishlistBadge = (explicitCount) => {
        const badge = document.querySelector('[data-wishlist-count-badge]');
        const label = document.querySelector('[data-wishlist-label]');
        const container = document.querySelector('[data-wishlist-count]');
        const count = typeof explicitCount === 'number' ? explicitCount : wishlistState.size;

        if (badge) {
            badge.textContent = count;
            badge.classList.toggle('hidden', count === 0);
        }
        if (container) {
            container.setAttribute('aria-label', `Wishlist (${count})`);
            container.dataset.wishlistCountValue = String(count);
        }
        if (label) {
            label.textContent = 'My Wishlist';
        }
    };

    const triggerCardPulse = (card, added) => {
        if (!card) return;
        card.classList.remove('wishlist-pulse-add', 'wishlist-pulse-remove');
        void card.offsetWidth;
        card.classList.toggle('wishlist-active', added);
        card.dataset.wishlistActive = added ? 'true' : 'false';
        card.setAttribute('aria-pressed', added ? 'true' : 'false');
        card.classList.add(added ? 'wishlist-pulse-add' : 'wishlist-pulse-remove');
        setTimeout(() => {
            card.classList.remove('wishlist-pulse-add', 'wishlist-pulse-remove');
        }, 400);
    };

    const setButtonState = (button, inWishlist, options = {}) => {
        if (!button) return;
        const { animateHeart = true } = options;
        const heartIcon = button.querySelector('.heart-icon');
        const outlineIcon = button.querySelector('.heart-outline-icon');
        button.dataset.wishlistActive = inWishlist ? 'true' : 'false';
        button.setAttribute('aria-pressed', inWishlist ? 'true' : 'false');
        button.setAttribute('aria-label', inWishlist ? 'Remove from wishlist' : 'Add to wishlist');

        if (heartIcon) {
            heartIcon.classList.toggle('hidden', !inWishlist);
            heartIcon.classList.toggle('text-red-500', inWishlist);
            if (inWishlist && animateHeart) {
                heartIcon.classList.remove('wishlist-heart-animate');
                void heartIcon.offsetWidth;
                heartIcon.classList.add('wishlist-heart-animate');
                setTimeout(() => heartIcon.classList.remove('wishlist-heart-animate'), 360);
            } else if (!inWishlist) {
                heartIcon.classList.remove('wishlist-heart-animate');
            }
        }
        if (outlineIcon) {
            outlineIcon.classList.toggle('hidden', inWishlist);
        }
    };

    const applyWishlistState = (productId, inWishlist, options = {}) => {
        const { animateHeart = true, animateCard = true } = options;
        document.querySelectorAll(`.wishlist-button[data-product-id="${productId}"]`).forEach(button => {
            setButtonState(button, inWishlist, { animateHeart });
        });
        document.querySelectorAll(`[data-wishlist-card][data-product-id="${productId}"]`).forEach(card => {
            card.classList.toggle('wishlist-active', inWishlist);
            card.dataset.wishlistActive = inWishlist ? 'true' : 'false';
            card.setAttribute('aria-pressed', inWishlist ? 'true' : 'false');
            if (animateCard) {
                triggerCardPulse(card, inWishlist);
            } else {
                card.classList.remove('wishlist-pulse-add', 'wishlist-pulse-remove');
            }
        });
    };

    const shouldIgnoreClick = (event) => {
        const target = event.target;
        if (!target) return false;
        if (target.closest('.wishlist-button')) {
            return false;
        }
        if (target.closest('[data-wishlist-ignore]')) {
            return true;
        }
        const interactiveSelector = 'a[href], button, input, select, textarea, label';
        return Boolean(target.closest(interactiveSelector));
    };

    const removeWishlistCardIfNeeded = (productId) => {
        document.querySelectorAll(`[data-wishlist-card][data-product-id="${productId}"]`).forEach(card => {
            if (card.dataset.wishlistRemoveOnToggle !== 'true') {
                return;
            }
            const existingTransition = card.style.transition || '';
            const fadeTransition = 'opacity 0.18s ease, transform 0.18s ease';
            card.style.transition = existingTransition ? `${existingTransition}, ${fadeTransition}` : fadeTransition;
            card.style.opacity = '0';
            card.style.pointerEvents = 'none';
            card.style.transform = 'scale(0.97)';
            setTimeout(() => {
                const grid = card.parentElement;
                card.remove();
                if (grid && grid.querySelectorAll('[data-wishlist-card]').length === 0) {
                    const emptyState = document.querySelector('[data-wishlist-empty]');
                    if (emptyState) {
                        emptyState.classList.remove('hidden');
                    }
                }
            }, 180);
        });
    };

    const toggleWishlist = async (button, productId) => {
        if (!Number.isFinite(productId) || pendingProducts.has(productId)) {
            return;
        }

        const wasInWishlist = wishlistState.has(productId);
        const willBeInWishlist = !wasInWishlist;

        pendingProducts.add(productId);
        if (button) {
            button.dataset.wishlistPending = 'true';
        }

        applyWishlistState(productId, willBeInWishlist, { animateHeart: true, animateCard: true });
        if (willBeInWishlist) {
            wishlistState.add(productId);
        } else {
            wishlistState.delete(productId);
        }
        updateBodyDataset();
        updateWishlistBadge();
        emitToast(willBeInWishlist ? 'success' : 'info', willBeInWishlist ? 'Added to wishlist' : 'Removed from wishlist');

        try {
            const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content;
            if (!csrfToken) {
                throw new Error('Security token missing. Please refresh the page.');
            }

            const response = await fetch(`/api/wishlist/toggle/${productId}`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': csrfToken,
                    'X-Requested-With': 'XMLHttpRequest'
                },
                credentials: 'same-origin',
                body: JSON.stringify({})
            });

            const data = await response.json().catch(() => ({}));
            if (!response.ok || data.status === 'error') {
                throw new Error(data.message || 'Failed to update wishlist');
            }

            const serverInWishlist = data.in_wishlist ?? data.status === 'added';
            if (serverInWishlist !== willBeInWishlist) {
                if (serverInWishlist) {
                    wishlistState.add(productId);
                } else {
                    wishlistState.delete(productId);
                }
                applyWishlistState(productId, serverInWishlist, { animateHeart: false, animateCard: false });
                updateBodyDataset();
            }

            const serverCount = typeof data.wishlist_count === 'number' ? data.wishlist_count : undefined;
            updateWishlistBadge(serverCount);

            if (!serverInWishlist) {
                removeWishlistCardIfNeeded(productId);
            }

            window.dispatchEvent(new CustomEvent('wishlist:changed', {
                detail: {
                    productId,
                    inWishlist: serverInWishlist,
                    count: typeof data.wishlist_count === 'number' ? data.wishlist_count : wishlistState.size,
                    context: data.context || (isAuthenticated ? 'authenticated' : 'guest')
                }
            }));
        } catch (error) {
            console.warn('Wishlist sync failed:', error);
            if (willBeInWishlist) {
                wishlistState.delete(productId);
            } else {
                wishlistState.add(productId);
            }
            applyWishlistState(productId, !willBeInWishlist, { animateHeart: false, animateCard: false });
            updateBodyDataset();
            updateWishlistBadge();
            emitToast('error', error.message || 'Failed to update wishlist');

            if (!isAuthenticated && error?.requiresLogin && loginUrl) {
                window.location.href = loginUrl;
            }
        } finally {
            pendingProducts.delete(productId);
            if (button) {
                delete button.dataset.wishlistPending;
            }
        }
    };

    const checkWishlistStatus = async () => {
        const buttons = Array.from(document.querySelectorAll('.wishlist-button'));
        if (buttons.length === 0) {
            return;
        }

        const productIds = [...new Set(buttons
            .map(btn => parseInt(btn.dataset.productId, 10))
            .filter(Number.isFinite))];

        if (productIds.length === 0) {
            return;
        }

        try {
            const params = new URLSearchParams({ ids: productIds.join(',') });
            const response = await fetch(`/api/wishlist/check-multiple?${params.toString()}`, {
                credentials: 'same-origin',
                headers: {
                    'Accept': 'application/json',
                    'X-Requested-With': 'XMLHttpRequest'
                }
            });
            if (!response.ok) {
                throw new Error('Unable to fetch wishlist state');
            }
            const data = await response.json();
            wishlistState.clear();
            productIds.forEach(productId => {
                const inWishlist = Boolean(data[String(productId)]);
                if (inWishlist) {
                    wishlistState.add(productId);
                }
                applyWishlistState(productId, inWishlist, { animateHeart: false, animateCard: false });
            });
            updateBodyDataset();
            updateWishlistBadge();
        } catch (error) {
            console.error('Error checking wishlist status:', error);
        }
    };

    const bindButton = (button) => {
        if (!button || processedButtons.has(button)) {
            return;
        }
        processedButtons.add(button);
        if (!button.hasAttribute('type')) {
            button.setAttribute('type', 'button');
        }
        button.addEventListener('click', (event) => {
            event.preventDefault();
            event.stopPropagation();
            const productId = parseInt(button.dataset.productId, 10);
            if (!Number.isFinite(productId)) {
                return;
            }
            toggleWishlist(button, productId);
        }, { passive: false });
    };

    const bindCard = (card) => {
        if (!card || processedCards.has(card)) {
            return;
        }
        processedCards.add(card);
        if (!card.getAttribute('tabindex')) {
            card.setAttribute('tabindex', '0');
        }
        if (!card.getAttribute('role')) {
            card.setAttribute('role', 'button');
        }
        card.setAttribute('aria-pressed', card.dataset.wishlistActive === 'true' ? 'true' : 'false');

        card.addEventListener('click', (event) => {
            if (shouldIgnoreClick(event)) {
                return;
            }
            event.preventDefault();
            event.stopPropagation();
            const productId = parseInt(card.dataset.productId, 10);
            if (!Number.isFinite(productId)) {
                return;
            }
            const button = card.querySelector('.wishlist-button');
            toggleWishlist(button, productId);
        }, { passive: false });

        card.addEventListener('keydown', (event) => {
            if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault();
                const productId = parseInt(card.dataset.productId, 10);
                if (!Number.isFinite(productId)) {
                    return;
                }
                const button = card.querySelector('.wishlist-button');
                toggleWishlist(button, productId);
            }
        });
    };

    const bindWishlistElements = (scope = document) => {
        scope.querySelectorAll('.wishlist-button').forEach(bindButton);
        scope.querySelectorAll('[data-wishlist-card]').forEach(bindCard);
    };

    const start = () => {
        wishlistState.forEach(productId => {
            applyWishlistState(productId, true, { animateHeart: false, animateCard: false });
        });
        updateWishlistBadge();
        bindWishlistElements();
        checkWishlistStatus();
    };

    window.BuxinWishlist = {
        toggle(productId) {
            const pid = Number(productId);
            if (!Number.isFinite(pid)) return;
            const button = document.querySelector(`.wishlist-button[data-product-id="${pid}"]`);
            toggleWishlist(button, pid);
        },
        refresh(scope) {
            bindWishlistElements(scope || document);
            checkWishlistStatus();
        },
        state: wishlistState
    };

    start();
}

initializeWishlist();
