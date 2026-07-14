import io
from telethon import TelegramClient
from core.ai import FishAudioClient
import asyncio
from mutagen.oggopus import OggOpus
from storage.messages import save_message

def _get_duration(audio_bytes: bytes) -> float:
    file_like = io.BytesIO(audio_bytes)
    audio = OggOpus(file_like)
    return audio.info.length

def make_send_voice_message_tool(client: TelegramClient, audio: FishAudioClient, chat_id: int, id_map: dict[int, int], **kwargs):
    async def send_voice_message(text: str, reply_to: int | None = None) -> str:
        """
        Convert text to speech and send it as a voice message (max 60 seconds) in current chat.
        Use it when you want to respond with your voice, for example, for a more emotional or informal moment.
        You can use emotions tags wrapped in brackets (e.g. "I can't believe it [gasp] you actually did it [laugh]")
        Common tags: [whisper] [laugh] [emphasis] [sigh] [gasp] [pause] [angry] [excited] [sad] [surprised] [inhale] [exhale]
        VERY IMPORTANT: Try to avoid numbers and special characters like @, #, $, %, ^, &, *, etc. in the text. Use words instead (e.g., "twenty-three" instead of "23").
        Also, avoid mixing other languages in the text. Stick to one language at a time. Write in transliteration if necessary.
        Text may be pronounced incorrectly or cause unexpected behavior if these rules are not followed.

        Args:
            text: Text to convert to speech.
            reply_to: Optional message number (e.g. 3) to reply to directly. Leave empty for a normal message.
        """
        kwargs_send = {}
        if reply_to is not None:
            tg_id = id_map.get(reply_to)
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
        return "Voice message is sent."

    return send_voice_message

def make_send_message_tool(client: TelegramClient, chat_id: int, id_map: dict[int, int], **kwargs):
    async def send_message(text: str, reply_to: int | None = None) -> str:
        """
        Send a text message to the current chat. Optionally reply directly to 
        a specific earlier message using its number (shown as #N in the 
        conversation history) — useful when responding to something specific 
        that isn't the most recent message.

        Args:
            text: The message text to send
            reply_to: Optional message number (e.g. 3) to reply to directly. Leave empty for a normal message.
        """
        typing_time = min(max(len(text) / 5, 0.8), 4.0)
        async with client.action(chat_id, "typing"):
            await asyncio.sleep(typing_time)

        kwargs_send = {}
        if reply_to is not None:
            try:
                reply_to = int(reply_to)
            except (ValueError, TypeError):
                reply_to = None
            tg_id = id_map.get(reply_to)
            if tg_id:
                kwargs_send["reply_to"] = tg_id

        sent = await client.send_message(chat_id, text, **kwargs_send)
        await save_message(chat_id=chat_id, role="assistant", text=text, tg_message_id=sent.id, reply_to_tg_id=kwargs_send.get("reply_to"))

        return "Message sent."
    
    return send_message

TG_TOOLS = [make_send_message_tool, make_send_voice_message_tool]