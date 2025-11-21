/**
 * Mobile Bottom Navigation Bar
 * Handles active state highlighting based on current route
 */

(function() {
  'use strict';

  // Wait for DOM to be ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initBottomNav);
  } else {
    initBottomNav();
  }

  function initBottomNav() {
    const navItems = document.querySelectorAll('.curved-nav .nav-item, .mobile-bottom-nav .nav-item');
    if (navItems.length === 0) return;

    // Get current URL
    const currentUrl = window.location.href;
    const currentPath = window.location.pathname;

    // Remove active class from all items
    navItems.forEach(item => {
      item.classList.remove('active');
    });

    // Find and activate the matching item
    navItems.forEach(item => {
      const href = item.getAttribute('href');
      if (!href) return;

      try {
        // Try to match by full URL first
        const itemUrl = new URL(href, window.location.origin);
        
        // Check if URLs match exactly
        if (itemUrl.href === currentUrl || itemUrl.href.split('?')[0] === currentUrl.split('?')[0]) {
          item.classList.add('active');
          return;
        }

        // Check if pathname matches
        const normalizedRoute = itemUrl.pathname;
        const normalizedPath = currentPath;

        if (normalizedPath === normalizedRoute || 
            (normalizedRoute !== '/' && normalizedPath.startsWith(normalizedRoute))) {
          item.classList.add('active');
        }
      } catch (e) {
        // Fallback: simple string comparison
        const normalizedRoute = href.replace(/^https?:\/\/[^\/]+/, '').split('?')[0];
        const normalizedPath = currentPath.split('?')[0];
        
        if (normalizedPath === normalizedRoute || 
            (normalizedRoute !== '/' && normalizedPath.startsWith(normalizedRoute))) {
          item.classList.add('active');
        }
      }
    });

    // Initialize Lucide icons if available
    if (window.lucide && typeof window.lucide.createIcons === 'function') {
      try {
        window.lucide.createIcons();
      } catch (err) {
        console.warn('Failed to initialize Lucide icons in bottom nav:', err);
      }
    }
  }

  // Re-initialize icons when theme changes
  if (window.addEventListener) {
    document.documentElement.addEventListener('theme:change', function() {
      if (window.lucide && typeof window.lucide.createIcons === 'function') {
        setTimeout(() => {
          try {
            window.lucide.createIcons();
          } catch (err) {
            console.warn('Failed to re-initialize Lucide icons after theme change:', err);
          }
        }, 100);
      }
    });
  }
})();

