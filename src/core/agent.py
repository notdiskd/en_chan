import base64

from core.ai import LLMClient, AudioClient
from config import llm
from tools.llm_tools import TOOLS, TOOL_FUNCS, stay_silent
from telethon.types import User
from tools.telegram_tools import make_send_message_tool, make_send_voice_message_tool
from tools.tool_schema import function_to_tool
import json

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

        to_insert = {"role": "user"}
        if images:
            content = [{"type": "text", "text": text}]
            for img in images:
                b64 = base64.b64encode(img).decode()
                content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})
            to_insert["content"] = content
        else:
            to_insert["content"] = text
        history.append(to_insert)
        messages += history

        print(messages)

        message_tool = make_send_message_tool(self.telegram_client, user.id)
        voice_tool = make_send_voice_message_tool(self.telegram_client, self.audio, user.id)
        tools = TOOLS + [function_to_tool(voice_tool), function_to_tool(message_tool)]

        msg_count = 0

        #loop
        for _ in range(10):
            response = await self.llm.tool_chat(
                tools=tools,
                messages=messages,
            )

            messages.append(response)

            if not response.tool_calls:
                print("оно это как вообще сделало лол")
                return None

            for call in response.tool_calls:
                fn_name = call.function.name
                fn_args = json.loads(call.function.arguments)
                print(f"Tool call: {fn_name} with args: {fn_args}")

                tool_impls = {**TOOL_FUNCS, "send_voice_message": voice_tool, "send_message": message_tool}

                fn = tool_impls.get(fn_name)
                result = await fn(**fn_args) if fn else f"Unknown tool: {fn_name}"

                if fn_name == "stay_silent":
                    return None
                if fn_name in ("send_voice_message", "send_message"):
                    msg_count += 1
                    if msg_count >= 3:
                        return None

                messages.append({
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": str(result),
                })

        return None