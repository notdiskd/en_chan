import asyncio
import json
from datetime import datetime, timezone, timedelta

import numpy as np
from sqlalchemy import delete, func, or_, select

from config import prompts
from core.ai import OpenRouterClient
from storage.db import DayState, MemoryEntry, SharedPersonFact, UserProfile, async_session
from tools.memory_tools import make_reflection_tools
from tools.tool_schema import function_to_tool

MAX_DAY_STATE_CHARS = 1500
MAX_PROFILE_FACTS = 20
MAX_PROFILE_FACT_CHARS = 300
AUTO_RETRIEVE_THRESHOLD = 0.55
AUTO_RETRIEVE_TOP_K = 2
SHARED_FACT_TOP_K = 3
SHARED_FACT_CATEGORIES = {"basic", "relationship", "interest"}

class Memory:
    def __init__(self, llm_client: OpenRouterClient):
        self.llm = llm_client

    async def save(self, type: str, text: str, user_id: int | None = None,
                   importance: float = 0.5, meta: dict | None = None) -> None:
        embedding = await self.llm.get_embedding(text)
        async with async_session() as session:
            session.add(MemoryEntry(
                type=type, text=text,
                embedding=np.array(embedding, dtype=np.float32).tobytes(),
                user_id=user_id, importance=importance, meta=meta or {},
            ))
            await session.commit()

    async def search(self, query: str, type: str | None = None,
                     user_id: int | None = None, include_global: bool = False,
                     top_k: int = 3) -> list[tuple[float, MemoryEntry]]:
        query_vec = np.array(await self.llm.get_embedding(query), dtype=np.float32)
        async with async_session() as session:
            stmt = select(MemoryEntry)
            if type is not None:
                stmt = stmt.where(MemoryEntry.type == type)
            if user_id is not None:
                allowed = MemoryEntry.user_id == user_id
                if include_global:
                    allowed = or_(allowed, MemoryEntry.user_id.is_(None))
                stmt = stmt.where(allowed)
            entries = (await session.execute(stmt)).scalars().all()

        scored = []
        for entry in entries:
            vector = np.frombuffer(entry.embedding, dtype=np.float32)
            if vector.shape != query_vec.shape:
                continue
            denominator = np.linalg.norm(query_vec) * np.linalg.norm(vector)
            if not denominator:
                continue
            score = float(np.dot(query_vec, vector) / denominator)
            scored.append((score * (0.85 + 0.3 * entry.importance), entry))
        scored.sort(key=lambda item: item[0], reverse=True)
        return scored[:top_k]

    async def auto_retrieve(self, query: str, user_id: int) -> str | None:
        # User episodes stay scoped to that person. Global diary entries are
        # intentionally excluded from private chats; shared facts have their
        # own provenance-aware store below.
        results = await self.search(query, user_id=user_id, include_global=False,
                                    top_k=AUTO_RETRIEVE_TOP_K)
        relevant = [(score, entry) for score, entry in results if score >= AUTO_RETRIEVE_THRESHOLD]
        lines = [f"[{entry.type}, {entry.created_at.date()}] {entry.text}"
                 for _, entry in relevant]

        shared_facts = await self.search_shared_person_facts(query, top_k=SHARED_FACT_TOP_K)
        for score, fact in shared_facts:
            if score >= AUTO_RETRIEVE_THRESHOLD:
                source = fact.source_name or f"user {fact.source_user_id}"
                lines.append(
                    f"[shared fact about {fact.subject}; {fact.category}; told by {source} "
                    f"on {fact.created_at.date()}] {fact.fact} "
                    f"(source user_id={fact.source_user_id})"
                )
        if not lines:
            return None
        return "Relevant memories (respect the source; shared facts are reports, not independently confirmed):\n" + "\n".join(lines)

    async def save_shared_person_fact(self, subject: str, fact: str, category: str,
                                     source_user_id: int, source_name: str = "") -> str:
        subject, fact, category = subject.strip(), fact.strip(), category.strip().lower()
        if not subject or len(subject) > 160:
            return "The person's name or identifying description must be 1-160 characters."
        if not fact or len(fact) > 500:
            return "The fact must be 1-500 characters."
        if category not in SHARED_FACT_CATEGORIES:
            return "Category must be basic, relationship, or interest."

        search_text = f"{subject}: {fact}"
        embedding = await self.llm.get_embedding(search_text)
        async with async_session() as session:
            existing = await session.execute(
                select(SharedPersonFact.id).where(
                    func.lower(SharedPersonFact.subject) == subject.lower(),
                    func.lower(SharedPersonFact.fact) == fact.lower(),
                    SharedPersonFact.source_user_id == source_user_id,
                ).limit(1)
            )
            if existing.scalar_one_or_none() is not None:
                return "That shared person fact from this source is already saved."
            session.add(SharedPersonFact(
                subject=subject,
                fact=fact,
                category=category,
                source_user_id=source_user_id,
                source_name=source_name.strip(),
                embedding=np.array(embedding, dtype=np.float32).tobytes(),
            ))
            await session.commit()
        return "Shared person fact saved with its source."

    async def search_shared_person_facts(self, query: str, top_k: int = 3) -> list[tuple[float, SharedPersonFact]]:
        async with async_session() as session:
            facts = (await session.execute(select(SharedPersonFact))).scalars().all()

        if not facts:
            return []
        query_vec = np.array(await self.llm.get_embedding(query), dtype=np.float32)
        scored = []
        for fact in facts:
            vector = np.frombuffer(fact.embedding, dtype=np.float32)
            if vector.shape != query_vec.shape:
                continue
            denominator = np.linalg.norm(query_vec) * np.linalg.norm(vector)
            if not denominator:
                continue
            score = float(np.dot(query_vec, vector) / denominator)
            scored.append((score, fact))
        scored.sort(key=lambda item: item[0], reverse=True)
        return scored[:max(0, min(int(top_k), 10))]

    async def forget_shared_person_fact(self, subject: str, fact: str,
                                        source_user_id: int) -> str:
        async with async_session() as session:
            result = await session.execute(
                delete(SharedPersonFact).where(
                    func.lower(SharedPersonFact.subject) == subject.strip().lower(),
                    func.lower(SharedPersonFact.fact) == fact.strip().lower(),
                    SharedPersonFact.source_user_id == source_user_id,
                )
            )
            await session.commit()
        return "Shared fact removed." if result.rowcount else "No matching fact from your account was found."

    async def get_day_state(self) -> str:
        today = datetime.now(timezone.utc).date()
        async with async_session() as session:
            row = await session.get(DayState, 1)
            return row.content if row and row.state_date == today else ""

    async def set_day_state(self, text: str, state_date=None) -> str:
        text = text.strip()
        if len(text) > MAX_DAY_STATE_CHARS:
            return f"Day state is too long ({len(text)}/{MAX_DAY_STATE_CHARS} chars)."
        state_date = state_date or datetime.now(timezone.utc).date()
        async with async_session() as session:
            row = await session.get(DayState, 1)
            if row is None:
                row = DayState(id=1, content="")
                session.add(row)
            row.content = text
            row.state_date = state_date
            row.updated_at = datetime.now(timezone.utc)
            await session.commit()
        return "Day state updated."

    async def set_next_day_state(self, text: str) -> str:
        tomorrow = datetime.now(timezone.utc).date() + timedelta(days=1)
        return await self.set_day_state(text, state_date=tomorrow)

    async def get_profile(self, user_id: int) -> list[str]:
        async with async_session() as session:
            row = await session.get(UserProfile, user_id)
            return list(row.facts or []) if row else []

    async def format_profile(self, user_id: int) -> str | None:
        facts = await self.get_profile(user_id)
        return "\n".join(f"- {fact}" for fact in facts) if facts else None

    async def update_profile(self, user_id: int, action: str, fact: str,
                             old_fact: str = "") -> str:
        fact, old_fact = fact.strip(), old_fact.strip()
        if action not in {"add", "replace", "remove"}:
            return "Invalid action. Use add, replace, or remove."
        if action != "remove" and (not fact or len(fact) > MAX_PROFILE_FACT_CHARS):
            return f"A profile fact must be 1-{MAX_PROFILE_FACT_CHARS} characters."
        if action in {"replace", "remove"} and not old_fact:
            return "old_fact is required for replace and remove."

        async with async_session() as session:
            row = await session.get(UserProfile, user_id)
            if row is None:
                row = UserProfile(user_id=user_id, facts=[])
                session.add(row)
            facts = list(row.facts or [])
            if action == "add":
                if fact not in facts:
                    if len(facts) >= MAX_PROFILE_FACTS:
                        return "Profile is full; replace or remove an outdated fact first."
                    facts.append(fact)
            else:
                try:
                    index = facts.index(old_fact)
                except ValueError:
                    return "That exact old_fact is not in this profile."
                if action == "replace":
                    facts[index] = fact
                else:
                    facts.pop(index)
            row.facts = facts
            row.updated_at = datetime.now(timezone.utc)
            await session.commit()
        return "User profile updated."

    async def schedule_daily_reflection(self, get_day_summaries_fn, hour: int = 23):
        while True:
            now = datetime.now(timezone.utc)
            next_run = now.replace(hour=hour, minute=0, second=0, microsecond=0)
            if next_run <= now:
                next_run += timedelta(days=1)
            await asyncio.sleep((next_run - now).total_seconds())
            await self.run_daily_reflection(await get_day_summaries_fn())

    async def run_daily_reflection(self, day_summaries: str):
        if not day_summaries.strip():
            await self.set_next_day_state("")
            return

        day_state = await self.get_day_state()
        save_diary, save_episode, set_carryover = make_reflection_tools(self)
        functions = (save_diary, save_episode, set_carryover)
        tools = [function_to_tool(fn) for fn in functions]
        tool_impls = {fn.__name__: fn for fn in functions}
        messages = [{"role": "user", "content": prompts["reflection_prompt"].format(
            day_state=day_state or "(empty)", summaries=day_summaries
        )}]

        for _ in range(8):
            response = await self.llm.tool_chat(messages=messages, tools=tools, tool_choice="auto")
            messages.append(response)
            if not response.tool_calls:
                break
            for call in response.tool_calls:
                fn = tool_impls.get(call.function.name)
                args = json.loads(call.function.arguments)
                result = await fn(**args) if fn else "Unknown tool"
                messages.append({"role": "tool", "tool_call_id": call.id, "content": result})
