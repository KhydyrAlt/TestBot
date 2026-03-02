import asyncio
import sqlite3
import logging
import os
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import (
    ReplyKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardRemove,
    ChatMemberUpdated
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.utils.markdown import hbold

# ===== ТВОИ НАСТРОЙКИ =====
ADMIN_ID = 911966345  # Твой ID
BOT_TOKEN = os.getenv('BOT_TOKEN')

if not BOT_TOKEN:
    print("❌ Токен не найден!")
    exit(1)

# ===== НАСТРОЙКИ =====
DB_PATH = "users.db"

# ===== ЛОГИРОВАНИЕ =====
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ===== СОЗДАЁМ БОТА =====
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# ===== БАЗА ДАННЫХ =====
class Database:
    @staticmethod
    def init_db():
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                workplace TEXT NOT NULL,
                registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                is_blocked INTEGER DEFAULT 0,
                last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_is_blocked ON users(is_blocked)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_last_active ON users(last_active)")
        conn.commit()
        conn.close()
        logger.info("✅ База данных сотрудников создана")

    @staticmethod
    def get_user(user_id):
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT name, workplace, is_blocked FROM users WHERE user_id = ?", 
            (user_id,)
        )
        user = cursor.fetchone()
        conn.close()
        return user

    @staticmethod
    def save_user(user_id, name, workplace):
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO users (user_id, name, workplace, last_active) 
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id) DO UPDATE SET 
                name = excluded.name,
                workplace = excluded.workplace,
                is_blocked = 0,
                last_active = CURRENT_TIMESTAMP
        """, (user_id, name, workplace))
        conn.commit()
        conn.close()
        return True

    @staticmethod
    def mark_user_blocked(user_id):
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE users SET is_blocked = 1 WHERE user_id = ?", 
            (user_id,)
        )
        conn.commit()
        conn.close()

    @staticmethod
    def mark_user_unblocked(user_id):
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE users SET is_blocked = 0, last_active = CURRENT_TIMESTAMP WHERE user_id = ?", 
            (user_id,)
        )
        conn.commit()
        conn.close()

    @staticmethod
    def get_all_users(include_blocked=False):
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        if include_blocked:
            cursor.execute("SELECT user_id, name FROM users ORDER BY registered_at DESC")
        else:
            cursor.execute("SELECT user_id, name FROM users WHERE is_blocked = 0 ORDER BY registered_at DESC")
        users = cursor.fetchall()
        conn.close()
        return users

    @staticmethod
    def get_stats():
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM users")
        total_users = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM users WHERE is_blocked = 1")
        blocked_users = cursor.fetchone()[0]
        conn.close()
        return {
            "total_users": total_users,
            "blocked_users": blocked_users,
            "active_users": total_users - blocked_users
        }

    @staticmethod
    def update_last_active(user_id):
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE users SET last_active = CURRENT_TIMESTAMP WHERE user_id = ?", 
            (user_id,)
        )
        conn.commit()
        conn.close()

# Создаём базу
Database.init_db()

# ===== СОСТОЯНИЯ =====
class Form(StatesGroup):
    name = State()
    confirm_name = State()
    workplace = State()
    confirm_workplace = State()
    problem = State()
    edit_choice = State()      # Главное меню
    edit_profile = State()      # Меню редактирования профиля
    edit_name = State()
    edit_workplace = State()

# ===== КЛАВИАТУРЫ =====
def get_main_menu_keyboard():
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📝 Новая заявка")],
            [KeyboardButton(text="⚙️ Изменить профиль")]
        ],
        resize_keyboard=True
    )
    return keyboard

def get_edit_profile_keyboard():
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="✏️ Изменить имя"), 
             KeyboardButton(text="📍 Изменить место")],
            [KeyboardButton(text="◀️ Назад")]
        ],
        resize_keyboard=True
    )
    return keyboard

def get_confirm_keyboard():
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="✅ Да"), KeyboardButton(text="❌ Нет")]
        ],
        resize_keyboard=True
    )
    return keyboard

def get_workplace_keyboard():
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Офис1"), KeyboardButton(text="Офис2")],
            [KeyboardButton(text="Ресепшен"), KeyboardButton(text="Менеджеры")],
            [KeyboardButton(text="Касса"), KeyboardButton(text="РОП,РКС,Приемка")],
            [KeyboardButton(text="Логистика"), KeyboardButton(text="Салон б/у")],
            [KeyboardButton(text="Сервис"), KeyboardButton(text="Склад")]
        ],
        resize_keyboard=True,
        input_field_placeholder="Выберите рабочее место"
    )
    return keyboard

def get_problem_keyboard():
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="1С"), KeyboardButton(text="Принтер")],
            [KeyboardButton(text="Сильвер"), KeyboardButton(text="ВПН")],
            [KeyboardButton(text="Проблемы с ПК"), KeyboardButton(text="Картридж")],
            [KeyboardButton(text="Камеры"), KeyboardButton(text="ПАМАГИТИ")]
        ],
        resize_keyboard=True,
        input_field_placeholder="Выберите проблему"
    )
    return keyboard

# ===== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ =====
async def show_main_menu(message: types.Message, state: FSMContext, user_data=None):
    """Показывает главное меню"""
    if user_data:
        name, workplace, _ = user_data
    else:
        data = await state.get_data()
        name = data.get('name', 'Пользователь')
        workplace = data.get('workplace', 'не указано')
    
    await state.set_state(Form.edit_choice)
    await message.answer(
        f"👋 С возвращением, {hbold(name)}!\n"
        f"📍 Ваше место: {hbold(workplace)}\n\n"
        f"Что хотите сделать?",
        reply_markup=get_main_menu_keyboard(),
        parse_mode="HTML"
    )

async def start_registration(message: types.Message, state: FSMContext):
    """Начинает регистрацию нового пользователя"""
    await state.set_state(Form.name)
    await message.answer(
        "👋 Привет! Я бот для вызова сисадмина.\n"
        "Давайте познакомимся.\n\n"
        "Как вас зовут?"
    )

# ===== ОБРАБОТЧИК СТАТУСА ЧАТА =====
@dp.my_chat_member()
async def handle_chat_member_update(update: ChatMemberUpdated):
    user_id = update.from_user.id
    if update.new_chat_member.status == "kicked":
        Database.mark_user_blocked(user_id)
        logger.info(f"🚫 Пользователь {user_id} заблокировал бота")
    elif update.new_chat_member.status == "member":
        user = Database.get_user(user_id)
        if user:
            Database.mark_user_unblocked(user_id)
            logger.info(f"✅ Пользователь {user_id} снова начал чат с ботом")

# ===== ОБРАБОТЧИКИ КОМАНД =====
@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    current_state = await state.get_state()
    
    if current_state:
        await state.clear()
        await message.answer("🔄 Перезапускаю бота...")
    
    user = Database.get_user(user_id)
    
    if user:
        if user[2]:
            Database.mark_user_unblocked(user_id)
        await state.update_data(name=user[0], workplace=user[1])
        await show_main_menu(message, state, user)
    else:
        await start_registration(message, state)

@dp.message(Command("cancel"))
async def cmd_cancel(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "❌ Действие отменено.\n"
        "Чтобы начать заново, нажмите /start",
        reply_markup=ReplyKeyboardRemove()
    )

@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    help_text = (
        f"{hbold('🤖 Помощь по боту')}\n\n"
        f"{hbold('Основные команды:')}\n"
        f"/start - начать работу\n"
        f"/cancel - отменить текущее действие\n"
        f"/help - показать эту справку\n\n"
        f"{hbold('Как пользоваться:')}\n"
        f"1. При первом запуске нужно зарегистрироваться\n"
        f"2. В главном меню можно создать заявку или изменить профиль\n"
        f"3. Для создания заявки выберите проблему из списка\n"
        f"4. Сисадмин получит уведомление и свяжется с вами"
    )
    await message.answer(help_text, parse_mode="HTML")

@dp.message(Command("stats"))
async def cmd_stats(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("⛔ У вас нет прав для этой команды")
        return
    
    stats = Database.get_stats()
    text = (
        f"{hbold('📊 Статистика бота:')}\n\n"
        f"👥 Всего сотрудников: {stats['total_users']}\n"
        f"✅ Активных: {stats['active_users']}\n"
        f"🚫 Заблокировали бота: {stats['blocked_users']}"
    )
    await message.answer(text, parse_mode="HTML")

@dp.message(Command("send"))
async def cmd_send(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("⛔ Только админ может делать рассылку")
        return
    
    text = message.text.replace("/send", "", 1).strip()
    if not text:
        await message.answer(
            "❌ Напишите текст после команды.\n"
            "Пример: /send Завтра сервер перезагрузится в 23:00"
        )
        return
    
    if len(text) > 4000:
        await message.answer("❌ Сообщение слишком длинное (макс. 4000 символов)")
        return
    
    users = Database.get_all_users(include_blocked=False)
    if not users:
        await message.answer("📭 Нет активных сотрудников для рассылки")
        return
    
    status_msg = await message.answer(f"📤 Отправляю {len(users)} сотрудникам...")
    
    success = 0
    failed = 0
    blocked = 0
    
    for user_id, name in users:
        try:
            await bot.send_message(
                user_id,
                f"📢 Уведомление от админа:\n\n{text}"
            )
            success += 1
            await asyncio.sleep(0.03)
        except Exception as e:
            failed += 1
            if "bot was blocked" in str(e):
                blocked += 1
                Database.mark_user_blocked(user_id)
                logger.info(f"Пользователь {name} ({user_id}) заблокировал бота")
    
    report = (
        f"✅ Рассылка завершена\n\n"
        f"📊 Всего: {len(users)}\n"
        f"✅ Доставлено: {success}\n"
        f"❌ Ошибок: {failed}"
    )
    
    if blocked > 0:
        report += f"\n🚫 Заблокировали бота: {blocked}"
    
    await status_msg.edit_text(report)

@dp.message(Command("users"))
async def cmd_users(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    
    users = Database.get_all_users(include_blocked=True)
    
    if not users:
        await message.answer("📭 База данных пуста")
        return
    
    text = f"📋 Список сотрудников:\n\n"
    for i, (user_id, name) in enumerate(users, 1):
        user_data = Database.get_user(user_id)
        blocked = " [ЗАБЛОКИРОВАН]" if user_data and user_data[2] else ""
        text += f"{i}. {name} (ID: {user_id}){blocked}\n"
        
        if len(text) > 3500:
            text += "..."
            break
    
    await message.answer(text)

@dp.message(Command("clear_blocked"))
async def cmd_clear_blocked(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM users WHERE is_blocked = 1")
    deleted = cursor.rowcount
    conn.commit()
    conn.close()
    
    await message.answer(f"✅ Удалено {deleted} заблокировавших пользователей")

# ===== ОСНОВНЫЕ ОБРАБОТЧИКИ СОСТОЯНИЙ =====
@dp.message(Form.name)
async def process_name(message: types.Message, state: FSMContext):
    name = message.text.strip()
    if len(name) < 2 or len(name) > 50:
        await message.answer("Имя должно быть от 2 до 50 символов. Попробуйте еще раз:")
        return
    
    await state.update_data(name=name)
    await state.set_state(Form.confirm_name)
    await message.answer(
        f"Вас зовут {name}?",
        reply_markup=get_confirm_keyboard()
    )

@dp.message(Form.confirm_name)
async def confirm_name(message: types.Message, state: FSMContext):
    if message.text == "✅ Да":
        await state.set_state(Form.workplace)
        await message.answer(
            "📍 Выберите ваше рабочее место:",
            reply_markup=get_workplace_keyboard()
        )
    elif message.text == "❌ Нет":
        await state.set_state(Form.name)
        await message.answer(
            "Введите имя заново:",
            reply_markup=ReplyKeyboardRemove()
        )
    else:
        await message.answer(
            "Пожалуйста, выберите ✅ Да или ❌ Нет",
            reply_markup=get_confirm_keyboard()
        )

@dp.message(Form.workplace)
async def process_workplace(message: types.Message, state: FSMContext):
    workplace = message.text
    valid_places = ["Офис1", "Офис2", "Ресепшен", "Менеджеры", "Касса", 
                    "РОП,РКС,Приемка", "Логистика", "Салон б/у", "Сервис", "Склад"]
    
    if workplace not in valid_places:
        await message.answer(
            "Пожалуйста, выберите место из списка:",
            reply_markup=get_workplace_keyboard()
        )
        return
    
    await state.update_data(workplace=workplace)
    await state.set_state(Form.confirm_workplace)
    await message.answer(
        f"Вы работаете в {workplace}?",
        reply_markup=get_confirm_keyboard()
    )

@dp.message(Form.confirm_workplace)
async def confirm_workplace(message: types.Message, state: FSMContext):
    if message.text == "✅ Да":
        data = await state.get_data()
        Database.save_user(message.from_user.id, data['name'], data['workplace'])
        await show_main_menu(message, state)
    elif message.text == "❌ Нет":
        await state.set_state(Form.workplace)
        await message.answer(
            "📍 Выберите рабочее место заново:",
            reply_markup=get_workplace_keyboard()
        )
    else:
        await message.answer(
            "Пожалуйста, выберите ✅ Да или ❌ Нет",
            reply_markup=get_confirm_keyboard()
        )

@dp.message(Form.edit_choice)
async def process_main_menu(message: types.Message, state: FSMContext):
    data = await state.get_data()
    
    if message.text == "📝 Новая заявка":
        await state.set_state(Form.problem)
        await message.answer(
            "Выберите проблему:",
            reply_markup=get_problem_keyboard()
        )
    elif message.text == "⚙️ Изменить профиль":
        await state.set_state(Form.edit_profile)
        await message.answer(
            f"✏️ Редактирование профиля\n\n"
            f"Текущее имя: {data['name']}\n"
            f"Текущее место: {data['workplace']}\n\n"
            f"Что хотите изменить?",
            reply_markup=get_edit_profile_keyboard()
        )
    else:
        await message.answer(
            "Пожалуйста, выберите действие из меню:",
            reply_markup=get_main_menu_keyboard()
        )

@dp.message(Form.edit_profile)
async def process_edit_profile(message: types.Message, state: FSMContext):
    if message.text == "✏️ Изменить имя":
        await state.set_state(Form.edit_name)
        await message.answer(
            "Введите новое имя:",
            reply_markup=ReplyKeyboardRemove()
        )
    elif message.text == "📍 Изменить место":
        await state.set_state(Form.edit_workplace)
        await message.answer(
            "📍 Выберите новое рабочее место:",
            reply_markup=get_workplace_keyboard()
        )
    elif message.text == "◀️ Назад":
        await show_main_menu(message, state)
    else:
        await message.answer(
            "Пожалуйста, выберите действие из меню:",
            reply_markup=get_edit_profile_keyboard()
        )

@dp.message(Form.edit_name)
async def process_edit_name(message: types.Message, state: FSMContext):
    new_name = message.text.strip()
    if len(new_name) < 2 or len(new_name) > 50:
        await message.answer("Имя должно быть от 2 до 50 символов. Попробуйте еще раз:")
        return
    
    data = await state.get_data()
    await state.update_data(name=new_name)
    Database.save_user(message.from_user.id, new_name, data['workplace'])
    await show_main_menu(message, state)

@dp.message(Form.edit_workplace)
async def process_edit_workplace(message: types.Message, state: FSMContext):
    new_workplace = message.text
    valid_places = ["Офис1", "Офис2", "Ресепшен", "Менеджеры", "Касса", 
                    "РОП,РКС,Приемка", "Логистика", "Салон б/у", "Сервис", "Склад"]
    
    if new_workplace not in valid_places:
        await message.answer(
            "Пожалуйста, выберите место из списка:",
            reply_markup=get_workplace_keyboard()
        )
        return
    
    data = await state.get_data()
    await state.update_data(workplace=new_workplace)
    Database.save_user(message.from_user.id, data['name'], new_workplace)
    await show_main_menu(message, state)

@dp.message(Form.problem)
async def process_problem(message: types.Message, state: FSMContext):
    problem = message.text
    valid_problems = ["1С", "Принтер", "Сильвер", "ВПН", "Проблемы с ПК", 
                      "Картридж", "Камеры", "ПАМАГИТИ"]
    
    if problem not in valid_problems:
        await message.answer(
            "Пожалуйста, выберите проблему из списка:",
            reply_markup=get_problem_keyboard()
        )
        return
    
    data = await state.get_data()
    Database.update_last_active(message.from_user.id)
    
    try:
        await bot.send_message(
            ADMIN_ID,
            f"🚨 Новая заявка!\n\n"
            f"👤 Имя: {data['name']}\n"
            f"📍 Место: {data['workplace']}\n"
            f"❓ Проблема: {problem}\n"
            f"🆔 ID: {message.from_user.id}"
        )
        logger.info(f"✅ Заявка отправлена админу от {data['name']}")
    except Exception as e:
        logger.error(f"❌ Ошибка отправки админу: {e}")
        await message.answer("⚠️ Не удалось отправить заявку. Попробуйте позже.")
        await state.clear()
        return
    
    await state.clear()
    await message.answer(
        f"✅ Заявка принята!\n\n"
        f"Сисадмин уже получил уведомление.\n"
        f"Чтобы создать новую заявку, нажмите /start",
        reply_markup=ReplyKeyboardRemove()
    )

# ===== УНИВЕРСАЛЬНЫЙ ОБРАБОТЧИК =====
@dp.message()
async def handle_any_message(message: types.Message, state: FSMContext):
    """Обработчик для любых других сообщений"""
    user_id = message.from_user.id
    current_state = await state.get_state()
    
    # Проверяем, есть ли пользователь в базе
    user = Database.get_user(user_id)
    
    # Если пользователя НЕТ в базе - начинаем регистрацию
    if not user:
        await start_registration(message, state)
        return
    
    # Пользователь есть в базе
    user_name, user_workplace, is_blocked = user
    
    # Если был заблокирован - снимаем блокировку
    if is_blocked:
        Database.mark_user_unblocked(user_id)
    
    # Обновляем активность
    Database.update_last_active(user_id)
    
    # Сохраняем данные в состояние
    await state.update_data(name=user_name, workplace=user_workplace)
    
    # Если нет активного состояния
    if current_state is None:
        await show_main_menu(message, state, user)
        return
    
    # Если состояние есть - напоминаем что делать
    # Для состояния problem показываем данные пользователя (только при первом сообщении после очистки)
    if current_state == Form.problem.state:
        # Проверяем, было ли уже напоминание в этой сессии
        data = await state.get_data()
        if not data.get('reminded', False):
            await state.update_data(reminded=True)
            await message.answer(
                f"👤 {hbold(user_name)} | 📍 {hbold(user_workplace)}\n\n"
                f"⚠️ Выберите проблему из списка:",
                reply_markup=get_problem_keyboard(),
                parse_mode="HTML"
            )
        else:
            await message.answer(
                "⚠️ Выберите проблему из списка:",
                reply_markup=get_problem_keyboard()
            )
        return
    
    # Для остальных состояний - просто напоминание
    state_info = {
        Form.name: "Введите ваше имя",
        Form.confirm_name: "Подтвердите имя кнопками ✅ Да или ❌ Нет",
        Form.workplace: "Выберите рабочее место из списка",
        Form.confirm_workplace: "Подтвердите место работы кнопками ✅ Да или ❌ Нет",
        Form.edit_choice: "Выберите действие из меню",
        Form.edit_profile: "Выберите, что хотите изменить",
        Form.edit_name: "Введите новое имя",
        Form.edit_workplace: "Выберите новое рабочее место из списка"
    }
    
    if current_state in state_info:
        await message.answer(
            f"⚠️ {state_info[current_state]}",
            reply_markup=get_appropriate_keyboard(current_state)
        )

def get_appropriate_keyboard(state):
    """Возвращает клавиатуру для конкретного состояния"""
    keyboards = {
        Form.confirm_name: get_confirm_keyboard(),
        Form.workplace: get_workplace_keyboard(),
        Form.confirm_workplace: get_confirm_keyboard(),
        Form.edit_choice: get_main_menu_keyboard(),
        Form.edit_profile: get_edit_profile_keyboard(),
        Form.edit_workplace: get_workplace_keyboard(),
        Form.problem: get_problem_keyboard()
    }
    return keyboards.get(state, ReplyKeyboardRemove())

# ===== ЗАПУСК БОТА =====
async def main():
    print("="*50)
    print("🚀 Бот для вызова сисадмина запущен!")
    print(f"👤 Админ ID: {ADMIN_ID}")
    print(f"📁 База данных: {DB_PATH}")
    print("="*50)
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("🛑 Бот остановлен")