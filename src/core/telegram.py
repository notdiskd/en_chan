import asyncio
import random
from datetime import datetime, timezone, timedelta
from telethon import TelegramClient, events, types
from telethon.types import User
from telethon.tl.functions.messages import GetMessagesReactionsRequest
from commands.registry import get_registered_commands
import commands.definitions
from config import telegram, prompts
from core.agent import Agent
from collections import defaultdict
from storage.messages import (
    get_message_by_telegram_id,
    get_message_by_telegram_id_anywhere,
    get_messages_by_telegram_ids,
    get_recent_messages,
    record_message_deletion,
    record_message_edit,
    save_message,
    save_reaction_event,
)
from core.conversation_context import build_chat_context
from core.ai import OpenRouterClient
from storage.proactive import (
    claim_follow_up,
    finish_follow_up,
    get_active_follow_ups,
    get_spontaneous_candidates,
    set_spontaneous_cooldown,
)
import traceback

#тут поделено на секции
class UserBot:
    #инициализация
    def __init__(self, agent: Agent, llm: OpenRouterClient):
        self.client: TelegramClient = TelegramClient('account', telegram["app_id"], telegram["api_hash"], system_version="4.16.30-vxCUSTOM")
        self.agent = agent
        self.llm = llm
        self.agent.telegram_client = self.client
        self.agent.userbot = self
        self.commands = get_registered_commands()
        self._pending_ingests: dict[int, set[asyncio.Task]] = defaultdict(set)
        self._active_respond_tasks: dict[int, asyncio.Task] = {}
        self._pending_response_images: dict[int, dict[int, list[bytes]]] = defaultdict(dict)
        self._reaction_states: dict[tuple[int, int], set[tuple[str, datetime | None]]] = {}
        self._pending_self_mutations: set[tuple[int, int]] = set()
        self._started_at = datetime.now(timezone.utc)

    async def start(self):
        await self.client.start()
        await self.client.get_dialogs()

        me = await self.client.get_me()
        self.my_id = me.id

        self.client.add_event_handler(self._handle_new_message, events.NewMessage(incoming=True))
        self.client.add_event_handler(self._handle_album, events.Album())
        self.client.add_event_handler(self._handle_command, events.NewMessage(outgoing=True))
        self.client.add_event_handler(self._handle_message_edited, events.MessageEdited())
        self.client.add_event_handler(self._handle_messages_deleted, events.MessageDeleted())
        asyncio.create_task(self._proactive_loop())
        asyncio.create_task(self._reaction_poll_loop())

        print("Userbot запущен")
        await self.client.run_until_disconnected()

    async def _proactive_loop(self):
        loop = asyncio.get_running_loop()
        next_spontaneous_check = loop.time() + random.randint(45, 75) * 60
        while True:
            try:
                await self._process_due_follow_up()
            except Exception as exc:
                print(f"Proactive follow-up scheduler error: {exc}")
                traceback.print_exc()
            if loop.time() >= next_spontaneous_check:
                try:
                    await self._process_spontaneous_message()
                except Exception as exc:
                    print(f"Spontaneous proactivity error: {exc}")
                    traceback.print_exc()
                next_spontaneous_check = loop.time() + random.randint(45, 75) * 60
            await asyncio.sleep(60)

    async def _reaction_poll_loop(self):
        """Poll recent outgoing messages in case Telegram doesn't push reaction updates."""
        while True:
            await asyncio.sleep(25)
            try:
                async for dialog in self.client.iter_dialogs(limit=20):
                    if not dialog.is_user or dialog.id == self.my_id:
                        continue

                    recent = await get_recent_messages(dialog.id, limit=30)
                    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
                    outgoing_ids = []
                    for message in reversed(recent):
                        timestamp = message.timestamp
                        if timestamp.tzinfo is None:
                            timestamp = timestamp.replace(tzinfo=timezone.utc)
                        if timestamp < cutoff:
                            break
                        if message.role == "assistant" and message.tg_message_id:
                            outgoing_ids.append(message.tg_message_id)
                        if len(outgoing_ids) >= 10:
                            break
                    if not outgoing_ids:
                        continue

                    result = await self.client(
                        GetMessagesReactionsRequest(
                            peer=dialog.input_entity,
                            id=outgoing_ids,
                        )
                    )
                    for update in getattr(result, "updates", ()):
                        if isinstance(update, types.UpdateMessageReactions):
                            await self._handle_reaction_update(update, polled=True)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                print(f"Reaction fallback poll error: {exc}")

    async def _process_spontaneous_message(
        self, target_chat_id: int | None = None
    ) -> str:
        active_chats = {
            chat_id
            for chat_id, task in self._active_respond_tasks.items()
            if not task.done()
        }
        active_chats.update(
            chat_id for chat_id, tasks in self._pending_ingests.items() if tasks
        )
        forced = target_chat_id is not None
        if forced:
            chat_id = target_chat_id
            if chat_id in active_chats:
                return f"Chat {chat_id} already has a message being processed."
            if await get_active_follow_ups(chat_id):
                return f"Chat {chat_id} has a scheduled follow-up; skipped spontaneous message."
        else:
            candidates = await get_spontaneous_candidates(
                self.my_id,
                excluded_chat_ids=active_chats,
            )
            if not candidates:
                print("No eligible chat for spontaneous proactivity")
                return "No eligible chat for spontaneous proactivity."
            random.shuffle(candidates)
            chat_id = candidates[0]
        try:
            user = await self.client.get_entity(chat_id)
        except Exception as exc:
            print(f"Skipping spontaneous candidate {chat_id}: can't load user: {exc}")
            return f"Couldn't load Telegram user/chat {chat_id}: {exc}"
        if not isinstance(user, User) or user.bot or user.id == self.my_id:
            return "Target must be another person's private Telegram account (not a bot or group)."

        summary, history, _ = await build_chat_context(chat_id, self.llm)
        if not history:
            return f"No saved conversation history for chat {chat_id}; nothing sent."

        self_model = await self.agent.memory.get_self_model()
        profile = await self.agent.memory.format_profile(chat_id)
        current_date = datetime.now().astimezone().strftime(
            "%A, %d %B %Y, %H:%M local time"
        )
        messages = [
            {"role": "system", "content": prompts["character_prompt"]},
            {
                "role": "system",
                "content": (
                    "This is a spontaneous check of one private chat. The application will "
                    "send the text you return; no Telegram tools are available in this call, "
                    "so ignore the usual instruction to send messages through tools. Consider "
                    "only this user's history. If their last message seems to need a reply, "
                    "reply naturally. Otherwise, you may start a casual conversation: a simple "
                    "'how are you?' or similar check-in is fine when it feels natural; you do "
                    "not need a special reason or recent event. Do not pretend to have done "
                    "or experienced things that are not in the context. Return only one short "
                    "message, without explanation or tool calls. "
                    + (
                        "The owner has already chosen this chat and decided to send a message; "
                        "do not output SKIP."
                        if forced
                        else "If even a casual message would feel unnatural or unwelcome in "
                        "this relationship, output exactly SKIP."
                    )
                ),
            },
            {"role": "system", "content": f"Today's date: {current_date}."},
            {
                "role": "system",
                "content": f"You're chatting with {user.first_name} (user ID {user.id}).",
            },
        ]
        if self_model:
            messages.append({"role": "system", "content": f"Your self-view:\n{self_model}"})
        if profile:
            messages.append({"role": "system", "content": f"Facts about this user:\n{profile}"})
        if summary:
            messages.append({"role": "system", "content": f"Earlier context:\n{summary}"})
        messages.extend(
            {"role": row["role"], "content": row["content"]}
            for row in history
        )

        response = await self.llm.chat(messages=messages)
        text = (response.content or "").strip()
        if forced and (not text or text.upper().startswith("SKIP")):
            retry_messages = [
                *messages,
                {"role": "assistant", "content": text or "(empty response)"},
                {
                    "role": "user",
                    "content": (
                        "That was not the requested output. The owner already chose this "
                        "recipient and explicitly wants to test a spontaneous message. Do not "
                        "evaluate whether to send and do not refuse with SKIP. Write one brief, "
                        "context-aware message as plain text now. The application will send it."
                    ),
                },
            ]
            response = await self.llm.chat(messages=retry_messages)
            text = (response.content or "").strip()
        if not text or text.upper().startswith("SKIP") or len(text) > 1200:
            if not forced:
                await set_spontaneous_cooldown(chat_id, timedelta(hours=24))
            print(f"Skipped spontaneous check-in for chat {chat_id}")
            return f"Model skipped chat {chat_id}; nothing was sent."

        sent = await self.client.send_message(chat_id, text)
        await save_message(
            chat_id=chat_id,
            role="assistant",
            text=text,
            tg_message_id=sent.id,
            timestamp=sent.date,
        )
        await set_spontaneous_cooldown(chat_id, timedelta(days=7))
        print(f"Sent spontaneous message to chat {chat_id}: {text}")
        return f"Sent to chat {chat_id}: {text}"

    async def _process_due_follow_up(
        self, follow_up_id: int | None = None, force: bool = False
    ):
        claimed = await claim_follow_up(
            follow_up_id=follow_up_id,
            force=force,
        )
        if claimed is None:
            return
        item = claimed
        follow_up_id = item["id"]
        chat_id = item["chat_id"]

        try:
            text = item["message"]
            sent = await self.client.send_message(chat_id, text)
            await save_message(
                chat_id=chat_id,
                role="assistant",
                text=text,
                tg_message_id=sent.id,
                timestamp=sent.date,
            )
            await finish_follow_up(follow_up_id, "sent")
            print(f"Sent follow-up {follow_up_id} to chat {chat_id}: {text}")
        except Exception:
            await finish_follow_up(follow_up_id, "failed")
            raise

    async def _handle_reaction_update(self, update, *, polled=False):
        if not isinstance(update, types.UpdateMessageReactions):
            update_name = type(update).__name__
            if "Reaction" in update_name:
                print(f"Other Telegram reaction update received: {update_name}")
            return
        if not polled:
            print(
                "Telegram reaction update received: "
                f"peer={getattr(update, 'peer', None)!r}, "
                f"msg_id={getattr(update, 'msg_id', None)}"
            )
        if not isinstance(update.peer, types.PeerUser):
            if not polled:
                print("Reaction update ignored: peer is not a private user chat")
            return
        chat_id = update.peer.user_id
        if chat_id == self.my_id:
            if not polled:
                print("Reaction update ignored: event belongs to our own account")
            return

        message = await get_message_by_telegram_id(chat_id, update.msg_id)
        if message is None or message.role != "assistant":
            if not polled:
                print(
                    "Reaction update ignored: message is not a saved outgoing message "
                    f"(chat={chat_id}, msg_id={update.msg_id})"
                )
            return

        reactions = getattr(update, "reactions", None)
        recent_reactions = getattr(reactions, "recent_reactions", None) or []
        if not recent_reactions and not polled:
            telegram_message = await self.client.get_messages(chat_id, ids=update.msg_id)
            current_reactions = getattr(telegram_message, "reactions", None)
            recent_reactions = getattr(current_reactions, "recent_reactions", None) or []
        if not recent_reactions and not polled:
            print(
                "Reaction update has no recent_reactions list "
                f"(chat={chat_id}, msg_id={update.msg_id})"
            )
        user_reactions = []
        for item in recent_reactions:
            peer_id = getattr(item, "peer_id", None)
            reaction_object = getattr(item, "reaction", None)
            reaction = getattr(reaction_object, "emoticon", None)
            if reaction is None:
                document_id = getattr(reaction_object, "document_id", None)
                reaction = f"custom emoji {document_id}" if document_id else "custom emoji"
            if not polled:
                print(
                    "Reaction entry: "
                    f"peer={peer_id!r}, my={getattr(item, 'my', None)}, "
                    f"unread={getattr(item, 'unread', None)}, emoji={reaction}"
                )
            if (
                isinstance(peer_id, types.PeerUser)
                and peer_id.user_id == chat_id
            ):
                user_reactions.append((reaction, item.date, item))

        state_key = (chat_id, update.msg_id)
        current_state = {(reaction, date) for reaction, date, _ in user_reactions}
        previous_state = self._reaction_states.get(state_key)
        self._reaction_states[state_key] = current_state
        if previous_state is None:
            newly_added = [
                item
                for _, _, item in user_reactions
                if item.unread
                or (
                    item.date is not None
                    and (item.date.replace(tzinfo=timezone.utc)
                         if item.date.tzinfo is None else item.date)
                    >= self._started_at.replace(microsecond=0)
                )
            ]
        else:
            newly_added = [
                item
                for reaction, date, item in user_reactions
                if (reaction, date) not in previous_state
            ]
        if not newly_added:
            if not polled:
                print(
                    "Reaction update ignored: no new reaction from the other user "
                    f"(chat={chat_id}, msg_id={update.msg_id})"
                )
            return

        selected = max(
            newly_added,
            key=lambda item: item.date.timestamp() if item.date else 0,
        )
        emoji = getattr(selected.reaction, "emoticon", "a custom emoji")
        event_text = (
            f"User reacted {emoji} to your message: "
            f"{(message.text or '[non-text message]')[:180]!r}."
        )
        saved = await save_reaction_event(
            chat_id=chat_id,
            tg_message_id=update.msg_id,
            text=event_text,
            timestamp=selected.date,
        )
        if not saved:
            return

        task = self._active_respond_tasks.get(chat_id)
        if task and not task.done():
            print(
                "Reaction recorded but no response started: chat is already being "
                f"processed (chat={chat_id})"
            )
            return

        print(
            f"Starting reaction response: chat={chat_id}, "
            f"msg_id={update.msg_id}, emoji={emoji}"
        )
        reaction_task = asyncio.create_task(self._respond_to_reaction(chat_id))
        self._active_respond_tasks[chat_id] = reaction_task

    async def _handle_message_edited(self, event):
        if not event.is_private or event.chat_id in (None, self.my_id):
            return
        message = event.message
        saved = await get_message_by_telegram_id(event.chat_id, message.id)
        if saved is None:
            return
        changed = await record_message_edit(
            event.chat_id,
            message.id,
            message.raw_text or "",
            edited_at=message.edit_date or datetime.now(timezone.utc),
        )
        if changed and saved.role == "user":
            await self._schedule_social_event_response(event.chat_id)

    async def _handle_messages_deleted(self, event):
        for message_id in event.deleted_ids:
            saved = None
            chat_id = event.chat_id
            if chat_id is not None:
                if chat_id <= 0 or chat_id == self.my_id:
                    continue
                saved = await get_message_by_telegram_id(chat_id, message_id)
            else:
                # Private-chat deletion updates omit the peer. Message IDs in
                # private chats share the account's message-ID sequence.
                saved = await get_message_by_telegram_id_anywhere(message_id)
                if saved is not None:
                    chat_id = saved.chat_id
            if saved is None or chat_id is None or chat_id <= 0 or chat_id == self.my_id:
                continue
            if (chat_id, message_id) in self._pending_self_mutations:
                continue
            author = await record_message_deletion(chat_id, message_id)
            if author is not None:
                await self._schedule_social_event_response(chat_id)

    async def _schedule_social_event_response(self, chat_id: int):
        active = self._active_respond_tasks.get(chat_id)
        if active and not active.done():
            return
        task = asyncio.create_task(self._respond_to_social_event(chat_id))
        self._active_respond_tasks[chat_id] = task

    async def _respond_to_social_event(self, chat_id: int):
        try:
            user = await self.client.get_entity(chat_id)
            if not isinstance(user, User) or user.bot:
                return
            summary, history, id_map = await build_chat_context(chat_id, self.llm)
            await self.agent.handle_message(
                history,
                user,
                id_map=id_map,
                chat_summary=summary,
                proactive_chat_id=chat_id,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"Telegram edit/delete response failed for chat {chat_id}: {exc}")
            traceback.print_exc()

    async def _respond_to_reaction(self, chat_id: int):
        try:
            user = await self.client.get_entity(chat_id)
            if not isinstance(user, User) or user.bot:
                return

            summary, history, id_map = await build_chat_context(chat_id, self.llm)
            await self.agent.handle_message(
                history,
                user,
                id_map=id_map,
                chat_summary=summary,
                proactive_chat_id=chat_id if chat_id > 0 else None,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"Reaction response failed for chat {chat_id}: {exc}")
            traceback.print_exc()

    #функции для внешних модулей
    async def get_today_summaries(self) -> str:
        today = datetime.now(timezone.utc).date()
        summaries = []

        async for dialog in self.client.iter_dialogs():
            if not dialog.is_user or dialog.id == self.my_id:
                continue

            messages = await self.client.get_messages(dialog.id, limit=100)

            today_messages = []
            for msg in reversed(messages):
                if not msg.date or msg.date.astimezone(timezone.utc).date() != today or not msg.raw_text:
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
            return

        images = []
        if msg.photo:
            img_bytes = await self.client.download_media(msg, file=bytes)
            images.append(img_bytes)

        await self._dispatch(event, images=images, text=msg.raw_text or "")

    async def _dispatch(
        self, event, images: list[bytes], text: str | None = None, message=None
    ):
        chat_id = event.chat_id
        message = message or event.message
        if images:
            self._pending_response_images[chat_id][message.id] = images

        ingest_task = asyncio.create_task(
            self._ingest_message(event, images=images, text=text, message=message)
        )
        self._pending_ingests[chat_id].add(ingest_task)
        ingest_task.add_done_callback(lambda t: self._pending_ingests[chat_id].discard(t))

        old_respond = self._active_respond_tasks.get(chat_id)
        if old_respond and not old_respond.done():
            old_respond.cancel()

        new_respond = asyncio.create_task(self._wait_and_respond(chat_id, event))
        self._active_respond_tasks[chat_id] = new_respond

    async def _handle_album(self, event):
        if not event.messages or event.messages[0].out:
            return

        images = []
        for message in event.messages:
            if message.photo:
                image_bytes = await self.client.download_media(message, file=bytes)
                if image_bytes:
                    images.append(image_bytes)

        if not images:
            print(f"Album {event.grouped_id} contained no downloadable photos")
            return

        primary_message = max(event.messages, key=lambda item: item.id)
        await self._dispatch(
            event,
            images=images,
            text=event.raw_text or "",
            message=primary_message,
        )

    async def _transcribe_voice(self, msg) -> str:
        try:
            audio_bytes = await self.client.download_media(msg, file=bytes)
            text = await self.llm.speech_to_text(audio_bytes, audio_format="ogg")
            return text if text else "(can't transcribe)"
        except Exception as e:
            print(f"STT error: {e}")
            return "(error when transcribing)"

    async def _ingest_message(
        self, event, images: list[bytes], text: str | None = None, message=None
    ):
        try:
            print("ingesting new message")

            if text is None:
                text = event.raw_text or ""

            msg = message or event.message
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
                timestamp=msg.date
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

            summary, history, id_map = await build_chat_context(chat_id, self.llm)
            visible_tg_ids = set(id_map.values())
            visible_rows = await get_messages_by_telegram_ids(
                chat_id, list(visible_tg_ids)
            )
            rows_by_tg_id = {row.tg_message_id: row for row in visible_rows}
            target_ids = []
            seen_targets = set()
            # Walk newest first so a direct reply in the current turn remains in
            # view even if the context mentions many distinct older replies.
            for local_id in range(len(history), 0, -1):
                message_id = id_map.get(local_id)
                row = rows_by_tg_id.get(message_id)
                target_id = row.reply_to_tg_id if row else None
                if target_id and target_id not in visible_tg_ids and target_id not in seen_targets:
                    target_ids.append(target_id)
                    seen_targets.add(target_id)
            latest_reply_id = event.message.reply_to_msg_id
            if (
                latest_reply_id
                and latest_reply_id not in visible_tg_ids
                and latest_reply_id not in seen_targets
            ):
                target_ids.insert(0, latest_reply_id)

            referenced_messages = []
            for target_id in reversed(target_ids[:10]):
                saved_reference = await get_message_by_telegram_id(chat_id, target_id)
                if saved_reference is not None:
                    role = saved_reference.role
                    content = saved_reference.text or ""
                    if saved_reference.attachment_summary:
                        content += f" [attachment: {saved_reference.attachment_summary}]"
                    elif saved_reference.attachment_type:
                        content += f" [{saved_reference.attachment_type} message]"
                    timestamp = saved_reference.timestamp
                    if timestamp.tzinfo is None:
                        timestamp = timestamp.replace(tzinfo=timezone.utc)
                    age = datetime.now(timezone.utc) - timestamp.astimezone(timezone.utc)
                    time_note = (
                        f"{int(age.total_seconds() // 86400)}d ago"
                        if age.total_seconds() >= 86400
                        else f"{int(age.total_seconds() // 3600)}h ago"
                    )
                    if saved_reference.is_deleted:
                        role = "system"
                        content = f"[deleted message; previously seen content: {content}]"
                else:
                    telegram_reference = await self.client.get_messages(
                        chat_id, ids=target_id
                    )
                    if not telegram_reference:
                        continue
                    role = "assistant" if telegram_reference.out else "user"
                    content = telegram_reference.raw_text or ""
                    if telegram_reference.media and not content:
                        content = "[media attachment; description unavailable]"
                    time_note = "older Telegram message"

                ref_id = f"R{len(referenced_messages) + 1}"
                if role != "system":
                    id_map[ref_id] = target_id
                referenced_messages.append({
                    "ref_id": ref_id,
                    "role": role,
                    "content": f"[{time_note}] {content}".strip(),
                })
            pending_images = self._pending_response_images[chat_id]
            images_by_tg_id = {
                message_id: image_bytes
                for message_id, image_bytes in pending_images.items()
                if message_id in id_map.values()
            }

            await self.agent.handle_message(
                history,
                user,
                id_map=id_map,
                chat_summary=summary,
                images_by_tg_id=images_by_tg_id,
                proactive_chat_id=chat_id if chat_id > 0 else None,
                referenced_messages=referenced_messages,
            )
            # A successful response used the pending images once. Their textual
            # descriptions remain in the database for later turns.
            pending_images.clear()

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
