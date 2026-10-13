from sqlalchemy import Column, Integer, String, DateTime, Date, BigInteger, JSON, LargeBinary, Float
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.orm import declarative_base
from datetime import datetime, timezone

Base = declarative_base()


class MemoryEntry(Base):
    __tablename__ = "memory_entries"

    id = Column(Integer, primary_key=True)
    type = Column(String, index=True)
    text = Column(String)
    embedding = Column(LargeBinary)
    user_id = Column(BigInteger, nullable=True, index=True)
    importance = Column(Float, default=0.5)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    meta = Column(JSON, default=dict)


class DayState(Base):
    __tablename__ = "day_state"

    id = Column(Integer, primary_key=True, default=1) # эта строка всегда только одна существует
    content = Column(String, default="")
    state_date = Column(Date, nullable=True)
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class UserProfile(Base):
    __tablename__ = "user_profiles"

    user_id = Column(BigInteger, primary_key=True)
    facts = Column(JSON, default=list)
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class SharedPersonFact(Base):
    """A neutral fact about a named person, with its conversational source."""
    __tablename__ = "shared_person_facts"

    id = Column(Integer, primary_key=True)
    subject = Column(String, index=True)
    fact = Column(String)
    category = Column(String, index=True)
    source_user_id = Column(BigInteger, index=True)
    source_name = Column(String, default="")
    embedding = Column(LargeBinary)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


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


class ChatSummary(Base):
    """Rolling, private continuity summary for one Telegram chat."""
    __tablename__ = "chat_summaries"

    chat_id = Column(BigInteger, primary_key=True)
    summarized_through_id = Column(Integer, nullable=False, default=0)
    content = Column(String, default="")
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


engine = create_async_engine("sqlite+aiosqlite:///./bot_memory.db")
async_session = async_sessionmaker(engine, expire_on_commit=False)


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with async_session() as session:
        existing = await session.get(DayState, 1)
        if existing is None:
            session.add(DayState(id=1, content=""))
            await session.commit()
