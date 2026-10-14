import io
from telethon import TelegramClient
from telethon import functions, types
from core.ai import FishAudioClient
import asyncio
from mutagen.oggopus import OggOpus
from storage.messages import save_message


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


def _get_duration(audio_bytes: bytes) -> float:
    file_like = io.BytesIO(audio_bytes)
    audio = OggOpus(file_like)
    return audio.info.length

def make_send_voice_message_tool(client: TelegramClient, audio: FishAudioClient, chat_id: int, id_map: dict[int | str, int], **kwargs):
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
            reply_to: Optional context reference such as 3 or R1. Leave empty for a normal message.
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
        return "Voice message sent. If the reply is complete, call stay_silent to end this turn; this will not undo the sent message. Send another only if it adds a distinct useful thought; do not repeat or paraphrase this one."

    return send_voice_message

def make_send_message_tool(client: TelegramClient, chat_id: int, id_map: dict[int | str, int], **kwargs):
    async def send_message(text: str, reply_to: int | str | None = None) -> str:
        """
        Send the provided text to the current chat. Optionally reply directly to 
            a specific earlier message using its context reference (shown as #N
            or #R1) — useful when responding to something specific
        that isn't the most recent message.

        Args:
            text: The exact user-facing message text to send.
            reply_to: Optional context reference such as 3 or R1. Leave empty for a normal message.
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

        return "Message sent. If the reply is complete, call stay_silent to end this turn; this will not undo the sent message. Send another only if it adds a distinct useful thought; do not repeat or paraphrase this one."
    
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
            message_number: The context reference shown as #N or #R1.
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
        if message_roles.get(message_key) != "user":
            return "You can only react to an incoming user message from the current context."
        telegram_message_id = _resolve_reply_target(message_key, id_map)
        if not telegram_message_id:
            return "That message number is not available in the current context."

        peer = await client.get_input_entity(chat_id)
        try:
            available_response = await client(
                functions.messages.GetAvailableReactionsRequest(hash=0)
            )
            available_emojis = {
                item.reaction
                for item in getattr(available_response, "reactions", [])
                if not getattr(item, "inactive", False)
            }
            if available_emojis and emoji not in available_emojis:
                preferred = {
                    "👍", "❤️", "😂", "🎉", "😢", "🔥", "👏", "🙏",
                    "😍", "🤔", "😱", "🥰",
                }
                choices = sorted(available_emojis & preferred) or sorted(available_emojis)[:12]
                return (
                    f"Telegram doesn't currently allow {emoji} as a reaction. "
                    f"Choose from the available standard reactions: {' '.join(choices)}"
                )

            await client(
                functions.messages.SendReactionRequest(
                    peer=peer,
                    msg_id=telegram_message_id,
                    reaction=[types.ReactionEmoji(emoticon=emoji)],
                )
            )
        except Exception as exc:
            return f"Telegram couldn't validate or send the {emoji} reaction: {exc}. If useful, try one other standard reaction."
        reacted_this_turn = True
        return f"Added {emoji} reaction to #{message_number}. If the reply is complete, call stay_silent to end this turn."

    return react_to_message


TG_TOOLS.append(make_react_to_message_tool)
