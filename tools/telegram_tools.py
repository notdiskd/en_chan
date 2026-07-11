import io
from telethon import TelegramClient
from core.ai import AudioClient
import asyncio
from mutagen.oggopus import OggOpus

def _get_duration(audio_bytes: bytes) -> float:
    file_like = io.BytesIO(audio_bytes)
    audio = OggOpus(file_like)
    return audio.info.length

def make_send_voice_message_tool(tg_client: TelegramClient, audio_client: AudioClient, chat_id: int):
    async def send_voice_message(text: str) -> str:
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
        """
        try:
            opus_bytes = await audio_client.text_to_speech(text)

            file = io.BytesIO(opus_bytes)
            file.name = "voice.ogg"

            length = _get_duration(opus_bytes)
            if length > 60:
                raise Exception(f"Voice message is too long.")
            async with tg_client.action(chat_id, "record-audio"):
                await asyncio.sleep(length)
            async with tg_client.action(chat_id, "audio"):
                await tg_client.send_file(chat_id, file, voice_note=True, caption=text)
            return "Voice message is sent."
        except Exception as e:
            return f"Error sending voice message: {e}"

    return send_voice_message

def make_send_message_tool(client: TelegramClient, chat_id: int):
    async def send_message(text: str) -> str:
        """
        Send a text message to the current chat. Can be called multiple times
        in a row if you want to send several separate messages — like a real
        person continuing a thought across multiple replies. Each call is a
        separate message.

        Args:
            text: The message text to send
        """
        typing_time = min(max(len(text) / 5, 0.8), 4.0)

        async with client.action(chat_id, "typing"):
            await asyncio.sleep(typing_time)

        await client.send_message(chat_id, text)
        return "Message sent."

    return send_message