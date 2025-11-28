import os
import json
from flask import Flask, request, jsonify
import requests

app = Flask(__name__)

# Загружаем переменные окружения
BOT_TOKEN = os.getenv("BOT_TOKEN")
SECRET_KEY = os.getenv("SECRET_KEY")
YOUR_CHAT_ID = os.getenv("YOUR_CHAT_ID")  # Твой chat_id
GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID")

# Файл для хранения состояния опросов
STATE_FILE = "states.json"

# Метрики
questions = [
    "Как вы оцениваете настроение (0–5)?",
    "Как вы оцениваете уровень энергии (0–5)?",
    "Насколько выражена ватность тела (0–5)?",
    "Слабость в ногах (0–5)?",
    "Либидо (0–5)?",
    "Качество сна (0–5)?",
    "Продуктивность (0–5)?",
    "Концентрация (0–5)?",
    "Аппетит (0–5)?",
    "Социализация (0–5)?",
    "Тревожность (0–5)?",
    "Раздражительность (0–5)?",
    "Плаксивость (0–5)?",
    "Гиперактивность (0–5)?",
    "Мысли о смерти (0–5)?",
    "Приём лекарств (да/нет)?",
    "Импульсивность (0–5)?"
]

# Загружаем состояние опросов
if os.path.exists(STATE_FILE):
    with open(STATE_FILE, "r") as f:
        states = json.load(f)
else:
    states = {}

def save_states():
    with open(STATE_FILE, "w") as f:
        json.dump(states, f)

def send_message(chat_id, text, reply_markup=None):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": text}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    requests.post(url, json=payload)

def send_question(chat_id):
    step = states[str(chat_id)]["step"]
    if step < len(questions):
        send_message(chat_id, f"{step + 1}. {questions[step]}")
    else:
        send_message(chat_id, "Спасибо! Опрос завершён.")
        # Здесь можно добавить отправку данных в Google Sheets
        states.pop(str(chat_id), None)
        save_states()

def start_survey(chat_id, survey_type):
    states[str(chat_id)] = {"survey_type": survey_type, "step": 0}
    save_states()
    send_message(chat_id, f"📝 Начат {survey_type} опрос")
    send_question(chat_id)

@app.route('/webhook/' + BOT_TOKEN, methods=["POST"])
def webhook():
    data = request.json
    if not data or "message" not in data:
        return jsonify({"status": "ignored"})
    message = data["message"]
    chat_id = message["chat"]["id"]
    text = message.get("text", "")

    if text.startswith("/"):
        command = text.split()[0][1:]  # без '/'
        if command in ["morning", "day", "evening"]:
            start_survey(chat_id, command)
        return jsonify({"ok": True})

    if str(chat_id) in states:
        step = states[str(chat_id)]["step"]
        states[str(chat_id)]["step"] += 1
        save_states()
        if step < len(questions):
            send_question(chat_id)
    return jsonify({"ok": True})

@app.route('/trigger/<time_of_day>', methods=['GET', 'POST'])
def trigger_survey(time_of_day):
    key = request.args.get('key', '')

    if key != SECRET_KEY:
        return jsonify({'status': 'error', 'message': 'invalid key'}), 403

    valid = ["morning", "day", "evening"]
    if time_of_day not in valid:
        return jsonify({'status': 'error', 'message': 'invalid trigger'}), 400

    try:
        chat_id = int(YOUR_CHAT_ID)
        start_survey(chat_id, time_of_day)
        return jsonify({'status': 'ok', 'message': 'survey auto-started'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/')
def root():
    return "Bot is running!"

if __name__ == '__main__':
    app.run(host="0.0.0.0", port=os.getenv("PORT", 5000))

