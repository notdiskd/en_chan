from sqlalchemy import select
from storage.db import DiaryEntry, async_session
from core.embeddings import get_embedding, cosine_similarity, serialize_embedding, deserialize_embedding

SIMILARITY_THRESHOLD = 0.5
MAX_ENTRY_CHARS = 1000

async def save_diary_entry(text: str, mood: str = "neutral", mentioned_users: list[int] = None) -> None:
    embedding = await get_embedding(text)
    async with async_session() as session:
        entry = DiaryEntry(
            text=text,
            mood=mood,
            mentioned_users=mentioned_users or [],
            embedding=serialize_embedding(embedding),
        )
        session.add(entry)
        await session.commit()


async def search_diary(query: str, top_k: int = 2, user_id: int | None = None) -> str:
    """
    Search the diary for entries relevant to the query. Use this when you want
    to recall something from a past day or conversation.

    Args:
        query: What to search for, e.g. 'conversation about Vanya's job'
        top_k: How many entries to return at most
    """
    query_vec = await get_embedding(query)

    async with async_session() as session:
        result = await session.execute(select(DiaryEntry))
        all_entries = result.scalars().all()

    scored = []
    for entry in all_entries:
        if user_id is not None and user_id not in (entry.mentioned_users or []):
            continue

        entry_vec = deserialize_embedding(entry.embedding)
        score = cosine_similarity(query_vec, entry_vec)

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