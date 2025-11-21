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
    const navItems = document.querySelectorAll('.mobile-bottom-nav__item');
    if (navItems.length === 0) return;

    // Get current pathname
    const currentPath = window.location.pathname;

    // Remove active class from all items
    navItems.forEach(item => {
      item.classList.remove('mobile-bottom-nav__item--active');
    });

    // Find and activate the matching item
    navItems.forEach(item => {
      const route = item.getAttribute('data-route') || item.getAttribute('href');
      if (!route) return;

      // Normalize routes for comparison
      const normalizedRoute = route.replace(/^https?:\/\/[^\/]+/, '').split('?')[0];
      const normalizedPath = currentPath.split('?')[0];

      // Check if current path matches the route
      if (normalizedPath === normalizedRoute || 
          (normalizedRoute !== '/' && normalizedPath.startsWith(normalizedRoute))) {
        item.classList.add('mobile-bottom-nav__item--active');
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

    // Add smooth press animation
    navItems.forEach(item => {
      item.addEventListener('touchstart', function() {
        this.style.transform = 'scale(0.95)';
      }, { passive: true });

      item.addEventListener('touchend', function() {
        setTimeout(() => {
          this.style.transform = '';
        }, 150);
      }, { passive: true });
    });
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

