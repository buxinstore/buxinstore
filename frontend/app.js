(() => {
  const config = window.BUXIN_CONFIG || {};
  const apiBase = (config.apiBaseUrl || '').replace(/\/$/, '');
  const app = document.querySelector('#app');
  const cartCount = document.querySelector('#cart-count');
  const toast = document.querySelector('#toast');
  const searchInput = document.querySelector('#search-input');
  let catalog = { products: [], categories: [], hero: {} };
  let authState = { authenticated: false, user: null };
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
    document.querySelector('#cart-count-badge').classList.toggle('hidden', !items.length);
  }

  function notify(message) {
    toast.textContent = message;
    toast.classList.remove('hidden');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => toast.classList.add('hidden'), 2400);
  }

  function categoryTiles(categories) {
    if (!categories.length) return '<p class="text-sm text-[color:var(--app-muted)]">Categories will appear here when the catalog is available.</p>';
    return `<div class="flex gap-3 overflow-x-auto pb-4 -mx-4 px-4 scrollbar-hide">${categories.map(category => `
      <a href="#/products?category=${encodeURIComponent(category.id)}" class="flex-shrink-0 bg-[color:var(--app-card)] hover:bg-[color:var(--app-surface-hover)] border border-[color:var(--app-border)] hover:border-cyan-500 rounded-xl p-4 transition-all hover:scale-105 min-w-[140px] text-[color:var(--app-text)]">
        <div class="w-16 h-16 rounded-lg overflow-hidden mb-3 mx-auto border border-[color:var(--app-border)]">
          ${category.image ? `<img src="${escapeHtml(imageUrl(category.image))}" alt="${escapeHtml(category.name)}" class="w-full h-full object-cover" loading="lazy">` : `<div class="w-full h-full bg-gradient-to-br from-cyan-500 to-blue-600 flex items-center justify-center"><i data-lucide="cpu" class="w-6 h-6 text-white"></i></div>`}
        </div>
        <h4 class="font-semibold text-sm mb-1 text-center">${escapeHtml(category.name)}</h4>
        <p class="text-xs text-[color:var(--app-muted)] text-center">${Number(category.count || 0)} ${Number(category.count) === 1 ? 'item' : 'items'}</p>
      </a>`).join('')}</div>`;
  }

  function productCards(products) {
    if (!products.length) return '<div class="p-8 text-center text-[color:var(--app-muted)] border border-[color:var(--app-border)] rounded-xl">No products found. Try another search or category.</div>';
    return `<div class="grid grid-cols-2 xl:grid-cols-7 2xl:grid-cols-10 gap-3">${products.map(product => `
      <div class="product-card cursor-pointer bg-[color:var(--app-card)] border border-[color:var(--app-border)] rounded-xl overflow-hidden hover:border-cyan-500/50 transition-all hover:scale-[1.02] hover:shadow-lg hover:shadow-cyan-500/10 text-[color:var(--app-text)]" data-product-id="${product.id}">
        <div class="relative">
          <a href="#/product/${product.id}" class="block h-40 overflow-hidden">
            ${product.image ? `<img src="${escapeHtml(imageUrl(product.image))}" alt="${escapeHtml(product.name)}" class="w-full h-full object-cover hover:scale-105 transition-transform duration-200" loading="lazy" onerror="this.src='https://placehold.co/400x400/34d399/ffffff?text=No+Image'; this.onerror=null;">` : '<div class="w-full h-full bg-gradient-to-br from-slate-100 to-slate-200 flex items-center justify-center"><i data-lucide="image" class="w-10 h-10 text-[color:var(--app-muted)]"></i></div>'}
          </a>
          ${product.available_in_gambia ? '<div class="absolute top-2 left-2 w-8 h-8 bg-[color:var(--app-card)]/90 backdrop-blur-sm rounded-full shadow-lg flex items-center justify-center z-10" title="Available in Gambia"><span class="text-xl">🇬🇲</span></div>' : ''}
          ${product.stock === 0 ? '<div class="absolute top-2 left-2 bg-red-500 text-white text-xs px-2 py-1 rounded-lg font-medium z-10">Out of Stock</div>' : ''}
        </div>
        <div class="p-3"><p class="text-xs text-[color:var(--app-muted)] mb-1">${escapeHtml(product.category || 'Electronics')}</p>
          <a href="#/product/${product.id}" class="block font-semibold text-sm mb-2 line-clamp-2">${escapeHtml(product.name)}</a>
          <div class="mt-1 mb-2"><span class="text-sm font-semibold text-green-600">${escapeHtml(product.display_price || 'Price on request')}</span></div>
          <div class="flex items-center justify-end"><button type="button" data-add="${product.id}" class="bg-gradient-to-r from-cyan-500 to-blue-500 text-white px-3 py-1.5 rounded-lg text-xs font-medium hover:shadow-lg hover:shadow-cyan-500/30 transition-all ${product.stock < 1 ? 'opacity-50 cursor-not-allowed' : ''}" ${product.stock < 1 ? 'disabled' : ''}>${product.stock > 0 ? 'Add to Cart' : 'Out of Stock'}</button></div>
        </div>
      </div>`).join('')}</div>`;
  }

  function viewHome() {
    const hero = catalog.hero || {};
    const featured = catalog.products.slice(0, 8);
    app.innerHTML = `
      <section class="mb-8 rounded-2xl p-6 backdrop-blur-sm border border-[color:var(--app-border)] bg-[color:var(--app-card)] transition-colors duration-300">
        <div class="flex items-center justify-between"><div>
          <h2 class="text-2xl font-bold mb-2 text-[color:var(--app-text)]">${escapeHtml(hero.title || 'Build the Future')}</h2>
          <p class="text-[color:var(--app-muted)]">${escapeHtml(hero.subtitle || 'Premium electronics and robotics components')}</p>
          <div class="mt-4"><a href="${escapeHtml(hero.button_link || '#featured')}" class="inline-flex items-center px-4 py-2 bg-gradient-to-r from-cyan-500 to-blue-500 text-white rounded-lg font-medium hover:shadow-lg hover:shadow-cyan-500/30 transition-all">${escapeHtml(hero.button_text || 'Shop Now')} <i data-lucide="arrow-right" class="ml-2 w-4 h-4"></i></a></div>
        </div>${hero.image ? `<div class="w-24 h-24 rounded-2xl overflow-hidden border border-cyan-200 shadow-lg"><img src="${escapeHtml(imageUrl(hero.image))}" alt="Hero image" class="w-full h-full object-cover"></div>` : '<div class="w-24 h-24 bg-gradient-to-br from-cyan-500 to-blue-500 rounded-2xl flex items-center justify-center animate-pulse"><i data-lucide="cpu" class="w-10 h-10 text-white"></i></div>'}</div>
      </section>
      <div class="mb-8" id="categories"><div class="flex items-center justify-between mb-4"><h3 class="text-lg font-bold">Shop by Category</h3><a href="#/categories" class="text-cyan-400 text-sm font-medium flex items-center gap-1 hover:gap-2 transition-all">View All <i data-lucide="chevron-right" class="w-4 h-4"></i></a></div>${categoryTiles(catalog.categories)}</div>
      <div class="mb-8" id="featured"><div class="flex items-center justify-between mb-4"><h3 class="text-lg font-bold">Featured Products</h3><a href="#/products" class="text-cyan-400 text-sm font-medium flex items-center gap-1 hover:gap-2 transition-all">See All <i data-lucide="chevron-right" class="w-4 h-4"></i></a></div>${productCards(featured)}</div>
      <div class="grid grid-cols-2 gap-4 mb-8">
        <div class="bg-[color:var(--app-card)] border border-[color:var(--app-border)] rounded-xl p-4 text-center text-[color:var(--app-text)]"><div class="w-10 h-10 bg-cyan-100 rounded-full flex items-center justify-center mx-auto mb-2"><i data-lucide="truck" class="w-5 h-5 text-cyan-500"></i></div><h4 class="font-medium text-sm mb-1">Free Delivery in The Gambia</h4><p class="text-xs text-[color:var(--app-muted)]">On orders over D10,000</p></div>
        <div class="bg-[color:var(--app-card)] border border-[color:var(--app-border)] rounded-xl p-4 text-center text-[color:var(--app-text)]"><div class="w-10 h-10 bg-green-100 rounded-full flex items-center justify-center mx-auto mb-2"><i data-lucide="shield-check" class="w-5 h-5 text-green-500"></i></div><h4 class="font-medium text-sm mb-1">Secure Payment</h4><p class="text-xs text-[color:var(--app-muted)]">100% secure payment</p></div>
        <div class="bg-[color:var(--app-card)] border border-[color:var(--app-border)] rounded-xl p-4 text-center text-[color:var(--app-text)]"><div class="w-10 h-10 bg-amber-100 rounded-full flex items-center justify-center mx-auto mb-2"><i data-lucide="refresh-cw" class="w-5 h-5 text-amber-500"></i></div><h4 class="font-medium text-sm mb-1">Easy Returns</h4><p class="text-xs text-[color:var(--app-muted)]">30-day return policy</p></div>
        <div class="bg-[color:var(--app-card)] border border-[color:var(--app-border)] rounded-xl p-4 text-center text-[color:var(--app-text)]"><div class="w-10 h-10 bg-blue-100 rounded-full flex items-center justify-center mx-auto mb-2"><i data-lucide="headphones" class="w-5 h-5 text-blue-500"></i></div><h4 class="font-medium text-sm mb-1">24/7 Support</h4><p class="text-xs text-[color:var(--app-muted)]">Dedicated support</p></div>
      </div>`;
    refreshIcons();
  }

  function renderAuth(register = false) {
    const params = new URLSearchParams(location.hash.split('?')[1] || '');
    const registered = params.get('registered') === '1';
    const loginUrl = '#/login';
    const registerUrl = '#/register';
    const nextUrl = `${window.location.origin}${window.location.pathname}#/`;
    const googleUrl = `${apiBase}/auth/google/login?next=${encodeURIComponent(nextUrl)}`;
    app.innerHTML = `<div class="max-w-7xl mx-auto py-12 px-4 sm:px-6 lg:px-8">
      <div class="sm:mx-auto sm:w-full sm:max-w-md text-center">
        <h1 class="text-3xl font-extrabold text-slate-900 dark:text-white">${register ? 'Create a new account' : 'Sign in to your account'}</h1>
        <p class="mt-2 text-sm text-slate-500 dark:text-slate-400">${register ? `Already have an account? <a href="${loginUrl}" class="font-semibold text-cyan-500">Sign in</a>` : `Or <a href="${registerUrl}" class="font-semibold text-cyan-500">create a new account</a>`}</p>
      </div>
      <div class="mt-8 sm:mx-auto sm:w-full sm:max-w-md"><div class="bg-[color:var(--app-card)] dark:bg-slate-900/70 border border-[color:var(--app-border)] dark:border-slate-800 py-8 px-6 sm:px-8 shadow-sm rounded-3xl">
        <div id="auth-message" class="${registered ? 'mb-4 rounded-xl border border-emerald-200/60 bg-emerald-500/10 text-emerald-600 p-4 text-sm' : 'hidden'}">${registered ? 'Account created. Sign in with your new credentials.' : ''}</div>
        <form id="auth-form" class="space-y-6" data-auth-mode="${register ? 'register' : 'login'}">
          <div><label for="auth-username" class="block text-sm font-medium text-slate-700 dark:text-slate-300">${register ? 'Username' : 'Username or email'}</label><div class="mt-1"><input id="auth-username" name="username" type="text" required autocomplete="username" class="appearance-none block w-full rounded-xl border border-slate-200 dark:border-slate-700 px-3 py-2 text-sm text-slate-900 dark:text-white bg-white dark:bg-slate-900/50 focus:outline-none focus:ring-2 focus:ring-cyan-400 focus:border-cyan-400"></div></div>
          ${register ? `<div><label for="auth-email" class="block text-sm font-medium text-slate-700 dark:text-slate-300">Email address</label><div class="mt-1"><input id="auth-email" name="email" type="email" autocomplete="email" required class="appearance-none block w-full rounded-xl border border-slate-200 dark:border-slate-700 px-3 py-2 text-sm text-slate-900 dark:text-white bg-white dark:bg-slate-900/50 focus:outline-none focus:ring-2 focus:ring-cyan-400 focus:border-cyan-400"></div></div>` : ''}
          <div><label for="auth-password" class="block text-sm font-medium text-slate-700 dark:text-slate-300">Password</label><div class="mt-1"><input id="auth-password" name="password" type="password" required autocomplete="${register ? 'new-password' : 'current-password'}" ${register ? 'minlength="8"' : ''} class="appearance-none block w-full rounded-xl border border-slate-200 dark:border-slate-700 px-3 py-2 text-sm text-slate-900 dark:text-white bg-white dark:bg-slate-900/50 focus:outline-none focus:ring-2 focus:ring-cyan-400 focus:border-cyan-400"></div></div>
          ${register ? `<div><label for="auth-confirm-password" class="block text-sm font-medium text-slate-700 dark:text-slate-300">Confirm Password</label><div class="mt-1"><input id="auth-confirm-password" name="confirm_password" type="password" autocomplete="new-password" minlength="8" required class="appearance-none block w-full rounded-xl border border-slate-200 dark:border-slate-700 px-3 py-2 text-sm text-slate-900 dark:text-white bg-white dark:bg-slate-900/50 focus:outline-none focus:ring-2 focus:ring-cyan-400 focus:border-cyan-400"></div></div><div class="flex items-center"><input id="auth-terms" name="terms" type="checkbox" required class="h-4 w-4 text-cyan-500 focus:ring-cyan-400 border-slate-300 rounded"><label for="auth-terms" class="ml-2 block text-sm text-slate-700 dark:text-slate-300">I agree to the Terms of Service and Privacy Policy.</label></div>` : `<div class="flex items-center justify-between"><label class="flex items-center gap-2 text-sm text-slate-700 dark:text-slate-300"><input id="auth-remember" name="remember" type="checkbox" class="h-4 w-4 text-cyan-500 focus:ring-cyan-400 border-slate-300 rounded">Remember me</label><a href="#/forgot-password" class="text-sm font-semibold text-cyan-500">Forgot your password?</a></div>`}
          <div><button id="auth-submit" type="submit" class="w-full flex justify-center py-2.5 px-4 rounded-xl text-sm font-semibold text-white bg-gradient-to-r from-cyan-500 to-blue-500 shadow-lg shadow-cyan-500/20 hover:shadow-xl focus:outline-none focus:ring-2 focus:ring-cyan-400 transition-all">${register ? 'Create Account' : 'Sign in'}</button></div>
        </form>
        <div class="mt-6"><div class="relative"><div class="absolute inset-0 flex items-center"><div class="w-full border-t border-slate-200 dark:border-slate-700"></div></div><div class="relative flex justify-center text-sm"><span class="px-2 bg-[color:var(--app-card)] dark:bg-slate-900/70 text-[color:var(--app-muted)]">Or continue with</span></div></div>
          <a href="${googleUrl}" class="mt-6 w-full inline-flex items-center justify-center gap-3 py-2.5 px-4 border border-[color:var(--app-border)] rounded-xl shadow-sm bg-[color:var(--app-card)] text-sm font-medium text-[color:var(--app-muted)] hover:border-cyan-400 hover:text-cyan-500 transition-colors"><i class="fab fa-google text-red-500"></i>${register ? 'Sign up' : 'Sign in'} with Google</a>
        </div>
      </div></div></div>`;
    refreshIcons();
  }

  function renderPasswordReset(token = '') {
    const confirm = Boolean(token);
    app.innerHTML = `<div class="max-w-7xl mx-auto py-12 px-4 sm:px-6 lg:px-8"><div class="sm:mx-auto sm:w-full sm:max-w-md text-center"><h1 class="text-3xl font-extrabold text-slate-900 dark:text-white">${confirm ? 'Choose a new password' : 'Reset your password'}</h1><p class="mt-2 text-sm text-slate-500 dark:text-slate-400">${confirm ? 'Your reset link expires after 30 minutes.' : 'Enter your email address and we’ll send a reset link if the account exists.'}</p></div>
      <div class="mt-8 sm:mx-auto sm:w-full sm:max-w-md"><div class="bg-[color:var(--app-card)] dark:bg-slate-900/70 border border-[color:var(--app-border)] dark:border-slate-800 py-8 px-6 sm:px-8 shadow-sm rounded-3xl"><div id="auth-message" class="hidden"></div>
        <form id="auth-form" data-auth-mode="${confirm ? 'password-confirm' : 'password-reset'}" data-token="${escapeHtml(token)}" class="space-y-6">
          ${confirm ? `<div><label for="auth-password" class="block text-sm font-medium text-slate-700 dark:text-slate-300">New password</label><input id="auth-password" name="password" type="password" required minlength="6" autocomplete="new-password" class="mt-1 appearance-none block w-full rounded-xl border border-slate-200 dark:border-slate-700 px-3 py-2 text-sm text-slate-900 dark:text-white bg-white dark:bg-slate-900/50 focus:outline-none focus:ring-2 focus:ring-cyan-400"></div><div><label for="auth-confirm-password" class="block text-sm font-medium text-slate-700 dark:text-slate-300">Confirm password</label><input id="auth-confirm-password" name="confirm_password" type="password" required minlength="6" autocomplete="new-password" class="mt-1 appearance-none block w-full rounded-xl border border-slate-200 dark:border-slate-700 px-3 py-2 text-sm text-slate-900 dark:text-white bg-white dark:bg-slate-900/50 focus:outline-none focus:ring-2 focus:ring-cyan-400"></div>` : `<div><label for="auth-email" class="block text-sm font-medium text-slate-700 dark:text-slate-300">Email address</label><input id="auth-email" name="email" type="email" required autocomplete="email" class="mt-1 appearance-none block w-full rounded-xl border border-slate-200 dark:border-slate-700 px-3 py-2 text-sm text-slate-900 dark:text-white bg-white dark:bg-slate-900/50 focus:outline-none focus:ring-2 focus:ring-cyan-400"></div>`}
          <button id="auth-submit" type="submit" class="w-full flex justify-center py-2.5 px-4 rounded-xl text-sm font-semibold text-white bg-gradient-to-r from-cyan-500 to-blue-500">${confirm ? 'Update password' : 'Send reset link'}</button></form>
        <p class="mt-6 text-center"><a href="#/login" class="text-sm font-semibold text-cyan-500">← Back to sign in</a></p></div></div></div>`;
  }

  function updateAccountHeader() {
    const link = document.querySelector('#account-link');
    const label = document.querySelector('#account-label');
    if (!link || !label) return;
    if (authState.authenticated) {
      link.href = '#/account';
      label.textContent = authState.user?.display_name || authState.user?.username || 'Account';
    } else {
      link.href = '#/login';
      label.textContent = 'Sign in';
    }
  }

  async function loadAuthState() {
    try {
      const result = await api('/api/auth/session');
      authState = { authenticated: Boolean(result.authenticated), user: result.user || null };
      updateAccountHeader();
    } catch {
      authState = { authenticated: false, user: null };
    }
  }

  async function viewAccount() {
    if (!authState.authenticated) { location.hash = '#/login'; return; }
    const result = await api('/api/profile');
    const profile = result.profile || {};
    app.innerHTML = `<div class="max-w-3xl mx-auto"><header class="mb-6"><h1 class="text-2xl font-bold">Your account</h1><p class="text-sm text-[color:var(--app-muted)] mt-1">Manage your profile and view your orders.</p></header>
      <div class="flex gap-3 mb-5"><a href="#/orders" class="px-4 py-2 rounded-xl bg-[color:var(--app-card)] border border-[color:var(--app-border)] text-sm font-semibold">Your orders</a><button type="button" data-logout class="px-4 py-2 rounded-xl border border-[color:var(--app-border)] text-sm font-semibold text-red-500">Sign out</button></div>
      <form id="profile-form" class="space-y-4 bg-[color:var(--app-card)] border border-[color:var(--app-border)] rounded-3xl p-6"><div id="profile-message" class="hidden"></div>
        <div class="grid sm:grid-cols-2 gap-4"><label class="block text-sm">First name<input name="first_name" value="${escapeHtml(profile.first_name || '')}" class="mt-1 block w-full rounded-xl border border-[color:var(--app-border)] bg-[color:var(--app-input)] px-3 py-2"></label><label class="block text-sm">Last name<input name="last_name" value="${escapeHtml(profile.last_name || '')}" class="mt-1 block w-full rounded-xl border border-[color:var(--app-border)] bg-[color:var(--app-input)] px-3 py-2"></label></div>
        <label class="block text-sm">Username<input name="username" value="${escapeHtml(profile.username || '')}" class="mt-1 block w-full rounded-xl border border-[color:var(--app-border)] bg-[color:var(--app-input)] px-3 py-2"></label>
        <label class="block text-sm">Email<input name="email" type="email" value="${escapeHtml(profile.email || '')}" class="mt-1 block w-full rounded-xl border border-[color:var(--app-border)] bg-[color:var(--app-input)] px-3 py-2"></label>
        <label class="block text-sm">Phone number<input name="phone_number" value="${escapeHtml(profile.phone_number || '')}" class="mt-1 block w-full rounded-xl border border-[color:var(--app-border)] bg-[color:var(--app-input)] px-3 py-2"></label>
        <label class="block text-sm">Address<input name="address" value="${escapeHtml(profile.address || '')}" class="mt-1 block w-full rounded-xl border border-[color:var(--app-border)] bg-[color:var(--app-input)] px-3 py-2"></label>
        <div class="grid sm:grid-cols-2 gap-4"><label class="block text-sm">City<input name="city" value="${escapeHtml(profile.city || '')}" class="mt-1 block w-full rounded-xl border border-[color:var(--app-border)] bg-[color:var(--app-input)] px-3 py-2"></label><label class="block text-sm">Country<input name="country" value="${escapeHtml(profile.country || '')}" class="mt-1 block w-full rounded-xl border border-[color:var(--app-border)] bg-[color:var(--app-input)] px-3 py-2"></label></div>
        <button class="px-4 py-2 rounded-xl bg-gradient-to-r from-cyan-500 to-blue-500 text-white font-semibold" type="submit">Save profile</button></form></div>`;
  }

  async function viewOrders() {
    if (!authState.authenticated) { location.hash = '#/login'; return; }
    const result = await api('/api/storefront/orders');
    app.innerHTML = `<div class="max-w-4xl mx-auto"><header class="mb-6"><h1 class="text-2xl font-bold">Your orders</h1><p class="text-sm text-[color:var(--app-muted)] mt-1">View your order history and purchase details.</p></header>
      ${result.orders.length ? `<div class="space-y-3">${result.orders.map(order => `<article class="p-5 bg-[color:var(--app-card)] border border-[color:var(--app-border)] rounded-2xl"><div class="flex flex-wrap items-center justify-between gap-2"><strong class="text-cyan-500">Order #${Number(order.id)}</strong><span class="px-2 py-1 rounded-full text-xs bg-amber-500/15 text-amber-700">${escapeHtml(order.status || 'Pending')}</span><time class="text-sm text-[color:var(--app-muted)]">${order.created_at ? new Date(order.created_at).toLocaleDateString() : ''}</time><strong>${escapeHtml(catalog.currency_symbol || 'D')}${Number(order.total || 0).toFixed(2)}</strong></div><ul class="mt-4 divide-y divide-[color:var(--app-border)]">${order.items.map(item => `<li class="py-2 flex items-center gap-3">${item.image ? `<img class="w-10 h-10 rounded-lg object-cover" src="${escapeHtml(item.image)}" alt="">` : ''}<span class="text-sm flex-1">${escapeHtml(item.name)}</span><span class="text-sm text-[color:var(--app-muted)]">× ${Number(item.quantity)}</span></li>`).join('')}</ul></article>`).join('')}</div>` : '<div class="p-8 text-center bg-[color:var(--app-card)] border border-[color:var(--app-border)] rounded-2xl">You have no orders yet. <a class="text-cyan-500 font-semibold" href="#/products">Continue shopping</a></div>'}
      <p class="mt-5"><a href="#/account" class="text-cyan-500 text-sm font-semibold">← Back to your account</a></p></div>`;
  }

  function refreshIcons() {
    if (window.lucide?.createIcons) window.lucide.createIcons();
  }

  document.querySelector('#copyright-year').textContent = new Date().getFullYear();

  async function viewProducts(params) {
    const query = params.get('q') || '';
    const categoryId = params.get('category') || '';
    const apiParams = new URLSearchParams();
    if (query) apiParams.set('q', query);
    if (categoryId) apiParams.set('category', categoryId);
    if (params.get('page')) apiParams.set('page', params.get('page'));
    app.innerHTML = '<section class="py-16 text-center text-[color:var(--app-muted)]">Loading products…</section>';
    const result = await api(`/api/storefront/catalog?${apiParams.toString()}`);
    catalog.categories = result.categories;
    catalog.pagination = result.pagination;
    catalog.products = result.products;
    const filtered = result.products;
    app.innerHTML = `<header class="mb-6"><h1 class="text-xl font-bold">Shop all products</h1><p class="text-sm text-[color:var(--app-muted)] mt-1">Browse the buxin store catalog.</p></header>
      <div class="flex flex-col sm:flex-row gap-3 mb-5"><label class="sr-only" for="catalog-search">Search products</label><input id="catalog-search" class="flex-1 bg-[color:var(--app-input)] border border-[color:var(--app-border)] rounded-xl px-4 py-3 text-sm" type="search" value="${escapeHtml(query)}" placeholder="Search products">
        <label class="sr-only" for="category-select">Filter by category</label><select id="category-select" class="bg-[color:var(--app-input)] border border-[color:var(--app-border)] rounded-xl px-4 py-3 text-sm"><option value="">All categories</option>${catalog.categories.map(category => `<option value="${category.id}" ${String(category.id) === categoryId ? 'selected' : ''}>${escapeHtml(category.name)}</option>`).join('')}</select></div>
      <div id="catalog-results">${productCards(filtered)}</div>${result.pagination?.has_more ? '<div class="flex justify-center mt-6"><button class="px-4 py-2 bg-gradient-to-r from-cyan-500 to-blue-500 text-white rounded-lg" id="load-more">Load more products</button></div>' : ''}`;
    refreshIcons();
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
        const grid = document.querySelector('#catalog-results > .grid');
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
    app.innerHTML = `<div class="grid md:grid-cols-2 gap-8 items-start"><div class="aspect-square rounded-xl overflow-hidden bg-[color:var(--app-input)]">${product.image ? `<img class="w-full h-full object-cover" src="${escapeHtml(imageUrl(product.image))}" alt="${escapeHtml(product.name)}">` : '<div class="w-full h-full flex items-center justify-center text-[color:var(--app-muted)]"><i data-lucide="image" class="w-12 h-12"></i></div>'}</div>
      <section class="pt-2"><span class="text-xs text-[color:var(--app-muted)]">${escapeHtml(product.category || 'Components')}</span><h1 class="text-2xl font-bold mt-2">${escapeHtml(product.name)}</h1>
      <span class="inline-block mt-3 text-xs px-2 py-1 rounded-lg ${product.stock > 0 ? 'bg-green-100 text-green-700' : 'bg-red-100 text-red-700'}">${product.stock > 0 ? `${product.stock} in stock` : 'Out of stock'}</span><p class="text-xl font-bold text-green-600 mt-4">${escapeHtml(product.display_price || 'Price on request')}</p>
      <p class="text-sm leading-7 text-[color:var(--app-muted)] mt-4 whitespace-pre-line">${escapeHtml(product.description || 'Contact us for more information about this product.')}</p>
      <div class="mt-5 flex flex-wrap items-center gap-3"><div class="inline-flex items-center border border-[color:var(--app-border)] rounded-lg overflow-hidden"><button class="w-10 h-10 hover:bg-[color:var(--app-surface-hover)]" type="button" data-qty="-1" aria-label="Decrease quantity">−</button><input class="w-12 text-center bg-transparent" id="detail-quantity" type="number" min="1" max="${Math.max(product.stock, 1)}" value="1" aria-label="Quantity"><button class="w-10 h-10 hover:bg-[color:var(--app-surface-hover)]" type="button" data-qty="1" aria-label="Increase quantity">+</button></div>
      <button class="bg-gradient-to-r from-cyan-500 to-blue-500 text-white px-4 py-2 rounded-lg font-medium ${product.stock < 1 ? 'opacity-50 cursor-not-allowed' : ''}" data-add="${product.id}" ${product.stock < 1 ? 'disabled' : ''}>Add to cart</button></div>
      <p class="mt-5"><a class="text-cyan-500 text-sm font-medium" href="#/products">← Back to products</a></p></section></div>`;
    refreshIcons();
    app.querySelectorAll('[data-qty]').forEach(button => button.addEventListener('click', () => {
      const input = document.querySelector('#detail-quantity');
      input.value = Math.min(Number(input.max), Math.max(1, Number(input.value) + Number(button.dataset.qty)));
    }));
  }

  function viewCart() {
    const items = readCart();
    if (!items.length) {
      app.innerHTML = `<header class="mb-6"><h1 class="text-xl font-bold">Your cart</h1></header><div class="p-8 text-center border border-[color:var(--app-border)] rounded-xl">Your cart is empty. <a class="text-cyan-500 font-medium" href="#/products">Find something to build with →</a></div>`;
      return;
    }
    const total = items.reduce((sum, item) => sum + Number(item.checkout_price ?? item.price ?? 0) * Number(item.quantity || 0), 0);
    const currency = catalog.currency_symbol || 'D';
    app.innerHTML = `<header class="mb-6"><h1 class="text-xl font-bold">Your cart</h1><p class="text-sm text-[color:var(--app-muted)] mt-1">${items.length} ${items.length === 1 ? 'product' : 'products'}</p></header>
      <section class="border-t border-[color:var(--app-border)]">${items.map(item => `<div class="grid grid-cols-[58px_minmax(0,1fr)_auto] md:grid-cols-[74px_minmax(0,1fr)_auto_auto] gap-3 md:gap-4 items-center py-3 border-b border-[color:var(--app-border)]"><img class="w-14 h-14 md:w-[74px] md:h-[74px] object-cover rounded-lg bg-[color:var(--app-input)]" src="${escapeHtml(imageUrl(item.image))}" alt=""><div><span class="font-semibold">${escapeHtml(item.name)}</span><small class="block text-xs text-[color:var(--app-muted)] mt-1">Quantity ${Number(item.quantity)}</small></div><span class="font-semibold">${escapeHtml(item.display_price || '')}</span><button class="text-red-500 text-sm" data-remove="${item.id}">Remove</button></div>`).join('')}</section>
      <section class="mt-6 ml-auto max-w-sm p-5 bg-[color:var(--app-card)] border border-[color:var(--app-border)] rounded-xl"><div class="flex justify-between mb-4 font-semibold"><span>Estimated subtotal</span><span>${escapeHtml(currency)} ${total.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</span></div><button class="w-full bg-gradient-to-r from-cyan-500 to-blue-500 text-white px-4 py-3 rounded-lg font-medium" id="checkout-button">Continue to secure checkout →</button></section>`;
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
    if (route === 'login') renderAuth(false);
    else if (route === 'register') renderAuth(true);
    else if (route === 'forgot-password') renderPasswordReset();
    else if (route.startsWith('reset-password/')) renderPasswordReset(route.slice('reset-password/'.length));
    else if (route === 'account') viewAccount().catch(showError);
    else if (route === 'orders') viewOrders().catch(showError);
    else if (route === 'products') viewProducts(params).catch(showError);
    else if (route === 'categories') {
      app.innerHTML = `<header class="mb-6"><h1 class="text-xl font-bold">Shop by category</h1><p class="text-sm text-[color:var(--app-muted)] mt-1">Browse all available product categories.</p></header>${categoryTiles(catalog.categories)}`;
    } else if (route.startsWith('product/')) viewProduct(route.slice('product/'.length)).catch(showError);
    else if (route === 'cart') viewCart();
    else viewHome();
    searchInput.value = params.get('q') || '';
    refreshIcons();
  }

  function showError(error) {
    app.innerHTML = `<div class="p-8 text-center border border-[color:var(--app-border)] rounded-xl"><strong class="block font-semibold mb-2">We couldn’t load the store just now.</strong><span class="text-sm text-[color:var(--app-muted)]">${escapeHtml(error.message || 'Please try again in a moment.')}</span><p><button class="mt-4 px-4 py-2 bg-gradient-to-r from-cyan-500 to-blue-500 text-white rounded-lg" id="retry-button">Try again</button></p></div>`;
    document.querySelector('#retry-button').addEventListener('click', start);
  }

  async function start() {
    app.innerHTML = '<section class="py-16 text-center text-[color:var(--app-muted)]">Loading the store…</section>';
    await loadAuthState();
    try {
      const result = await api('/api/storefront/catalog');
      catalog = result;
      renderRoute();
      saveCart(readCart());
    } catch (error) {
      const route = location.hash.replace(/^#\/?/, '').split('?')[0];
      if (['login', 'register', 'forgot-password', 'account', 'orders', 'cart'].includes(route) || route.startsWith('reset-password/')) renderRoute();
      else showError(error);
    }
  }

  document.querySelector('#search-form').addEventListener('submit', event => {
    event.preventDefault();
    const query = searchInput.value.trim();
    location.hash = `#/products${query ? `?q=${encodeURIComponent(query)}` : ''}`;
  });

  app.addEventListener('click', event => {
    const logoutButton = event.target.closest('[data-logout]');
    if (logoutButton) {
      api('/api/auth/logout', { method: 'POST', body: JSON.stringify({}) })
        .then(() => { authState = { authenticated: false, user: null }; updateAccountHeader(); location.hash = '#/'; })
        .catch(error => notify(error.message));
      return;
    }
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

  app.addEventListener('submit', async event => {
    const profileForm = event.target.closest('#profile-form');
    if (profileForm) {
      event.preventDefault();
      const payload = Object.fromEntries(new FormData(profileForm));
      const notice = document.querySelector('#profile-message');
      try {
        await api('/api/profile', { method: 'PATCH', body: JSON.stringify(payload) });
        notice.textContent = 'Your profile was saved.';
        notice.className = 'mb-4 rounded-xl border border-emerald-200/60 bg-emerald-500/10 text-emerald-600 p-4 text-sm';
      } catch (error) {
        notice.textContent = error.message;
        notice.className = 'mb-4 rounded-xl border border-rose-200/60 bg-rose-500/10 text-rose-600 p-4 text-sm';
      }
      return;
    }
    const form = event.target.closest('#auth-form');
    if (!form) return;
    event.preventDefault();
    const mode = form.dataset.authMode || (form.dataset.register === 'true' ? 'register' : 'login');
    const register = mode === 'register';
    let endpoint = register ? '/api/auth/register' : '/api/auth/login';
    let payload = { username: form.elements.username?.value.trim(), password: form.elements.password?.value };
    if (mode === 'password-reset') {
      endpoint = '/api/auth/password-reset';
      payload = { email: form.elements.email.value.trim() };
    } else if (mode === 'password-confirm') {
      endpoint = '/api/auth/password-reset/confirm';
      payload = { token: form.dataset.token, password: form.elements.password.value, confirm_password: form.elements.confirm_password.value };
    } else if (register) {
      payload.email = form.elements.email.value.trim();
      payload.confirm_password = form.elements.confirm_password.value;
      payload.terms = form.elements.terms.checked;
    } else {
      payload.remember = form.elements.remember.checked;
    }
    const button = document.querySelector('#auth-submit');
    const notice = document.querySelector('#auth-message');
    button.disabled = true;
      button.textContent = mode === 'password-reset' ? 'Sending…' : mode === 'password-confirm' ? 'Updating…' : register ? 'Creating account…' : 'Signing in…';
    try {
      const result = await api(endpoint, { method: 'POST', body: JSON.stringify(payload) });
      if (mode === 'password-reset') {
        notice.textContent = result.message;
        notice.className = 'mb-4 rounded-xl border border-emerald-200/60 bg-emerald-500/10 text-emerald-600 p-4 text-sm';
        button.disabled = false;
        button.textContent = 'Send reset link';
      } else if (mode === 'password-confirm') {
        location.hash = '#/login?password_reset=1';
      } else if (register) {
        location.hash = '#/login?registered=1';
      } else {
        authState = { authenticated: true, user: result.user };
        updateAccountHeader();
        location.hash = '#/';
      }
    } catch (error) {
      notice.textContent = error.message || 'Could not submit the form. Please try again.';
      notice.className = 'mb-4 rounded-xl border border-rose-200/60 bg-rose-500/10 text-rose-600 p-4 text-sm';
      button.disabled = false;
      button.textContent = mode === 'password-reset' ? 'Send reset link' : mode === 'password-confirm' ? 'Update password' : register ? 'Create Account' : 'Sign in';
    }
  });

  window.addEventListener('hashchange', renderRoute);
  document.querySelector('#theme-toggle').addEventListener('click', () => {
    const dark = !document.documentElement.classList.contains('dark');
    document.documentElement.classList.toggle('dark', dark);
    localStorage.setItem('theme', dark ? 'dark' : 'light');
    document.querySelector('#theme-color-meta').content = dark ? '#0f172a' : '#ffffff';
    updateThemeIcon();
  });
  function updateThemeIcon() {
    const dark = document.documentElement.classList.contains('dark');
    document.querySelector('[data-theme-icon="light"]')?.classList.toggle('hidden', dark);
    document.querySelector('[data-theme-icon="dark"]')?.classList.toggle('hidden', !dark);
  }
  saveCart(readCart());
  refreshIcons();
  updateThemeIcon();
  start();
})();
