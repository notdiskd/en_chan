from storage.diary import save_diary_entry

async def write_diary_entry(text: str, mood: str, mentioned_user_ids: list[int]) -> str:
    """
    Save a diary entry for today. Use this to record your reflection on the day.

    Args:
        text: The diary entry text, written in first person (3-5 sentences)
        mood: Your overall mood today — one word, e.g. 'happy', 'neutral', 'tired', 'annoyed'
        mentioned_user_ids: List of user_id values (from the conversation headers) 
                             for every person mentioned or referenced in this entry. 
                             Use the exact user_id numbers shown in the conversation summaries — 
                             do not guess or invent IDs.
    """
    await save_diary_entry(text=text, mood=mood, mentioned_users=mentioned_user_ids)
    return "Diary entry saved."