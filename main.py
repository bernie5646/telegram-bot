
import os
import io
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import List, Dict, Any, Tuple

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

import gspread
from google.oauth2.service_account import Credentials

from fastapi import FastAPI, Request, HTTPException
from aiogram import Bot, Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.types import Update, Message, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove, BufferedInputFile
from aiogram.filters import Command, StateFilter
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("Set BOT_TOKEN env var with your Telegram bot token")

WEBHOOK_BASE_URL = os.getenv("WEBHOOK_BASE_URL", "").rstrip("/")
SECRET_KEY = os.getenv("SECRET_KEY")
SHEET_ID = os.getenv("GOOGLE_SHEETS_ID")
CREDS_JSON = os.getenv("GOOGLE_CREDENTIALS")  # JSON string of Service Account

TZ = ZoneInfo("Europe/Amsterdam")
DB_PATH = "data.db"

bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())
app = FastAPI()

# ---------- Google Sheets helpers ----------
def get_gspread():
    if not SHEET_ID or not CREDS_JSON:
        return None, None
    info = json.loads(CREDS_JSON)
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive.file",
    ]
    credentials = Credentials.from_service_account_info(info, scopes=scopes)
    client = gspread.authorize(credentials)
    sh = client.open_by_key(SHEET_ID)
    return client, sh

def ensure_worksheet(sh, title: str, header: List[str]):
    try:
        ws = sh.worksheet(title)
    except gspread.exceptions.WorksheetNotFound:
        ws = sh.add_worksheet(title=title, rows=1, cols=max(10, len(header)))
        ws.append_row(header)
    # ensure header present
    if ws.row_values(1) != header:
        try:
            ws.delete_rows(1)
        except Exception:
            pass
        ws.insert_row(header, 1)
    return ws

def append_row(kind: str, chat_id: int, data: Dict[str, Any]):
    client, sh = get_gspread()
    if not sh:
        return
    header = ["timestamp", "chat_id", "period"] + list(data.keys())
    ws = ensure_worksheet(sh, "data", header)
    row = [datetime.now(TZ).isoformat(), str(chat_id), kind] + [str(data[k]) for k in data.keys()]
    ws.append_row(row)

def fetch_rows(days: int = 30) -> List[Dict[str, Any]]:
    client, sh = get_gspread()
    if not sh:
        return []
    try:
        ws = sh.worksheet("data")
    except gspread.exceptions.WorksheetNotFound:
        return []
    values = ws.get_all_records()
    # filter by last N days
    cutoff = datetime.now(TZ) - timedelta(days=days)
    out = []
    for r in values:
        try:
            ts = datetime.fromisoformat(r.get("timestamp"))
        except Exception:
            continue
        if ts >= cutoff:
            out.append(r)
    return out

# ---------- DB helpers (local fallback) ----------
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
                answers_json TEXT
            )
            """
        )
        conn.commit()

def add_user(chat_id: int):
    with closing(sqlite3.connect(DB_PATH)) as conn:
        c = conn.cursor()
        c.execute("INSERT OR IGNORE INTO users(chat_id, is_active) VALUES(?, 1)", (chat_id,))
        conn.commit()

def save_entry_local(chat_id: int, kind: str, answers: Dict[str, Any]):
    with closing(sqlite3.connect(DB_PATH)) as conn:
        c = conn.cursor()
        c.execute(
            "INSERT INTO entries(chat_id, kind, created_at, answers_json) VALUES(?,?,?,?)",
            (chat_id, kind, datetime.now(TZ).isoformat(), json.dumps(answers, ensure_ascii=False)),
        )
        conn.commit()

def get_active_users():
    with closing(sqlite3.connect(DB_PATH)) as conn:
        c = conn.cursor()
        rows = c.execute("SELECT chat_id FROM users WHERE is_active = 1").fetchall()
        return [r[0] for r in rows]

# ---------- Survey definition ----------
MORNING_QUESTIONS: List[Tuple[str, str, str]] = [
    ("mood", "Настроение (0–5)", "scale"),
    ("energy", "Энергия (0–5)", "scale"),
    ("sleep_quality", "Сон (качество 0–5)", "scale"),
    ("body_heaviness", "Ватность тела (0–5)", "scale"),
    ("leg_weakness", "Слабость в ногах (0–5)", "scale"),
    ("libido", "Либидо (0–5)", "scale"),
    ("appetite", "Аппетит (0–5)", "scale"),
    ("anxiety", "Тревожность (0–5)", "scale"),
    ("impulsivity", "Импульсивность (0–5)", "scale"),
    ("meds", "Принимал(а) лекарства? (Да/Нет)", "yesno"),
]

DAY_QUESTIONS: List[Tuple[str, str, str]] = [
    ("mood", "Настроение (0–5)", "scale"),
    ("energy", "Энергия (0–5)", "scale"),
    ("productivity", "Продуктивность (0–5)", "scale"),
    ("focus", "Концентрация (0–5)", "scale"),
    ("social", "Социализация (0–5)", "scale"),
    ("anxiety", "Тревожность (0–5)", "scale"),
    ("irritability", "Раздражительность (0–5)", "scale"),
    ("impulsivity", "Импульсивность (0–5)", "scale"),
    ("hyperactivity", "Гиперактивность (0–5)", "scale"),
    ("suicidal", "Мысли о смерти (нет/мимолётные/навязчивые)", "suicide"),
]

EVENING_QUESTIONS: List[Tuple[str, str, str]] = [
    ("mood", "Настроение (0–5)", "scale"),
    ("energy", "Энергия (0–5)", "scale"),
    ("productivity", "Продуктивность (0–5)", "scale"),
    ("focus", "Концентрация (0–5)", "scale"),
    ("social", "Социализация (0–5)", "scale"),
    ("anxiety", "Тревожность (0–5)", "scale"),
    ("irritability", "Раздражительность (0–5)", "scale"),
    ("tearfulness", "Плаксивость (0–5)", "scale"),
    ("hyperactivity", "Гиперактивность (0–5)", "scale"),
    ("suicidal", "Мысли о смерти (нет/мимолётные/навязчивые)", "suicide"),
]

def options_for(type_: str) -> List[str]:
    if type_ == "scale":
        return [str(i) for i in range(6)]  # 0..5
    if type_ == "yesno":
        return ["Да", "Нет"]
    if type_ == "suicide":
        return ["нет", "мимолётные", "навязчивые"]
    return []

class Survey(StatesGroup):
    answering = State()

def survey_keyboard_for(type_: str) -> ReplyKeyboardMarkup:
    opts = options_for(type_)
    rows = [[KeyboardButton(text=o) for o in opts[i:i+3]] for i in range(0, len(opts), 3)]
    rows.append([KeyboardButton(text="Отмена ❌")])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)

async def start_survey(message: Message, state: FSMContext, kind: str):
    await state.set_state(Survey.answering)
    await state.update_data(kind=kind, index=0, answers={})
    await ask_next_question(message, state)

def get_questions(kind: str):
    if kind == "morning": return MORNING_QUESTIONS
    if kind == "day": return DAY_QUESTIONS
    return EVENING_QUESTIONS

async def ask_next_question(message: Message, state: FSMContext):
    data = await state.get_data()
    kind = data["kind"]
    idx = data["index"]
    questions = get_questions(kind)
    if idx >= len(questions):
        answers = data["answers"]
        save_entry_local(message.chat.id, kind, answers)
        try:
            append_row(kind, message.chat.id, answers)
        except Exception as e:
            print("Sheets append error:", e)
        await message.answer("Спасибо! Ответы сохранены ✅", reply_markup=ReplyKeyboardRemove())
        await maybe_trigger_alerts(message, kind, answers)
        await state.clear()
        return
    key, label, type_ = questions[idx]
    kb = survey_keyboard_for(type_)
    await message.answer(label, reply_markup=kb)

async def maybe_trigger_alerts(message: Message, kind: str, answers: Dict[str, Any]):
    try:
        if "suicidal" in answers and answers["suicidal"] != "нет":
            await message.answer("⚠️ Замечаю ответ про мысли о смерти. Если чувствуешь опасность — пожалуйста, обратись за поддержкой к близким или специалистам. Я рядом.")
        for k in ("anxiety", "irritability", "impulsivity"):
            if k in answers:
                try:
                    v = int(answers[k])
                    if v >= 4:
                        await message.answer("⚠️ Похоже, высокий уровень напряжения. Хочешь дыхательную технику? Напиши /helpme.")
                        break
                except:
                    pass
    except Exception as e:
        print("trigger error:", e)

# ---------- Handlers ----------
@dp.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    init_db()
    add_user(message.chat.id)
    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="/morning")],
            [KeyboardButton(text="/day")],
            [KeyboardButton(text="/evening")],
        ],
        resize_keyboard=True
    )
    await message.answer("Привет! Нажми /morning /day /evening, чтобы пройти опрос.", reply_markup=kb)

@dp.message(Command("morning"))
async def cmd_morning(message: Message, state: FSMContext):
    await start_survey(message, state, "morning")

@dp.message(Command("day"))
async def cmd_day(message: Message, state: FSMContext):
    await start_survey(message, state, "day")

@dp.message(Command("evening"))
async def cmd_evening(message: Message, state: FSMContext):
    await start_survey(message, state, "evening")

@dp.message(Command("helpme"))
async def cmd_helpme(message: Message):
    await message.answer("Быстрая техника: 4-7-8. Вдох на 4, задержка на 7, выдох на 8 — 4 цикла. И можно коротко записать, что тревожит, чтобы 'выгрузить' из головы.")

@dp.message(Command("statistics"))
async def cmd_statistics(message: Message):
    rows = fetch_rows(days=30)
    if not rows:
        await message.answer("Пока нет данных за последние 30 дней.")
        return

    def parse(v):
        try:
            return float(v)
        except:
            return None

    metrics = ["mood", "energy", "anxiety"]
    daily = {m: {} for m in metrics}

    for r in rows:
        try:
            ts = datetime.fromisoformat(r["timestamp"])
        except Exception:
            continue
        day = ts.date().isoformat()
        for m in metrics:
            v = parse(r.get(m))
            if v is not None:
                daily[m].setdefault(day, []).append(v)

    days_sorted = sorted(set(sum([list(d.keys()) for d in daily.values()], [])))

    for m in metrics:
        xs, ys = [], []
        for d in days_sorted:
            vals = daily[m].get(d, [])
            if vals:
                xs.append(d)
                ys.append(sum(vals)/len(vals))
        if xs:
            fig = plt.figure()
            plt.plot(xs, ys, marker="o")
            plt.title(f"{m} — среднее по дням (30д)")
            plt.xticks(rotation=45, ha="right")
            plt.ylim(0, 5)
            plt.tight_layout()
            buf = io.BytesIO()
            plt.savefig(buf, format="png")
            plt.close(fig)
            buf.seek(0)
            img = BufferedInputFile(buf.read(), filename=f"{m}.png")
            await bot.send_photo(message.chat.id, img)

    await message.answer("Готово: настроение, энергия, тревожность за 30 дней.")

@dp.message(F.text == "Отмена ❌")
async def cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Опрос прерван.", reply_markup=ReplyKeyboardRemove())

@dp.message(StateFilter(Survey.answering))
async def handle_answer(message: Message, state: FSMContext):
    data = await state.get_data()
    kind = data["kind"]
    idx = data["index"]
    answers = data["answers"]
    questions = get_questions(kind)
    if idx >= len(questions):
        await message.answer("Кажется, опрос уже завершён. Напиши /statistics или начни заново.")
        await state.clear()
        return

    key, label, type_ = questions[idx]
    text = (message.text or "").strip()

    # Validation
    valid = True
    if type_ == "scale":
        valid = text in [str(i) for i in range(6)]
    elif type_ == "yesno":
        valid = text.lower() in ["да", "нет"]
        text = "Да" if text.lower() == "да" else "Нет"
    elif type_ == "suicide":
        valid = text.lower() in ["нет", "мимолётные", "навязчивые"]
        text = text.lower()

    if not valid:
        await message.answer("Пожалуйста, выбери один из вариантов на клавиатуре.")
        return

    answers[key] = text
    await state.update_data(index=idx + 1, answers=answers)
    await ask_next_question(message, state)

# ---------- Trigger endpoints (cron) ----------
async def send_prompt(chat_id: int, kind: str):
    mapping = {"morning": "/morning", "day": "/day", "evening": "/evening"}
    prompt = {
        "morning": "Утренний опрос. Нажми /morning, чтобы ответить.",
        "day": "Дневной опрос. Нажми /day, чтобы ответить.",
        "evening": "Вечерний опрос. Нажми /evening, чтобы ответить.",
    }[kind]
    kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=mapping[kind])]], resize_keyboard=True)
    await bot.send_message(chat_id, prompt, reply_markup=kb)

async def send_bulk_prompt(kind: str):
    for chat_id in get_active_users():
        try:
            await send_prompt(chat_id, kind)
        except Exception as e:
            print("send prompt error:", e)

@app.post("/trigger/{kind}")
async def trigger(kind: str, request: Request):
    if kind not in {"morning", "day", "evening"}:
        raise HTTPException(status_code=404, detail="Unknown kind")
    if SECRET_KEY and request.query_params.get("key") != SECRET_KEY:
        raise HTTPException(status_code=403, detail="Forbidden")
    await send_bulk_prompt(kind)
    return {"ok": True}

# ---------- Webhook ----------
@app.post("/webhook/{token}")
async def telegram_webhook(token: str, request: Request):
    if token != BOT_TOKEN:
        raise HTTPException(status_code=403, detail="Invalid token")
    data = await request.json()
    update = Update.model_validate(data)
    await dp.feed_update(bot, update)
    return {"ok": True}

@app.get("/healthz")
async def healthz():
    return {"status": "ok"}

@app.on_event("startup")
async def on_startup():
    init_db()
    if WEBHOOK_BASE_URL:
        url = f"{WEBHOOK_BASE_URL}/webhook/{BOT_TOKEN}"
        try:
            await bot.set_webhook(url)
            print(f"Webhook set to: {url}")
        except Exception as e:
            print("Failed to set webhook:", e)
