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

    async def cmd_reflect(args: str) -> str:
        summaries = await bot.get_today_summaries()
        await run_daily_reflection(llm, summaries)
        return "Diary entry created."

    async def cmd_summaries(args: str) -> str:
        summaries = await bot.get_today_summaries()
        return summaries[:1024] + "..." if len(summaries) > 1024 else summaries
    
    async def cmd_diary(args: str) -> str:
        return await search_diary(query=args, top_k=3)

    bot.register_command("reflect", cmd_reflect)
    bot.register_command("summaries", cmd_summaries)
    bot.register_command("diary", cmd_diary)

    asyncio.create_task(schedule_daily_reflection(llm, bot))

    await bot.start()

if __name__ == "__main__":
    asyncio.run(main())