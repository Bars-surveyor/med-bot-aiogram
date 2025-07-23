# med_bot_aiogram.py (Фінальна версія)

import asyncio
import logging
import sqlite3
import datetime
import re
import time
import os
import matplotlib.pyplot as plt
from fpdf import FPDF

from openai import AsyncOpenAI

from aiogram import Bot, F, types, Router
from aiogram.filters import Command
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardButton, InlineKeyboardMarkup, CallbackQuery

import schedule

# --- КОНФІГУРАЦІЯ ---
DATABASE_NAME = 'health_log.db'
PRIVACY_POLICY_URL = "https://telegra.ph/Pol%D1%96tika-konf%D1%96denc%D1%96jnost%D1%96-dlya-medichnogo-pom%D1%96chnika-med-pomichnyk-bot-07-22-2" # Приклад, замініть на своє посилання
ANALYZE_BTN_TEXT = "🤔 Проаналізувати симптоми (AI)"

# Ініціалізація роутера
router = Router()

# --- СТАНИ FSM ---
class Form(StatesGroup):
    symptoms_analysis = State()
    answering_clarification = State()
    symptom_checker_start = State()
    symptom_checker_headache_type = State()
    symptom_checker_headache_location = State()
    symptom_checker_headache_additional = State()
    edit_age = State()
    edit_gender = State()
    edit_weight_kg = State()
    edit_height_cm = State()
    edit_blood_group = State()
    edit_allergies = State()
    edit_chronic_diseases = State()
    edit_emergency_contact = State()
    add_med_name = State()
    add_med_dosage = State()
    add_med_schedule = State()
    checkin_mood = State()
    checkin_sleep = State()
    # Нові стани для Check-in
    checkin_activity = State()
    checkin_stress = State()
    checkin_water = State()
    waiting_for_note = State()

# --- БАЗА ДАНИХ ---
def setup_database():
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    # Створення основних таблиць
    cursor.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, first_name TEXT, age INTEGER, gender TEXT, weight_kg REAL, height_cm REAL)")
    cursor.execute("CREATE TABLE IF NOT EXISTS health_entries (entry_id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP, mood TEXT, sleep_quality TEXT, systolic_pressure INTEGER, diastolic_pressure INTEGER, FOREIGN KEY (user_id) REFERENCES users(user_id))")
    cursor.execute("CREATE TABLE IF NOT EXISTS medications (med_id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, med_name TEXT NOT NULL, dosage TEXT, schedule TEXT, is_active BOOLEAN DEFAULT 1, FOREIGN KEY (user_id) REFERENCES users(user_id))")
    cursor.execute("CREATE TABLE IF NOT EXISTS medication_log (log_id INTEGER PRIMARY KEY AUTOINCREMENT, med_id INTEGER, user_id INTEGER, timestamp DATETIME, status TEXT, FOREIGN KEY (med_id) REFERENCES medications(med_id))")
    cursor.execute("CREATE TABLE IF NOT EXISTS openai_interactions (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP, prompt TEXT, response TEXT)")
    cursor.execute("CREATE TABLE IF NOT EXISTS cycles (cycle_id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, start_date DATE, end_date DATE, FOREIGN KEY (user_id) REFERENCES users(user_id))")

    # Додавання нових полів через ALTER TABLE, ігноруючи помилки, якщо вони вже існують
    try: cursor.execute("ALTER TABLE users ADD COLUMN checkin_streak INTEGER DEFAULT 0")
    except: pass
    try: cursor.execute("ALTER TABLE users ADD COLUMN last_checkin_date DATE")
    except: pass
    try: cursor.execute("ALTER TABLE health_entries ADD COLUMN note TEXT")
    except: pass
    try: cursor.execute("ALTER TABLE users ADD COLUMN blood_group TEXT")
    except: pass
    try: cursor.execute("ALTER TABLE users ADD COLUMN allergies TEXT")
    except: pass
    try: cursor.execute("ALTER TABLE users ADD COLUMN chronic_diseases TEXT")
    except: pass
    try: cursor.execute("ALTER TABLE users ADD COLUMN emergency_contact TEXT")
    except: pass
    # Нові поля для Check-in
    try: cursor.execute("ALTER TABLE health_entries ADD COLUMN activity_level TEXT")
    except: pass
    try: cursor.execute("ALTER TABLE health_entries ADD COLUMN stress_level TEXT")
    except: pass
    try: cursor.execute("ALTER TABLE health_entries ADD COLUMN water_intake TEXT")
    except: pass

    # Створення та заповнення таблиць для гейміфікації
    cursor.execute("CREATE TABLE IF NOT EXISTS achievements (code TEXT PRIMARY KEY, name TEXT, description TEXT, icon TEXT)")
    cursor.execute("CREATE TABLE IF NOT EXISTS user_achievements (user_id INTEGER, achievement_code TEXT, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (user_id, achievement_code), FOREIGN KEY (user_id) REFERENCES users(user_id), FOREIGN KEY (achievement_code) REFERENCES achievements(code))")
    achievements_data = [('FIRST_REPORT', 'Перший звіт', 'Ви згенерували свій перший звіт для лікаря.', '📄'), ('STREAK_5_DAYS', 'Стабільність', 'Ви ведете щоденник 5 днів поспіль.', '🔥'), ('FIRST_NOTE', 'Нотатки', 'Ви зробили свій перший швидкий запис.', '✍️')]
    cursor.executemany("INSERT OR IGNORE INTO achievements (code, name, description, icon) VALUES (?, ?, ?, ?)", achievements_data)

    conn.commit()
    conn.close()
    logging.info("Базу даних перевірено та налаштовано.")

# --- Функції для роботи з БД ---
def create_or_update_user(user_id: int, first_name: str):
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    cursor.execute("INSERT OR IGNORE INTO users (user_id, first_name) VALUES (?, ?)", (user_id, first_name))
    cursor.execute("UPDATE users SET first_name = ? WHERE user_id = ?", (first_name, user_id))
    conn.commit()
    conn.close()

def get_user_profile(user_id: int):
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.execute("SELECT first_name, age, gender, weight_kg, height_cm, blood_group, allergies, chronic_diseases, emergency_contact FROM users WHERE user_id = ?", (user_id,))
    profile = cursor.fetchone()
    conn.close()
    return profile

def update_user_field(user_id: int, field: str, value):
    allowed_fields = ["age", "gender", "weight_kg", "height_cm", "blood_group", "allergies", "chronic_diseases", "emergency_contact"]
    if field not in allowed_fields: return
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    cursor.execute(f"UPDATE users SET {field} = ? WHERE user_id = ?", (value, user_id))
    conn.commit()
    conn.close()

def save_health_entry(user_id, **kwargs):
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    valid_keys = ['mood', 'sleep_quality', 'note', 'activity_level', 'stress_level', 'water_intake']
    filtered_kwargs = {k: v for k, v in kwargs.items() if k in valid_keys}
    if not filtered_kwargs: return
    columns = ', '.join(filtered_kwargs.keys())
    placeholders = ', '.join('?' * len(filtered_kwargs))
    sql = f"INSERT INTO health_entries (user_id, {columns}) VALUES (?, {placeholders})"
    values = (user_id,) + tuple(filtered_kwargs.values())
    cursor.execute(sql, values)
    conn.commit()
    conn.close()

def get_user_history(user_id: int):
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT timestamp, mood, sleep_quality, note, activity_level, stress_level, water_intake FROM health_entries WHERE user_id = ? ORDER BY timestamp DESC LIMIT 15", (user_id,))
    history = cursor.fetchall()
    conn.close()
    return history

def check_achievement(user_id: int, achievement_code: str) -> bool:
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM user_achievements WHERE user_id = ? AND achievement_code = ?", (user_id, achievement_code))
    exists = cursor.fetchone()
    conn.close()
    return exists is not None

async def award_achievement(user_id: int, achievement_code: str, message: Message):
    if not check_achievement(user_id, achievement_code):
        conn = sqlite3.connect(DATABASE_NAME)
        cursor = conn.cursor()
        cursor.execute("INSERT INTO user_achievements (user_id, achievement_code) VALUES (?, ?)", (user_id, achievement_code))
        cursor.execute("SELECT name, icon FROM achievements WHERE code = ?", (achievement_code,))
        ach = cursor.fetchone()
        conn.commit()
        conn.close()
        if ach:
            await message.answer(f"{ach[1]} Досягнення отримано: **{ach[0]}**!")
# ... (решта функцій БД: add_medication, get_user_medications і т.д. залишаються без змін) ...
def get_user_medications(user_id: int):
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT med_id, med_name, dosage, schedule FROM medications WHERE user_id = ? AND is_active = 1", (user_id,))
    meds = cursor.fetchall()
    conn.close()
    return meds

def log_medication_status(user_id: int, med_id: int, status: str):
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO medication_log (user_id, med_id, timestamp, status) VALUES (?, ?, ?, ?)", (user_id, med_id, datetime.datetime.now(), status))
    conn.commit()
    conn.close()

def set_medication_inactive(med_id: int, user_id: int):
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    cursor.execute("UPDATE medications SET is_active = 0 WHERE med_id = ? AND user_id = ?", (med_id, user_id))
    conn.commit()
    conn.close()

def save_openai_interaction(user_id, prompt, response):
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO openai_interactions (user_id, prompt, response) VALUES (?, ?, ?)", (user_id, prompt, response))
    conn.commit()
    conn.close()

def start_new_cycle(user_id: int):
    today = datetime.date.today()
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    cursor.execute("UPDATE cycles SET end_date = ? WHERE user_id = ? AND end_date IS NULL", (today - datetime.timedelta(days=1), user_id))
    cursor.execute("INSERT INTO cycles (user_id, start_date) VALUES (?, ?)", (user_id, today))
    conn.commit()
    conn.close()

def end_current_cycle(user_id: int):
    today = datetime.date.today()
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    cursor.execute("UPDATE cycles SET end_date = ? WHERE user_id = ? AND end_date IS NULL", (today, user_id))
    updated_rows = cursor.rowcount
    conn.commit()
    conn.close()
    return updated_rows > 0

def get_cycle_predictions(user_id: int):
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT start_date, end_date FROM cycles WHERE user_id = ? AND end_date IS NOT NULL ORDER BY start_date DESC LIMIT 5", (user_id,))
    cycles = cursor.fetchall()
    conn.close()
    if len(cycles) < 2: return None, None
    lengths = [(datetime.datetime.strptime(cycles[i][0], '%Y-%m-%d').date() - datetime.datetime.strptime(cycles[i+1][0], '%Y-%m-%d').date()).days for i in range(len(cycles) - 1)]
    avg_length = int(sum(lengths) / len(lengths))
    last_start_date = datetime.datetime.strptime(cycles[0][0], '%Y-%m-%d').date()
    predicted_date = last_start_date + datetime.timedelta(days=avg_length)
    return avg_length, predicted_date.strftime("%d-%m-%Y")


# --- Рушій Рекомендацій ---
def generate_daily_recommendation(data: dict) -> str:
    recommendations = []
    if data.get("stress_level") == "Високий":
        recommendations.append("Високий рівень стресу. Спробуйте знайти 10-15 хвилин для короткої прогулянки або дихальних вправ, щоб розслабитися.")
    if data.get("activity_level") == "Низька":
        recommendations.append("Низька активність сьогодні. Навіть коротка 20-хвилинна прогулянка може значно покращити ваше самопочуття.")
    if data.get("mood") == "😞 Поганий" and data.get("activity_level") == "Низька":
        recommendations.append("Іноді фізична активність допомагає покращити настрій. Можливо, невелика прогулянка буде корисною.")
    if "погано" in data.get("sleep_quality", "").lower() or "мало" in data.get("sleep_quality", "").lower():
        recommendations.append("Поганий сон впливає на весь день. Спробуйте провітрити кімнату перед сном і відкласти телефон за годину до засинання.")
    if data.get("water_intake") == "Менше 1 літра":
        recommendations.append("Не забувайте пити достатньо води протягом дня. Це важливо для енергії та концентрації.")
    if not recommendations:
        return "✨ Чудові показники сьогодні! Так тримати!"
    else:
        return "💡 **Ось декілька порад на основі ваших сьогоднішніх записів:**\n\n- " + "\n- ".join(recommendations)

# --- Аналітика, звіти, планувальник ---
# ... (код для аналітики, звітів, планувальника залишається без змін) ...
def get_weekly_health_data(user_id: int):
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT timestamp, mood, systolic_pressure, diastolic_pressure FROM health_entries WHERE user_id = ? AND timestamp >= date('now', '-14 days') ORDER BY timestamp ASC", (user_id,))
    data = cursor.fetchall()
    conn.close()
    return data

def analyze_weekly_data(data: list):
    if not data: return None
    mood_scores = {"😊 Чудовий": 3, "😐 Нормальний": 2, "😞 Поганий": 1}
    today = datetime.datetime.now()
    last_week_data = [row for row in data if (today - datetime.datetime.strptime(row[0], '%Y-%m-%d %H:%M:%S.%f')).days < 7]
    prev_week_data = [row for row in data if 7 <= (today - datetime.datetime.strptime(row[0], '%Y-%m-%d %H:%M:%S.%f')).days < 14]
    if not last_week_data: return "На минулому тижні не було записів. Намагайтеся робити Check-in щодня, щоб отримувати аналітику."
    
    def get_avg_metrics(week_data):
        moods = [mood_scores.get(r[1], 0) for r in week_data if r[1]]
        pressures = [(r[2], r[3]) for r in week_data if r[2] and r[3]]
        return (sum(moods) / len(moods) if moods else 0, sum(p[0] for p in pressures) / len(pressures) if pressures else 0, sum(p[1] for p in pressures) / len(pressures) if pressures else 0)

    avg_mood_last, avg_sys_last, avg_dias_last = get_avg_metrics(last_week_data)
    report_lines = ["**📊 Ваш звіт за минулий тиждень:**\n"]
    if avg_mood_last > 0: report_lines.append(f"• Середній настрій: {'😊' if avg_mood_last > 2.5 else '😐' if avg_mood_last > 1.5 else '😞'}")
    if avg_sys_last > 0: report_lines.append(f"• Середній тиск: {int(avg_sys_last)}/{int(avg_dias_last)}")
    
    if prev_week_data:
        _, avg_sys_prev, _ = get_avg_metrics(prev_week_data)
        if avg_sys_last > 0 and avg_sys_prev > 0 and abs(avg_sys_last - avg_sys_prev) > 3:
             trend = "зріс" if avg_sys_last > avg_sys_prev else "знизився"
             report_lines.append(f"• Ваш середній систолічний тиск **{trend}** порівняно з позаминулим тижнем.")
    return "\n".join(report_lines)

def create_pressure_graph(user_id: int, days: int = 30) -> str | None:
    # Примітка: ця функція тепер не буде працювати, оскільки ми більше не збираємо тиск у Check-in
    # Її потрібно буде або видалити, або адаптувати для нових даних (напр. графік настрою)
    return None

def generate_doctor_report_pdf(user_id: int) -> str:
    profile, history = get_user_profile(user_id), get_user_history(user_id)
    pdf = FPDF()
    pdf.add_page()
    pdf.add_font('DejaVu', '', 'DejaVuSans.ttf', uni=True)
    pdf.set_font('DejaVu', '', 16)
    pdf.cell(0, 10, 'Звіт про стан здоров\'я', 0, 1, 'C')
    pdf.set_font('DejaVu', '', 12)
    pdf.cell(0, 10, f'Пацієнт: {profile[0] if profile else "N/A"}', 0, 1, 'C')
    pdf.cell(0, 10, f'Дата генерації: {datetime.date.today().strftime("%d-%m-%Y")}', 0, 1, 'C'), pdf.ln(10)

    pdf.set_font('DejaVu', '', 14), pdf.cell(0, 10, 'Останні записи:', 0, 1), pdf.set_font('DejaVu', '', 10)
    for record in history:
        timestamp, mood, sleep, note, activity, stress, water = record
        dt_object = datetime.datetime.strptime(timestamp, '%Y-%m-%d %H:%M:%S.%f')
        line = f"{dt_object.strftime('%d-%m-%y %H:%M')}: "
        if note: line += f"Нотатка - {note}. "
        if mood: line += f"Настрій - {mood}. "
        if sleep: line += f"Сон - {sleep}. "
        if activity: line += f"Активність - {activity}. "
        if stress: line += f"Стрес - {stress}. "
        if water: line += f"Вода - {water}. "
        pdf.multi_cell(0, 5, line)
    
    filepath = f"report_{user_id}.pdf"
    pdf.output(filepath)
    return filepath

async def send_weekly_report(bot: Bot, user_id: int):
    logging.info(f"Генерація тижневого звіту для user_id={user_id}")
    if report_text := analyze_weekly_data(get_weekly_health_data(user_id)):
        try: await bot.send_message(user_id, report_text)
        except Exception as e: logging.error(f"Не вдалося надіслати звіт користувачу {user_id}: {e}")

async def send_reminder(bot: Bot, user_id: int, med_id: int, med_name: str, dosage: str):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ Прийнято", callback_data=f"med_log:taken:{med_id}"), InlineKeyboardButton(text="❌ Пропущено", callback_data=f"med_log:skipped:{med_id}")]])
    try: await bot.send_message(user_id, f"⏰ **Нагадування!**\n\nЧас прийняти ліки: **{med_name}**\nДозування: {dosage}", reply_markup=keyboard)
    except Exception as e: logging.error(f"Не вдалося надіслати нагадування user_id={user_id}: {e}")

def schedule_reminders(bot: Bot):
    schedule.clear()
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, med_id, med_name, dosage, schedule FROM medications WHERE is_active = 1")
    for user_id, med_id, med_name, dosage, schedule_str in cursor.fetchall():
        for t in re.findall(r"(\d{2}:\d{2})", schedule_str):
            schedule.every().day.at(t).do(lambda u=user_id, m_id=med_id, m_n=med_name, d=dosage: asyncio.create_task(send_reminder(bot, u, m_id, m_n, d)))
    
    cursor.execute("SELECT DISTINCT user_id FROM health_entries")
    for user in cursor.fetchall():
        schedule.every().sunday.at("10:00").do(lambda u_id=user[0]: asyncio.create_task(send_weekly_report(bot, u_id)))
    conn.close()

async def scheduler_loop(bot: Bot):
    schedule_reminders(bot)
    while True:
        schedule.run_pending()
        await asyncio.sleep(60)

async def on_startup(bot: Bot):
    setup_database()
    asyncio.create_task(scheduler_loop(bot))
    logging.info("Бот запущено, базу даних налаштовано, планувальник активовано.")


# --- Клавіатури та Хендлери ---
def get_main_menu_keyboard(user_id: int):
    profile, is_female = get_user_profile(user_id), False
    if profile and profile[2] and profile[2].lower() in ['жіноча', 'female']: is_female = True
    
    keyboard = [
        [KeyboardButton(text=ANALYZE_BTN_TEXT)], # <-- Змінено тут
        [KeyboardButton(text="☀️ Щоденний Check-in"), KeyboardButton(text="📝 Швидкий запис")],
        [KeyboardButton(text="👤 Мій профіль"), KeyboardButton(text="💊 Мої ліки")],
        [KeyboardButton(text="📖 Переглянути історію"), KeyboardButton(text="📄 Створити звіт")]
    ]
    if is_female: keyboard.insert(2, [KeyboardButton(text="🌸 Жіноче здоров'я")])
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)

cancel_keyboard = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="⬅️ Головне меню")]], resize_keyboard=True)

@router.message(F.text == "⬅️ Головне меню")
async def back_to_main_menu(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Дію скасовано. Ви повернулися в головне меню.", reply_markup=get_main_menu_keyboard(message.from_user.id))

# ... (всі інші хендлери залишаються тут, але Check-in повністю замінено) ...
@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ Приймаю умови", callback_data="accept_privacy"), InlineKeyboardButton(text="➡️ Пропустити", callback_data="skip_privacy")]])
    await message.answer(f"👋 **Вітаю!**\n\nПеред початком роботи, будь ласка, ознайомтеся з політикою конфіденційності.\n➡️ **Прочитати:** {PRIVACY_POLICY_URL}", reply_markup=keyboard, disable_web_page_preview=True)

@router.callback_query(F.data.in_({"accept_privacy", "skip_privacy"}))
async def process_privacy_choice(callback: CallbackQuery, state: FSMContext):
    create_or_update_user(callback.from_user.id, callback.from_user.first_name)
    confirmation_text = "Дякуємо за згоду!" if callback.data == "accept_privacy" else "Ви можете ознайомитися з політикою конфіденційності командою /privacy."
    await callback.message.edit_text(confirmation_text)
    await callback.message.answer("Оберіть дію:", reply_markup=get_main_menu_keyboard(callback.from_user.id))

@router.message(Command("sos"))
async def cmd_sos(message: Message):
    profile_data = get_user_profile(message.from_user.id)
    if not profile_data: return await message.answer("Профіль не знайдено. Заповніть його через меню.")
    _, _, _, _, _, blood, allergies, chronic, contact = profile_data
    sos_text = (f"**🚑 Ваша Екстрена картка:**\n\n"
                f"**Група крові:** {blood or 'Не вказано'}\n"
                f"**Алергії:** {allergies or 'Не вказано'}\n"
                f"**Хронічні захворювання:** {chronic or 'Не вказано'}\n"
                f"**Екстрений контакт:** {contact or 'Не вказано'}")
    await message.answer(sos_text)

# --- НОВИЙ CHECK-IN ---
@router.message(F.text == "☀️ Щоденний Check-in")
async def start_checkin(message: Message, state: FSMContext):
    await state.set_state(Form.checkin_mood)
    keyboard = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="😊 Чудовий"), KeyboardButton(text="😐 Нормальний"), KeyboardButton(text="😞 Поганий")], [KeyboardButton(text="⬅️ Головне меню")]], resize_keyboard=True)
    await message.answer("Як ваш настрій сьогодні?", reply_markup=keyboard)

@router.message(Form.checkin_mood)
async def process_checkin_mood(message: Message, state: FSMContext):
    await state.update_data(mood=message.text)
    await state.set_state(Form.checkin_sleep)
    await message.answer("Як ви спали? (напр., '8 годин, добре' або 'погано')", reply_markup=cancel_keyboard)

@router.message(Form.checkin_sleep)
async def process_checkin_sleep(message: Message, state: FSMContext):
    await state.update_data(sleep_quality=message.text)
    await state.set_state(Form.checkin_activity)
    keyboard = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="Низька"), KeyboardButton(text="Середня"), KeyboardButton(text="Висока")], [KeyboardButton(text="⬅️ Головне меню")]], resize_keyboard=True)
    await message.answer("Яким був ваш рівень фізичної активності сьогодні?", reply_markup=keyboard)

@router.message(Form.checkin_activity)
async def process_checkin_activity(message: Message, state: FSMContext):
    await state.update_data(activity_level=message.text)
    await state.set_state(Form.checkin_stress)
    keyboard = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="Низький"), KeyboardButton(text="Середній"), KeyboardButton(text="Високий")], [KeyboardButton(text="⬅️ Головне меню")]], resize_keyboard=True)
    await message.answer("Оцініть ваш середній рівень стресу за сьогодні:", reply_markup=keyboard)

@router.message(Form.checkin_stress)
async def process_checkin_stress(message: Message, state: FSMContext):
    await state.update_data(stress_level=message.text)
    await state.set_state(Form.checkin_water)
    keyboard = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="Менше 1 літра"), KeyboardButton(text="1-2 літри"), KeyboardButton(text="Більше 2 літрів")], [KeyboardButton(text="⬅️ Головне меню")]], resize_keyboard=True)
    await message.answer("Скільки води ви випили сьогодні?", reply_markup=keyboard)

@router.message(Form.checkin_water)
async def process_checkin_water(message: Message, state: FSMContext):
    await state.update_data(water_intake=message.text)
    data = await state.get_data()
    save_health_entry(user_id=message.from_user.id, **data)
    
    recommendation = generate_daily_recommendation(data)
    await message.answer(recommendation, reply_markup=get_main_menu_keyboard(message.from_user.id))
    
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT checkin_streak, last_checkin_date FROM users WHERE user_id = ?", (message.from_user.id,))
    streak_data = cursor.fetchone()
    current_streak, last_date_str = (streak_data[0] or 0, streak_data[1]) if streak_data else (0, None)
    today, new_streak = datetime.date.today(), current_streak
    
    if last_date_str:
        last_date = datetime.datetime.strptime(last_date_str, '%Y-%m-%d').date()
        delta = today - last_date
        if delta.days == 1: new_streak += 1
        elif delta.days > 1: new_streak = 1
    else: new_streak = 1
    cursor.execute("UPDATE users SET checkin_streak = ?, last_checkin_date = ? WHERE user_id = ?", (new_streak, today.strftime('%Y-%m-%d'), message.from_user.id))
    conn.commit(), conn.close()
    
    await state.clear()
    
    if new_streak > 1: await message.answer(f"🔥 Ви ведете щоденник вже **{new_streak}** днів поспіль!")
    if new_streak >= 5: await award_achievement(message.from_user.id, 'STREAK_5_DAYS', message)

# ... (решта хендлерів, що залишились, копіюються сюди)
# med_bot_aiogram.py

@router.message(F.text == "📖 Переглянути історію")
async def view_history(message: Message):
    if not (history := get_user_history(message.from_user.id)): return await message.answer("Ваша історія записів порожня.")
    response = "**Останні записи про здоров'я:**\n\n"
    for record in history:
        timestamp, mood, sleep, note, activity, stress, water = record
        # ↓↓↓ ВИПРАВЛЕНО ТУТ (прибрали .%f) ↓↓↓
        dt_object = datetime.datetime.strptime(timestamp, '%Y-%m-%d %H:%M:%S')
        response += f"🗓️ **{dt_object.strftime('%d-%m-%y %H:%M')}**\n"
        if note: response += f"   - 📝 Нотатка: {note}\n"
        if mood: response += f"   - Настрій: {mood}\n"
        if sleep: response += f"   - Сон: {sleep}\n"
        if activity: response += f"   - Активність: {activity}\n"
        if stress: response += f"   - Стрес: {stress}\n"
        if water: response += f"   - Вода: {water}\n"
        response += "---\n"
    await message.answer(response)

@router.message(F.text == "📝 Швидкий запис")
async def ask_for_note(message: Message, state: FSMContext):
    await state.set_state(Form.waiting_for_note)
    await message.answer("Введіть вашу нотатку. Вона буде збережена з поточною датою і часом.", reply_markup=cancel_keyboard)
    
    

@router.message(Form.waiting_for_note)
async def process_note(message: Message, state: FSMContext):
    save_health_entry(user_id=message.from_user.id, note=message.text)
    await state.clear()
    await message.answer("✅ Нотатку збережено.", reply_markup=get_main_menu_keyboard(message.from_user.id))
    await award_achievement(message.from_user.id, 'FIRST_NOTE', message)
    
# ... після хендлера process_note ...

@router.message(F.text == ANALYZE_BTN_TEXT)
async def start_symptom_checker(message: Message, state: FSMContext):
    await state.set_state(Form.symptom_checker_start)
    await message.answer(
        "Оберіть основний симптом або опишіть його:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🤯 Головний біль", callback_data="symptom:headache")],
            [InlineKeyboardButton(text="🤒 Біль у горлі", callback_data="symptom:sore_throat")],
            [InlineKeyboardButton(text="📝 Інше (описати текстом)", callback_data="symptom:other")]
        ])
    )

# !!! ДІАГНОСТИЧНИЙ ОБРОБНИК - ВСТАВТЕ В САМИЙ КІНЕЦЬ ФАЙЛУ !!!
@router.message()
async def catch_all_unhandled_messages(message: Message, state: FSMContext):
    """
    Цей хендлер ловить ВСІ текстові повідомлення, які не були оброблені
    іншими хендлерами, і показує поточний стан бота.
    """
    current_state = await state.get_state()
    await message.answer(
        f"<b>Діагностичне повідомлення:</b>\n\n"
        f"Отримано текст: «<code>{message.text}</code>»\n"
        f"Поточний стан бота: <b>{current_state}</b>"
    )
    
