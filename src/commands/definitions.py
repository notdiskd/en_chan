from commands.registry import command
from scheduler.daily_reflection import run_daily_reflection
from storage.diary import search_diary

@command()
async def reflect(bot, args: str) -> str:
    summaries = await bot.get_today_summaries()
    await run_daily_reflection(bot.agent.llm, summaries)
    return "Diary entry created."

@command()
async def summaries(bot, args: str) -> str:
    text = await bot.get_today_summaries()
    return text[:1024] + "..." if len(text) > 1024 else text

@command()
async def diary(bot, args: str) -> str:
    return await search_diary(query=args, top_k=3)