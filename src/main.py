import asyncio
from core.ai import OpenRouterClient, FishAudioClient
from core.agent import Agent
from core.telegram import UserBot
from storage.memory import Memory
from storage.db import init_db

async def main():
    await init_db()

    llm = OpenRouterClient()
    audio = FishAudioClient()
    
    memory = Memory(llm)

    agent = Agent(llm, audio, memory)
    bot = UserBot(agent, llm)

    asyncio.create_task(memory.schedule_daily_reflection(get_day_summaries_fn=bot.get_today_summaries))

    await bot.start()

if __name__ == "__main__":
    asyncio.run(main())