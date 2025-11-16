import os
import json

"""
Local smoke test for `/admin/email/customers`.

This version is safe to run even when no database URL is configured,
by providing a temporary in-memory SQLite DATABASE_URL just for this test.
"""


def _ensure_test_database_url() -> None:
    """Ensure DATABASE_URL is set so Flask-SQLAlchemy can initialize."""
    if not os.getenv("DATABASE_URL") and not os.getenv("SQLALCHEMY_DATABASE_URI"):
        # Use an in-memory SQLite DB just for this smoke test.
        os.environ["DATABASE_URL"] = "sqlite:///:memory:"


def create_test_client():
    """
    Create a Flask test client using the main application, but with
    a guaranteed non-empty DATABASE_URL so extensions can initialize.
    """
    _ensure_test_database_url()

    # Import after setting DATABASE_URL so app initialization sees it.
    from app import app  # import inside function on purpose

    return app.test_client()


def test_admin_email_customers_get():
    """
    Simple local test to ensure /admin/email/customers responds quickly and
    does not crash at the WSGI level.
    """
    client = create_test_client()

    # We don't have an authenticated admin user in this simple smoke test,
    # so we only verify that the route does not crash at the WSGI level.
    # Do NOT follow redirects; we just want to see that the route responds.
    resp = client.get("/admin/email/customers", follow_redirects=False)

    print("Status code:", resp.status_code)
    print("Content-Type:", resp.headers.get("Content-Type"))

    body_text = resp.get_data(as_text=True)
    print("Raw body (first 300 chars):", body_text[:300])

    # Only attempt JSON parsing on successful JSON responses
    content_type = resp.headers.get("Content-Type") or ""
    if 200 <= resp.status_code < 300 and "application/json" in content_type:
        try:
            data = json.loads(body_text)
            print("JSON body:", data)
        except Exception as exc:  # pragma: no cover - debugging aid
            print("Failed to parse JSON (unexpected):", exc)


if __name__ == "__main__":
    test_admin_email_customers_get()


