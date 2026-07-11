from config import llm, fishaudio
from fishaudio.types import TTSConfig
from fishaudio import AsyncFishAudio
from openai import AsyncOpenAI

#олламовская шняга
# class LLMClient():
#     def __init__(self):
#         self.client = AsyncClient(
#             host="https://ollama.com",
#             headers={'Authorization': 'Bearer ' + llm["api_key"]}
#         )

#     async def chat(self, messages: list, tools: list | None) -> str:
#         response: ChatResponse = await self.client.chat(
#             model=llm["ai_model"],
#             tools=tools,
#             messages=messages,
#         )
#         return response.message

class LLMClient:
    def __init__(self):
        self.client = AsyncOpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=llm["api_key"],
        )

    async def tool_chat(self, messages: list[dict], tools: list) -> dict:
        response = await self.client.chat.completions.create(
            model=llm["ai_model"],
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
            model=llm["ai_model"],
            messages=messages,
        )

        return response.choices[0].message
    
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