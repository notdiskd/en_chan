from config import api_key, character_prompt, ai_model
from ollama import AsyncClient, ChatResponse

class LLMClient():
    def __init__(self, model: str = ai_model):
        self.model = model
        self.client = AsyncClient(
            host="https://ollama.com",
            headers={'Authorization': 'Bearer ' + api_key}
        )

    async def chat(self, text) -> str:
        response: ChatResponse = await self.client.chat(
            model='gpt-oss:120b',
            messages=[
                {'role': 'system', 'content': character_prompt},
                {'role': 'user', 'content': text},
            ]
        )

        return response["message"]["content"]