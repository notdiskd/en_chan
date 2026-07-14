from sqlalchemy import Column, Integer, String, DateTime, JSON, LargeBinary, BigInteger
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
    mentioned_users = Column(JSON, default=list)
    embedding = Column(LargeBinary)

class Message(Base):
    __tablename__ = "messages"

    id = Column(Integer, primary_key=True)
    chat_id = Column(BigInteger, index=True)
    tg_message_id = Column(BigInteger, nullable=True)
    role = Column(String)
    text = Column(String, default="")
    attachment_type = Column(String, nullable=True)
    attachment_summary = Column(String, nullable=True)
    timestamp = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    reply_to_tg_id = Column(BigInteger, nullable=True)

engine = create_async_engine("sqlite+aiosqlite:///./bot_memory.db")
async_session = async_sessionmaker(engine, expire_on_commit=False)

async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)