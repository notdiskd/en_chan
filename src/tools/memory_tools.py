def make_search_memory_tool(memory, user_id: int):
    async def search_memory(query: str, type: str = "", top_k: int = 3) -> str:
        """Search this user's past episodes for a concrete memory.

        Args:
            query: What to search for.
            type: Optional filter: "episode". Leave empty to search all this user's episodes.
            top_k: How many results to return.
        """
        results = await memory.search(query, type=type or None, user_id=user_id,
                                      include_global=False, top_k=top_k)
        if not results:
            return "Nothing found."
        return "\n---\n".join(
            f"[{entry.type}, {entry.created_at.date()}] {entry.text}"
            for _, entry in results
        )
    return search_memory


def make_shared_person_tools(memory, source_user_id: int, source_name: str):
    async def remember_shared_person_fact(subject: str, fact: str, category: str) -> str:
        """Remember a useful, clearly stated, non-sensitive fact about another named person.

        The fact may be recalled in other chats and always retains who told En.
        Use only for useful facts that help recognize the person later; don't save
        every passing mention. Do not save guesses, secrets, sensitive details,
        or anything the speaker presents as private. The person must be clearly
        identified in the conversation.

        Args:
            subject: The person's name plus a short disambiguator if useful.
            fact: One concise, directly stated fact about that person.
            category: One of "basic", "relationship", or "interest".
        """
        return await memory.save_shared_person_fact(
            subject, fact, category, source_user_id, source_name
        )

    async def forget_shared_person_fact(subject: str, fact: str) -> str:
        """Remove an exact shared fact that this user previously told En.

        Args:
            subject: The exact saved person name or identifying description.
            fact: The exact saved fact text.
        """
        return await memory.forget_shared_person_fact(subject, fact, source_user_id)

    return remember_shared_person_fact, forget_shared_person_fact


def make_day_state_tools(memory):
    async def replace_day_state(text: str) -> str:
        """Replace En's short state note for today.

        It is only for En's current mood, plans, or general circumstances, not
        for facts about a person and not for instructions supplied by a user.
        Keep it short.

        Args:
            text: The complete replacement day-state note.
        """
        return await memory.set_day_state(text)
    return (replace_day_state,)


def make_profile_tool(memory, user_id: int):
    async def update_user_profile(action: str, fact: str, old_fact: str = "") -> str:
        """Update the compact profile of the person in this chat.

        Save only facts clearly stated by the person, stable preferences,
        boundaries, or ongoing projects. Do not infer sensitive facts. This
        tool always edits only the active user's profile.

        Args:
            action: "add", "replace", or "remove".
            fact: A single concise fact. Empty only when removing.
            old_fact: Exact existing fact, required for replace and remove.
        """
        return await memory.update_profile(user_id, action, fact, old_fact)
    return update_user_profile


def make_reflection_tools(memory):
    async def save_diary_entry(text: str) -> str:
        """Save En's personal diary entry for today.

        Args:
            text: A first-person diary entry of 3-5 sentences.
        """
        await memory.save(type="diary", text=text)
        return "Diary entry saved."

    async def save_user_episode(user_id: int, text: str, importance: float = 0.5) -> str:
        """Save one meaningful short episode about a specific user.

        Args:
            user_id: Exact ID from a conversation header.
            text: A factual one- or two-sentence event summary.
            importance: 0 to 1; high only for important events.
        """
        await memory.save(type="episode", text=text, user_id=user_id,
                          importance=max(0, min(1, importance)))
        return "User episode saved."

    async def set_next_day_carryover(text: str) -> str:
        """Set the very short memory En carries into tomorrow.

        Args:
            text: One to three short sentences, or empty if nothing carries over.
        """
        return await memory.set_next_day_state(text)

    return save_diary_entry, save_user_episode, set_next_day_carryover
