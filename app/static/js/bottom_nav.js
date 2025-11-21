/**
 * Mobile Bottom Navigation Bar
 * Handles active state highlighting based on current route
 * Ensures proper icon initialization and smooth interactions
 */

(function() {
  'use strict';

  let isInitialized = false;

  // Wait for DOM to be ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initBottomNav);
  } else {
    initBottomNav();
  }

  function initBottomNav() {
    if (isInitialized) return;
    
    const navItems = document.querySelectorAll('.curved-nav .nav-item, .mobile-bottom-nav .nav-item');
    if (navItems.length === 0) {
      // Retry after a short delay if nav items aren't ready yet
      setTimeout(initBottomNav, 100);
      return;
    }

    // Get current URL and path
    const currentUrl = window.location.href;
    const currentPath = window.location.pathname;
    const currentSearch = window.location.search;

    // Remove active class from all items
    navItems.forEach(item => {
      item.classList.remove('active');
    });

    // Find and activate the matching item
    navItems.forEach(item => {
      const href = item.getAttribute('href');
      if (!href) return;

      let shouldActivate = false;

      try {
        // Try to match by full URL first
        const itemUrl = new URL(href, window.location.origin);
        const itemPath = itemUrl.pathname;
        const itemSearch = itemUrl.search;

        // Exact match (path + query)
        if (itemPath === currentPath && itemSearch === currentSearch) {
          shouldActivate = true;
        }
        // Path match only (ignore query params)
        else if (itemPath === currentPath) {
          shouldActivate = true;
        }
        // Home route special case
        else if (itemPath === '/' && currentPath === '/') {
          shouldActivate = true;
        }
        // Check if current path starts with item path (for nested routes)
        else if (itemPath !== '/' && currentPath.startsWith(itemPath)) {
          // Additional check: make sure it's not a false positive
          // e.g., /products should match /products but not /products/123/details
          const nextChar = currentPath[itemPath.length];
          if (!nextChar || nextChar === '/' || nextChar === '?') {
            shouldActivate = true;
          }
        }
        // Forum route special handling
        else if (itemPath.includes('/forum') && currentPath.includes('/forum')) {
          shouldActivate = true;
        }
        // Category route special handling
        else if (item.getAttribute('data-route') === 'category' && currentPath.startsWith('/category/')) {
          shouldActivate = true;
        }
        // Product route special handling
        else if (item.getAttribute('data-route') === 'products' && 
                 (currentPath.startsWith('/product/') || currentPath === '/products')) {
          shouldActivate = true;
        }
      } catch (e) {
        // Fallback: simple string comparison
        const normalizedRoute = href.replace(/^https?:\/\/[^\/]+/, '').split('?')[0];
        const normalizedPath = currentPath.split('?')[0];
        
        if (normalizedPath === normalizedRoute) {
          shouldActivate = true;
        } else if (normalizedRoute !== '/' && normalizedPath.startsWith(normalizedRoute)) {
          shouldActivate = true;
        }
      }

      if (shouldActivate) {
        item.classList.add('active');
      }
    });

    // Initialize Lucide icons
    initializeIcons();

    // Add click handlers for haptic-like feedback
    navItems.forEach(item => {
      item.addEventListener('click', function(e) {
        // Add a temporary class for visual feedback
        this.classList.add('nav-item-clicked');
        setTimeout(() => {
          this.classList.remove('nav-item-clicked');
        }, 200);
      });
    });

    isInitialized = true;
  }

  function initializeIcons() {
    if (window.lucide && typeof window.lucide.createIcons === 'function') {
      try {
        // Target only icons in the bottom nav
        const navContainer = document.querySelector('.curved-nav, .mobile-bottom-nav');
        if (navContainer) {
          window.lucide.createIcons({
            attrs: {
              'stroke-width': 2
            }
          });
        }
      } catch (err) {
        console.warn('Failed to initialize Lucide icons in bottom nav:', err);
      }
    }
  }

  // Re-initialize icons when theme changes
  if (window.addEventListener) {
    document.documentElement.addEventListener('theme:change', function() {
      setTimeout(() => {
        initializeIcons();
        // Re-check active state after theme change
        const navItems = document.querySelectorAll('.curved-nav .nav-item, .mobile-bottom-nav .nav-item');
        navItems.forEach(item => {
          // Force re-render by toggling active class
          const wasActive = item.classList.contains('active');
          if (wasActive) {
            item.classList.remove('active');
            setTimeout(() => item.classList.add('active'), 10);
          }
        });
      }, 150);
    });
  }

  // Re-initialize on navigation (for SPA-like behavior if implemented)
  if (window.addEventListener) {
    window.addEventListener('popstate', function() {
      setTimeout(initBottomNav, 50);
    });
  }

  // Expose initialization function for manual re-initialization if needed
  window.reinitBottomNav = initBottomNav;
})();

