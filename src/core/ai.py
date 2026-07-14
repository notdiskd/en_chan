from config import openrouter, fishaudio
from fishaudio.types import TTSConfig
from fishaudio import AsyncFishAudio
from openai import AsyncOpenAI
import base64
import httpx

class OpenRouterClient():
    def __init__(self):
        self._api_key = openrouter["api_key"]
        self.client = AsyncOpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=self._api_key,
        )
        self.chat_model = openrouter["chat_model"]
        self.embedding_model = openrouter["embedding_model"]
        self.transcription_model = openrouter["transcription_model"]

    async def tool_chat(self, messages: list[dict], tools: list) -> dict:
        response = await self.client.chat.completions.create(
            model=self.chat_model,
            messages=messages,
            tools=tools,
            tool_choice="required",
            extra_body={
                "reasoning": {
                    "effort": "none"
                }
            },
        )

        return response.choices[0].message
    
    async def chat(self, messages: list[dict]) -> dict:
        response = await self.client.chat.completions.create(
            model=self.chat_model,
            messages=messages,
        )

        return response.choices[0].message
    
    async def get_embedding(self, text: str) -> list[float]:
        response = await self.client.embeddings.create(
            model=self.embedding_model,
            input=text,
        )

        return response.data[0].embedding
    
    async def speech_to_text(self, audio_bytes: bytes, audio_format: str = "ogg") -> str:
        audio_b64 = base64.b64encode(audio_bytes).decode()

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                "https://openrouter.ai/api/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={
                    "model": self.transcription_model,
                    "input_audio": {"data": audio_b64, "format": audio_format},
                },
            )
            resp.raise_for_status()
            return resp.json().get("text", "")
    
class FishAudioClient():
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