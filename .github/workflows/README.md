The GitHub Pages workflow publishes the self-contained site in `frontend/` to
`https://buxinstore.github.io/buxinstore/`. The static site's API base URL is
configured in `frontend/config.js`.

Render continues to serve the Flask application and existing pages. `FRONTEND_URL`
is the allowed GitHub Pages origin for API CORS and enables secure cross-origin
session cookies for the checkout handoff.
