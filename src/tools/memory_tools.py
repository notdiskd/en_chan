def make_search_memory_tool(memory):
    async def search_memory(query: str, type: str = "", top_k: int = 3) -> str:
        """
        Search your memory archive for something specific — past facts, 
        reflections, or events. Use this when you deliberately want to recall 
        something concrete, distinct from what's already surfaced automatically.

        Args:
            query: What to search for
            type: Optional filter — "reflection", "fact", or "event". Leave empty to search all.
            top_k: How many results to return
        """
        results = await memory.search(query, type=type or None, top_k=top_k)
        if not results:
            return "Nothing found."
        return "\n---\n".join(f"[{e.type}, {e.created_at.date()}] {e.text}" for _, e in results)
    return search_memory


def make_core_memory_tools(memory):
    async def core_memory_append(text: str) -> str:
        """
        Permanently add a short fact or note to your core memory — the small 
        set of things you always keep in mind, no searching required. Use this 
        for things worth remembering indefinitely across all conversations: 
        an important ongoing fact about your life, a commitment, something 
        that shapes how you currently feel. Keep it short — one clear sentence.

        Args:
            text: The fact or note to remember, as a short first-person statement
        """
        return await memory.append_core(text)

    async def core_memory_replace(old_text: str, new_text: str) -> str:
        """
        Update or remove something in your core memory — e.g. when a fact 
        changes or something is no longer relevant. Pass an empty new_text 
        to remove it entirely.

        Args:
            old_text: The exact existing text to replace (must match what's currently stored)
            new_text: The replacement text, or empty string to just remove it
        """
        return await memory.replace_core(old_text, new_text)

    return core_memory_append, core_memory_replace

def make_save_reflection_tool(memory):
    async def save_reflection(text: str, mentioned_user_ids: list[int]) -> str:
        """
        Save today's diary reflection to your memory archive.

        Args:
            text: The reflection text, first person, 3-5 sentences
            mentioned_user_ids: user_id of everyone mentioned, from the conversation headers
        """
        await memory.save(type="reflection", text=text, meta={"mentioned_users": mentioned_user_ids})
        return "Reflection saved."
    return save_reflection