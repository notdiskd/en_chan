from config import api_key, ai_model
from ollama import AsyncClient, ChatResponse

class LLMClient():
    def __init__(self, model: str = ai_model):
        self.model = model
        self.client = AsyncClient(
            host="https://ollama.com",
            headers={'Authorization': 'Bearer ' + api_key}
        )

    async def chat(self, messages: list, tools: list | None) -> str:
        response: ChatResponse = await self.client.chat(
            model=ai_model,
            tools=tools,
            messages=messages,
        )
        return response.message