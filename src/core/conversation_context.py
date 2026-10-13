"""Build bounded chat history and maintain a private rolling chat summary."""

from storage.messages import (
    format_for_llm,
    get_chat_summary,
    get_messages_after,
    save_chat_summary,
)


HISTORY_MESSAGE_TARGET = 30
HISTORY_CHAR_TARGET = 20_000
SUMMARY_TRIGGER_MESSAGE_COUNT = 40
SUMMARY_TRIGGER_CHARS = 30_000
SUMMARY_BATCH_CHARS = 9_000
MAX_SUMMARY_BATCHES_PER_TURN = 2
MAX_SUMMARY_CHARS = 3_000


def _message_text(message) -> str:
    attachment = ""
    if message.attachment_type in ("image", "album") and message.attachment_summary:
        attachment = f" [image: {message.attachment_summary}]"
    elif message.attachment_type == "voice":
        attachment = " [voice message]"
    timestamp = message.timestamp.isoformat() if message.timestamp else "unknown time"
    text = message.text or ""
    if len(text) > SUMMARY_BATCH_CHARS:
        text = text[:7_000] + " [middle omitted] " + text[-1_500:]
    return f"[{timestamp}] {message.role}: {text}{attachment}"


def _select_recent_suffix(messages):
    formatted, _ = format_for_llm(messages)
    sizes = [len(message["content"]) for message in formatted]
    selected = []
    used = 0
    for message, size in zip(reversed(messages), reversed(sizes)):
        if selected and (
            len(selected) >= HISTORY_MESSAGE_TARGET
            or used + size > HISTORY_CHAR_TARGET
        ):
            break
        selected.append(message)
        used += size
    selected.reverse()
    split = len(messages) - len(selected)
    return messages[:split], selected


def _summary_batches(messages):
    batches = []
    current = []
    current_chars = 0
    for message in messages:
        line = _message_text(message)
        line_chars = len(line)
        if current and current_chars + line_chars > SUMMARY_BATCH_CHARS:
            batches.append(current)
            current = []
            current_chars = 0
        current.append((message, line))
        current_chars += line_chars
    if current:
        batches.append(current)
    return batches


async def build_chat_context(chat_id: int, llm):
    stored_summary = await get_chat_summary(chat_id)
    summary_text = stored_summary.content if stored_summary else ""
    summarized_through = stored_summary.summarized_through_id if stored_summary else 0
    unsummarized = await get_messages_after(chat_id, summarized_through)

    formatted, _ = format_for_llm(unsummarized)
    total_chars = sum(len(message["content"]) for message in formatted)

    # Batch compression keeps its cadence predictable without a model tokenizer.
    if (
        len(unsummarized) <= SUMMARY_TRIGGER_MESSAGE_COUNT
        and total_chars <= SUMMARY_TRIGGER_CHARS
    ):
        old_messages, recent_messages = [], unsummarized
    else:
        old_messages, recent_messages = _select_recent_suffix(unsummarized)

    batches = _summary_batches(old_messages)[:MAX_SUMMARY_BATCHES_PER_TURN]
    summarized_messages = []

    for batch in batches:
        transcript = "\n".join(line for _, line in batch)
        prompt = (
            "Update the compact continuity summary for this one private Telegram chat. "
            "Keep ongoing topics, plans and promises, stable preferences and boundaries, "
            "important relationship context, and meaningful events. Preserve who said a "
            "fact and mark uncertainty; do not turn guesses into facts. Omit small talk "
            "and details that are no longer useful. This summary is private to this chat "
            "and must not be shared with other chats. Write concise notes, at most 3000 "
            "characters.\n\n"
            f"Existing summary:\n{summary_text or '(empty)'}\n\n"
            f"New messages in chronological order:\n{transcript}"
        )
        try:
            response = await llm.chat(messages=[{"role": "user", "content": prompt}])
            updated = (response.content or "").strip()
            if not updated:
                break
            summary_text = updated[:MAX_SUMMARY_CHARS]
            summarized_messages.extend(message for message, _ in batch)
        except Exception as exc:
            print(f"Chat summary update failed for chat {chat_id}: {exc}")
            break

    if summarized_messages:
        summarized_through = summarized_messages[-1].id
        await save_chat_summary(chat_id, summarized_through, summary_text)

    history, id_map = format_for_llm(recent_messages)
    return summary_text, history, id_map
