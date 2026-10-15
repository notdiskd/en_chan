from sqlalchemy import select
from storage.db import ChatSummary, Message, async_session
from datetime import datetime, timezone, timedelta

MAX_MESSAGE_CONTEXT_CHARS = 12_000

async def save_message(
    chat_id: int,
    role: str,
    text: str = "",
    tg_message_id: int | None = None,
    attachment_type: str | None = None,
    attachment_summary: str | None = None,
    reply_to_tg_id: int | None = None,
    timestamp: datetime | None = None,
) -> None:
    async with async_session() as session:
        msg = Message(
            chat_id=chat_id,
            role=role,
            text=text,
            tg_message_id=tg_message_id,
            attachment_type=attachment_type,
            attachment_summary=attachment_summary,
            reply_to_tg_id=reply_to_tg_id,
            timestamp=timestamp or datetime.now(timezone.utc)
        )
        session.add(msg)
        await session.commit()


async def save_reaction_event(
    chat_id: int,
    tg_message_id: int,
    text: str,
    timestamp: datetime | None = None,
) -> bool:
    """Persist one distinct reaction event, linked to the reacted-to message."""
    event_time = timestamp or datetime.now(timezone.utc)
    async with async_session() as session:
        existing = await session.execute(
            select(Message.id).where(
                Message.chat_id == chat_id,
                Message.role == "system",
                Message.reply_to_tg_id == tg_message_id,
                Message.text == text,
                Message.timestamp == event_time,
            ).limit(1)
        )
        if existing.scalar_one_or_none() is not None:
            return False
        session.add(Message(
            chat_id=chat_id,
            role="system",
            text=text,
            reply_to_tg_id=tg_message_id,
            timestamp=event_time,
        ))
        await session.commit()
        return True

async def get_recent_messages(chat_id: int, limit: int = 20) -> list[Message]:
    async with async_session() as session:
        result = await session.execute(
            select(Message)
            .where(Message.chat_id == chat_id)
            .order_by(Message.timestamp.desc())
            .limit(limit)
        )
        messages = result.scalars().all()
    return list(reversed(messages))


async def get_message_by_telegram_id(chat_id: int, tg_message_id: int) -> Message | None:
    async with async_session() as session:
        result = await session.execute(
            select(Message).where(
                Message.chat_id == chat_id,
                Message.tg_message_id == tg_message_id,
            ).limit(1)
        )
        return result.scalar_one_or_none()


async def get_message_by_telegram_id_anywhere(tg_message_id: int) -> Message | None:
    """Private-chat message IDs are unique across this account's common ID sequence."""
    async with async_session() as session:
        result = await session.execute(
            select(Message).where(Message.tg_message_id == tg_message_id).limit(1)
        )
        return result.scalar_one_or_none()


async def get_messages_by_telegram_ids(
    chat_id: int, tg_message_ids: list[int]
) -> list[Message]:
    if not tg_message_ids:
        return []
    async with async_session() as session:
        result = await session.execute(
            select(Message).where(
                Message.chat_id == chat_id,
                Message.tg_message_id.in_(tg_message_ids),
            )
        )
        return list(result.scalars().all())


async def _invalidate_summary_if_needed(session, chat_id: int, message: Message) -> None:
    summary = await session.get(ChatSummary, chat_id)
    if summary and message.id <= summary.summarized_through_id:
        summary.summarized_through_id = 0
        summary.content = ""
        summary.updated_at = datetime.now(timezone.utc)


async def record_message_edit(
    chat_id: int,
    tg_message_id: int,
    new_text: str,
    edited_at: datetime | None = None,
) -> bool:
    async with async_session() as session:
        result = await session.execute(
            select(Message).where(
                Message.chat_id == chat_id,
                Message.tg_message_id == tg_message_id,
            ).limit(1)
        )
        message = result.scalar_one_or_none()
        if message is None or message.is_deleted or (message.text or "") == new_text:
            return False

        old_text = message.text or ""
        author = "you" if message.role == "assistant" else "the user"
        event_text = (
            f"Telegram edit event: {author} edited a message. "
            f"Previous text: {old_text[:240]!r}; current text: {new_text[:240]!r}. "
            "Consider the change; reply only if natural."
        )
        message.text = new_text
        await _invalidate_summary_if_needed(session, chat_id, message)
        session.add(Message(
            chat_id=chat_id,
            role="system",
            text=event_text,
            reply_to_tg_id=tg_message_id,
            timestamp=edited_at or datetime.now(timezone.utc),
        ))
        await session.commit()
        return True


async def record_message_deletion(
    chat_id: int,
    tg_message_id: int,
    deleted_at: datetime | None = None,
    initiated_by: str = "user",
) -> str | None:
    async with async_session() as session:
        result = await session.execute(
            select(Message).where(
                Message.chat_id == chat_id,
                Message.tg_message_id == tg_message_id,
            ).limit(1)
        )
        message = result.scalar_one_or_none()
        if message is None or message.is_deleted:
            return None

        author = "your" if message.role == "assistant" else "the user's"
        initiator = "En" if initiated_by == "assistant" else "the user"
        event_text = (
            f"Telegram reports that {author} message was deleted. "
            f"The recorded initiator is {initiator}. "
            f"The message had been saved in the conversation history: {(message.text or '[no text]')[:240]!r}. "
            "Consider whether a response feels natural."
        )
        message.is_deleted = True
        message.deleted_by = initiated_by
        await _invalidate_summary_if_needed(session, chat_id, message)
        session.add(Message(
            chat_id=chat_id,
            role="system",
            text=event_text,
            reply_to_tg_id=tg_message_id,
            timestamp=deleted_at or datetime.now(timezone.utc),
        ))
        await session.commit()
        return message.role


async def get_chat_summary(chat_id: int) -> ChatSummary | None:
    async with async_session() as session:
        return await session.get(ChatSummary, chat_id)


async def get_messages_after(chat_id: int, message_id: int) -> list[Message]:
    async with async_session() as session:
        result = await session.execute(
            select(Message)
            .where(Message.chat_id == chat_id, Message.id > message_id)
            .order_by(Message.id.asc())
        )
        return list(result.scalars().all())


async def save_chat_summary(chat_id: int, through_id: int, content: str) -> None:
    async with async_session() as session:
        summary = await session.get(ChatSummary, chat_id)
        if summary is None:
            summary = ChatSummary(chat_id=chat_id)
            session.add(summary)
        summary.summarized_through_id = through_id
        summary.content = content
        summary.updated_at = datetime.now(timezone.utc)
        await session.commit()

def format_for_llm(messages: list[Message]) -> tuple[list[dict], dict[int, int]]:
    formatted = []
    id_map: dict[int, int] = {}          # local_id -> tg_message_id
    reverse_map: dict[int, int] = {}     # tg_message_id -> local_id

    now = datetime.now(timezone.utc)

    for local_id, msg in enumerate(messages, start=1):
        ts = msg.timestamp
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        time_note = _format_relative_time(now - ts)

        # A system-role record without a Telegram ID is an internal event, not a
        # message the model can reply to or target with Telegram tools.
        if msg.role == "system" and msg.tg_message_id is None:
            target_number = reverse_map.get(msg.reply_to_tg_id)
            target_note = f" (about message #{target_number})" if target_number else ""
            formatted.append({
                "role": "system",
                "content": f"{time_note} Telegram event{target_note}: {msg.text}",
                "attachment_summary": None,
            })
            continue

        if msg.is_deleted:
            if msg.tg_message_id:
                reverse_map[msg.tg_message_id] = local_id
            deleted_content = msg.text or "[no text]"
            if msg.attachment_type in ("image", "album") and msg.attachment_summary:
                deleted_content += f" [image: {msg.attachment_summary}]"
            elif msg.attachment_type == "voice":
                deleted_content += " [voice message]"
            formatted.append({
                "role": "system",
                "content": (
                    f"#{local_id} {time_note} [Telegram message deleted; "
                    f"original author: {msg.role}; previously seen content: {deleted_content}]"
                ),
                "attachment_summary": None,
            })
            continue

        id_map[local_id] = msg.tg_message_id
        if msg.tg_message_id:
            reverse_map[msg.tg_message_id] = local_id

        reply_note = ""
        if msg.reply_to_tg_id and msg.reply_to_tg_id in reverse_map:
            reply_note = f" (replying to #{reverse_map[msg.reply_to_tg_id]})"

        attachment_note = ""
        if msg.attachment_type in ("image", "album"):
            attachment_note = f" [image: {msg.attachment_summary}]"
        elif msg.attachment_type == "voice":
            attachment_note = " [voice message]"
        text = msg.text or ""
        if len(text) > MAX_MESSAGE_CONTEXT_CHARS:
            head_size = MAX_MESSAGE_CONTEXT_CHARS - 2_000
            text = text[:head_size] + "\n[middle omitted]\n" + text[-2_000:]

        formatted.append({
            "role": msg.role,
            "content": f"#{local_id} {time_note}{reply_note}{attachment_note} {text}".strip(),
            "attachment_summary": msg.attachment_summary,
        })

    return formatted, id_map

def _format_relative_time(delta: timedelta) -> str:
    seconds = delta.total_seconds()
    if seconds < 60:
        return "[just now]"
    if seconds < 3600:
        return f"[{int(seconds // 60)}m ago]"
    if seconds < 86400:
        return f"[{int(seconds // 3600)}h ago]"
    return f"[{int(seconds // 86400)}d ago]"
