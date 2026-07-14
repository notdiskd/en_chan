from sqlalchemy import select
from storage.db import Message, async_session
from datetime import datetime, timezone, timedelta

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

        formatted.append({
            "role": msg.role,
            "content": f"#{local_id} {time_note}{reply_note}{attachment_note} {msg.text}".strip()
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