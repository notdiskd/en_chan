from sqlalchemy import select
from storage.db import DiaryEntry, async_session
import numpy as np
from core.ai import OpenRouterClient
from tools.diary_tools import make_write_diary_entry_tool
from tools.tool_schema import function_to_tool
import json
import asyncio
import datetime
from config import prompts

def _cosine_similarity(a: list[float], b: np.ndarray) -> float:
    a = np.array(a)
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))

def _serialize_embedding(vec: list[float]) -> bytes:
    return np.array(vec, dtype=np.float32).tobytes()

def _deserialize_embedding(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.float32)

SIMILARITY_THRESHOLD = 0.5
MAX_ENTRY_CHARS = 1000

class Diary:
    def __init__(self, llm: OpenRouterClient):
        self.llm = llm

    async def save_diary_entry(self, text: str, mood: str = "neutral", mentioned_users: list[int] = None) -> None:
        embedding = await self.llm.get_embedding(text)
        async with async_session() as session:
            entry = DiaryEntry(
                text=text,
                mood=mood,
                mentioned_users=mentioned_users or [],
                embedding=_serialize_embedding(embedding),
            )
            session.add(entry)
            await session.commit()

    async def search_diary(self, query: str, top_k: int = 2, user_id: int | None = None) -> str:
        """
        Search the diary for entries relevant to the query. Use this when you want
        to recall something from a past day or conversation.

        Args:
            query: What to search for, e.g. 'conversation about Vanya's job'
            top_k: How many entries to return at most
            user_id: Optional — filter to entries mentioning this specific user_id
        """
        query_vec = await self.llm.get_embedding(query)

        async with async_session() as session:
            result = await session.execute(select(DiaryEntry))
            all_entries = result.scalars().all()

        scored = []
        for entry in all_entries:
            if user_id is not None and user_id not in (entry.mentioned_users or []):
                continue

            entry_vec = _deserialize_embedding(entry.embedding)
            score = _cosine_similarity(query_vec, entry_vec)

            if score >= SIMILARITY_THRESHOLD:
                scored.append((score, entry))

        if not scored:
            return "Nothing relevant found in the diary."

        scored.sort(key=lambda x: x[0], reverse=True)
        top = scored[:top_k]

        lines = []
        for score, entry in top:
            text = entry.text
            if len(text) > MAX_ENTRY_CHARS:
                text = text[:MAX_ENTRY_CHARS] + "..."
            lines.append(f"[{entry.date.date()}] {text}")

        return "\n---\n".join(lines)
    
    async def run_daily_reflection(self, day_summaries: str):
        if not day_summaries.strip():
            return
        
        write_diary_entry = make_write_diary_entry_tool(self)

        tools = [function_to_tool(write_diary_entry)]

        response = await self.llm.tool_chat(
            messages=[{"role": "user", "content": prompts["reflection_prompt"].format(summaries=day_summaries)}],
            tools=tools
        )

        for call in response.tool_calls:
            if call.function.name == "write_diary_entry":
                args = json.loads(call.function.arguments)
                await write_diary_entry(**args)

    async def schedule_daily_reflection(self, telegram_bot, hour: int = 23):
        while True:
            now = datetime.datetime.now()
            next_run = now.replace(hour=hour, minute=0, second=0, microsecond=0)
            if next_run <= now:
                next_run += datetime.timedelta(days=1)

            await asyncio.sleep((next_run - now).total_seconds())

            summaries = await telegram_bot.get_today_summaries()
            await self.run_daily_reflection(summaries)