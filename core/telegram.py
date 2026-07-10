import asyncio
from numpy import clip
from telethon import TelegramClient, events
from config import telegram
from core.agent import Agent
from collections import defaultdict

class UserBot:
    def __init__(self, agent: Agent):
        self.client: TelegramClient = TelegramClient('account', telegram["app_id"], telegram["api_hash"], system_version="4.16.30-vxCUSTOM")
        self.agent = agent
        self.agent.telegram_client = self.client
        self.on_message = agent.handle_message
        self._album_buffer: dict[int, list] = defaultdict(list)
        self._album_tasks: dict[int, asyncio.Task] = {}

    async def start(self):
        await self.client.start()
        await self.client.get_dialogs()
        self.client.add_event_handler(
            self._handle_new_message,
            events.NewMessage(incoming=True)
        )
        print("Userbot запущен")
        await self.client.run_until_disconnected()

    async def get_recent_messages(self, chat, limit: int = 20) -> list[dict]:
        messages = await self.client.get_messages(chat, limit=limit)

        formatted = []
        for msg in reversed(messages):
            if not msg.text:
                continue

            role = "assistant" if msg.out else "user"
            formatted.append({"role": role, "content": msg.text})

        return formatted[:-1]
    
    async def _handle_new_message(self, event):
        msg = event.message

        if msg.grouped_id:
            await self._handle_album_message(event)
        else:
            images = []
            if msg.photo:
                img_bytes = await self.client.download_media(msg, file=bytes)
                images.append(img_bytes)

            await self._process_message(event, images=images, text=msg.raw_text or "")

    async def _handle_album_message(self, event):
        msg = event.message
        group_id = msg.grouped_id

        self._album_buffer[group_id].append(event)

        if group_id in self._album_tasks:
            return

        self._album_tasks[group_id] = asyncio.create_task(
            self._flush_album_after_delay(group_id)
        )

    async def _flush_album_after_delay(self, group_id: int, delay: float = 1.5):
        await asyncio.sleep(delay)

        events_batch = self._album_buffer.pop(group_id, [])
        self._album_tasks.pop(group_id, None)

        if not events_batch:
            return

        images = []
        for ev in events_batch:
            if ev.message.photo:
                img_bytes = await self.client.download_media(ev.message, file=bytes)
                images.append(img_bytes)

        text = next((ev.raw_text for ev in events_batch if ev.raw_text), "")
        last_event = events_batch[-1]

        await self._process_message(last_event, images=images, text=text)

    async def _process_message(self, event, images: list[bytes], text: str):
        chat = await event.get_input_chat()
        user = await event.get_sender()

        await self.client.send_read_acknowledge(chat)

        messages = await self.get_recent_messages(chat)

        async with self.client.action(chat, "typing"):
            reply_text = await self.agent.handle_message(messages, user, text=text, images=images)

        if reply_text:
            await asyncio.sleep(clip(len(reply_text) / 100, 0.3, 5))
            await event.reply(reply_text)