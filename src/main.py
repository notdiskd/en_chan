import asyncio
from core.ai import OpenRouterClient, FishAudioClient
from core.agent import Agent
from core.telegram import UserBot
from storage.diary import Diary
from storage.db import init_db

async def main():
    await init_db()

    llm = OpenRouterClient()
    audio = FishAudioClient()

    diary = Diary(llm)
    
    agent = Agent(llm, audio, diary)
    bot = UserBot(agent, llm)

    asyncio.create_task(diary.schedule_daily_reflection(bot))

    await bot.start()

if __name__ == "__main__":
    asyncio.run(main())