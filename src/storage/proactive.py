from datetime import datetime, timedelta, timezone

from sqlalchemy import func, or_, select, update

from storage.db import Message, ProactiveFollowUp, ProactiveSettings, async_session


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.astimezone()  # Interpret timezone-free values in the machine's local timezone.
    return value.astimezone(timezone.utc)


async def create_follow_up(
    chat_id: int, topic: str, message_text: str, due_at: datetime
) -> str:
    topic = topic.strip()
    message_text = message_text.strip()
    now = datetime.now(timezone.utc)
    if not topic or len(topic) > 500:
        return "The topic must be 1-500 characters."
    if not message_text or len(message_text) > 1200:
        return "The message must be 1-1200 characters."
    due_at = _as_utc(due_at)
    if due_at < now + timedelta(minutes=1):
        return "Schedule it at least one minute in the future."
    if due_at > now + timedelta(days=90):
        return "Schedule it within the next 90 days."

    async with async_session() as session:
        result = await session.execute(
            select(func.count(ProactiveFollowUp.id)).where(
                ProactiveFollowUp.chat_id == chat_id,
                ProactiveFollowUp.status.in_(("pending", "processing")),
            )
        )
        if result.scalar_one() >= 10:
            return "This chat already has ten active follow-ups. Update or remove one first."
        item = ProactiveFollowUp(
            chat_id=chat_id,
            purpose=topic,
            message=message_text,
            due_at=due_at,
        )
        session.add(item)
        await session.commit()
        return f"Follow-up {item.id} saved for {due_at.astimezone().strftime('%Y-%m-%d %H:%M')} local time."


async def claim_follow_up(
    follow_up_id: int | None = None,
    force: bool = False,
) -> dict | None:
    now = datetime.now(timezone.utc)
    stale_before = now - timedelta(minutes=15)
    async with async_session() as session:
        query = select(ProactiveFollowUp).where(
            or_(
                ProactiveFollowUp.status == "pending",
                (ProactiveFollowUp.status == "processing")
                & (ProactiveFollowUp.updated_at <= stale_before),
            )
        )
        if not force:
            query = query.where(ProactiveFollowUp.due_at <= now)
        if follow_up_id is not None:
            query = query.where(ProactiveFollowUp.id == follow_up_id)
        result = await session.execute(
            query.order_by(ProactiveFollowUp.due_at, ProactiveFollowUp.id).limit(1)
        )
        item = result.scalar_one_or_none()
        if item is None:
            return None
        item.status = "processing"
        item.updated_at = now
        await session.commit()
        return {
            "id": item.id,
            "chat_id": item.chat_id,
            "topic": item.purpose,
            "message": item.message or item.purpose,
        }


async def finish_follow_up(follow_up_id: int, status: str) -> None:
    async with async_session() as session:
        item = await session.get(ProactiveFollowUp, follow_up_id)
        if item is None:
            return
        item.status = status
        item.updated_at = datetime.now(timezone.utc)
        await session.commit()


async def list_follow_ups(limit: int = 20) -> list[ProactiveFollowUp]:
    async with async_session() as session:
        result = await session.execute(
            select(ProactiveFollowUp)
            .where(ProactiveFollowUp.status.in_(("pending", "processing", "draft")))
            .order_by(ProactiveFollowUp.due_at, ProactiveFollowUp.id)
            .limit(limit)
        )
        return list(result.scalars().all())


async def get_active_follow_ups(chat_id: int, limit: int = 10) -> list[dict]:
    async with async_session() as session:
        result = await session.execute(
            select(ProactiveFollowUp)
            .where(
                ProactiveFollowUp.chat_id == chat_id,
                ProactiveFollowUp.status.in_(("pending", "processing")),
            )
            .order_by(ProactiveFollowUp.due_at, ProactiveFollowUp.id)
            .limit(limit)
        )
        return [
            {
                "id": item.id,
                "topic": item.purpose,
                "message": item.message or item.purpose,
                "due_at": item.due_at,
            }
            for item in result.scalars().all()
        ]


async def update_follow_up(
    follow_up_id: int,
    chat_id: int,
    topic: str | None = None,
    message: str | None = None,
    due_at: datetime | None = None,
) -> str:
    if topic is None and message is None and due_at is None:
        return "Provide at least one field to update."
    if topic is not None and (not topic.strip() or len(topic.strip()) > 500):
        return "The topic must be 1-500 characters."
    if message is not None and (not message.strip() or len(message.strip()) > 1200):
        return "The message must be 1-1200 characters."
    if due_at is not None:
        due_at = _as_utc(due_at)
        now = datetime.now(timezone.utc)
        if due_at < now + timedelta(minutes=1):
            return "Schedule it at least one minute in the future."
        if due_at > now + timedelta(days=90):
            return "Schedule it within the next 90 days."

    async with async_session() as session:
        item = await session.get(ProactiveFollowUp, follow_up_id)
        if item is None or item.chat_id != chat_id or item.status != "pending":
            return "No editable follow-up with that ID in this chat."
        old_message = item.message or item.purpose
        if item.message is None:
            item.message = old_message
        if topic is not None:
            item.purpose = topic.strip()
        if message is not None:
            item.message = message.strip()
        if due_at is not None:
            item.due_at = due_at
        item.updated_at = datetime.now(timezone.utc)
        await session.commit()
        return f"Follow-up {item.id} updated."


async def get_follow_up(follow_up_id: int) -> ProactiveFollowUp | None:
    async with async_session() as session:
        return await session.get(ProactiveFollowUp, follow_up_id)


async def delete_follow_up(follow_up_id: int, chat_id: int | None = None) -> bool:
    async with async_session() as session:
        item = await session.get(ProactiveFollowUp, follow_up_id)
        if (
            item is None
            or item.status not in {"pending", "draft"}
            or (chat_id is not None and item.chat_id != chat_id)
        ):
            return False
        item.status = "cancelled"
        item.updated_at = datetime.now(timezone.utc)
        await session.commit()
        return True


async def make_follow_up_due_now(follow_up_id: int) -> bool:
    now = datetime.now(timezone.utc)
    async with async_session() as session:
        result = await session.execute(
            update(ProactiveFollowUp)
            .where(
                ProactiveFollowUp.id == follow_up_id,
                ProactiveFollowUp.status.in_(("pending", "draft")),
            )
            .values(status="pending", due_at=now, updated_at=now)
        )
        await session.commit()
        return bool(result.rowcount)


async def get_spontaneous_candidates(
    owner_id: int,
    now: datetime | None = None,
    excluded_chat_ids: set[int] | None = None,
) -> list[int]:
    now = now or datetime.now(timezone.utc)
    idle_before = now - timedelta(minutes=30)
    excluded_chat_ids = excluded_chat_ids or set()
    async with async_session() as session:
        result = await session.execute(
            select(Message.chat_id, func.max(Message.timestamp))
            .where(Message.chat_id > 0, Message.chat_id != owner_id)
            .group_by(Message.chat_id)
        )
        latest_by_chat = result.all()
        candidates = []
        for chat_id, last_message_at in latest_by_chat:
            if chat_id in excluded_chat_ids or _as_utc(last_message_at) > idle_before:
                continue

            settings = await session.get(ProactiveSettings, chat_id)
            if settings and settings.spontaneous_after:
                if _as_utc(settings.spontaneous_after) > now:
                    continue

            active_follow_up = await session.execute(
                select(ProactiveFollowUp.id)
                .where(
                    ProactiveFollowUp.chat_id == chat_id,
                    ProactiveFollowUp.status.in_(("pending", "processing")),
                )
                .limit(1)
            )
            if active_follow_up.scalar_one_or_none() is not None:
                continue
            candidates.append(chat_id)
        return candidates


async def set_spontaneous_cooldown(chat_id: int, duration: timedelta) -> None:
    now = datetime.now(timezone.utc)
    async with async_session() as session:
        settings = await session.get(ProactiveSettings, chat_id)
        if settings is None:
            settings = ProactiveSettings(chat_id=chat_id)
            session.add(settings)
        settings.spontaneous_after = now + duration
        settings.updated_at = now
        await session.commit()
