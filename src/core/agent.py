import base64
from datetime import datetime, timezone
from core.ai import OpenRouterClient, FishAudioClient
from config import prompts
from tools.llm_tools import TOOLS, TOOL_IMPLS
from telethon.types import User
from tools.telegram_tools import TG_TOOLS
from tools.tool_schema import function_to_tool
from storage.memory import Memory
from tools.memory_tools import (make_search_memory_tool, make_day_state_tools,
                                make_profile_tool, make_shared_person_tools)
import json

ACTION_TOOL_NAMES = ("send_message", "send_voice_message", "stay_silent", "wait")
THINK_TOOL_NAME = "think"
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

        day_state = await self.memory.get_day_state()
        profile = await self.memory.format_profile(user.id)
        last_user_text = next((m["content"] for m in reversed(history) if m["role"] == "user"), "")
        auto_context = await self.memory.auto_retrieve(last_user_text, user_id=user.id) if last_user_text else None
        current_date = datetime.now(timezone.utc).strftime("%A, %d %B %Y, %H:%M (UTC)")

        messages=[
            {'role': 'system', 'content': prompts["character_prompt"]},
            {"role": "system", "content": f"Today's date is {current_date}. Use it to interpret relative dates."},
            {'role': 'system', 'content': f"You're chatting with a user named \"{name}\", who has the username \"{user.username}\" and ID \"{user.id}\"."},
        ]

        if day_state:
            messages.append({"role": "system", "content": f"Your state for today:\n{day_state}"})
        if profile:
            messages.append({"role": "system", "content": f"Facts about this user:\n{profile}"})
        if auto_context:
            messages.append({"role": "system", "content": auto_context})
        messages += history

        print(messages[1:])

        tg_context = {"client": self.telegram_client, "chat_id": user.id, "audio": self.audio, "id_map": id_map}
        tg_tool_fns = [factory(**tg_context) for factory in TG_TOOLS]

        search_memory_tool = make_search_memory_tool(self.memory, user.id)
        replace_day_state, = make_day_state_tools(self.memory)
        update_profile = make_profile_tool(self.memory, user.id)
        shared_person_tools = make_shared_person_tools(self.memory, user.id, name)
        memory_tool_fns = [search_memory_tool, replace_day_state, update_profile,
                           *shared_person_tools]

        dynamic_fns = tg_tool_fns + memory_tool_fns
        dynamic_impls = {fn.__name__: fn for fn in dynamic_fns}
        dynamic_schema = [function_to_tool(fn) for fn in dynamic_fns]

        all_tool_impls = {**TOOL_IMPLS, **dynamic_impls}
        all_tools_schema = TOOLS + dynamic_schema

        info_call_count = 0
        msg_count = 0
        think_call_count = 0

        #цикл для действий ллмки
        for _ in range(10):
            available = [
                t for t in all_tools_schema
                if (info_call_count < MAX_INFO_CALLS
                    or t["function"]["name"] in ACTION_TOOL_NAMES
                    or t["function"]["name"] == THINK_TOOL_NAME)
                and (t["function"]["name"] != THINK_TOOL_NAME or think_call_count == 0)
            ]

            response = await self.llm.tool_chat(tools=available, messages=messages)
            messages.append(response)

            if not response.tool_calls:
                print("stop")
                return None

            # Give the model a separate turn to review its plan before any
            # other tool call from the same response is allowed to execute.
            allowed_names = {t["function"]["name"] for t in available}
            think_call = next(
                (call for call in response.tool_calls
                 if call.function.name == THINK_TOOL_NAME and call.function.name in allowed_names),
                None,
            )
            if think_call is not None:
                think_args = json.loads(think_call.function.arguments)
                think_fn = all_tool_impls[THINK_TOOL_NAME]
                try:
                    think_result = await think_fn(**think_args)
                except Exception as e:
                    print(f"Tool '{THINK_TOOL_NAME}' raised an exception: {e}")
                    think_result = "Error recording the plan. Continue with the next appropriate tool."
                think_call_count += 1

                for call in response.tool_calls:
                    if call.id == think_call.id:
                        result = think_result
                    else:
                        result = "Deferred: review the plan first, then make this tool call in the next turn if it is still needed."
                    messages.append({
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": str(result),
                    })
                continue

            for call in response.tool_calls:
                fn_name = call.function.name

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
