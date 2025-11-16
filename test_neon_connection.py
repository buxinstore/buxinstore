import os

from dotenv import load_dotenv
from sqlalchemy import create_engine, text


def main():
    load_dotenv()
    database_uri = os.getenv("DATABASE_URL")
    if not database_uri:
        raise RuntimeError("DATABASE_URL is not set. Update your .env before testing.")

    engine = create_engine(database_uri)
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    print("Connected to Neon PostgreSQL successfully via SQLAlchemy!")


if __name__ == "__main__":
    main()

