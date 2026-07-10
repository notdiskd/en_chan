import io
from ollama import chat
from telethon import TelegramClient
from core.ai import AudioClient
import asyncio
from mutagen.oggopus import OggOpus

def _get_duration(audio_bytes: bytes) -> float:
    file_like = io.BytesIO(audio_bytes)
    audio = OggOpus(file_like)
    return audio.info.length

def make_voice_tool(tg_client: TelegramClient, audio_client: AudioClient, chat_id: int):
    """
    Фабрика: создаёт тул send_voice_message, привязанный к конкретному чату.
    Вызывается заново для каждого входящего сообщения.
    """

    async def send_voice_message(text: str) -> str:
        """
        Convert text to speech and send it as a voice message (max 60 seconds) in current chat. Use it when you want to respond with your voice, for example, for a more emotional or informal moment.
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