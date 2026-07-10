from config import llm, fishaudio
from ollama import AsyncClient, ChatResponse
from fishaudio.types import TTSConfig
from fishaudio import AsyncFishAudio
from fishaudio.utils import save

class LLMClient():
    def __init__(self):
        self.client = AsyncClient(
            host="https://ollama.com",
            headers={'Authorization': 'Bearer ' + llm["api_key"]}
        )

    async def chat(self, messages: list, tools: list | None) -> str:
        response: ChatResponse = await self.client.chat(
            model=llm["ai_model"],
            tools=tools,
            messages=messages,
        )
        return response.message
    
class AudioClient():
    def __init__(self):
        self.client = AsyncFishAudio(api_key=fishaudio["api_key"])

    async def text_to_speech(self, text: str) -> bytes:
        audio = await self.client.tts.convert(
            text=text,
            model=fishaudio["model"],
            reference_id=fishaudio["reference_id"],
            config=TTSConfig(format="opus", opus_bitrate=-1000),
        )
        return audio