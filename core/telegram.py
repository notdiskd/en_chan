from telethon import TelegramClient, events
from config import app_id, api_hash
from typing import Callable, Awaitable

class UserBot:
    def __init__(self, on_message: Callable[[int, int, str], Awaitable[str]]):
        self.client: TelegramClient = TelegramClient('account', app_id, api_hash, system_version="4.16.30-vxCUSTOM")
        self.on_message = on_message

    async def start(self):
        await self.client.start()
        await self.client.get_dialogs()
        self.client.add_event_handler(
            self._handle_new_message,
            events.NewMessage(incoming=True)
        )
        print("Userbot запущен")
        await self.client.run_until_disconnected()

    async def get_recent_messages(self, chat, limit: int = 10) -> list[dict]:
        messages = await self.client.get_messages(chat, limit=limit)

        formatted = []
        for msg in reversed(messages):
            if not msg.text:
                continue

            role = "assistant" if msg.out else "user"
            formatted.append({"role": role, "content": msg.text})

        return formatted

    async def _handle_new_message(self, event):
        text = event.raw_text

        if not text:
            return
        
        chat = await event.get_input_chat()
        user = await event.get_sender()

        messages = await self.get_recent_messages(chat)

        async with self.client.action(chat, "typing"):
            reply_text = await self.on_message(messages, user)

        if reply_text:
            await event.reply(reply_text)