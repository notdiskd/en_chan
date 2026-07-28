from commands.registry import command
from sqlalchemy import delete
from storage.db import Message, MemoryEntry, CoreMemory, Reminder, async_session

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
    if len(parts) == 2 and parts[1] in ("reflection", "fact", "event"):
        query, mem_type = parts
    else:
        query, mem_type = args, None

    results = await bot.agent.memory.search(query, type=mem_type, top_k=5)
    if not results:
        return "Nothing found."
    return "\n---\n".join(f"[{e.type}, {e.created_at.date()}] ({score:.2f}) {e.text}" for score, e in results)

@command()
async def core(bot, args: str) -> str:
    """Show current core memory content."""
    content = await bot.agent.memory.get_core()
    return content if content else "(empty)"

@command(name="clear")
async def clear_table(bot, args: str) -> str:
    """Clear a table. Usage: .clear messages / .clear memory / .clear reminders / .clear core / .clear all"""
    tables = {"messages": Message, "memory": MemoryEntry, "reminders": Reminder}

    if args.strip() == "core":
        async with async_session() as session:
            row = await session.get(CoreMemory, 1)
            row.content = ""
            await session.commit()
        return "Cleared: core memory"

    targets = tables.items() if args.strip() == "all" else [(args.strip(), tables[args.strip()])]

    async with async_session() as session:
        for name, model in targets:
            await session.execute(delete(model))
        await session.commit()

    return f"Cleared: {args}"