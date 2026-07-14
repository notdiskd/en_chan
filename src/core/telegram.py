import asyncio
from telethon import TelegramClient, events
from commands.registry import get_registered_commands
import commands.definitions
from config import telegram
from core.agent import Agent
from collections import defaultdict
from datetime import datetime, timezone
from storage.messages import save_message, get_recent_messages, format_for_llm
from core.ai import OpenRouterClient
import traceback

#тут поделено на секции
class UserBot:
    #инициализация
    def __init__(self, agent: Agent, llm: OpenRouterClient):
        self.client: TelegramClient = TelegramClient('account', telegram["app_id"], telegram["api_hash"], system_version="4.16.30-vxCUSTOM")
        self.agent = agent
        self.llm = llm
        self.agent.telegram_client = self.client
        self.commands = get_registered_commands()
        self._album_buffer: dict[int, list] = defaultdict(list)
        self._album_tasks: dict[int, asyncio.Task] = {}
        self._pending_ingests: dict[int, set[asyncio.Task]] = defaultdict(set)
        self._active_respond_tasks: dict[int, asyncio.Task] = {}

    async def start(self):
        await self.client.start()
        await self.client.get_dialogs()

        me = await self.client.get_me()
        self.my_id = me.id

        self.client.add_event_handler(self._handle_new_message, events.NewMessage(incoming=True))
        self.client.add_event_handler(self._handle_command, events.NewMessage(outgoing=True))

        print("Userbot запущен")
        await self.client.run_until_disconnected()

    #функции для внешних модулей
    async def get_today_summaries(self) -> str:
        today = datetime.now(timezone.utc).date()
        summaries = []

        async for dialog in self.client.iter_dialogs():
            if not dialog.is_user or dialog.id == self.my_id:
                continue

            messages = await self.client.get_messages(dialog.id, limit=50)

            today_messages = []
            for msg in reversed(messages):
                if not msg.date or msg.date.date() != today or not msg.raw_text:
                    continue

                role = "En" if msg.out else (dialog.name or "user")
                today_messages.append(f"{role}: {msg.raw_text}")

            if today_messages:
                user_id = dialog.entity.id
                summaries.append(f"--- Chat with {dialog.name} (user_id={user_id}) ---\n" + "\n".join(today_messages))

        return "\n\n".join(summaries)
    
    #обработка сообщений
    async def _handle_new_message(self, event):
        msg = event.message

        if msg.grouped_id:
            await self._handle_album_message(event)
            return

        images = []
        if msg.photo:
            img_bytes = await self.client.download_media(msg, file=bytes)
            images.append(img_bytes)

        await self._dispatch(event, images=images, text=msg.raw_text or "")

    async def _dispatch(self, event, images: list[bytes], text: str | None = None):
        chat_id = event.chat_id

        ingest_task = asyncio.create_task(self._ingest_message(event, images=images, text=text))
        self._pending_ingests[chat_id].add(ingest_task)
        ingest_task.add_done_callback(lambda t: self._pending_ingests[chat_id].discard(t))

        old_respond = self._active_respond_tasks.get(chat_id)
        if old_respond and not old_respond.done():
            old_respond.cancel()

        new_respond = asyncio.create_task(self._wait_and_respond(chat_id, event))
        self._active_respond_tasks[chat_id] = new_respond

    async def _handle_album_message(self, event):
        msg = event.message
        group_id = msg.grouped_id
        chat_id = event.chat_id

        self._album_buffer[group_id].append(event)

        if group_id in self._album_tasks:
            return

        self._album_tasks[group_id] = asyncio.create_task(
            self._flush_album_after_delay(group_id, chat_id)
        )

    async def _flush_album_after_delay(self, group_id: int, delay: float = 1):
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

        await self._dispatch(last_event, images=images, text=text)

    async def _transcribe_voice(self, msg) -> str:
        try:
            audio_bytes = await self.client.download_media(msg, file=bytes)
            text = await self.llm.speech_to_text(audio_bytes, audio_format="ogg")
            return text if text else "(can't transcribe)"
        except Exception as e:
            print(f"STT error: {e}")
            return "(error when transcribing)"

    async def _ingest_message(self, event, images: list[bytes], text: str | None = None):
        try:
            if text is None:
                text = event.raw_text or ""

            msg = event.message
            attachment_type = None
            attachment_summary = None

            if msg.voice:
                attachment_type = "voice"
                text = await self._transcribe_voice(msg)
            elif images:
                attachment_type = "image" if len(images) == 1 else "album"
                descriptions = []
                for img in images:
                    desc = await self.agent.describe_image(img, context_text=text)
                    descriptions.append(desc)
                attachment_summary = "\n\n".join(descriptions) if len(descriptions) > 1 else descriptions[0]

            await save_message(
                chat_id=event.chat_id,
                role="user",
                text=text,
                tg_message_id=msg.id,
                attachment_type=attachment_type,
                attachment_summary=attachment_summary,
                reply_to_tg_id=msg.reply_to_msg_id,
                timestamp=msg.date.replace(tzinfo=None) if msg.date.tzinfo else msg.date
            )
        except Exception as e:
            print(f"Unhandled exception in _ingest_message: {e}")
            traceback.print_exc()

    async def _wait_and_respond(self, chat_id: int, event):
        try:
            pending = list(self._pending_ingests[chat_id])
            if pending:
                await asyncio.shield(asyncio.gather(*pending, return_exceptions=True))

            chat = await event.get_input_chat()
            await self.client.send_read_acknowledge(chat)
            user = await event.get_sender()

            db_messages = await get_recent_messages(chat_id, limit=20)
            history, id_map = format_for_llm(db_messages)

            async with self.client.action(chat, "typing"):
                await self.agent.handle_message(history, user, id_map=id_map)

        except asyncio.CancelledError:
            print(f"Respond cancelled for chat {chat_id} — a newer message arrived")
            raise
        except Exception as e:
            print(f"Unhandled exception in _wait_and_respond: {e}")
            traceback.print_exc()
    
    #команды
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
        except Exception:
            await event.reply(f"Error: {traceback.format_exc()}")