from commands.registry import command
from sqlalchemy import delete
from storage.db import DayState, Message, MemoryEntry, SharedPersonFact, async_session
import inspect


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
    """Clear a table. Usage: .clear messages / .clear memory / .clear core / .clear all"""
    tables = {"messages": Message, "memory": MemoryEntry}

    if args.strip() == "core":
        async with async_session() as session:
            row = await session.get(DayState, 1)
            row.content = ""
            row.state_date = None
            await session.commit()
        return "Cleared: core memory"

    targets = tables.items() if args.strip() == "all" else [(args.strip(), tables[args.strip()])]

    async with async_session() as session:
        for name, model in targets:
            await session.execute(delete(model))
        if args.strip() in {"memory", "all"}:
            await session.execute(delete(SharedPersonFact))
        await session.commit()

    return f"Cleared: {args}"
