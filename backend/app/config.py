
import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine

# Load the backend-specific environment configuration.
ENV_FILE = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(ENV_FILE)


def database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL is missing. Configure backend/.env."
        )
    return url


def engine():
    return create_engine(
        database_url(),
        pool_pre_ping=True,
        echo=os.getenv("SQL_ECHO", "false").lower() == "true",
    )
