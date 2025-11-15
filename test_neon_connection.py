import os

from dotenv import load_dotenv
import psycopg2


def main():
    load_dotenv()
    database_uri = os.getenv("DATABASE_URL")
    if not database_uri:
        raise RuntimeError("DATABASE_URL is not set. Update your .env before testing.")

    with psycopg2.connect(database_uri) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
    print("Connected to Neon PostgreSQL successfully!")


if __name__ == "__main__":
    main()

