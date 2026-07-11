import asyncio
import datetime
from config import diary
from storage.diary import save_diary_entry
from core.ai import LLMClient

async def run_daily_reflection(llm: LLMClient, day_summaries: str):
    if not day_summaries.strip():
        return

    response = await llm.chat(
        messages=[{"role": "user", "content": diary["reflection_prompt"].format(summaries=day_summaries)}],
    )
    entry_text = response.content

    await save_diary_entry(text=entry_text, mood="neutral")

async def schedule_daily_reflection(llm: LLMClient, telegram_bot, hour: int = 23):
    while True:
        now = datetime.datetime.now()
        next_run = now.replace(hour=hour, minute=0, second=0, microsecond=0)
        if next_run <= now:
            next_run += datetime.timedelta(days=1)

        await asyncio.sleep((next_run - now).total_seconds())

        summaries = await telegram_bot.get_today_summaries()
        await run_daily_reflection(llm, summaries)