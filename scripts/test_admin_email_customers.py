import json

from app import app


def test_admin_email_customers_get():
    """
    Simple local test to ensure /admin/email/customers responds quickly and
    returns JSON instead of HTML or raising exceptions.
    """
    client = app.test_client()

    # We don't have an authenticated admin user in this simple smoke test,
    # so we only verify that the route does not crash at the WSGI level.
    resp = client.get("/admin/email/customers")

    print("Status code:", resp.status_code)
    print("Content-Type:", resp.headers.get("Content-Type"))

    try:
        data = json.loads(resp.get_data(as_text=True))
    except Exception as exc:  # pragma: no cover - debugging aid
        print("Failed to parse JSON:", exc)
        print("Raw body:", resp.get_data(as_text=True)[:500])
        raise

    print("JSON body:", data)


if __name__ == "__main__":
    test_admin_email_customers_get()


