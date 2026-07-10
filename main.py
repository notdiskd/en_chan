import asyncio
from core.ai import LLMClient, AudioClient
from core.agent import Agent
from core.telegram import UserBot

async def main():
    llm = LLMClient()
    audio = AudioClient()
    agent = Agent(llm, audio)
    bot = UserBot(agent=agent)
    await bot.start()

if __name__ == "__main__":
    asyncio.run(main())