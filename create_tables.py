"""Create every table from the SQLAlchemy models in a fresh LOCAL database.
Usage (from the backend folder, venv active):  python create_tables.py
Do not run against production; production uses migration.sql."""
import asyncio

from dotenv import load_dotenv

load_dotenv()

from sqlalchemy import text  # noqa: E402

from app import models  # noqa: E402,F401  (registers all tables)
from app.db import Base, engine  # noqa: E402


async def main() -> None:
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()
    print("Tables created:", ", ".join(sorted(Base.metadata.tables)))


asyncio.run(main())
