import base64
from core.ai import OpenRouterClient, FishAudioClient
from config import prompts
from tools.llm_tools import TOOLS, TOOL_IMPLS
from telethon.types import User
from tools.telegram_tools import TG_TOOLS
from tools.tool_schema import function_to_tool
from storage.diary import Diary
import json

ACTION_TOOL_NAMES = ("send_message", "send_voice_message", "stay_silent", "wait")
MAX_INFO_CALLS = 7

class Agent:
    def __init__(self, llm: OpenRouterClient, audio: FishAudioClient, diary: Diary):
        self.llm = llm
        self.audio = audio
        self.diary = diary
        self.telegram_client = None
        self.history: dict[int, list[dict]] = {}

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
        messages=[
            {'role': 'system', 'content': prompts["character_prompt"]},
            {'role': 'system', 'content': f"You're chatting with a user named \"{name}\", who has the username \"{user.username}\" and ID \"{user.id}\"."},
        ]

        messages += history

        print(messages)

        search_diary_tool = self.diary.search_diary

        tg_context = {"client": self.telegram_client, "chat_id": user.id, "audio": self.audio, "id_map": id_map}
        tg_tool_fns = [factory(**tg_context) for factory in TG_TOOLS]

        dynamic_impls = {fn.__name__: fn for fn in tg_tool_fns}
        dynamic_impls[search_diary_tool.__name__] = search_diary_tool
        dynamic_schema = [function_to_tool(fn) for fn in tg_tool_fns] + [function_to_tool(search_diary_tool)]

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