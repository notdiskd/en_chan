import asyncio
from core.ai import LLMClient, AudioClient
from core.agent import Agent
from core.telegram import UserBot
from storage.db import init_db
from scheduler.daily_reflection import schedule_daily_reflection, run_daily_reflection
from storage.diary import search_diary

async def main():
    await init_db()

    llm = LLMClient()
    audio = AudioClient()
    agent = Agent(llm, audio)
    bot = UserBot(agent=agent)

    asyncio.create_task(schedule_daily_reflection(llm, bot))

    await bot.start()

if __name__ == "__main__":
    asyncio.run(main())