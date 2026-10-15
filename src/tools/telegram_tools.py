import io
from telethon import TelegramClient
from telethon import functions, types
from core.ai import FishAudioClient
import asyncio
from mutagen.oggopus import OggOpus
from storage.messages import (
    get_message_by_telegram_id,
    record_message_deletion,
    record_message_edit,
    save_message,
)


def _resolve_reply_target(reply_to: int | str | None, id_map: dict[int | str, int]):
    if reply_to is None:
        return None
    value = str(reply_to).strip().upper().lstrip("#")
    if value.startswith("R"):
        return id_map.get(value)
    try:
        return id_map.get(int(value))
    except ValueError:
        return None


def _normalize_emoji(value: str) -> str:
    return value.replace("\ufe0f", "").replace("\ufe0e", "")


def _get_duration(audio_bytes: bytes) -> float:
    file_like = io.BytesIO(audio_bytes)
    audio = OggOpus(file_like)
    return audio.info.length

def _register_sent_message(
    telegram_id: int,
    id_map: dict[int | str, int],
    message_roles: dict[int | str, str] | None,
    context_number_state: dict[str, int] | None,
) -> int | None:
    if context_number_state is None:
        return None
    local_id = context_number_state["next"]
    context_number_state["next"] += 1
    id_map[local_id] = telegram_id
    if message_roles is not None:
        message_roles[local_id] = "assistant"
    return local_id


def make_send_voice_message_tool(
    client: TelegramClient,
    audio: FishAudioClient,
    chat_id: int,
    id_map: dict[int | str, int],
    message_roles: dict[int | str, str] | None = None,
    context_number_state: dict[str, int] | None = None,
    **kwargs,
):
    async def send_voice_message(text: str, reply_to: int | str | None = None) -> str:
        """
        Convert the provided text to speech and send it as a voice message (max 60 seconds) in the current chat.
        Use it when you want to respond with your voice, for example, for a more emotional or informal moment.
        You can use emotions tags wrapped in brackets (e.g. "I can't believe, it you actually did it! [laugh]")
        Common tags: [laugh], [sighing], [clear throat], [moaning], [pause], [long pause], [angry], [excited], [sad], [surprised], [soft]
        VERY IMPORTANT: Try to avoid numbers and special characters like @, #, $, %, ^, &, *, etc. in the text. Use words instead (e.g., "twenty-three" instead of "23").
        Also, avoid mixing other languages in the text. Stick to one language at a time. Write in transliteration if necessary.
        Text may be pronounced incorrectly or cause unexpected behavior if these rules are not followed.

        Args:
            text: The exact user-facing text to convert to speech and send.
            reply_to: Optional context reference such as 3, R1, or R2. Each R number points to a different older message.
        """
        kwargs_send = {}
        if reply_to is not None:
            tg_id = _resolve_reply_target(reply_to, id_map)
            if tg_id:
                kwargs_send["reply_to"] = tg_id

        opus_bytes = await audio.text_to_speech(text)

        file = io.BytesIO(opus_bytes)
        file.name = "voice.ogg"

        length = _get_duration(opus_bytes)
        if length > 60:
            raise Exception(f"Voice message is too long.")
        async with client.action(chat_id, "record-audio"):
            await asyncio.sleep(length)
        async with client.action(chat_id, "audio"):
            sent = await client.send_file(chat_id, file, voice_note=True, **kwargs_send)
        await save_message(chat_id=chat_id, role="assistant", text=text, tg_message_id=sent.id, attachment_type="voice", reply_to_tg_id=kwargs_send.get("reply_to"))
        local_id = _register_sent_message(
            sent.id, id_map, message_roles, context_number_state
        )
        number_note = f" as #{local_id}" if local_id is not None else ""
        return f"Voice message sent{number_note}. If the reply is complete, call stay_silent to end this turn; this will not undo the sent message. Send another only if it adds a distinct useful thought; do not repeat or paraphrase this one."

    return send_voice_message

def make_send_message_tool(
    client: TelegramClient,
    chat_id: int,
    id_map: dict[int | str, int],
    message_roles: dict[int | str, str] | None = None,
    context_number_state: dict[str, int] | None = None,
    **kwargs,
):
    async def send_message(text: str, reply_to: int | str | None = None) -> str:
        """
        Send the provided text to the current chat. Optionally reply directly to 
            a specific earlier message using its context reference (shown as #N
            or #R1, #R2, etc.) — each R number points to a different older message; useful when responding to something specific
        that isn't the most recent message.

        Args:
            text: The exact user-facing message text to send.
            reply_to: Optional context reference such as 3, R1, or R2. Each R number points to a different older message.
        """
        typing_time = min(max(len(text) / 5, 0.8), 4.0)
        async with client.action(chat_id, "typing"):
            await asyncio.sleep(typing_time)

        kwargs_send = {}
        if reply_to is not None:
            tg_id = _resolve_reply_target(reply_to, id_map)
            if tg_id:
                kwargs_send["reply_to"] = tg_id

        sent = await client.send_message(chat_id, text, **kwargs_send)
        await save_message(chat_id=chat_id, role="assistant", text=text, tg_message_id=sent.id, reply_to_tg_id=kwargs_send.get("reply_to"))
        local_id = _register_sent_message(
            sent.id, id_map, message_roles, context_number_state
        )
        number_note = f" as #{local_id}" if local_id is not None else ""

        return f"Message sent{number_note}. If the reply is complete, call stay_silent to end this turn; this will not undo the sent message. Send another only if it adds a distinct useful thought; do not repeat or paraphrase this one."
    
    return send_message

TG_TOOLS = [make_send_message_tool, make_send_voice_message_tool]


def make_react_to_message_tool(
    client: TelegramClient,
    chat_id: int,
    id_map: dict[int | str, int],
    message_roles: dict[int | str, str],
    **kwargs,
):
    reacted_this_turn = False
    reaction_attempts = 0

    async def react_to_message(message_number: int | str, emoji: str) -> str:
        """React to a recent incoming message in this chat with one standard emoji reaction.

        Use sparingly when a reaction feels more natural than a text reply. Only one
        reaction can be added during a response turn. Telegram's currently available
        reactions are checked before sending.

        Args:
            message_number: A context reference such as #N, #R1, or #R2; each R number identifies a different older message.
            emoji: A standard emoji reaction, such as 👍, ❤️, 😂, 🎉, 😢, or 🔥.
        """
        nonlocal reacted_this_turn, reaction_attempts
        if reacted_this_turn:
            return "You have already reacted once this turn. Continue without another reaction."
        if reaction_attempts >= 2:
            return "Reaction attempt limit reached for this turn; continue without reacting."
        reaction_attempts += 1
        reference = str(message_number).strip().upper().lstrip("#")
        if reference.startswith("R"):
            message_key: int | str = reference
        else:
            try:
                message_key = int(reference)
            except ValueError:
                return "That message reference is not available in the current context."
        target_role = message_roles.get(message_key)
        if target_role != "user":
            print(
                "Reaction not sent: target is not an incoming user message "
                f"(reference={message_number!r}, role={target_role!r})"
            )
            return "You can only react to an incoming user message from the current context."
        telegram_message_id = _resolve_reply_target(message_key, id_map)
        if not telegram_message_id:
            print(f"Reaction not sent: no Telegram ID for reference {message_number!r}")
            return "That message number is not available in the current context."

        peer = await client.get_input_entity(chat_id)
        try:
            available_response = await client(
                functions.messages.GetAvailableReactionsRequest(hash=0)
            )
            available_emojis = {
                _normalize_emoji(item.reaction): item.reaction
                for item in getattr(available_response, "reactions", [])
                if not getattr(item, "inactive", False)
            }
            normalized_emoji = _normalize_emoji(emoji)
            reaction_to_send = available_emojis.get(normalized_emoji, emoji)
            if available_emojis and normalized_emoji not in available_emojis:
                preferred = {
                    "👍", "❤️", "😂", "🎉", "😢", "🔥", "👏", "🙏",
                    "😍", "🤔", "😱", "🥰",
                }
                preferred_normalized = {
                    _normalize_emoji(item) for item in preferred
                }
                choices = [
                    canonical
                    for normalized, canonical in available_emojis.items()
                    if normalized in preferred_normalized
                ] or list(available_emojis.values())[:12]
                print(
                    "Reaction not sent: Telegram doesn't list the requested emoji "
                    f"(requested={emoji!r}, available={choices!r})"
                )
                return (
                    f"Telegram doesn't currently allow {emoji} as a reaction. "
                    f"Choose from the available standard reactions: {' '.join(choices)}"
                )

            await client(
                functions.messages.SendReactionRequest(
                    peer=peer,
                    msg_id=telegram_message_id,
                    reaction=[types.ReactionEmoji(emoticon=reaction_to_send)],
                )
            )
        except Exception as exc:
            print(
                "Telegram reaction failed: "
                f"chat={chat_id}, msg_id={telegram_message_id}, emoji={emoji!r}: {exc}"
            )
            return f"Telegram couldn't validate or send the {emoji} reaction: {exc}. If useful, try one other standard reaction."
        reacted_this_turn = True
        print(
            "Telegram reaction sent: "
            f"chat={chat_id}, msg_id={telegram_message_id}, emoji={emoji}"
        )
        return f"Added {emoji} reaction to #{message_number}. If the reply is complete, call stay_silent to end this turn."

    return react_to_message


TG_TOOLS.append(make_react_to_message_tool)


def make_edit_message_tool(
    client: TelegramClient,
    chat_id: int,
    id_map: dict[int | str, int],
    message_roles: dict[int | str, str],
    userbot=None,
    **kwargs,
):
    async def edit_message(message_number: int | str, new_text: str) -> str:
        """Edit one of your own recent text messages in this chat.

        Only edit a message authored by you and shown in the current context.
        Never use this to alter a user's message. Prefer editing to correct a
        typo or clarify something you just said; don't rewrite old messages
        without a clear reason.

        Args:
            message_number: Your message reference, such as #N, #R1, or #R2; each R number identifies a different older message.
            new_text: The complete replacement text for your message.
        """
        reference = str(message_number).strip().upper().lstrip("#")
        if reference.startswith("R"):
            key: int | str = reference
        else:
            try:
                key = int(reference)
            except ValueError:
                return "That message reference is not available in the current context."
        telegram_id = _resolve_reply_target(key, id_map)
        if not telegram_id or message_roles.get(key) != "assistant":
            return "You can only edit your own message from the current context."
        saved = await get_message_by_telegram_id(chat_id, telegram_id)
        if saved is None or saved.role != "assistant" or saved.is_deleted:
            return "That message is no longer available to edit."
        if saved.attachment_type:
            return "Only text messages can be edited with this tool."
        if not new_text.strip():
            return "The replacement text cannot be empty."

        mutation = (chat_id, telegram_id)
        if userbot is not None:
            userbot._pending_self_mutations.add(mutation)
        try:
            await client.edit_message(chat_id, telegram_id, new_text)
        except Exception as exc:
            return f"Telegram couldn't edit that message: {exc}"
        finally:
            if userbot is not None:
                userbot._pending_self_mutations.discard(mutation)
        await record_message_edit(chat_id, telegram_id, new_text)
        return f"Edited your message #{message_number}. Continue the conversation or call stay_silent if done."

    return edit_message


def make_delete_message_tool(
    client: TelegramClient,
    chat_id: int,
    id_map: dict[int | str, int],
    message_roles: dict[int | str, str],
    userbot=None,
    **kwargs,
):
    async def delete_message(message_number: int | str) -> str:
        """Delete one of your own recent messages from this chat.

        Deletion is irreversible. Only delete a message authored by you and
        shown in the current context, for example to remove an accidental or
        clearly inappropriate message. Never delete a user's message.

        Args:
            message_number: Your message reference, such as #N, #R1, or #R2; each R number identifies a different older message.
        """
        reference = str(message_number).strip().upper().lstrip("#")
        if reference.startswith("R"):
            key: int | str = reference
        else:
            try:
                key = int(reference)
            except ValueError:
                return "That message reference is not available in the current context."
        telegram_id = _resolve_reply_target(key, id_map)
        if not telegram_id or message_roles.get(key) != "assistant":
            return "You can only delete your own message from the current context."
        saved = await get_message_by_telegram_id(chat_id, telegram_id)
        if saved is None or saved.role != "assistant" or saved.is_deleted:
            return "That message is no longer available to delete."

        mutation = (chat_id, telegram_id)
        if userbot is not None:
            userbot._pending_self_mutations.add(mutation)
        try:
            await client.delete_messages(chat_id, [telegram_id], revoke=True)
        except Exception as exc:
            return f"Telegram couldn't delete that message: {exc}"
        finally:
            if userbot is not None:
                userbot._pending_self_mutations.discard(mutation)
        await record_message_deletion(
            chat_id, telegram_id, initiated_by="assistant"
        )
        return f"Deleted your message #{message_number}. Continue the conversation or call stay_silent if done."

    return delete_message


def make_block_user_tool(
    client: TelegramClient,
    chat_id: int,
    **kwargs,
):
    async def block_user(reason: str) -> str:
        """Block the person in the current private chat.

        This is a serious relationship boundary. Use it only when the ongoing
        interaction is persistently harmful, harassing, or deeply exhausting,
        not for a single awkward message, ordinary disagreement, or momentary
        irritation. The reason is for internal context and is not sent to them.

        Args:
            reason: A brief reason grounded in the conversation for deciding to block this person.
        """
        if chat_id <= 0:
            return "Blocking is available only for a private user chat."
        try:
            person = await client.get_entity(chat_id)
            if not isinstance(person, types.User) or person.bot:
                return "This chat is not with a human user account."
            me = await client.get_me()
            if person.id == me.id:
                return "You cannot block your own account."
            await client(functions.contacts.BlockRequest(
                id=await client.get_input_entity(person)
            ))
        except Exception as exc:
            print(f"Telegram block failed for chat {chat_id}: {exc}")
            return f"Telegram couldn't block this user: {exc}"

        saved_reason = reason.strip()[:500] or "No reason provided"
        await save_message(
            chat_id=chat_id,
            role="system",
            text=f"En blocked this user. Internal reason: {saved_reason}",
        )
        print(f"Blocked Telegram user {chat_id}: {saved_reason}")
        return "User blocked. Do not send any further messages in this turn."

    return block_user


TG_TOOLS.extend([make_edit_message_tool, make_delete_message_tool, make_block_user_tool])
