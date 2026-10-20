(() => {
  const config = window.BUXIN_CONFIG || {};
  const apiBase = (config.apiBaseUrl || '').replace(/\/$/, '');
  const app = document.querySelector('#app');
  const cartCount = document.querySelector('#cart-count');
  const toast = document.querySelector('#toast');
  const searchInput = document.querySelector('#search-input');
  let catalog = { products: [], categories: [], hero: {} };
  let toastTimer;

  const escapeHtml = (value = '') => String(value).replace(/[&<>"']/g, char => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  })[char]);

  const imageUrl = value => {
    if (!value) return '';
    if (/^https?:\/\//i.test(value)) return value;
    return `${apiBase}/${String(value).replace(/^\/+/, '')}`;
  };

  async function api(path, options = {}) {
    if (!apiBase) throw new Error('The store API URL is not configured.');
    const response = await fetch(`${apiBase}${path}`, {
      ...options,
      credentials: 'include',
      headers: { ...(options.body ? { 'Content-Type': 'application/json' } : {}), ...(options.headers || {}) }
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(body.message || `Store service returned ${response.status}.`);
    return body;
  }

  function readCart() {
    try {
      const parsed = JSON.parse(localStorage.getItem('buxin-static-cart') || '[]');
      return Array.isArray(parsed) ? parsed.filter(item => Number(item.id) > 0 && Number(item.quantity) > 0) : [];
    } catch { return []; }
  }

  function saveCart(items) {
    localStorage.setItem('buxin-static-cart', JSON.stringify(items));
    cartCount.textContent = items.reduce((sum, item) => sum + Number(item.quantity || 0), 0);
    cartCount.hidden = !items.length;
  }

  function notify(message) {
    toast.textContent = message;
    toast.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => toast.classList.remove('show'), 2400);
  }

  function categoryTiles(categories) {
    if (!categories.length) return '<p class="empty-state">Categories will appear here when the catalog is available.</p>';
    return `<div class="category-list">${categories.map(category => `
      <a class="category-tile" href="#/products?category=${encodeURIComponent(category.id)}">
        ${category.image ? `<img class="category-image" src="${escapeHtml(imageUrl(category.image))}" alt="" loading="lazy">` : '<span class="category-image category-fallback" aria-hidden="true">✳</span>'}
        <span><strong>${escapeHtml(category.name)}</strong><small>${Number(category.count || 0)} ${Number(category.count) === 1 ? 'item' : 'items'}</small></span>
      </a>`).join('')}</div>`;
  }

  function productCards(products) {
    if (!products.length) return '<div class="empty-state">No products found. Try another search or category.</div>';
    return `<div class="product-grid">${products.map(product => `
      <article class="product-card">
        <a class="product-photo" href="#/product/${product.id}" aria-label="View ${escapeHtml(product.name)}">
          ${product.image ? `<img src="${escapeHtml(imageUrl(product.image))}" alt="${escapeHtml(product.name)}" loading="lazy">` : '<span class="product-placeholder">Product image coming soon</span>'}
          <span class="stock-tag">${product.stock > 0 ? 'In stock' : 'Sold out'}</span>
        </a>
        <div class="product-info">
          <span class="product-category">${escapeHtml(product.category || 'Components')}</span>
          <a class="product-name" href="#/product/${product.id}">${escapeHtml(product.name)}</a>
          <div class="product-bottom"><span class="price">${escapeHtml(product.display_price || 'Price on request')}</span>
            <button class="add-button" type="button" data-add="${product.id}" ${product.stock < 1 ? 'disabled' : ''}>Add</button>
          </div>
        </div>
      </article>`).join('')}</div>`;
  }

  function viewHome() {
    const hero = catalog.hero || {};
    const featured = catalog.products.slice(0, 8);
    app.innerHTML = `
      <section class="hero"><div class="hero-copy"><span class="eyebrow">Curiosity, meet capability</span>
        <h1>${escapeHtml(hero.title || 'Build the Future')}</h1>
        <p>${escapeHtml(hero.subtitle || 'Premium electronics and robotics components for your next great idea.')}</p>
        <a class="button" href="#/products">Explore the collection <span aria-hidden="true">→</span></a>
      </div></section>
      <section class="section"><div class="section-head"><div><h2>Shop by category</h2><p>Find the right parts for your next build.</p></div><a class="text-link" href="#/categories">All categories →</a></div>${categoryTiles(catalog.categories)}</section>
      <section class="section"><div class="section-head"><div><h2>Featured products</h2><p>Useful tools and components, ready to ship.</p></div><a class="text-link" href="#/products">View all →</a></div>${productCards(featured)}</section>`;
  }

  async function viewProducts(params) {
    const query = params.get('q') || '';
    const categoryId = params.get('category') || '';
    const apiParams = new URLSearchParams();
    if (query) apiParams.set('q', query);
    if (categoryId) apiParams.set('category', categoryId);
    if (params.get('page')) apiParams.set('page', params.get('page'));
    app.innerHTML = '<section class="loading-state"><span class="spinner"></span><p>Loading products…</p></section>';
    const result = await api(`/api/storefront/catalog?${apiParams.toString()}`);
    catalog.categories = result.categories;
    catalog.pagination = result.pagination;
    catalog.products = result.products;
    const filtered = result.products;
    app.innerHTML = `<header class="page-title"><h1>Shop all products</h1><p>Browse the buxin store catalog.</p></header>
      <div class="catalog-tools"><label class="sr-only" for="catalog-search">Search products</label><input id="catalog-search" type="search" value="${escapeHtml(query)}" placeholder="Search products">
        <label class="sr-only" for="category-select">Filter by category</label><select id="category-select"><option value="">All categories</option>${catalog.categories.map(category => `<option value="${category.id}" ${String(category.id) === categoryId ? 'selected' : ''}>${escapeHtml(category.name)}</option>`).join('')}</select></div>
      <div id="catalog-results">${productCards(filtered)}</div>${result.pagination?.has_more ? '<div class="section-head" style="justify-content:center"><button class="button" id="load-more">Load more products</button></div>' : ''}`;
    const field = document.querySelector('#catalog-search');
    let searchTimer;
    field.addEventListener('input', () => {
      clearTimeout(searchTimer);
      searchTimer = setTimeout(() => {
      const next = new URLSearchParams();
      if (field.value.trim()) next.set('q', field.value.trim());
      if (categoryId) next.set('category', categoryId);
      const queryString = next.toString();
      location.hash = `#/products${queryString ? `?${queryString}` : ''}`;
      }, 250);
    });
    document.querySelector('#category-select').addEventListener('change', event => {
      const next = new URLSearchParams(location.hash.split('?')[1] || '');
      if (event.target.value) next.set('category', event.target.value); else next.delete('category');
      const suffix = next.toString();
      location.hash = `#/products${suffix ? `?${suffix}` : ''}`;
    });
    document.querySelector('#load-more')?.addEventListener('click', async event => {
      const button = event.currentTarget;
      button.disabled = true; button.textContent = 'Loading…';
      try {
        const next = new URLSearchParams(apiParams);
        next.set('page', String(Number(result.pagination.page) + 1));
        const more = await api(`/api/storefront/catalog?${next.toString()}`);
        catalog.products.push(...more.products);
        const template = document.createElement('template');
        template.innerHTML = productCards(more.products);
        const grid = document.querySelector('#catalog-results .product-grid');
        template.content.querySelectorAll('.product-card').forEach(card => grid?.append(card));
        result.pagination = more.pagination;
        if (more.pagination?.has_more) { button.disabled = false; button.textContent = 'Load more products'; }
        else button.remove();
      } catch (error) { button.disabled = false; button.textContent = 'Retry loading products'; notify(error.message); }
    });
  }

  async function viewProduct(id) {
    const product = catalog.products.find(item => String(item.id) === String(id)) || (await api(`/api/storefront/products/${encodeURIComponent(id)}`)).product;
    if (!catalog.products.some(item => String(item.id) === String(product.id))) catalog.products.push(product);
    app.innerHTML = `<div class="detail-layout"><div class="detail-image">${product.image ? `<img src="${escapeHtml(imageUrl(product.image))}" alt="${escapeHtml(product.name)}">` : '<span class="product-placeholder">Product image coming soon</span>'}</div>
      <section class="detail-copy"><span class="product-category">${escapeHtml(product.category || 'Components')}</span><h1>${escapeHtml(product.name)}</h1>
      <span class="stock-tag stock-tag-inline">${product.stock > 0 ? `${product.stock} in stock` : 'Out of stock'}</span><p class="detail-price">${escapeHtml(product.display_price || 'Price on request')}</p>
      <p class="detail-description">${escapeHtml(product.description || 'Contact us for more information about this product.')}</p>
      <div><div class="quantity-control"><button type="button" data-qty="-1" aria-label="Decrease quantity">−</button><input id="detail-quantity" type="number" min="1" max="${Math.max(product.stock, 1)}" value="1" aria-label="Quantity"><button type="button" data-qty="1" aria-label="Increase quantity">+</button></div>
      <button class="button" data-add="${product.id}" ${product.stock < 1 ? 'disabled' : ''}>Add to cart</button></div>
      <p><a class="text-link" href="#/products">← Back to products</a></p></section></div>`;
    app.querySelectorAll('[data-qty]').forEach(button => button.addEventListener('click', () => {
      const input = document.querySelector('#detail-quantity');
      input.value = Math.min(Number(input.max), Math.max(1, Number(input.value) + Number(button.dataset.qty)));
    }));
  }

  function viewCart() {
    const items = readCart();
    if (!items.length) {
      app.innerHTML = `<header class="page-title"><h1>Your cart</h1></header><div class="empty-state">Your cart is empty. <a class="text-link" href="#/products">Find something to build with →</a></div>`;
      return;
    }
    const total = items.reduce((sum, item) => sum + Number(item.checkout_price ?? item.price ?? 0) * Number(item.quantity || 0), 0);
    const currency = catalog.currency_symbol || 'D';
    app.innerHTML = `<header class="page-title"><h1>Your cart</h1><p>${items.length} ${items.length === 1 ? 'product' : 'products'}</p></header>
      <section>${items.map(item => `<div class="cart-row"><img src="${escapeHtml(imageUrl(item.image))}" alt=""><div><span class="cart-row-name">${escapeHtml(item.name)}</span><small>Quantity ${Number(item.quantity)}</small></div><span class="price">${escapeHtml(item.display_price || '')}</span><button class="remove-button" data-remove="${item.id}">Remove</button></div>`).join('')}</section>
      <section class="cart-total"><div><span>Estimated subtotal</span><span>${escapeHtml(currency)} ${total.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</span></div><button class="button" id="checkout-button">Continue to secure checkout →</button></section>`;
    app.querySelectorAll('[data-remove]').forEach(button => button.addEventListener('click', () => {
      saveCart(readCart().filter(item => String(item.id) !== button.dataset.remove));
      viewCart();
    }));
    document.querySelector('#checkout-button').addEventListener('click', syncCartAndCheckout);
  }

  async function syncCartAndCheckout() {
    const button = document.querySelector('#checkout-button');
    const items = readCart();
    button.disabled = true; button.textContent = 'Preparing secure checkout…';
    try {
      const remote = await api('/api/cart/summary');
      const merged = new Map();
      for (const item of remote.cart?.items || []) {
        merged.set(String(item.id), { id: item.id, quantity: Number(item.quantity) });
      }
      for (const item of items) {
        const key = String(item.id);
        const current = merged.get(key);
        merged.set(key, { id: item.id, quantity: Number(item.quantity) + Number(current?.quantity || 0) });
      }
      const result = await api('/api/storefront/cart/sync', {
        method: 'POST', body: JSON.stringify({ items: [...merged.values()] })
      });
      if (!result.success) throw new Error(result.message || 'Could not prepare your cart.');
      localStorage.removeItem('buxin-static-cart');
      saveCart([]);
      window.location.assign(config.checkoutUrl || `${apiBase}/cart/proceed`);
    } catch (error) {
      button.disabled = false; button.textContent = 'Continue to secure checkout →';
      notify(error.message || 'The store service is unavailable. Please try again.');
    }
  }

  function renderRoute() {
    const raw = location.hash.replace(/^#\/?/, '');
    const [path, queryString = ''] = raw.split('?');
    const params = new URLSearchParams(queryString);
    const route = path || '';
    if (route === 'products') viewProducts(params).catch(showError);
    else if (route === 'categories') {
      app.innerHTML = `<header class="page-title"><h1>Shop by category</h1><p>Browse all available product categories.</p></header>${categoryTiles(catalog.categories)}`;
    } else if (route.startsWith('product/')) viewProduct(route.slice('product/'.length)).catch(showError);
    else if (route === 'cart') viewCart();
    else viewHome();
    searchInput.value = params.get('q') || '';
  }

  function showError(error) {
    app.innerHTML = `<div class="error-state"><strong>We couldn’t load the store just now.</strong>${escapeHtml(error.message || 'Please try again in a moment.')}<p><button class="button" id="retry-button">Try again</button></p></div>`;
    document.querySelector('#retry-button').addEventListener('click', start);
  }

  async function start() {
    app.innerHTML = '<section class="loading-state"><span class="spinner"></span><p>Loading the store…</p></section>';
    try {
      const result = await api('/api/storefront/catalog');
      catalog = result;
      renderRoute();
      saveCart(readCart());
    } catch (error) { showError(error); }
  }

  document.querySelector('#search-form').addEventListener('submit', event => {
    event.preventDefault();
    const query = searchInput.value.trim();
    location.hash = `#/products${query ? `?q=${encodeURIComponent(query)}` : ''}`;
  });

  app.addEventListener('click', event => {
    const button = event.target.closest('[data-add]');
    if (!button) return;
    const product = catalog.products.find(item => String(item.id) === button.dataset.add);
    if (!product) return;
    const quantity = Number(document.querySelector('#detail-quantity')?.value || 1);
    const items = readCart();
    const existing = items.find(item => String(item.id) === String(product.id));
    if (existing) existing.quantity = Math.min(product.stock, existing.quantity + quantity);
    else items.push({ id: product.id, name: product.name, price: product.price, checkout_price: product.checkout_price, display_price: product.display_price, image: product.image, quantity });
    saveCart(items);
    notify(`${product.name} added to your cart`);
  });

  window.addEventListener('hashchange', renderRoute);
  saveCart(readCart());
  start();
})();
