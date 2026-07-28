import base64
from core.ai import OpenRouterClient, FishAudioClient
from config import prompts
from tools.llm_tools import TOOLS, TOOL_IMPLS
from telethon.types import User
from tools.telegram_tools import TG_TOOLS
from tools.tool_schema import function_to_tool
from storage.memory import Memory
from tools.memory_tools import make_search_memory_tool, make_core_memory_tools
import json

ACTION_TOOL_NAMES = ("send_message", "send_voice_message", "stay_silent", "wait")
MAX_INFO_CALLS = 7

class Agent:
    def __init__(self, llm: OpenRouterClient, audio: FishAudioClient, memory: Memory):
        self.llm = llm
        self.audio = audio
        self.memory = memory
        self.telegram_client = None

    async def describe_image(self, image_bytes: bytes, context_text: str = "") -> str:
        b64 = base64.b64encode(image_bytes).decode()

        prompt = prompts["image_description_prompt"]
        if context_text:
            prompt += f"\n\nThe user sent this along with the caption: \"{context_text}\""

        response = await self.llm.chat(
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                ],
            }]
        )
        return response.content

    async def handle_message(self, history: str, user: User, id_map: dict[int, int]) -> str:
        name = f"{user.first_name} {user.last_name}" if user.last_name else user.first_name

        core_content = await self.memory.get_core()
        last_user_text = next((m["content"] for m in reversed(history) if m["role"] == "user"), "")
        auto_context = await self.memory.auto_retrieve(last_user_text, user_id=user.id) if last_user_text else None

        messages=[
            {'role': 'system', 'content': prompts["character_prompt"]},
            {'role': 'system', 'content': f"You're chatting with a user named \"{name}\", who has the username \"{user.username}\" and ID \"{user.id}\"."},
        ]

        if core_content:
            messages.append({"role": "system", "content": f"Things you always keep in mind:\n{core_content}"})
        if auto_context:
            messages.append({"role": "system", "content": auto_context})
        messages += history

        print(messages[1:])

        tg_context = {"client": self.telegram_client, "chat_id": user.id, "audio": self.audio, "id_map": id_map}
        tg_tool_fns = [factory(**tg_context) for factory in TG_TOOLS]

        search_memory_tool = make_search_memory_tool(self.memory)
        core_append, core_replace = make_core_memory_tools(self.memory)
        memory_tool_fns = [search_memory_tool, core_append, core_replace]

        dynamic_fns = tg_tool_fns + memory_tool_fns
        dynamic_impls = {fn.__name__: fn for fn in dynamic_fns}
        dynamic_schema = [function_to_tool(fn) for fn in dynamic_fns]

        all_tool_impls = {**TOOL_IMPLS, **dynamic_impls}
        all_tools_schema = TOOLS + dynamic_schema

        info_call_count = 0
        msg_count = 0

        #цикл для действий ллмки
        for _ in range(10):
            available = [
                t for t in all_tools_schema
                if info_call_count < MAX_INFO_CALLS or t["function"]["name"] in ACTION_TOOL_NAMES
            ]

            response = await self.llm.tool_chat(tools=available, messages=messages)
            messages.append(response)

            if not response.tool_calls:
                print("stop")
                return None

            for call in response.tool_calls:
                fn_name = call.function.name

                allowed_names = {t["function"]["name"] for t in available}
                if fn_name not in allowed_names:
                    messages.append({"role": "tool", "tool_call_id": call.id, "content": f"'{fn_name}' wasn't available this turn."})
                    continue

                fn_args = json.loads(call.function.arguments)
                print(f"Tool call: {fn_name} with args: {fn_args}")

                fn = all_tool_impls.get(fn_name)
                try:
                    result = await fn(**fn_args) if fn else f"Unknown tool: {fn_name}"
                except Exception as e:
                    print(f"Tool '{fn_name}' raised an exception: {e}")
                    result = f"Error executing {fn_name}"

                if fn_name == "stay_silent":
                    return None
                elif fn_name in ("send_message", "send_voice_message"):
                    msg_count += 1
                    if msg_count >= 3:
                        return None
                elif fn_name not in ACTION_TOOL_NAMES:
                    info_call_count += 1

                messages.append({
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": str(result),
                })

        return None