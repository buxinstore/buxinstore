(function () {
  const config = window.profilePageConfig || {};
  const csrfToken =
    document.querySelector('meta[name="csrf-token"]')?.getAttribute('content') ||
    config.csrfToken ||
    '';

  const jsonHeaders = { Accept: 'application/json' };
  if (csrfToken) {
    jsonHeaders['X-CSRFToken'] = csrfToken;
  }

  const profileHeaders = {
    ...jsonHeaders,
    'Content-Type': 'application/json',
  };

  config.googleConnected = Boolean(config.googleConnected);
  config.hasPassword = Boolean(config.hasPassword);
  config.paymentMethods = Array.isArray(config.paymentMethods) ? config.paymentMethods : [];
  config.notificationSettings = config.notificationSettings || {};

  const infoFieldElements = Array.from(document.querySelectorAll('[data-field]'));
  infoFieldElements.forEach((el) => {
    if (!el.dataset.empty) {
      el.dataset.empty = el.textContent.trim();
    }
  });

  const openOverlays = new Set();
  const collapsibleSections = [];
  let toastTimeout;
 
  const avatarTouchArea = document.getElementById('avatar-touch-area');
  const avatarFileInput = document.getElementById('avatar-input');
  const profileAvatarImg = document.getElementById('profile-avatar');
  const avatarUploadTrigger = document.getElementById('avatar-upload-trigger');
  const avatarUploadRow = document.getElementById('avatar-upload-row');
  const avatarRemoveTrigger = document.getElementById('avatar-remove-trigger');

  const editProfileForm = document.getElementById('edit-profile-form');
  const openEditProfileBtn = document.getElementById('open-edit-profile');
  const inlineEditButton = document.getElementById('edit-profile-inline');

  const openPasswordButton = document.getElementById('open-password-modal');
  const passwordForm = document.getElementById('password-form');

  const paymentModalButton = document.getElementById('open-payment-modal');
  const paymentForm = document.getElementById('payment-method-form');
  const paymentList = document.getElementById('payment-method-list');

  function renderPaymentMethods(methods = []) {
    if (!paymentList) return;
    if (!Array.isArray(methods) || methods.length === 0) {
      paymentList.innerHTML =
        '<div class="empty-state">Store a wallet to speed through checkout.</div>';
      return;
    }

    const markup = methods
      .map((method = {}) => {
        const methodId = method.id != null ? String(method.id) : '';
        const provider = escapeHtml(method.provider || '');
        const label = escapeHtml(method.label || method.provider || '');
        const masked =
          escapeHtml(method.masked_identifier || method.maskedIdentifier || '');
        const isDefault = Boolean(method.is_default ?? method.isDefault);
        return `
          <div class="payment-item" data-method-id="${methodId}">
            <div>
              <strong>${label}</strong>
              <div class="profile-card__subtitle" style="margin-top:2px;">${provider} • ${masked}</div>
            </div>
            <div style="display:flex; gap:10px; align-items:center;">
              ${isDefault ? '<span class="status-pill success">Default</span>' : ''}
              <button type="button" class="profile-btn outline delete-payment-method" style="padding:6px 12px; min-height:auto; font-size:13px;" data-method-id="${methodId}">Remove</button>
            </div>
          </div>
        `;
      })
      .join('');

    paymentList.innerHTML = markup.trim();
  }

  function updateSwitchStates(settings = {}) {
    const normalized = {
      notify_email: settings.notify_email ?? settings.email,
      notify_sms: settings.notify_sms ?? settings.sms,
      notify_push: settings.notify_push ?? settings.push,
      marketing_opt_in: settings.marketing_opt_in ?? settings.marketing,
    };

    document.querySelectorAll('.profile-switch[data-preference]').forEach((input) => {
      const preference = input.dataset.preference;
      if (!preference || !(preference in normalized)) return;
      input.checked = Boolean(normalized[preference]);
    });

    document
      .querySelectorAll('#edit-profile-form .profile-switch[name]')
      .forEach((input) => {
        const name = input.name;
        if (!name || !(name in normalized)) return;
        input.checked = Boolean(normalized[name]);
      });
  }

  function reflowCollapsibles() {
    collapsibleSections.forEach((entry) => {
      if (!entry || !entry.section || !entry.body) return;
      if (entry.section.classList.contains('collapsed')) {
        entry.body.style.maxHeight = '0px';
        return;
      }
      entry.body.style.maxHeight = `${entry.body.scrollHeight}px`;
    });
  }

  function initCollapsibles() {
    collapsibleSections.length = 0;
    const sections = Array.from(document.querySelectorAll('.collapsible'));
    sections.forEach((section) => {
      const body = section.querySelector('.collapsible-body');
      const toggle = section.querySelector('[data-toggle-card]');
      if (!body || !toggle) return;

      const entry = { section, body };
      collapsibleSections.push(entry);

      const openSection = () => {
        section.classList.remove('collapsed');
        body.style.maxHeight = `${body.scrollHeight}px`;
        body.style.opacity = '1';
      };

      const closeSection = () => {
        section.classList.add('collapsed');
        body.style.maxHeight = '0px';
        body.style.opacity = '0';
      };

      if (section.classList.contains('collapsed')) {
        closeSection();
      } else {
        body.style.maxHeight = `${body.scrollHeight}px`;
        body.style.opacity = '1';
      }

      toggle.addEventListener('click', () => {
        if (section.classList.contains('collapsed')) {
          openSection();
        } else {
          closeSection();
        }
      });
    });
  }

  function toggleBodyScroll() {
    if (openOverlays.size) {
      document.body.style.overflow = 'hidden';
    } else {
      document.body.style.overflow = '';
    }
  }

  function openOverlay(id) {
    if (!id) return;
    const overlay = document.getElementById(id);
    if (!overlay) return;
    overlay.classList.add('active');
    overlay.setAttribute('aria-hidden', 'false');
    openOverlays.add(id);
    toggleBodyScroll();
  }

  function closeOverlay(id) {
    if (!id) return;
    const overlay = document.getElementById(id);
    if (!overlay) return;
    overlay.classList.remove('active');
    overlay.setAttribute('aria-hidden', 'true');
    openOverlays.delete(id);
    toggleBodyScroll();
  }

  document.querySelectorAll('[data-close-modal]').forEach((element) => {
    element.addEventListener('click', () => closeOverlay(element.dataset.closeModal));
  });

  document.querySelectorAll('[data-close-sheet]').forEach((element) => {
    element.addEventListener('click', () => {
      const sheet = element.closest('.action-sheet');
      if (sheet?.id) closeOverlay(sheet.id);
    });
  });

  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && openOverlays.size) {
      const last = Array.from(openOverlays).pop();
      closeOverlay(last);
    }
  });

  function showToast(message, type = 'success') {
    const toastWrapper = document.getElementById('profile-toast');
    const toastMessage = document.getElementById('profile-toast-message');
    const toastIcon = toastWrapper?.querySelector('.toast-icon i');
    if (!toastWrapper || !toastMessage) return;

    toastWrapper.classList.remove('active', 'error');
    toastMessage.textContent = message;
    if (toastIcon) {
      toastIcon.className = type === 'error' ? 'fas fa-circle-exclamation' : 'fas fa-circle-check';
    }
    if (type === 'error') {
      toastWrapper.classList.add('error');
    }

    clearTimeout(toastTimeout);
    requestAnimationFrame(() => toastWrapper.classList.add('active'));
    toastTimeout = setTimeout(() => {
      toastWrapper.classList.remove('active');
    }, 3200);
  }

  function postFormData(endpoint, formData) {
    const headers = new Headers(jsonHeaders);
    return fetch(endpoint, {
      method: 'POST',
      headers,
      body: formData,
      credentials: 'same-origin',
    });
  }

  function patchJson(endpoint, payload) {
    return fetch(endpoint, {
      method: 'PATCH',
      headers: profileHeaders,
      body: JSON.stringify(payload),
      credentials: 'same-origin',
    });
  }

  async function deleteJson(endpoint) {
    const response = await fetch(endpoint, {
      method: 'DELETE',
      headers: jsonHeaders,
      credentials: 'same-origin',
    });
    const data = await response.json();
    return { response, data };
  }

  function clearFormErrors(form) {
    if (!form) return;
    form.querySelectorAll('.input-error').forEach((el) => el.remove());
    form.querySelectorAll('.ring-1, .ring-2, .ring-rose-400').forEach((input) => {
      input.classList.remove('ring-1', 'ring-2', 'ring-rose-400');
    });
  }

  function applyFormErrors(form, errors = {}) {
    if (!form || !errors) return;
    Object.entries(errors).forEach(([field, message]) => {
      const input = form.querySelector(`[name="${field}"]`);
      if (!input) return;
      input.classList.add('ring-2', 'ring-rose-400');
      let helper = input.closest('label')?.querySelector('.input-error');
      if (!helper) {
        helper = document.createElement('p');
        helper.className = 'input-error text-xs text-rose-500 mt-1';
        const label = input.closest('label');
        (label || input.parentElement)?.appendChild(helper);
      }
      helper.textContent = Array.isArray(message) ? message.join(' ') : message;
    });
  }

  function setButtonLoading(button, isLoading, loadingText) {
    if (!button) return;
    if (isLoading) {
      button.dataset.originalText = button.innerHTML;
      button.disabled = true;
      button.innerHTML = loadingText || '<i class="fas fa-spinner fa-spin"></i>';
    } else if (button.dataset.originalText) {
      button.disabled = false;
      button.innerHTML = button.dataset.originalText;
      delete button.dataset.originalText;
    } else {
      button.disabled = false;
    }
  }

  function escapeHtml(value = '') {
    const div = document.createElement('div');
    div.textContent = value;
    return div.innerHTML;
  }

  function deriveDisplayName(data = {}) {
    if (data.display_name) return data.display_name;
    const fullName = [data.first_name, data.last_name].filter(Boolean).join(' ').trim();
    if (fullName) return fullName;
    return data.username || '';
  }

  function refreshProfileFields(profile) {
    if (!profile) return;
    infoFieldElements.forEach((el) => {
      const field = el.dataset.field;
      if (!field) return;
      const value =
        profile[field] ?? config.user?.[field] ?? config.user?.profile?.[field] ?? '';
      const text =
        value != null && String(value).trim() !== ''
          ? String(value).trim()
          : el.dataset.empty || '';
      el.textContent = text;
    });
  }

  function updateHeaderFields(userData = {}) {
    const displayName = deriveDisplayName(userData);
    const headerName = document.getElementById('header-display-name');
    const headerHandle = document.getElementById('header-handle');
    const headerEmail = document.getElementById('header-email');

    if (headerName && displayName) headerName.textContent = displayName;
    if (headerHandle && userData.username) headerHandle.textContent = `@${userData.username}`;
    if (headerEmail && userData.email) headerEmail.textContent = userData.email;
  }

  function updateGoogleStatus(isConnected) {
    const pill = document.getElementById('header-google-status');
    if (!pill) return;
    pill.classList.toggle('success', Boolean(isConnected));
    pill.innerHTML = `<i class="fab fa-google"></i> ${
      isConnected ? 'Google connected' : 'Google not linked'
    }`;
  }

  function updatePasswordStatus(hasPassword) {
    const pill = document.getElementById('header-password-status');
    if (!pill) return;
    pill.innerHTML = `<i class="fas fa-shield-alt"></i> ${
      hasPassword ? 'Password protected' : 'Add a password'
    }`;
  }

  function updateAvatarImages(url) {
    if (!url) return;
    const displayUrl = withCacheBust(url);
    ['profile-avatar', 'navbar-avatar', 'profile-menu-avatar'].forEach((id) => {
      const img = document.getElementById(id);
      if (img) img.src = displayUrl;
    });
  }

  function updateNavbar(payload = {}) {
    if (typeof window.updateNavbarProfile === 'function') {
      const nextPayload = { ...payload };
      if (nextPayload.avatarUrl) {
        nextPayload.avatarUrl = withCacheBust(nextPayload.avatarUrl);
      }
      window.updateNavbarProfile(nextPayload);
    }
  }
 
  function updateProfileState(profile) {
    if (!profile) return;
    const previousUser = config.user || {};
    config.user = { ...previousUser, ...profile };

    if (profile.notifications) {
      config.notificationSettings = profile.notifications;
      updateSwitchStates(profile.notifications);
    }

    if (typeof profile.google_connected !== 'undefined') {
      config.googleConnected = Boolean(profile.google_connected);
      updateGoogleStatus(config.googleConnected);
    }

    if (typeof profile.has_password !== 'undefined') {
      config.hasPassword = Boolean(profile.has_password);
      updatePasswordStatus(config.hasPassword);
    }

    if (profile.avatar_url) {
      config.user.avatar_url = profile.avatar_url;
      updateAvatarImages(profile.avatar_url);
    }

    updateHeaderFields(config.user);
    refreshProfileFields(profile);

    updateNavbar({
      avatarUrl: config.user.avatar_url,
      displayName: deriveDisplayName(config.user),
      email: config.user.email,
      googleConnected: config.googleConnected,
    });

    setTimeout(reflowCollapsibles, 60);
  }
 
  const ALLOWED_MIME_TYPES = ['image/png', 'image/jpeg', 'image/webp'];
  const ALLOWED_EXTENSIONS = ['png', 'jpg', 'jpeg', 'webp'];
  const MAX_FILE_SIZE_MB = 5;
  const MAX_FILE_SIZE = MAX_FILE_SIZE_MB * 1024 * 1024;

  function getFileExtension(name = '') {
    const parts = name.split('.');
    if (parts.length < 2) return '';
    return parts.pop().toLowerCase();
  }

  function validateAvatarFile(file) {
    if (!file) {
      showToast('Please choose an image to continue.', 'error');
      return false;
    }
    const mime = (file.type || '').toLowerCase();
    const ext = getFileExtension(file.name || '');
    const mimeAllowed = mime ? ALLOWED_MIME_TYPES.includes(mime) : false;
    const extAllowed = ext ? ALLOWED_EXTENSIONS.includes(ext) : false;
    if (!mimeAllowed && !extAllowed) {
      showToast('Please choose a PNG, JPG, JPEG, or WEBP image.', 'error');
      return false;
    }
    if (file.size > MAX_FILE_SIZE) {
      showToast(`Image must be smaller than ${MAX_FILE_SIZE_MB} MB.`, 'error');
      return false;
    }
    return true;
  }

  function withCacheBust(url) {
    if (!url) return url;
    const separator = url.includes('?') ? '&' : '?';
    return `${url}${separator}v=${Date.now()}`;
  }

  let pendingAvatarObjectUrl = null;

  function setAvatarUploading(isUploading) {
    const busy = Boolean(isUploading);
    avatarTouchArea?.classList.toggle('is-uploading', busy);
    if (avatarTouchArea) {
      avatarTouchArea.disabled = busy;
    }
    if (avatarUploadTrigger) {
      avatarUploadTrigger.disabled = busy;
    }
    if (avatarFileInput) {
      avatarFileInput.disabled = busy;
    }
    if (avatarRemoveTrigger) {
      avatarRemoveTrigger.disabled = busy;
    }
  }

  async function handleAvatarSelection(file) {
    if (!profileAvatarImg) {
      showToast('Unable to update your profile photo right now. Please try again later.', 'error');
      return;
    }
    if (!validateAvatarFile(file)) {
      return;
    }
 
    const persistedUrl = config.user?.avatar_url ? withCacheBust(config.user.avatar_url) : profileAvatarImg.src;
    if (pendingAvatarObjectUrl) {
      URL.revokeObjectURL(pendingAvatarObjectUrl);
      pendingAvatarObjectUrl = null;
    }

    const objectUrl = URL.createObjectURL(file);
    pendingAvatarObjectUrl = objectUrl;
    profileAvatarImg.src = objectUrl;
    setAvatarUploading(true);

    const formData = new FormData();
    formData.append('avatar', file);

    try {
      const response = await postFormData(config.endpoints.avatar, formData);
      const data = await response.json();
      if (!response.ok || data.status !== 'success') {
        if (persistedUrl) {
          profileAvatarImg.src = persistedUrl;
        }
        const message = data.message || data.error || 'Unable to update profile picture.';
        showToast(message, 'error');
        return;
      }

      const avatarUrl = data.avatar_url || data.image_url;
      if (avatarUrl) {
        config.user = config.user || {};
        config.user.avatar_url = avatarUrl;
        updateAvatarImages(avatarUrl);
        updateNavbar({
          avatarUrl,
          displayName: deriveDisplayName(config.user),
          email: config.user.email,
          googleConnected: config.googleConnected,
        });
      }
      showToast('Profile picture updated.');
    } catch (error) {
      console.error(error);
      if (persistedUrl) {
        profileAvatarImg.src = persistedUrl;
      }
      showToast('Unable to update profile picture.', 'error');
    } finally {
      setAvatarUploading(false);
      if (pendingAvatarObjectUrl === objectUrl) {
        URL.revokeObjectURL(objectUrl);
        pendingAvatarObjectUrl = null;
      } else {
        URL.revokeObjectURL(objectUrl);
      }
    }
  }

  async function removeAvatar() {
    const previousUrl = config.user?.avatar_url ? withCacheBust(config.user.avatar_url) : profileAvatarImg?.src;
    setAvatarUploading(true);
    try {
      const { response, data } = await deleteJson(config.endpoints.avatar);
      if (!response.ok || data.status !== 'success') {
        if (previousUrl) {
          profileAvatarImg.src = previousUrl;
        }
        showToast(data.message || 'Unable to remove profile picture.', 'error');
        return;
      }
      const avatarUrl = data.avatar_url || data.image_url;
      if (avatarUrl) {
        config.user = config.user || {};
        config.user.avatar_url = avatarUrl;
        updateAvatarImages(avatarUrl);
        updateNavbar({
          avatarUrl,
          displayName: deriveDisplayName(config.user),
          email: config.user.email,
          googleConnected: config.googleConnected,
        });
      }
      showToast('Profile picture removed.');
    } catch (error) {
      console.error(error);
      showToast('Unable to remove profile picture.', 'error');
    } finally {
      setAvatarUploading(false);
    }
  }

  openEditProfileBtn?.addEventListener('click', () => openOverlay('edit-profile-modal'));
  inlineEditButton?.addEventListener('click', () => openOverlay('edit-profile-modal'));

  editProfileForm?.addEventListener('submit', async (event) => {
    event.preventDefault();
    clearFormErrors(editProfileForm);

    const formData = new FormData(editProfileForm);
    const booleanFields = ['notify_email', 'notify_sms', 'notify_push', 'marketing_opt_in'];
    const payload = {};

    for (const [key, value] of formData.entries()) {
      if (booleanFields.includes(key)) {
        payload[key] = value === 'on';
      } else {
        payload[key] = value?.trim?.() ?? value;
      }
    }

    const submitButton = editProfileForm.querySelector('button[type="submit"]');
    setButtonLoading(submitButton, true, '<i class="fas fa-spinner fa-spin"></i>');

    try {
      const response = await patchJson(editProfileForm.dataset.endpoint, payload);
      const data = await response.json();
      if (!response.ok || data.status !== 'success') {
        applyFormErrors(editProfileForm, data.errors || {});
        showToast(data.message || 'Unable to update profile.', 'error');
        return;
      }

      updateProfileState(data.profile);
      closeOverlay('edit-profile-modal');
      showToast('Profile updated successfully.');
    } catch (error) {
      console.error(error);
      showToast('Something went wrong while updating your profile.', 'error');
    } finally {
      setButtonLoading(submitButton, false);
    }
  });

  openPasswordButton?.addEventListener('click', () => openOverlay('password-modal'));

  passwordForm?.addEventListener('submit', async (event) => {
    event.preventDefault();
    clearFormErrors(passwordForm);

    const formData = new FormData(passwordForm);
    const payload = Object.fromEntries(formData.entries());
    const submitButton = passwordForm.querySelector('button[type="submit"]');
    setButtonLoading(submitButton, true, '<i class="fas fa-spinner fa-spin"></i>');

    try {
      const response = await fetch(passwordForm.dataset.endpoint, {
        method: 'POST',
        headers: profileHeaders,
        body: JSON.stringify(payload),
        credentials: 'same-origin',
      });
      const data = await response.json();

      if (!response.ok || data.status !== 'success') {
        applyFormErrors(passwordForm, data.errors || {});
        showToast(data.message || 'Unable to update password.', 'error');
        return;
      }

      config.hasPassword = true;
      updatePasswordStatus(true);
      passwordForm.reset();
      closeOverlay('password-modal');
      showToast('Password updated successfully.');
    } catch (error) {
      console.error(error);
      showToast('Something went wrong while updating your password.', 'error');
    } finally {
      setButtonLoading(submitButton, false);
    }
  });

  document.querySelectorAll('.profile-switch[data-preference]').forEach((input) => {
    input.addEventListener('change', async () => {
      const preference = input.dataset.preference;
      if (!preference) return;
      input.disabled = true;
      const payload = { [preference]: input.checked };
      try {
        const response = await patchJson(config.endpoints.profile, payload);
        const data = await response.json();
        if (!response.ok || data.status !== 'success') {
          input.checked = !input.checked;
          showToast(data.message || 'Unable to update preference.', 'error');
          return;
        }
        updateProfileState(data.profile);
        showToast('Preference saved.');
      } catch (error) {
        console.error(error);
        input.checked = !input.checked;
        showToast('Unable to update preference. Try again.', 'error');
      } finally {
        input.disabled = false;
      }
    });
  });

  paymentModalButton?.addEventListener('click', () => openOverlay('payment-method-modal'));

  paymentForm?.addEventListener('submit', async (event) => {
    event.preventDefault();
    clearFormErrors(paymentForm);

    const formData = new FormData(paymentForm);
    const payload = Object.fromEntries(formData.entries());
    payload.is_default = formData.get('is_default') === 'on';

    const submitButton = paymentForm.querySelector('button[type="submit"]');
    setButtonLoading(submitButton, true, '<i class="fas fa-spinner fa-spin"></i>');

    try {
      const response = await fetch(paymentForm.dataset.endpoint, {
        method: 'POST',
        headers: profileHeaders,
        body: JSON.stringify(payload),
        credentials: 'same-origin',
      });
      const data = await response.json();

      if (!response.ok || data.status !== 'success') {
        applyFormErrors(paymentForm, data.errors || {});
        showToast(data.message || 'Unable to save payment method.', 'error');
        return;
      }

      if (!Array.isArray(config.paymentMethods)) {
        config.paymentMethods = [];
      }
      if (data.method.is_default) {
        config.paymentMethods.forEach((method) => {
          method.is_default = false;
        });
      }
      config.paymentMethods.push(data.method);
      renderPaymentMethods(config.paymentMethods);
      paymentForm.reset();
      closeOverlay('payment-method-modal');
      showToast('Payment method saved.');
    } catch (error) {
      console.error(error);
      showToast('Unable to save payment method. Try again.', 'error');
    } finally {
      setButtonLoading(submitButton, false);
    }
  });

  paymentList?.addEventListener('click', async (event) => {
    const button = event.target.closest('.delete-payment-method');
    if (!button) return;
    const methodId = button.dataset.methodId;
    if (!methodId) return;

    if (!window.confirm('Remove this payment method?')) return;

    button.disabled = true;
    button.innerHTML = '<i class="fas fa-spinner fa-spin"></i>';

    try {
      const { response, data } = await deleteJson(
        `${config.endpoints.paymentMethods}/${methodId}`,
      );
      if (!response.ok || data.status !== 'success') {
        showToast(data.message || 'Unable to delete payment method.', 'error');
        return;
      }
      config.paymentMethods = (config.paymentMethods || []).filter(
        (method) => String(method.id) !== String(methodId),
      );
      renderPaymentMethods(config.paymentMethods);
      showToast('Payment method removed.');
    } catch (error) {
      console.error(error);
      showToast('Unable to delete payment method. Try again.', 'error');
    } finally {
      button.disabled = false;
      button.textContent = 'Remove';
    }
  });

  function initAvatarUploader() {
    if (!avatarFileInput) return;
    const openPicker = () => {
      if (avatarFileInput.disabled) return;
      avatarFileInput.click();
    };
    avatarTouchArea?.addEventListener('click', openPicker);
    avatarUploadTrigger?.addEventListener('click', openPicker);
    avatarUploadRow?.addEventListener('click', (event) => {
      if (event.target.closest('#avatar-remove-trigger')) {
        return;
      }
      if (event.target.closest('#avatar-upload-trigger')) {
        return;
      }
      openPicker();
    });
    avatarRemoveTrigger?.addEventListener('click', () => {
      if (avatarRemoveTrigger.disabled) return;
      removeAvatar();
    });
    avatarFileInput.addEventListener('change', (event) => {
      const file = event.target.files?.[0];
      event.target.value = '';
      if (!file) return;
      handleAvatarSelection(file);
    });
  }
 
  ['edit-profile-modal', 'password-modal', 'payment-method-modal'].forEach((id) => {
    document.getElementById(id)?.addEventListener('click', (event) => {
      if (event.target.id === id) {
        closeOverlay(id);
      }
    });
  });

  initCollapsibles();
  initAvatarUploader();
  renderPaymentMethods(config.paymentMethods);
  updateSwitchStates(config.notificationSettings);
  updateHeaderFields(config.user || {});
  refreshProfileFields(config.user || {});
  updateGoogleStatus(config.googleConnected);
  updatePasswordStatus(config.hasPassword);

  updateNavbar({
    avatarUrl: config.user?.avatar_url,
    displayName: deriveDisplayName(config.user || {}),
    email: config.user?.email,
    googleConnected: config.googleConnected,
  });

  const headerCard = document.querySelector('.profile-header-card');
  if (headerCard) {
    const TOP_THRESHOLD = 2;
    const DELTA_THRESHOLD = 8;
    let lastScrollY = Math.max(window.scrollY, 0);
    let isHeaderVisible = lastScrollY <= TOP_THRESHOLD;
    let isTicking = false;

    headerCard.classList.toggle('is-hidden', !isHeaderVisible);

    const setHeaderVisibility = (visible) => {
      if (visible === isHeaderVisible) return;
      isHeaderVisible = visible;
      headerCard.classList.toggle('is-hidden', !visible);
    };

    const updateHeaderVisibility = () => {
      isTicking = false;
      const currentScroll = Math.max(window.scrollY, 0);
      const delta = currentScroll - lastScrollY;

      if (currentScroll <= TOP_THRESHOLD) {
        if (!isHeaderVisible) {
          setHeaderVisibility(true);
        }
        lastScrollY = currentScroll;
        return;
      }

      if (Math.abs(delta) < DELTA_THRESHOLD) {
        return;
      }

      if (delta > 0 && isHeaderVisible) {
        setHeaderVisibility(false);
      }

      lastScrollY = currentScroll;
    };

    window.addEventListener(
      'scroll',
      () => {
        if (isTicking) return;
        isTicking = true;
        window.requestAnimationFrame(updateHeaderVisibility);
      },
      { passive: true },
    );
  }
})();

