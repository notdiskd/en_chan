from commands.registry import command
from sqlalchemy import delete
from storage.db import Message, DiaryEntry, async_session

@command()
async def reflect(bot, args: str) -> str:
    summaries = await bot.get_today_summaries()
    await bot.agent.diary.run_daily_reflection(summaries)
    return "Diary entry created."

@command()
async def summaries(bot, args: str) -> str:
    text = await bot.get_today_summaries()
    return text[:1024] + "..." if len(text) > 1024 else text

@command()
async def diary(bot, args: str) -> str:
    return await bot.agent.diary.search_diary(query=args, top_k=3)

@command(name="clear")
async def clear_table(bot, args: str) -> str:
    tables = {"messages": Message, "diary": DiaryEntry}
    targets = tables.items() if args.strip() == "all" else [(args.strip(), tables[args.strip()])]

    async with async_session() as session:
        for name, model in targets:
            await session.execute(delete(model))
        await session.commit()

    return f"Cleared: {args}"