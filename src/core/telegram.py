import asyncio
from telethon import TelegramClient, events
from commands.registry import get_registered_commands
import commands.definitions
from config import telegram
from core.agent import Agent
from collections import defaultdict
from datetime import datetime, timezone

class UserBot:
    def __init__(self, agent: Agent):
        self.client: TelegramClient = TelegramClient('account', telegram["app_id"], telegram["api_hash"], system_version="4.16.30-vxCUSTOM")
        self.agent = agent
        self.agent.telegram_client = self.client
        self.on_message = agent.handle_message
        self._album_buffer: dict[int, list] = defaultdict(list)
        self._album_tasks: dict[int, asyncio.Task] = {}
        self.commands = get_registered_commands()

    def register_command(self, name: str, handler):
        self.commands[name] = handler

    async def _handle_command(self, event):
        if event.chat_id != self.my_id:
            return

        text = event.raw_text.strip()
        if not text.startswith("."):
            return

        parts = text[1:].split(maxsplit=1)
        cmd_name = parts[0]
        args = parts[1] if len(parts) > 1 else ""

        handler = self.commands.get(cmd_name)
        if handler is None:
            await event.reply(f"Unknown command: {cmd_name}")
            return

        await event.reply(f"Running: {cmd_name}...")
        try:
            result = await handler(self, args)
            await event.reply(f"Done: {result}" if result else "Done.")
        except Exception as e:
            await event.reply(f"Error: {e}")

    async def start(self):
        await self.client.start()
        await self.client.get_dialogs()

        me = await self.client.get_me()
        self.my_id = me.id

        self.client.add_event_handler(self._handle_new_message, events.NewMessage(incoming=True))
        self.client.add_event_handler(self._handle_command, events.NewMessage(outgoing=True))

        print("Userbot запущен")
        await self.client.run_until_disconnected()

    async def get_recent_messages(self, chat, limit: int = 30) -> list[dict]:
        messages = await self.client.get_messages(chat, limit=limit)

        formatted = []
        for msg in reversed(messages):
            has_attachment = msg.photo or msg.voice
            if not msg.raw_text and not has_attachment:
                continue

            text = ("[Voice] " if msg.voice else "") + ("[Image] " if msg.photo else "") + msg.raw_text

            role = "assistant" if msg.out else "user"
            formatted.append({"role": role, "content": text})

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

        await self.agent.handle_message(messages, user, text=text, images=images)
    
    async def get_today_summaries(self) -> str:
        today = datetime.now(timezone.utc).date()
        summaries = []

        async for dialog in self.client.iter_dialogs():
            if not dialog.is_user or dialog.id == self.my_id:
                continue

            messages = await self.client.get_messages(dialog.id, limit=50)

            today_messages = []
            for msg in reversed(messages):
                if not msg.date:
                    continue
                if msg.date.date() != today:
                    continue
                if not msg.raw_text:
                    continue

                role = "En" if msg.out else (dialog.name or "user")
                today_messages.append(f"{role}: {msg.raw_text}")

            if today_messages:
                summaries.append(f"--- Chat with {dialog.name} ---\n" + "\n".join(today_messages))

        return "\n\n".join(summaries)