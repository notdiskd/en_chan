import asyncio
import datetime
from config import diary
from core.ai import LLMClient
from tools.diary_tools import write_diary_entry
from tools.tool_schema import function_to_tool
import json

async def run_daily_reflection(llm: LLMClient, day_summaries: str):
    if not day_summaries.strip():
        return

    tools = [function_to_tool(write_diary_entry)]

    response = await llm.tool_chat(
        messages=[{"role": "user", "content": diary["reflection_prompt"].format(summaries=day_summaries)}],
        tools=tools
    )

    for call in response.tool_calls:
        if call.function.name == "write_diary_entry":
            args = json.loads(call.function.arguments)
            await write_diary_entry(**args)

async def schedule_daily_reflection(llm: LLMClient, telegram_bot, hour: int = 23):
    while True:
        now = datetime.datetime.now()
        next_run = now.replace(hour=hour, minute=0, second=0, microsecond=0)
        if next_run <= now:
            next_run += datetime.timedelta(days=1)

        await asyncio.sleep((next_run - now).total_seconds())

        summaries = await telegram_bot.get_today_summaries()
        await run_daily_reflection(llm, summaries)