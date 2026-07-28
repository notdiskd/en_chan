import asyncio
import datetime
from sqlalchemy import select
from storage.db import Reminder, async_session

def make_set_reminder_tool():
    async def set_reminder(due_date: str, note: str, user_id: int = 0) -> str:
        """
        Schedule something to be reminded of on a future date — e.g. a birthday 
        or a promise to follow up on something. Call this the moment you learn 
        of a future date worth remembering, not later.

        Args:
            due_date: Date in YYYY-MM-DD format
            note: What to remember, written so it makes sense when it resurfaces
            user_id: Optional — the user this reminder relates to, if any
        """
        try:
            date_obj = datetime.datetime.strptime(due_date, "%Y-%m-%d").replace(tzinfo=datetime.timezone.utc)
        except ValueError:
            return "Invalid date format, use YYYY-MM-DD."

        async with async_session() as session:
            session.add(Reminder(due_date=date_obj, note=note, user_id=user_id or None))
            await session.commit()
        return f"Reminder set for {due_date}."

    return set_reminder

async def check_due_reminders() -> list[Reminder]:
    today = datetime.datetime.now(datetime.timezone.utc).date()
    async with async_session() as session:
        result = await session.execute(select(Reminder).where(Reminder.fired == ""))
        pending = result.scalars().all()

        due = [r for r in pending if r.due_date.date() <= today]
        for r in due:
            r.fired = datetime.datetime.now(datetime.timezone.utc).isoformat()
        await session.commit()

    return due

async def schedule_reminder_checks(telegram_bot, check_interval_hours: int = 6):
    while True:
        due = await check_due_reminders()
        for reminder in due:
            note = f"Reminder that came due today: {reminder.note}"
            if reminder.user_id:
                await telegram_bot.client.send_message(reminder.user_id, note)
            print(f"Reminder fired: {reminder.note}")
        await asyncio.sleep(check_interval_hours * 3600)