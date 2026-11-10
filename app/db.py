import os
from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase

# Load environment variables directly in db.py to ensure they are captured
load_dotenv()

# Use 127.0.0.1 instead of localhost in the fallback to prevent Windows DNS resolution errors
DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("Set DATABASE_URL in backend/.env (use 127.0.0.1, not localhost, on Windows)")

engine = create_async_engine(DATABASE_URL, pool_pre_ping=True, pool_size=5,
                             connect_args={"statement_cache_size": 0})
Session = async_sessionmaker(engine, expire_on_commit=False)

class Base(DeclarativeBase):
    pass

async def get_db():
    async with Session() as s:
        yield s