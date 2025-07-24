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
    checkin_activity = State()
    checkin_stress = State()
    checkin_water = State()
    waiting_for_note = State()

# --- БАЗА ДАНИХ ---
def setup_database():
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    cursor.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, first_name TEXT, age INTEGER, gender TEXT, weight_kg REAL, height_cm REAL)")
    cursor.execute("CREATE TABLE IF NOT EXISTS health_entries (entry_id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP, mood TEXT, sleep_quality TEXT, systolic_pressure INTEGER, diastolic_pressure INTEGER, FOREIGN KEY (user_id) REFERENCES users(user_id))")
    cursor.execute("CREATE TABLE IF NOT EXISTS medications (med_id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, med_name TEXT NOT NULL, dosage TEXT, schedule TEXT, is_active BOOLEAN DEFAULT 1, FOREIGN KEY (user_id) REFERENCES users(user_id))")
    cursor.execute("CREATE TABLE IF NOT EXISTS medication_log (log_id INTEGER PRIMARY KEY AUTOINCREMENT, med_id INTEGER, user_id INTEGER, timestamp DATETIME, status TEXT, FOREIGN KEY (med_id) REFERENCES medications(med_id))")
    cursor.execute("CREATE TABLE IF NOT EXISTS openai_interactions (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP, prompt TEXT, response TEXT)")
    cursor.execute("CREATE TABLE IF NOT EXISTS cycles (cycle_id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, start_date DATE, end_date DATE, FOREIGN KEY (user_id) REFERENCES users(user_id))")

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
    try: cursor.execute("ALTER TABLE health_entries ADD COLUMN activity_level TEXT")
    except: pass
    try: cursor.execute("ALTER TABLE health_entries ADD COLUMN stress_level TEXT")
    except: pass
    try: cursor.execute("ALTER TABLE health_entries ADD COLUMN water_intake TEXT")
    except: pass

    cursor.execute("CREATE TABLE IF NOT EXISTS achievements (code TEXT PRIMARY KEY, name TEXT, description TEXT, icon TEXT)")
    cursor.execute("CREATE TABLE IF NOT EXISTS user_achievements (user_id INTEGER, achievement_code TEXT, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (user_id, achievement_code), FOREIGN KEY (user_id) REFERENCES users(user_id), FOREIGN KEY (achievement_code) REFERENCES achievements(code))")
    achievements_data = [('FIRST_REPORT', 'Перший звіт', 'Ви згенерували свій перший звіт для лікаря.', '📄'), ('STREAK_5_DAYS', 'Стабільність', 'Ви ведете щоденник 5 днів поспіль.', '🔥'), ('FIRST_NOTE', 'Нотатки', 'Ви зробили свій перший швидкий запис.', '✍️')]
    cursor.executemany("INSERT OR IGNORE INTO achievements (code, name, description, icon) VALUES (?, ?, ?, ?)", achievements_data)

    conn.commit()
    conn.close()
    logging.info("Базу даних перевірено та налаштовано.")

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
    filtered_kwargs = {k: v for k, v in kwargs.items() if k in valid_keys and v is not None}
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
# ... (інші функції БД) ...
def add_medication(user_id: int, name: str, dosage: str, schedule: str):
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO medications (user_id, med_name, dosage, schedule) VALUES (?, ?, ?, ?)", (user_id, name, dosage, schedule))
    conn.commit()
    conn.close()

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
        recommendations.append("Високий рівень стресу. Спробуйте знайти 10-15 хвилин для короткої прогулянки або дихальних вправ.")
    if data.get("activity_level") == "Низька":
        recommendations.append("Низька активність сьогодні. Навіть коротка 20-хвилинна прогулянка може значно покращити самопочуття.")
    if data.get("mood") == "😞 Поганий" and data.get("activity_level") == "Низька":
        recommendations.append("Іноді фізична активність допомагає покращити настрій.")
    if "погано" in data.get("sleep_quality", "").lower() or "мало" in data.get("sleep_quality", "").lower():
        recommendations.append("Поганий сон впливає на весь день. Спробуйте провітрити кімнату і відкласти телефон за годину до сну.")
    if data.get("water_intake") == "Менше 1 літра":
        recommendations.append("Не забувайте пити достатньо води протягом дня. Це важливо для енергії та концентрації.")
    if not recommendations:
        return "✨ Чудові показники сьогодні! Так тримати!"
    else:
        return "💡 **Ось декілька порад на основі ваших записів:**\n\n- " + "\n- ".join(recommendations)

# --- Аналітика та звіти ---
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
        dt_object = datetime.datetime.strptime(timestamp, '%Y-%m-%d %H:%M:%S')
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

# --- Планувальник та Startup ---
async def send_weekly_report(bot: Bot, user_id: int):
    # Ця функція потребує оновлення для роботи з новими даними, поки що заглушка
    logging.info(f"Спроба генерації тижневого звіту для user_id={user_id}")

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
    cursor.execute("SELECT DISTINCT user_id FROM users") # Розсилаємо всім, хто є в базі
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

# --- Клавіатури ---
def get_main_menu_keyboard(user_id: int):
    profile, is_female = get_user_profile(user_id), False
    if profile and profile[2] and profile[2].lower() in ['жіноча', 'female']: is_female = True
    keyboard = [
        [KeyboardButton(text=ANALYZE_BTN_TEXT)],
        [KeyboardButton(text="☀️ Щоденний Check-in"), KeyboardButton(text="📝 Швидкий запис")],
        [KeyboardButton(text="👤 Мій профіль"), KeyboardButton(text="💊 Мої ліки")],
        [KeyboardButton(text="📖 Переглянути історію"), KeyboardButton(text="📄 Створити звіт")]
    ]
    if is_female: keyboard.insert(2, [KeyboardButton(text="🌸 Жіноче здоров'я")])
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)

cancel_keyboard = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="⬅️ Головне меню")]], resize_keyboard=True)

# --- Обробники (Handlers) ---
@router.message(F.text == "⬅️ Головне меню")
async def back_to_main_menu(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Дію скасовано. Ви повернулися в головне меню.", reply_markup=get_main_menu_keyboard(message.from_user.id))

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
    if not profile_data: return await message.answer("Профіль не знайдено.")
    _, _, _, _, _, blood, allergies, chronic, contact = profile_data
    sos_text = (f"**🚑 Ваша Екстрена картка:**\n\n**Група крові:** {blood or 'Не вказано'}\n**Алергії:** {allergies or 'Не вказано'}\n**Хронічні захворювання:** {chronic or 'Не вказано'}\n**Екстрений контакт:** {contact or 'Не вказано'}")
    await message.answer(sos_text)
    
@router.message(F.text == "👤 Мій профіль")
async def show_profile(message: Message, state: FSMContext):
    profile_data = get_user_profile(message.from_user.id)
    if not profile_data: return await message.answer("Помилка. Спробуйте /start")
    name, age, gender, weight, height, _, _, _, _ = profile_data
    profile_text = (f"**👤 Ваш профіль:**\n\nІм'я: {name}\nВік: {age or 'Не вказано'}\nСтать: {gender or 'Не вказано'}\nВага: {f'{weight} кг' if weight else 'Не вказано'}\nЗріст: {f'{height} см' if height else 'Не вказано'}")
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✏️ Редагувати профіль", callback_data="edit_profile")], [InlineKeyboardButton(text="🚑 Екстрена картка", callback_data="edit_emergency_card")]])
    await message.answer(profile_text, reply_markup=keyboard)

@router.callback_query(F.data == "edit_emergency_card")
async def edit_emergency_card_menu(callback: CallbackQuery):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Група крові", callback_data="edit_field:blood_group")],[InlineKeyboardButton(text="Алергії", callback_data="edit_field:allergies")],[InlineKeyboardButton(text="Хронічні захворювання", callback_data="edit_field:chronic_diseases")],[InlineKeyboardButton(text="Екстрений контакт", callback_data="edit_field:emergency_contact")],[InlineKeyboardButton(text="⬅️ Назад до профілю", callback_data="back_to_profile")]])
    await callback.message.edit_text("Що ви хочете змінити в екстреній картці?", reply_markup=keyboard)

@router.callback_query(F.data == "edit_profile")
async def edit_profile_menu(callback: CallbackQuery):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Вік", callback_data="edit_field:age"), InlineKeyboardButton(text="Стать", callback_data="edit_field:gender")], [InlineKeyboardButton(text="Вага (кг)", callback_data="edit_field:weight_kg"), InlineKeyboardButton(text="Зріст (см)", callback_data="edit_field:height_cm")], [InlineKeyboardButton(text="⬅️ Назад до профілю", callback_data="back_to_profile")]])
    await callback.message.edit_text("Що ви хочете змінити?", reply_markup=keyboard)

@router.callback_query(F.data.startswith("edit_field:"))
async def ask_for_field(callback: CallbackQuery, state: FSMContext):
    field = callback.data.split(":")[1]
    prompts = {"age": "Введіть ваш вік:", "gender": "Введіть вашу стать ('жіноча' або 'чоловіча'):", "weight_kg": "Введіть вашу вагу в кілограмах:", "height_cm": "Введіть ваш зріст в сантиметрах:", "blood_group": "Введіть вашу групу крові та резус-фактор (напр., 'A(II) Rh+'):", "allergies": "Перелічіть ваші алергії (напр., 'пеніцилін'):", "chronic_diseases": "Перелічіть ваші хронічні захворювання:", "emergency_contact": "Введіть ім'я та номер екстреного контакту:"}
    await state.set_state(getattr(Form, f"edit_{field}"))
    await state.update_data(field_to_edit=field)
    await callback.message.answer(prompts.get(field, "Введіть нове значення:"), reply_markup=cancel_keyboard)
    await callback.answer()

async def process_field_update(message: Message, state: FSMContext):
    user_data = await state.get_data()
    field, value = user_data.get("field_to_edit"), message.text
    if field in ['age', 'weight_kg', 'height_cm'] and not value.replace('.', '', 1).isdigit(): return await message.answer("Будь ласка, введіть числове значення.")
    if field == 'gender' and value.lower() not in ['жіноча', 'чоловіча', 'female', 'male']: return await message.answer("Будь ласка, введіть 'жіноча' або 'чоловіча'.")
    update_user_field(message.from_user.id, field, value)
    await state.clear()
    await message.answer("✅ Дані оновлено.", reply_markup=get_main_menu_keyboard(message.from_user.id))
    await show_profile(message, state)

for field_name in ["age", "gender", "weight_kg", "height_cm", "blood_group", "allergies", "chronic_diseases", "emergency_contact"]:
    router.message.register(process_field_update, getattr(Form, f"edit_{field_name}"))

@router.callback_query(F.data == "back_to_profile")
async def back_to_profile_view(callback: CallbackQuery, state: FSMContext):
    await callback.message.delete()
    await show_profile(callback.message, state)

@router.message(F.text == "💊 Мої ліки")
async def show_meds(message: Message, state: FSMContext):
    meds = get_user_medications(message.from_user.id)
    text = "**💊 Ваші ліки:**\n\n" if meds else "У вас немає доданих ліків."
    if meds: text += "\n".join([f"• **{name}** ({dosage})\n   └ Розклад: {schedule} /del{med_id}" for med_id, name, dosage, schedule in meds])
    await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="➕ Додати ліки", callback_data="add_medication")]]))

@router.callback_query(F.data == "add_medication")
async def add_med_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(Form.add_med_name), await callback.message.answer("Введіть назву ліків:", reply_markup=cancel_keyboard), await callback.answer()

@router.message(Form.add_med_name)
async def process_med_name(message: Message, state: FSMContext):
    await state.update_data(name=message.text), await state.set_state(Form.add_med_dosage), await message.answer("Тепер введіть дозування (напр., '1 таблетка', '50 mg'):", reply_markup=cancel_keyboard)

@router.message(Form.add_med_dosage)
async def process_med_dosage(message: Message, state: FSMContext):
    await state.update_data(dosage=message.text), await state.set_state(Form.add_med_schedule), await message.answer("Введіть розклад у форматі HH:MM (через кому, напр., '09:00, 21:00'):", reply_markup=cancel_keyboard)

@router.message(Form.add_med_schedule)
async def process_med_schedule(message: Message, state: FSMContext, bot: Bot):
    if not re.match(r"^\d{2}:\d{2}(,\s*\d{2}:\d{2})*$", message.text): return await message.answer("Неправильний формат. Введіть час як 'HH:MM'.")
    data = await state.get_data()
    add_medication(message.from_user.id, data['name'], data['dosage'], message.text)
    await message.answer(f"✅ Ліки '{data['name']}' додано.", reply_markup=get_main_menu_keyboard(message.from_user.id))
    await state.clear(), schedule_reminders(bot), await show_meds(message, state)

@router.message(F.text.startswith("/del"))
async def delete_med(message: Message, bot: Bot):
    try: set_medication_inactive(int(message.text[4:]), message.from_user.id), await message.answer(f"Ліки видалено з активних."), schedule_reminders(bot)
    except (ValueError, IndexError): await message.answer("Неправильний формат. Використовуйте /del<ID>.")

@router.callback_query(F.data.startswith("med_log:"))
async def log_med_status(callback: CallbackQuery):
    _, status, med_id_str = callback.data.split(":")
    log_medication_status(callback.from_user.id, int(med_id_str), status)
    status_text = "Прийнято" if status == "taken" else "Пропущено"
    await callback.message.edit_text(f"Відзначено: **{status_text}**"), await callback.answer(f"Статус оновлено: {status_text}")

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

@router.message(F.text == "📄 Створити звіт")
async def cmd_create_report(message: Message):
    await message.answer("Починаю готувати ваш звіт... ⏳")
    try:
        if report_path := generate_doctor_report_pdf(message.from_user.id):
            await message.answer_document(types.FSInputFile(report_path), caption="Ваш звіт готовий.")
            if os.path.exists(report_path): os.remove(report_path)
            await award_achievement(message.from_user.id, 'FIRST_REPORT', message)
        else: await message.answer("Недостатньо даних для створення звіту.")
    except Exception as e:
        logging.exception("Помилка при генерації звіту:"), await message.answer("Вибачте, сталася помилка.")

@router.message(F.text == "📖 Переглянути історію")
async def view_history(message: Message):
    if not (history := get_user_history(message.from_user.id)): return await message.answer("Ваша історія записів порожня.")
    response = "**Останні записи про здоров'я:**\n\n"
    for record in history:
        timestamp, mood, sleep, note, activity, stress, water = record
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

@router.message(F.text == "☀️ Щоденний Check-in")
async def start_checkin(message: Message, state: FSMContext):
    await state.set_state(Form.checkin_mood)
    keyboard = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="😊 Чудовий"), KeyboardButton(text="😐 Нормальний"), KeyboardButton(text="😞 Поганий")], [KeyboardButton(text="⬅️ Головне меню")]], resize_keyboard=True)
    await message.answer("Як ваш настрій сьогодні?", reply_markup=keyboard)

@router.message(Form.checkin_mood)
async def process_checkin_mood(message: Message, state: FSMContext):
    await state.update_data(mood=message.text)
    await state.set_state(Form.checkin_sleep)
    await message.answer("Як ви спали? (напр., '8 годин, добре')", reply_markup=cancel_keyboard)

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

@router.message(F.text == "🌸 Жіноче здоров'я")
async def show_cycle_menu(message: Message):
    avg_len, next_date = get_cycle_predictions(message.from_user.id)
    text = f"Ваша середня тривалість циклу: ~{avg_len} днів.\nОрієнтовний початок наступного циклу: **{next_date}**." if avg_len else "Даних для прогнозу ще недостатньо."
    await message.answer(f"{text}\n\nОберіть дію:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🩸 Почався сьогодні", callback_data="cycle:start")], [InlineKeyboardButton(text="🩸 Закінчився сьогодні", callback_data="cycle:end")]]))

@router.callback_query(F.data == "cycle:start")
async def process_cycle_start(callback: CallbackQuery):
    start_new_cycle(callback.from_user.id), await callback.answer("✅ Новий цикл розпочато.", show_alert=True), await callback.message.delete()

@router.callback_query(F.data == "cycle:end")
async def process_cycle_end(callback: CallbackQuery):
    await callback.answer("✅ Поточний цикл завершено." if end_current_cycle(callback.from_user.id) else "❗️ У вас немає активного циклу.", show_alert=True), await callback.message.delete()
    
async def process_symptoms_generic(message: Message, state: FSMContext, openai_client: AsyncOpenAI, symptoms_text: str):
    await message.answer("Аналізую інформацію... ⏳", reply_markup=get_main_menu_keyboard(message.from_user.id))
    await state.update_data(initial_symptoms=symptoms_text)
    profile_data = get_user_profile(message.from_user.id)
    profile_text, emergency_text = "Дані профілю не вказані.", ""
    if profile_data:
        _, age, gender, weight, height, _, allergies, chronic, _ = profile_data
        profile_text = f"Вік: {age or 'N/A'}. Стать: {gender or 'N/A'}. Вага: {weight or 'N/A'} кг. Зріст: {height or 'N/A'} см."
        if allergies: emergency_text += f"Алергії пацієнта: {allergies}.\n"
        if chronic: emergency_text += f"Хронічні захворювання пацієнта: {chronic}.\n"
    # med_bot_aiogram.py (всередині process_symptoms_generic)

    system_prompt = (
        "Ти - досвідчений медичний AI-асистент. Твоя мета - допомогти користувачеві з попереднім аналізом його стану здоров'я.\n"
        "--- НОВІ ІНСТРУКЦІЇ ПРО СТИЛЬ ---\n"
        "**Спілкуйся простою та зрозумілою мовою, як турботливий, але професійний помічник.**\n"
        "**Уникай надто складних медичних термінів.** Пояснюй так, ніби говориш зі звичайною людиною, а не з лікарем.\n"
        "**Структуруй відповідь логічно, а не просто сухим переліком.** Наприклад: 'Ви скаржитесь на [симптом]. Це може вказувати на кілька речей. Найчастіше це буває через...'\n"
        "--- КІНЕЦЬ НОВИХ ІНСТРУКЦІЙ ---\n"
        "- Проаналізуй симптоми користувача в контексті його профілю.\n"
        "- **ДУЖЕ ВАЖЛИВО:** Обов'язково враховуй вказані алергії та хронічні захворювання пацієнта при аналізі.\n"
        "- Сформулюй 3-4 найбільш імовірні причини.\n"
        "- Запропонуй загальні рекомендації (відпочинок, рідина).\n"
        "- Додай розділ '**Можливі напрямки лікування, які варто обговорити з лікарем:**', дотримуючись правил безпеки (без назв ліків та дозувань).\n"
        "- Якщо симптоми серйозні, порадь НЕГАЙНО звернутися до лікаря.\n"
        "- ЗАВЖДИ закінчуй усю відповідь фразою: \"Пам'ятайте, цей аналіз не є діагнозом. Для точної діагностики зверніться до лікаря.\"\n"
        "- Якщо інформації недостатньо, постав ОДНЕ коротке уточнююче питання."
    )
    user_prompt = f"Профіль пацієнта: {profile_text}\n\n{emergency_text}Проаналізуй наступні скарги пацієнта: «{symptoms_text}»"
    try:
        completion = await openai_client.chat.completions.create(model="openai/gpt-4o-mini", messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}])
        response_text = completion.choices[0].message.content
        save_openai_interaction(message.from_user.id, user_prompt, response_text)
        if "?" in response_text and len(response_text) < 300:
            await state.set_state(Form.answering_clarification)
            await message.answer(response_text, reply_markup=cancel_keyboard)
        else:
            await message.answer(response_text), await state.clear()
    except Exception as e:
        logging.error(f"Помилка OpenAI: {e}"), await message.answer("На жаль, сталася помилка."), await state.clear()

@router.message(Form.answering_clarification)
async def process_clarification_answer(message: Message, state: FSMContext, openai_client: AsyncOpenAI):
    user_answer, user_data = message.text, await state.get_data()
    original_symptoms = user_data.get("initial_symptoms", "")
    combined_text = f"{original_symptoms}\n\nУточнення від пацієнта: {user_answer}"
    await process_symptoms_generic(message, state, openai_client, combined_text)
    
@router.message(F.text == ANALYZE_BTN_TEXT)
async def start_symptom_checker(message: Message, state: FSMContext):
    await state.set_state(Form.symptom_checker_start)
    await message.answer("Оберіть основний симптом або опишіть його:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🤯 Головний біль", callback_data="symptom:headache")], [InlineKeyboardButton(text="🤒 Біль у горлі", callback_data="symptom:sore_throat")], [InlineKeyboardButton(text="📝 Інше (описати текстом)", callback_data="symptom:other")]]))

@router.message(Form.symptom_checker_start)
async def process_other_symptom_text(message: Message, state: FSMContext, openai_client: AsyncOpenAI):
    await process_symptoms_generic(message, state, openai_client, message.text)
    
@router.callback_query(F.data == 'symptom:other', Form.symptom_checker_start)
async def ask_for_other_symptom(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer("Будь ласка, опишіть ваші симптоми одним повідомленням.", reply_markup=cancel_keyboard), await callback.answer()

@router.callback_query(F.data == 'symptom:sore_throat', Form.symptom_checker_start)
async def process_sore_throat(callback: CallbackQuery, state: FSMContext, openai_client: AsyncOpenAI):
    await callback.message.delete()
    await process_symptoms_generic(callback.message, state, openai_client, "Основний симптом: біль у горлі."), await callback.answer()

@router.callback_query(F.data == 'symptom:headache', Form.symptom_checker_start)
async def ask_headache_type(callback: CallbackQuery, state: FSMContext):
    await state.update_data(main_symptom="Головний біль"), await state.set_state(Form.symptom_checker_headache_type)
    await callback.message.edit_text("Який характер вашого головного болю?", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Пульсуючий", callback_data="h_type:pulsing")], [InlineKeyboardButton(text="Стискаючий", callback_data="h_type:squeezing")], [InlineKeyboardButton(text="Постійний, тупий", callback_data="h_type:dull")]]))

@router.callback_query(Form.symptom_checker_headache_type)
async def ask_headache_location(callback: CallbackQuery, state: FSMContext):
    await state.update_data(headache_type=callback.data.split(':')[1]), await state.set_state(Form.symptom_checker_headache_location)
    await callback.message.edit_text("Де саме болить найбільше? (напр., 'в скронях', 'потилиця')")

@router.message(Form.symptom_checker_headache_location)
async def ask_headache_additional(message: Message, state: FSMContext):
    await state.update_data(headache_location=message.text), await state.set_state(Form.symptom_checker_headache_additional)
    await message.answer("Чи є у вас інші симптоми? (напр., 'нудота')\nЯкщо ні, напишіть 'немає'.", reply_markup=cancel_keyboard)

@router.message(Form.symptom_checker_headache_additional)
async def process_headache_final(message: Message, state: FSMContext, openai_client: AsyncOpenAI):
    await state.update_data(additional_symptoms=message.text)
    user_data = await state.get_data()
    prompt = (f"Основний симптом: {user_data.get('main_symptom')}.\n"
              f"Характер болю: {user_data.get('headache_type')}.\n"
              f"Локалізація: {user_data.get('headache_location')}.\n"
              f"Додаткові симптоми: {user_data.get('additional_symptoms')}.")
    await process_symptoms_generic(message, state, openai_client, prompt)