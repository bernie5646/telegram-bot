
import os
import sqlite3
from contextlib import closing
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Request, HTTPException
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.types import Update, Message, ReplyKeyboardMarkup, KeyboardButton

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("Set BOT_TOKEN env var with your Telegram bot token")

WEBHOOK_BASE_URL = os.getenv("WEBHOOK_BASE_URL", "").rstrip("/")
SECRET_KEY = os.getenv("SECRET_KEY")  # for trigger endpoints

TZ = ZoneInfo("Europe/Amsterdam")
DB_PATH = "data.db"

bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
app = FastAPI()

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
        "Привет! Я буду присылать опросы (по расписанию через cron).\n"
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


# ---------- Webhook endpoints ----------
@app.post("/webhook/{token}")
async def telegram_webhook(token: str, request: Request):
    # Simple token check so only Telegram can call this exact path
    if token != BOT_TOKEN:
        raise HTTPException(status_code=403, detail="Invalid token")
    data = await request.json()
    update = Update.model_validate(data)
    await dp.feed_update(bot, update)
    return {"ok": True}


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


@app.post("/trigger/{kind}")
async def trigger(kind: str, request: Request):
    # Optional secret key protection for external crons
    secret = request.query_params.get("key")
    if SECRET_KEY and secret != SECRET_KEY:
        raise HTTPException(status_code=403, detail="Forbidden")
    if kind not in {"morning", "day", "evening"}:
        raise HTTPException(status_code=404, detail="Unknown kind")
    await send_bulk_survey(kind)
    return {"ok": True, "sent": True, "kind": kind}


async def send_bulk_survey(kind: str):
    users = get_active_users()
    for chat_id in users:
        try:
            await send_survey(chat_id, kind)
        except Exception as e:
            print(f"Failed to send to {chat_id}: {e}")


@app.on_event("startup")
async def on_startup():
    init_db()
    # Set webhook if base url provided
    if WEBHOOK_BASE_URL:
        url = f"{WEBHOOK_BASE_URL}/webhook/{BOT_TOKEN}"
        try:
            await bot.set_webhook(url)
            print(f"Webhook set to: {url}")
        except Exception as e:
            print(f"Failed to set webhook: {e}")
