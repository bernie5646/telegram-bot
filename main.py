
import asyncio
import os
import sqlite3
from contextlib import closing
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton
from aiogram.enums import ParseMode
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("Set BOT_TOKEN env var with your Telegram bot token")

TZ = ZoneInfo("Europe/Amsterdam")
DB_PATH = "data.db"

MORNING_TIME = (10, 0)
DAY_TIME = (15, 0)
EVENING_TIME = (21, 0)

bot = Bot(BOT_TOKEN, parse_mode=ParseMode.HTML)
dp = Dispatcher()
scheduler = AsyncIOScheduler(timezone=TZ)

# ---------- DB helpers ----------

def init_db():
    with closing(sqlite3.connect(DB_PATH)) as conn:
        c = conn.cursor()
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                chat_id INTEGER PRIMARY KEY,
                is_active INTEGER DEFAULT 1
            )
            """
        )
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER,
                kind TEXT,
                created_at TEXT,
                answers TEXT
            )
            """
        )
        conn.commit()


def add_user(chat_id: int):
    with closing(sqlite3.connect(DB_PATH)) as conn:
        c = conn.cursor()
        c.execute("INSERT OR IGNORE INTO users(chat_id, is_active) VALUES(?, 1)", (chat_id,))
        conn.commit()


def get_active_users():
    with closing(sqlite3.connect(DB_PATH)) as conn:
        c = conn.cursor()
        rows = c.execute("SELECT chat_id FROM users WHERE is_active = 1").fetchall()
        return [r[0] for r in rows]


def save_entry(chat_id: int, kind: str, answers: str):
    with closing(sqlite3.connect(DB_PATH)) as conn:
        c = conn.cursor()
        c.execute(
            "INSERT INTO entries(chat_id, kind, created_at, answers) VALUES(?,?,?,?)",
            (chat_id, kind, datetime.now(TZ).isoformat(), answers),
        )
        conn.commit()


def entries_count(chat_id: int, kind: str | None = None):
    with closing(sqlite3.connect(DB_PATH)) as conn:
        c = conn.cursor()
        if kind:
            row = c.execute(
                "SELECT COUNT(*) FROM entries WHERE chat_id=? AND kind=?",
                (chat_id, kind),
            ).fetchone()
        else:
            row = c.execute(
                "SELECT COUNT(*) FROM entries WHERE chat_id=?",
                (chat_id,),
            ).fetchone()
        return row[0] if row else 0


# ---------- UI helpers ----------
def survey_keyboard(kind: str) -> ReplyKeyboardMarkup:
    if kind == "morning":
        buttons = [
            [KeyboardButton(text="Настроение: 😄/🙂/😐/☹️/😖")],
            [KeyboardButton(text="Сон: отлично/норм/плохо")],
            [KeyboardButton(text="Энергия: высокая/средняя/низкая")],
            [KeyboardButton(text="Готово ✅")],
        ]
    elif kind == "day":
        buttons = [
            [KeyboardButton(text="Продуктивность: высокая/средняя/низкая")],
            [KeyboardButton(text="Тревога: 0/1/2/3/4/5")],
            [KeyboardButton(text="Готово ✅")],
        ]
    else:  # evening
        buttons = [
            [KeyboardButton(text="Раздражительность: 0-5")],
            [KeyboardButton(text="Мысли о смерти: нет/мимолётные/навязчивые")],
            [KeyboardButton(text="Готово ✅")],
        ]
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)


async def send_survey(chat_id: int, kind: str):
    titles = {
        "morning": "Утренний опрос",
        "day": "Дневной опрос",
        "evening": "Вечерний опрос",
    }
    await bot.send_message(
        chat_id,
        f"<b>{titles[kind]}</b>\nОтветьте на вопросы и нажмите ‘Готово ✅’.",
        reply_markup=survey_keyboard(kind),
    )


# ---------- Handlers ----------

@dp.message(Command("start"))
async def cmd_start(message: Message):
    init_db()
    add_user(message.chat.id)
    await message.answer(
        "Привет! Я буду присылать опросы в 10:00, 15:00 и 21:00 (Europe/Amsterdam).\n"
        "Команды: /morning /day /evening /statistics /stop",
    )


@dp.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(
        "Доступные команды:\n"
        "/morning — отправить утренний опрос сейчас\n"
        "/day — дневной опрос сейчас\n"
        "/evening — вечерний опрос сейчас\n"
        "/statistics — краткая статистика\n"
        "/stop — остановить напоминания",
    )


@dp.message(Command("stop"))
async def cmd_stop(message: Message):
    with closing(sqlite3.connect(DB_PATH)) as conn:
        c = conn.cursor()
        c.execute("UPDATE users SET is_active=0 WHERE chat_id=?", (message.chat.id,))
        conn.commit()
    await message.answer("Ок, напоминания приостановлены. Напишите /start, чтобы возобновить.")


@dp.message(Command("statistics"))
async def cmd_statistics(message: Message):
    total = entries_count(message.chat.id)
    m = entries_count(message.chat.id, "morning")
    d = entries_count(message.chat.id, "day")
    e = entries_count(message.chat.id, "evening")
    await message.answer(
        f"Сохранено записей: {total}\n"
        f"• Утро: {m}\n• День: {d}\n• Вечер: {e}"
    )


@dp.message(Command("morning"))
async def cmd_morning(message: Message):
    await send_survey(message.chat.id, "morning")


@dp.message(Command("day"))
async def cmd_day(message: Message):
    await send_survey(message.chat.id, "day")


@dp.message(Command("evening"))
async def cmd_evening(message: Message):
    await send_survey(message.chat.id, "evening")


@dp.message(F.text == "Готово ✅")
async def done_collect(message: Message):
    save_entry(message.chat.id, "generic", "свободный ответ")
    await message.answer("Спасибо! Ответ сохранён.")


# ---------- Scheduling ----------

async def schedule_jobs():
    scheduler.remove_all_jobs()

    hour, minute = MORNING_TIME
    scheduler.add_job(
        func=send_bulk_survey,
        trigger=CronTrigger(hour=hour, minute=minute),
        kwargs={"kind": "morning"},
        id="morning_job",
        replace_existing=True,
    )

    hour, minute = DAY_TIME
    scheduler.add_job(
        func=send_bulk_survey,
        trigger=CronTrigger(hour=hour, minute=minute),
        kwargs={"kind": "day"},
        id="day_job",
        replace_existing=True,
    )

    hour, minute = EVENING_TIME
    scheduler.add_job(
        func=send_bulk_survey,
        trigger=CronTrigger(hour=hour, minute=minute),
        kwargs={"kind": "evening"},
        id="evening_job",
        replace_existing=True,
    )


async def send_bulk_survey(kind: str):
    users = get_active_users()
    for chat_id in users:
        try:
            await send_survey(chat_id, kind)
        except Exception as e:
            print(f"Failed to send to {chat_id}: {e}")


async def on_startup():
    init_db()
    scheduler.start()
    await schedule_jobs()


async def main():
    await on_startup()
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
