from sqlalchemy import Column, Integer, String, DateTime, Date, BigInteger, JSON, LargeBinary, Float, Boolean
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


class EnSelfModel(Base):
    """A compact, global, evolving self-view for En."""
    __tablename__ = "en_self_model"

    id = Column(Integer, primary_key=True, default=1)
    content = Column(String, default="")
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
    is_deleted = Column(Boolean, default=False, nullable=False)
    deleted_by = Column(String, nullable=True)
    timestamp = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    reply_to_tg_id = Column(BigInteger, nullable=True)


class ChatSummary(Base):
    """Rolling, private continuity summary for one Telegram chat."""
    __tablename__ = "chat_summaries"

    chat_id = Column(BigInteger, primary_key=True)
    summarized_through_id = Column(Integer, nullable=False, default=0)
    content = Column(String, default="")
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class ProactiveSettings(Base):
    """Per-chat spontaneous-message cooldown, with older mode columns kept for compatibility."""
    __tablename__ = "proactive_settings"

    chat_id = Column(BigInteger, primary_key=True)
    enabled = Column(Boolean, default=False, nullable=False)
    auto_send = Column(Boolean, default=False, nullable=False)
    timezone_name = Column(String, nullable=True)
    spontaneous_after = Column("next_allowed_at", DateTime(timezone=True), nullable=True)
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class ProactiveFollowUp(Base):
    """A message En explicitly committed to send later in a private chat."""
    __tablename__ = "proactive_follow_ups"

    id = Column(Integer, primary_key=True)
    chat_id = Column(BigInteger, index=True, nullable=False)
    purpose = Column(String, nullable=False)  # Why En promised to follow up.
    due_at = Column(DateTime(timezone=True), index=True, nullable=False)
    status = Column(String, default="pending", index=True, nullable=False)
    message = Column("draft_text", String, nullable=True)  # Exact text sent when due.
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    sent_at = Column(DateTime(timezone=True), nullable=True)


engine = create_async_engine("sqlite+aiosqlite:///./bot_memory.db")
async_session = async_sessionmaker(engine, expire_on_commit=False)


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        message_columns = {
            row[1] for row in (await conn.exec_driver_sql("PRAGMA table_info(messages)")).all()
        }
        if "is_deleted" not in message_columns:
            await conn.exec_driver_sql(
                "ALTER TABLE messages ADD COLUMN is_deleted BOOLEAN NOT NULL DEFAULT 0"
            )
        if "deleted_by" not in message_columns:
            await conn.exec_driver_sql(
                "ALTER TABLE messages ADD COLUMN deleted_by VARCHAR"
            )

    async with async_session() as session:
        existing = await session.get(DayState, 1)
        if existing is None:
            session.add(DayState(id=1, content=""))
            await session.commit()
