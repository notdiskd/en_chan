from datetime import datetime, timezone

from storage.proactive import (
    create_follow_up,
    delete_follow_up as delete_follow_up_record,
    get_active_follow_ups,
    update_follow_up as update_follow_up_record,
)


def _parse_local_time(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _format_local_time(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone().strftime("%Y-%m-%d %H:%M local")


def make_proactive_tools(chat_id: int | None):
    if chat_id is None:
        return ()

    async def schedule_follow_up(topic: str, message: str, due_at: str) -> str:
        """Schedule a message and the reason it matters for this user.

        Use when En has promised to return to a topic or the user asked for a follow-up.
        The exact message is sent at the chosen local time.

        Args:
            topic: The reason or subject of the reminder, for example waiting for exam results.
            message: The exact text to send when the reminder is due.
            due_at: Local date and time in ISO format, such as 2026-10-20T18:00.
        """
        parsed_due_at = _parse_local_time(due_at)
        if parsed_due_at is None:
            return "Invalid due_at. Use local ISO format YYYY-MM-DDTHH:MM."
        return await create_follow_up(chat_id, topic, message, parsed_due_at)

    async def list_follow_ups() -> str:
        """List active follow-ups in this private chat, including topic, message, and time."""
        items = await get_active_follow_ups(chat_id)
        if not items:
            return "There are no active follow-ups in this chat."
        return "\n\n".join(
            f"ID {item['id']} — {item['topic']}\n"
            f"Send: {item['message']}\n"
            f"Time: {_format_local_time(item['due_at'])}"
            for item in items
        )

    async def update_follow_up(
        follow_up_id: int,
        topic: str | None = None,
        message: str | None = None,
        due_at: str | None = None,
    ) -> str:
        """Change the topic, outgoing text, or time of an active follow-up in this chat.

        Use when new conversation context clearly changes an existing commitment.
        Keep its original goal unless the user or new facts clearly change it.
        Args:
            follow_up_id: ID from the active follow-up list.
            topic: New reason or subject; omit to keep the current topic.
            message: New exact outgoing text; omit to keep the current message.
            due_at: New local ISO date and time; omit to keep the current time.
        """
        parsed_due_at = None
        if due_at is not None:
            parsed_due_at = _parse_local_time(due_at)
            if parsed_due_at is None:
                return "Invalid due_at. Use local ISO format YYYY-MM-DDTHH:MM."
        return await update_follow_up_record(
            follow_up_id,
            chat_id,
            topic=topic,
            message=message,
            due_at=parsed_due_at,
        )

    async def delete_follow_up(follow_up_id: int) -> str:
        """Delete an active follow-up in this chat when its commitment is clearly cancelled or resolved."""
        deleted = await delete_follow_up_record(follow_up_id, chat_id=chat_id)
        return (
            "Follow-up deleted."
            if deleted
            else "No deletable follow-up with that ID in this chat."
        )

    return (
        schedule_follow_up,
        list_follow_ups,
        update_follow_up,
        delete_follow_up,
    )
