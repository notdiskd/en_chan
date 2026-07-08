import asyncio
from core.llm import LLMClient
from core.agent import Agent
from core.telegram import UserBot

async def main():
    llm = LLMClient()
    agent = Agent(llm)

    bot = UserBot(on_message=agent.handle_message)
    await bot.start()

if __name__ == "__main__":
    asyncio.run(main())