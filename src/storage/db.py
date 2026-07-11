from sqlalchemy import Column, Integer, String, DateTime, JSON, LargeBinary
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.orm import declarative_base
from datetime import datetime, timezone

Base = declarative_base()

class DiaryEntry(Base):
    __tablename__ = "diary_entries"

    id = Column(Integer, primary_key=True)
    date = Column(DateTime, default=datetime.now(timezone.utc))
    text = Column(String)
    mood = Column(String, nullable=True)
    mentioned_users = Column(JSON, default=list)   # список user_id
    embedding = Column(LargeBinary)

engine = create_async_engine("sqlite+aiosqlite:///./bot_memory.db")
async_session = async_sessionmaker(engine, expire_on_commit=False)

async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)