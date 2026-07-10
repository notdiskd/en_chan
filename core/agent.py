from core.ai import LLMClient, AudioClient
from config import llm
from ollama import Message
from tools.llm_tools import TOOLS, TOOL_FUNCS, stay_silent
from telethon.types import User
from tools.telegram_tools import make_voice_tool

class Agent:
    def __init__(self, llm: LLMClient, audio: AudioClient):
        self.llm = llm
        self.audio = audio
        self.telegram_client = None
        self.history: dict[int, list[dict]] = {}

    async def handle_message(self, history: str, user: User, text: str, images: list[bytes]) -> str:
        name = f"{user.first_name} {user.last_name}" if user.last_name else user.first_name
        messages=[
            {'role': 'system', 'content': llm["character_prompt"]},
            {'role': 'system', 'content': f"You're chatting with a user named \"{name}\", who has the username \"{user.username}\" and ID \"{user.id}\"."},
        ]

        to_insert = {"role": "user", "content": text}
        if images:
            to_insert["images"] = images
        history.append(to_insert)
        messages += history

        print(messages)

        voice_tool = make_voice_tool(self.telegram_client, self.audio, user.id)

        #tool loop
        for _ in range(10):
            response: Message = await self.llm.chat(
                tools=TOOLS + [voice_tool],
                messages=messages,
            )

            messages.append(response)

            if not response.tool_calls:
                return response.content

            for call in response.tool_calls:
                fn_name = call.function.name
                print(f"Tool call: {fn_name} with args: {call.function.arguments}")
                fn_args = call.function.arguments

                tool_impls = {**TOOL_FUNCS, "send_voice_message": voice_tool}

                fn = tool_impls.get(fn_name)
                if fn is None:
                    result = f"Unknown tool: {fn_name}"
                else:
                    result = await fn(**fn_args)

                if fn_name == "stay_silent" or fn_name == "send_voice_message":
                    return None

                messages.append({
                    "role": "tool",
                    "content": str(result),
                })

        #tool loop exceeded
        response: Message = await self.llm.chat(
            messages=messages,
            tools=[voice_tool, stay_silent]
        )

        return response.content