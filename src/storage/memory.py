from sqlalchemy import select
from storage.db import MemoryEntry, CoreMemory, async_session
import numpy as np
from datetime import datetime, timezone, timedelta
from core.ai import OpenRouterClient
from tools.memory_tools import make_core_memory_tools, make_save_reflection_tool
from tools.tool_schema import function_to_tool
from config import prompts
import json
import asyncio

MAX_CORE_MEMORY_CHARS = 2500
AUTO_RETRIEVE_THRESHOLD = 0.55
AUTO_RETRIEVE_TOP_K = 2


class Memory:
    def __init__(self, llm_client: OpenRouterClient):
        self.llm = llm_client

    #архив

    async def save(self, type: str, text: str, user_id: int | None = None, importance: float = 0.5, meta: dict = None) -> None:
        embedding = await self.llm.get_embedding(text)
        async with async_session() as session:
            entry = MemoryEntry(
                type=type,
                text=text,
                embedding=np.array(embedding, dtype=np.float32).tobytes(),
                user_id=user_id,
                importance=importance,
                meta=meta or {},
            )
            session.add(entry)
            await session.commit()

    async def search(self, query: str, type: str | None = None, user_id: int | None = None, top_k: int = 3) -> list[tuple[float, "MemoryEntry"]]:
        query_vec = np.array(await self.llm.get_embedding(query))

        async with async_session() as session:
            stmt = select(MemoryEntry)
            if type is not None:
                stmt = stmt.where(MemoryEntry.type == type)
            if user_id is not None:
                stmt = stmt.where(MemoryEntry.user_id == user_id)
            result = await session.execute(stmt)
            entries = result.scalars().all()

        scored = []
        for entry in entries:
            vec = np.frombuffer(entry.embedding, dtype=np.float32)
            score = float(np.dot(query_vec, vec) / (np.linalg.norm(query_vec) * np.linalg.norm(vec)))
            adjusted = score * (0.85 + 0.3 * entry.importance)
            scored.append((adjusted, entry))

        scored.sort(key=lambda x: x[0], reverse=True)
        return scored[:top_k]

    async def auto_retrieve(self, query: str, user_id: int | None = None) -> str | None:
        results = await self.search(query, user_id=user_id, top_k=AUTO_RETRIEVE_TOP_K)
        relevant = [(score, e) for score, e in results if score >= AUTO_RETRIEVE_THRESHOLD]

        if not relevant:
            return None

        lines = [f"[{e.type}, {e.created_at.date()}] {e.text}" for _, e in relevant]
        return "Relevant memories that came to mind:\n" + "\n".join(lines)

    #главная память

    async def get_core(self) -> str:
        async with async_session() as session:
            row = await session.get(CoreMemory, 1)
            return row.content if row else ""

    async def append_core(self, text: str) -> str:
        async with async_session() as session:
            row = await session.get(CoreMemory, 1)
            new_content = (row.content + "\n" + text).strip() if row.content else text

            if len(new_content) > MAX_CORE_MEMORY_CHARS:
                return f"Core memory is full ({len(row.content)}/{MAX_CORE_MEMORY_CHARS} chars). Use core_memory_replace to make room first, or drop something less important."

            row.content = new_content
            row.updated_at = datetime.now(timezone.utc)
            await session.commit()
            return "Added to core memory."

    async def replace_core(self, old_text: str, new_text: str) -> str:
        async with async_session() as session:
            row = await session.get(CoreMemory, 1)
            if old_text not in row.content:
                return f"Text not found in core memory. Current content:\n{row.content}"

            row.content = row.content.replace(old_text, new_text, 1).strip()
            row.updated_at = datetime.now(timezone.utc)
            await session.commit()
            return "Core memory updated."
        
    # дневник

    async def schedule_daily_reflection(self, get_day_summaries_fn, hour: int = 23):
        while True:
            now = datetime.now(timezone.utc)
            next_run = now.replace(hour=hour, minute=0, second=0, microsecond=0)
            if next_run <= now:
                next_run += timedelta(days=1)

            await asyncio.sleep((next_run - now).total_seconds())

            summaries = await get_day_summaries_fn()
            await self.run_daily_reflection(summaries)

    async def run_daily_reflection(self, day_summaries: str):
        if not day_summaries.strip():
            return

        core_content = await self.get_core()
        save_tool = make_save_reflection_tool(self)
        core_append, core_replace = make_core_memory_tools(self)

        tools = [function_to_tool(save_tool), function_to_tool(core_append), function_to_tool(core_replace)]
        tool_impls = {"save_reflection": save_tool, "core_memory_append": core_append, "core_memory_replace": core_replace}

        messages = [{"role": "user", "content": prompts["reflection_prompt"].format(core_memory=core_content or "(empty)", summaries=day_summaries)}]

        for _ in range(4):   # рефлексия + опционально 1-2 правки
            response = await self.llm.tool_chat(messages=messages, tools=tools)
            messages.append(response)

            if not response.tool_calls:
                break

            for call in response.tool_calls:
                fn_args = json.loads(call.function.arguments)
                fn = tool_impls.get(call.function.name)
                result = await fn(**fn_args) if fn else "Unknown tool"
                messages.append({"role": "tool", "tool_call_id": call.id, "content": result})