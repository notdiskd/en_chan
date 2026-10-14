from commands.registry import command
from sqlalchemy import delete
from storage.db import (
    ChatSummary,
    Base,
    DayState,
    EnSelfModel,
    MemoryEntry,
    Message,
    ProactiveFollowUp,
    ProactiveSettings,
    SharedPersonFact,
    UserProfile,
    async_session,
)
import inspect
from datetime import datetime, timezone
from storage.proactive import (
    delete_follow_up,
    get_follow_up,
    list_follow_ups,
    make_follow_up_due_now,
)


@command()
async def help(bot, args: str) -> str:
    """Show all available owner commands and their descriptions."""
    lines = []
    for name, handler in sorted(bot.commands.items()):
        description = (inspect.getdoc(handler) or "No description.").splitlines()[0]
        lines.append(f".{name} — {description}")
    return "Available commands:\n" + "\n".join(lines)

@command()
async def reflect(bot, args: str) -> str:
    """Force-run today's diary reflection right now, without waiting for the schedule."""
    summaries = await bot.get_today_summaries()
    await bot.agent.memory.run_daily_reflection(summaries)
    return "Reflection created."

@command()
async def summaries(bot, args: str) -> str:
    """Show today's collected conversation summaries."""
    text = await bot.get_today_summaries()
    return text[:1024] + "..." if len(text) > 1024 else text

@command()
async def search(bot, args: str) -> str:
    """Search the memory archive. Usage: .search <query> [type]"""
    parts = args.rsplit(maxsplit=1)
    if len(parts) == 2 and parts[1] in ("diary", "episode"):
        query, mem_type = parts
    else:
        query, mem_type = args, None

    results = await bot.agent.memory.search(query, type=mem_type, top_k=5)
    shared_results = await bot.agent.memory.search_shared_person_facts(query, top_k=5)
    lines = [f"[{e.type}, {e.created_at.date()}] ({score:.2f}) {e.text}" for score, e in results]
    if mem_type is None:
        for score, fact in shared_results:
            if score < 0.55:
                continue
            source = fact.source_name or "user " + str(fact.source_user_id)
            lines.append(
                f"[shared fact about {fact.subject}; {fact.category}; told by "
                f"{source}; {fact.created_at.date()}] ({score:.2f}) {fact.fact}"
            )
    if not lines:
        return "Nothing found."
    return "\n---\n".join(lines)

@command()
async def core(bot, args: str) -> str:
    """Show En's current short-term day state."""
    content = await bot.agent.memory.get_day_state()
    return content if content else "(empty)"

@command(name="clear")
async def clear_table(bot, args: str) -> str:
    """Clear stored data. Usage: .clear messages / memory / core / personality / all"""
    target = args.strip().lower()
    if target not in {"messages", "memory", "core", "personality", "all"}:
        return "Usage: .clear messages / memory / core / personality / all"

    delete_models = {
        "messages": (Message, ChatSummary, ProactiveFollowUp, ProactiveSettings),
        "memory": (MemoryEntry, SharedPersonFact, UserProfile),
        "personality": (),
        "core": (),
    }.get(target, ())

    async with async_session() as session:
        if target == "all":
            for table in reversed(Base.metadata.sorted_tables):
                await session.execute(delete(table))
        else:
            for model in delete_models:
                await session.execute(delete(model))

        if target == "core":
            day_state = await session.get(DayState, 1)
            if day_state:
                day_state.content = ""
                day_state.state_date = None
                day_state.updated_at = datetime.now(timezone.utc)

        if target in {"personality", "core"}:
            self_model = await session.get(EnSelfModel, 1)
            if self_model:
                self_model.content = ""
                self_model.updated_at = datetime.now(timezone.utc)

        await session.commit()

    descriptions = {
        "messages": "messages, chat summaries, follow-ups, and proactive cooldowns",
        "memory": "diary, episodes, shared-person facts, and user profiles",
        "core": "En's day state and evolving self-model",
        "personality": "En's evolving self-model",
        "all": "all rows in every registered database table",
    }
    return f"Cleared {descriptions[target]}."


@command()
async def proactive(bot, args: str) -> str:
    """Manage follow-ups or force a spontaneous message to a private chat."""
    parts = args.split()
    if not parts or parts[0].lower() == "help":
        return (
            "Usage: .proactive list, .proactive delete <id>, "
            ".proactive run <id> (sends now), "
            ".proactive spontaneous <private_chat_id> (ask En to message now). "
            "Times use local time."
        )

    action = parts[0].lower()
    if action in {"spontaneous", "write"}:
        if len(parts) != 2 or not parts[1].isdigit() or int(parts[1]) <= 0:
            return "Usage: .proactive spontaneous <private_chat_id>"
        return await bot._process_spontaneous_message(int(parts[1]))

    if action in {"list", "pending"}:
        items = await list_follow_ups()
        if not items:
            return "No scheduled follow-ups."
        lines = []
        for item in items:
            due_at = item.due_at
            if due_at.tzinfo is None:
                due_at = due_at.replace(tzinfo=timezone.utc)
            local_time = due_at.astimezone().strftime("%Y-%m-%d %H:%M local")
            message = item.message or item.purpose
            lines.append(
                f"{item.id} → chat {item.chat_id} at {local_time} "
                f"[{item.status}]\nTopic: {item.purpose}\nMessage: {message}"
            )
        return "\n\n".join(lines)

    if action in {"delete", "cancel"}:
        if len(parts) != 2 or not parts[1].isdigit():
            return "Provide a follow-up ID to delete."
        return "Follow-up deleted." if await delete_follow_up(int(parts[1])) else "Follow-up not found or already running."

    if action == "run":
        if len(parts) != 2 or not parts[1].isdigit():
            return "Provide a follow-up ID to send now."
        follow_up_id = int(parts[1])
        if not await make_follow_up_due_now(follow_up_id):
            return "Follow-up not found or already running."
        await bot._process_due_follow_up(
            follow_up_id=follow_up_id,
            force=True,
        )
        item = await get_follow_up(follow_up_id)
        if item is None:
            return "Follow-up no longer exists."
        if item.status == "sent":
            return f"Follow-up {follow_up_id} was sent to chat {item.chat_id}."
        if item.status == "skipped":
            return f"Follow-up {follow_up_id} was skipped because the model judged it unnecessary or already resolved."
        if item.status == "failed":
            return f"Follow-up {follow_up_id} failed. Check the application log for the error."
        return f"Follow-up {follow_up_id} is now in status: {item.status}."

    return "Unknown action. Use .proactive help for options."
