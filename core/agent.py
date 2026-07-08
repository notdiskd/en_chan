from core.llm import LLMClient
from config import character_prompt
from ollama import Message
from tools.implementations import web_search, web_fetch, get_current_time
from telethon.types import User

TOOL_FUNCS = {
    "web_search": web_search,
    "web_fetch": web_fetch,
    "get_current_time": get_current_time
}

class Agent:
    def __init__(self, llm: LLMClient):
        self.llm = llm
        self.history: dict[int, list[dict]] = {}

    async def handle_message(self, history: str, user: User) -> str:
        messages=[
            {'role': 'system', 'content': character_prompt},
            {'role': 'system', 'content': f"You're chatting user with name \"{user.first_name}\""},
        ]

        messages += history

        print(messages)

        #tool loop
        for _ in range(10):
            response: Message = await self.llm.chat(
                tools=[web_search, web_fetch, get_current_time],
                messages=messages,
            )

            messages.append(response)

            if not response.tool_calls:
                return response.content

            for call in response.tool_calls:
                fn_name = call.function.name
                fn_args = call.function.arguments

                fn = TOOL_FUNCS.get(fn_name)
                if fn is None:
                    result = f"Unknown tool: {fn_name}"
                else:
                    result = await fn(**fn_args)

                messages.append({
                    "role": "tool",
                    "content": str(result),
                })

        #tool loop exceeded
        response: Message = await self.llm.chat(
            messages=messages
        )

        return response.content