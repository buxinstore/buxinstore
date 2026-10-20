# Static storefront

This first migration stage moves catalog browsing, search, product details, and
the local cart to GitHub Pages. Catalog data and prices come from Flask's
`/api/storefront/*` endpoints. At checkout, the cart is validated and synced to
the existing Flask session before the browser continues to Render's checkout.
The static storefront reuses the Flask storefront's Tailwind classes, header,
homepage sections, product-card layout, and `app/static/css/theme.css` theme;
the latter is copied to `assets/theme.css` for Pages. Flask templates and routes
remain in place and continue to serve the live Render storefront during rollout.

## Deploy

- GitHub Actions publishes this folder to `https://buxinstore.github.io/buxinstore/` on pushes to `main`.
- The workflow enables GitHub Pages with the GitHub Actions source when repository policy allows it. Otherwise, set **Settings → Pages → Build and deployment → Source** to **GitHub Actions**.
- `config.js` points the frontend to `https://store.techbuxin.com`, the existing Render-facing Flask host.
- `render.yaml` sets `FRONTEND_URL=https://buxinstore.github.io` for CORS and cross-origin session cookies. If the Render service is not managed by the Blueprint, set the same environment variable in the Render dashboard.

The original Flask pages and routes remain available on Render during the
migration. Keep the existing Render domain live until the static checkout flow
has been reviewed in production.
