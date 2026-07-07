import asyncio
from core.llm import LLMClient
from core.telegram import UserBot

async def main():
    llm = LLMClient()

    async def handle_message(text):
        return await llm.chat(text)

    bot = UserBot(on_message=handle_message)
    await bot.start()

if __name__ == "__main__":
    asyncio.run(main())