import asyncio
import sqlite3
import logging
import os
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import (
    ReplyKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardRemove,
    ChatMemberUpdated,
    InlineKeyboardMarkup,
    InlineKeyboardButton
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
TICKETS_RETENTION_DAYS = 30  # Храним решенные заявки 30 дней
MAX_TICKETS_PER_USER = 20      # В истории показываем только последние 20
CLEANUP_INTERVAL_HOURS = 24    # Чистим БД раз в сутки

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
        
        # Включаем режим WAL для лучшей производительности
        cursor.execute("PRAGMA journal_mode=WAL")
        
        # Таблица пользователей
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
        
        # Таблица заявок
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS tickets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                user_name TEXT NOT NULL,
                workplace TEXT NOT NULL,
                problem TEXT NOT NULL,
                status TEXT DEFAULT 'new',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                accepted_at TIMESTAMP,
                resolved_at TIMESTAMP,
                admin_notes TEXT,
                FOREIGN KEY (user_id) REFERENCES users (user_id)
            )
        """)
        
        # Индексы для быстрого поиска
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_tickets_status ON tickets(status)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_tickets_user ON tickets(user_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_tickets_created ON tickets(created_at)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_users_active ON users(last_active)")
        
        conn.commit()
        conn.close()
        logger.info("✅ База данных инициализирована")

    @staticmethod
    def cleanup_old_tickets():
        """Автоматически удаляет старые решенные заявки"""
        try:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            
            cutoff_date = (datetime.now() - timedelta(days=TICKETS_RETENTION_DAYS)).strftime('%Y-%m-%d %H:%M:%S')
            cursor.execute("""
                DELETE FROM tickets 
                WHERE status = 'resolved' 
                AND resolved_at < ?
            """, (cutoff_date,))
            
            deleted = cursor.rowcount
            conn.commit()
            conn.close()
            
            if deleted > 0:
                logger.info(f"🧹 Автоочистка: удалено {deleted} старых заявок")
            
            return deleted
        except Exception as e:
            logger.error(f"Ошибка при очистке БД: {e}")
            return 0

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
        cursor.execute("SELECT COUNT(*) FROM tickets WHERE status = 'new'")
        new_tickets = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM tickets WHERE status = 'accepted'")
        active_tickets = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM tickets WHERE status = 'resolved'")
        resolved_tickets = cursor.fetchone()[0]
        conn.close()
        return {
            "total_users": total_users,
            "blocked_users": blocked_users,
            "active_users": total_users - blocked_users,
            "new_tickets": new_tickets,
            "active_tickets": active_tickets,
            "resolved_tickets": resolved_tickets
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

    @staticmethod
    def create_ticket(user_id, user_name, workplace, problem):
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO tickets (user_id, user_name, workplace, problem, status)
            VALUES (?, ?, ?, ?, 'new')
        """, (user_id, user_name, workplace, problem))
        ticket_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return ticket_id

    @staticmethod
    def get_ticket(ticket_id):
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, user_id, user_name, workplace, problem, status, 
                   created_at, accepted_at, resolved_at, admin_notes
            FROM tickets WHERE id = ?
        """, (ticket_id,))
        ticket = cursor.fetchone()
        conn.close()
        return ticket

    @staticmethod
    def get_user_tickets(user_id, limit=5):
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, problem, status, created_at, resolved_at
            FROM tickets 
            WHERE user_id = ? 
            ORDER BY created_at DESC 
            LIMIT ?
        """, (user_id, limit))
        tickets = cursor.fetchall()
        conn.close()
        return tickets

    @staticmethod
    def get_active_tickets():
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, user_id, user_name, workplace, problem, created_at
            FROM tickets 
            WHERE status IN ('new', 'accepted')
            ORDER BY 
                CASE status 
                    WHEN 'new' THEN 1 
                    WHEN 'accepted' THEN 2 
                END,
                created_at ASC
        """)
        tickets = cursor.fetchall()
        conn.close()
        return tickets

    @staticmethod
    def accept_ticket(ticket_id, admin_notes=None):
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE tickets 
            SET status = 'accepted', 
                accepted_at = CURRENT_TIMESTAMP,
                admin_notes = ?
            WHERE id = ? AND status = 'new'
        """, (admin_notes, ticket_id))
        success = cursor.rowcount > 0
        conn.commit()
        conn.close()
        return success

    @staticmethod
    def resolve_ticket(ticket_id, resolution_note=None):
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE tickets 
            SET status = 'resolved', 
                resolved_at = CURRENT_TIMESTAMP,
                admin_notes = CASE 
                    WHEN admin_notes IS NULL THEN ? 
                    ELSE admin_notes || '\nРешение: ' || ? 
                END
            WHERE id = ? AND status IN ('new', 'accepted')
        """, (resolution_note, resolution_note, ticket_id))
        success = cursor.rowcount > 0
        conn.commit()
        conn.close()
        return success

# Создаём базу
Database.init_db()

# ===== СОСТОЯНИЯ =====
class Form(StatesGroup):
    name = State()           # Ввод имени
    workplace = State()       # Выбор места
    problem = State()         # Выбор проблемы
    edit_choice = State()     # Главное меню
    edit_profile = State()    # Меню редактирования
    edit_name = State()       # Редактирование имени
    edit_workplace = State()  # Редактирование места

class AdminStates(StatesGroup):
    choosing_action = State()

# ===== КЛАВИАТУРЫ =====

# 👑 АДМИН-КЛАВИАТУРА
def get_admin_main_keyboard():
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="👑 Админ-панель")],
            [KeyboardButton(text="📋 Активные заявки"), 
             KeyboardButton(text="📊 Статистика")],
            [KeyboardButton(text="📢 Рассылка"), 
             KeyboardButton(text="👥 Сотрудники")],
            [KeyboardButton(text="🧹 Очистить старые заявки")]
        ],
        resize_keyboard=True,
        input_field_placeholder="👑 Меню админа"
    )
    return keyboard

# 👤 КЛАВИАТУРА ДЛЯ ПОЛЬЗОВАТЕЛЕЙ
def get_main_menu_keyboard():
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📝 Новая заявка")],
            [KeyboardButton(text="📋 Мои заявки")],
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

def get_ticket_action_keyboard(ticket_id):
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Принять", callback_data=f"accept_{ticket_id}"),
                InlineKeyboardButton(text="✔️ Решено", callback_data=f"resolve_{ticket_id}")
            ],
            [InlineKeyboardButton(text="🔄 Обновить", callback_data=f"refresh_{ticket_id}")]
        ]
    )
    return keyboard

# ===== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ =====
async def show_main_menu(message: types.Message, state: FSMContext, user_data=None):
    """Показывает главное меню для пользователя"""
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

async def show_admin_panel(message: types.Message, state: FSMContext):
    """Показывает админ-панель"""
    await state.set_state(AdminStates.choosing_action)
    
    stats = Database.get_stats()
    
    await message.answer(
        f"👑 {hbold('АДМИН-ПАНЕЛЬ')}\n\n"
        f"📊 Статистика:\n"
        f"👥 Сотрудников: {stats['total_users']}\n"
        f"🆕 Новых заявок: {stats['new_tickets']}\n"
        f"🔄 В работе: {stats['active_tickets']}\n"
        f"✅ Решено: {stats['resolved_tickets']}\n\n"
        f"Выберите действие:",
        reply_markup=get_admin_main_keyboard(),
        parse_mode="HTML"
    )

async def start_registration(message: types.Message, state: FSMContext):
    """Начинает регистрацию нового пользователя (БЕЗ ПЕРЕСПРОСОВ)"""
    await state.set_state(Form.name)
    await message.answer(
        "👋 Привет! Я бот для вызова сисадмина.\n"
        "Давайте познакомимся.\n\n"
        "✏️ **Введите ваше имя:**",
        parse_mode="Markdown"
    )

def get_status_emoji(status):
    """Возвращает эмодзи для статуса заявки"""
    status_emojis = {
        'new': '🆕',
        'accepted': '🔄',
        'resolved': '✅'
    }
    return status_emojis.get(status, '❓')

def format_ticket_info(ticket):
    """Форматирует информацию о заявке"""
    ticket_id, user_id, user_name, workplace, problem, status, created_at, accepted_at, resolved_at, admin_notes = ticket
    
    status_emoji = get_status_emoji(status)
    status_text = {
        'new': 'Новая',
        'accepted': 'В работе',
        'resolved': 'Решена'
    }.get(status, status)
    
    created_time = datetime.strptime(created_at, '%Y-%m-%d %H:%M:%S').strftime('%d.%m.%Y %H:%M')
    
    info = (
        f"{status_emoji} {hbold(f'Заявка #{ticket_id}')}\n"
        f"👤 {user_name} | 📍 {workplace}\n"
        f"❓ Проблема: {problem}\n"
        f"📅 Создана: {created_time}\n"
        f"📊 Статус: {status_text}"
    )
    
    if accepted_at:
        accepted_time = datetime.strptime(accepted_at, '%Y-%m-%d %H:%M:%S').strftime('%d.%m.%Y %H:%M')
        info += f"\n✅ Принята: {accepted_time}"
    
    if resolved_at:
        resolved_time = datetime.strptime(resolved_at, '%Y-%m-%d %H:%M:%S').strftime('%d.%m.%Y %H:%M')
        info += f"\n🎉 Решена: {resolved_time}"
    
    if admin_notes:
        info += f"\n📝 Заметки: {admin_notes}"
    
    return info

# Фоновые задачи
async def periodic_cleanup():
    """Периодическая очистка БД"""
    while True:
        await asyncio.sleep(CLEANUP_INTERVAL_HOURS * 3600)
        deleted = Database.cleanup_old_tickets()
        if deleted > 0:
            try:
                await bot.send_message(
                    ADMIN_ID,
                    f"🧹 Автоматическая очистка завершена\n"
                    f"Удалено старых заявок: {deleted}"
                )
            except:
                pass

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
        
        if user_id == ADMIN_ID:
            await show_admin_panel(message, state)
        else:
            await show_main_menu(message, state, user)
    else:
        await start_registration(message, state)

@dp.message(Command("admin"))
async def cmd_admin(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        await message.answer("⛔ У вас нет прав для этой команды")
        return
    
    await state.clear()
    await show_admin_panel(message, state)

@dp.message(Command("cancel"))
async def cmd_cancel(message: types.Message, state: FSMContext):
    await state.clear()
    
    if message.from_user.id == ADMIN_ID:
        await show_admin_panel(message, state)
    else:
        user = Database.get_user(message.from_user.id)
        if user:
            await state.update_data(name=user[0], workplace=user[1])
            await show_main_menu(message, state, user)
        else:
            await message.answer(
                "❌ Действие отменено.\n"
                "Чтобы начать заново, нажмите /start",
                reply_markup=ReplyKeyboardRemove()
            )

# ===== ОБРАБОТЧИКИ РЕГИСТРАЦИИ (БЕЗ ПЕРЕСПРОСОВ) =====

# 1. ПОЛЬЗОВАТЕЛЬ ВВОДИТ ИМЯ
@dp.message(Form.name)
async def process_name(message: types.Message, state: FSMContext):
    name = message.text.strip()
    if len(name) < 2 or len(name) > 50:
        await message.answer("❌ Имя должно быть от 2 до 50 символов. Попробуйте еще раз:")
        return
    
    # СРАЗУ сохраняем имя и переходим к выбору места
    await state.update_data(name=name)
    await state.set_state(Form.workplace)
    
    await message.answer(
        f"✅ Приятно познакомиться, {hbold(name)}!\n\n"
        f"📍 Теперь выберите ваше рабочее место:",
        reply_markup=get_workplace_keyboard(),
        parse_mode="HTML"
    )

# 2. ПОЛЬЗОВАТЕЛЬ ВЫБИРАЕТ МЕСТО
@dp.message(Form.workplace)
async def process_workplace(message: types.Message, state: FSMContext):
    workplace = message.text
    valid_places = ["Офис1", "Офис2", "Ресепшен", "Менеджеры", "Касса", 
                    "РОП,РКС,Приемка", "Логистика", "Салон б/у", "Сервис", "Склад"]
    
    if workplace not in valid_places:
        await message.answer(
            "❌ Пожалуйста, выберите место из списка:",
            reply_markup=get_workplace_keyboard()
        )
        return
    
    # Получаем имя из состояния
    data = await state.get_data()
    name = data.get('name')
    
    # СРАЗУ сохраняем в базу (БЕЗ ПОДТВЕРЖДЕНИЯ)
    Database.save_user(message.from_user.id, name, workplace)
    
    # Поздравляем с успешной регистрацией
    await message.answer(
        f"✅ {hbold('Регистрация завершена!')}\n\n"
        f"👤 Имя: {hbold(name)}\n"
        f"📍 Место: {hbold(workplace)}\n\n"
        f"Теперь вы можете создавать заявки!",
        reply_markup=ReplyKeyboardRemove(),
        parse_mode="HTML"
    )
    
    # Сразу показываем главное меню
    if message.from_user.id == ADMIN_ID:
        await show_admin_panel(message, state)
    else:
        await show_main_menu(message, state, (name, workplace, 0))

# ===== ОБРАБОТЧИКИ ГЛАВНОГО МЕНЮ =====
@dp.message(Form.edit_choice)
async def process_main_menu(message: types.Message, state: FSMContext):
    data = await state.get_data()
    
    if message.text == "📝 Новая заявка":
        await state.set_state(Form.problem)
        await message.answer(
            f"👤 {hbold(data['name'])} | 📍 {hbold(data['workplace'])}\n\n"
            f"❓ Выберите проблему:",
            reply_markup=get_problem_keyboard(),
            parse_mode="HTML"
        )
    
    elif message.text == "📋 Мои заявки":
        tickets = Database.get_user_tickets(message.from_user.id)
        
        if not tickets:
            await message.answer(
                "📭 У вас пока нет заявок.\n"
                "Создайте новую заявку через меню.",
                reply_markup=get_main_menu_keyboard()
            )
            return
        
        text = f"{hbold('📋 Ваши последние заявки:')}\n\n"
        
        for ticket in tickets:
            ticket_id, problem, status, created_at, resolved_at = ticket
            status_emoji = get_status_emoji(status)
            status_text = {
                'new': 'Новая',
                'accepted': 'В работе',
                'resolved': 'Решена'
            }.get(status, status)
            
            created_time = datetime.strptime(created_at, '%Y-%m-%d %H:%M:%S').strftime('%d.%m.%Y %H:%M')
            
            text += f"{status_emoji} {hbold(f'#{ticket_id}')} | {problem}\n"
            text += f"   Статус: {status_text}\n"
            text += f"   Создана: {created_time}\n\n"
        
        await message.answer(text, parse_mode="HTML", reply_markup=get_main_menu_keyboard())
    
    elif message.text == "⚙️ Изменить профиль":
        await state.set_state(Form.edit_profile)
        await message.answer(
            f"✏️ Редактирование профиля\n\n"
            f"Текущее имя: {hbold(data['name'])}\n"
            f"Текущее место: {hbold(data['workplace'])}\n\n"
            f"Что хотите изменить?",
            reply_markup=get_edit_profile_keyboard(),
            parse_mode="HTML"
        )
    else:
        await message.answer(
            "Пожалуйста, выберите действие из меню:",
            reply_markup=get_main_menu_keyboard()
        )

# ===== ОБРАБОТЧИКИ РЕДАКТИРОВАНИЯ ПРОФИЛЯ =====
@dp.message(Form.edit_profile)
async def process_edit_profile(message: types.Message, state: FSMContext):
    if message.text == "✏️ Изменить имя":
        await state.set_state(Form.edit_name)
        await message.answer(
            "✏️ Введите новое имя:",
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
        await message.answer("❌ Имя должно быть от 2 до 50 символов. Попробуйте еще раз:")
        return
    
    data = await state.get_data()
    await state.update_data(name=new_name)
    Database.save_user(message.from_user.id, new_name, data['workplace'])
    
    await message.answer(f"✅ Имя изменено на {hbold(new_name)}", parse_mode="HTML")
    await show_main_menu(message, state)

@dp.message(Form.edit_workplace)
async def process_edit_workplace(message: types.Message, state: FSMContext):
    new_workplace = message.text
    valid_places = ["Офис1", "Офис2", "Ресепшен", "Менеджеры", "Касса", 
                    "РОП,РКС,Приемка", "Логистика", "Салон б/у", "Сервис", "Склад"]
    
    if new_workplace not in valid_places:
        await message.answer(
            "❌ Пожалуйста, выберите место из списка:",
            reply_markup=get_workplace_keyboard()
        )
        return
    
    data = await state.get_data()
    await state.update_data(workplace=new_workplace)
    Database.save_user(message.from_user.id, data['name'], new_workplace)
    
    await message.answer(f"✅ Место изменено на {hbold(new_workplace)}", parse_mode="HTML")
    await show_main_menu(message, state)

# ===== ОБРАБОТЧИК СОЗДАНИЯ ЗАЯВКИ =====
@dp.message(Form.problem)
async def process_problem(message: types.Message, state: FSMContext):
    problem = message.text
    valid_problems = ["1С", "Принтер", "Сильвер", "ВПН", "Проблемы с ПК", 
                      "Картридж", "Камеры", "ПАМАГИТИ"]
    
    if problem not in valid_problems:
        await message.answer(
            "❌ Пожалуйста, выберите проблему из списка:",
            reply_markup=get_problem_keyboard()
        )
        return
    
    data = await state.get_data()
    Database.update_last_active(message.from_user.id)
    
    # Создаём заявку
    ticket_id = Database.create_ticket(
        message.from_user.id,
        data['name'],
        data['workplace'],
        problem
    )
    
    # Уведомление админу
    try:
        admin_message = (
            f"🚨 {hbold('НОВАЯ ЗАЯВКА')} #{ticket_id}\n\n"
            f"👤 Имя: {data['name']}\n"
            f"📍 Место: {data['workplace']}\n"
            f"❓ Проблема: {problem}\n"
            f"🆔 ID: {message.from_user.id}"
        )
        
        await bot.send_message(
            ADMIN_ID,
            admin_message,
            reply_markup=get_ticket_action_keyboard(ticket_id),
            parse_mode="HTML"
        )
        
        # Подтверждение пользователю
        await message.answer(
            f"✅ {hbold('Заявка создана!')}\n\n"
            f"🎫 Номер заявки: {hbold(f'#{ticket_id}')}\n"
            f"❓ Проблема: {problem}\n\n"
            f"👨‍💻 Сисадмин получил уведомление.",
            parse_mode="HTML",
            reply_markup=ReplyKeyboardRemove()
        )
        
        # Возврат в меню
        await asyncio.sleep(1)
        await show_main_menu(message, state)
        
    except Exception as e:
        logger.error(f"❌ Ошибка отправки админу: {e}")
        await message.answer(
            "⚠️ Не удалось отправить заявку. Попробуйте позже.",
            reply_markup=ReplyKeyboardRemove()
        )
        await show_main_menu(message, state)

# ===== ОБРАБОТЧИКИ АДМИН-ПАНЕЛИ =====
@dp.message(AdminStates.choosing_action)
async def admin_actions(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    
    if message.text == "👑 Админ-панель":
        await show_admin_panel(message, state)
    
    elif message.text == "📋 Активные заявки":
        tickets = Database.get_active_tickets()
        
        if not tickets:
            await message.answer(
                "📭 Нет активных заявок",
                reply_markup=get_admin_main_keyboard()
            )
            return
        
        for ticket in tickets:
            ticket_id, user_id, user_name, workplace, problem, created_at = ticket
            created_time = datetime.strptime(created_at, '%Y-%m-%d %H:%M:%S').strftime('%d.%m.%Y %H:%M')
            
            full_ticket = Database.get_ticket(ticket_id)
            status_emoji = get_status_emoji(full_ticket[5])
            
            text = (
                f"{status_emoji} {hbold(f'Заявка #{ticket_id}')}\n"
                f"👤 {user_name}\n"
                f"📍 {workplace}\n"
                f"❓ {problem}\n"
                f"📅 {created_time}"
            )
            
            await message.answer(
                text,
                reply_markup=get_ticket_action_keyboard(ticket_id),
                parse_mode="HTML"
            )
            await asyncio.sleep(0.1)
        
        await message.answer(
            f"✅ Показано {len(tickets)} активных заявок",
            reply_markup=get_admin_main_keyboard()
        )
    
    elif message.text == "📊 Статистика":
        stats = Database.get_stats()
        db_size = os.path.getsize(DB_PATH) / 1024 if os.path.exists(DB_PATH) else 0
        
        text = (
            f"{hbold('📊 СТАТИСТИКА')}\n\n"
            f"👥 Пользователи: {stats['total_users']}\n"
            f"✅ Активных: {stats['active_users']}\n"
            f"🚫 Заблокировали: {stats['blocked_users']}\n\n"
            f"🎫 Заявки:\n"
            f"• 🆕 Новых: {stats['new_tickets']}\n"
            f"• 🔄 В работе: {stats['active_tickets']}\n"
            f"• ✅ Решено: {stats['resolved_tickets']}\n\n"
            f"💾 БД: {db_size:.2f} KB"
        )
        await message.answer(text, parse_mode="HTML", reply_markup=get_admin_main_keyboard())
    
    elif message.text == "👥 Сотрудники":
        users = Database.get_all_users(include_blocked=True)
        
        if not users:
            await message.answer("📭 Нет сотрудников", reply_markup=get_admin_main_keyboard())
            return
        
        text = f"{hbold('👥 СОТРУДНИКИ')}\n\n"
        for user_id, name in users:
            user_data = Database.get_user(user_id)
            status = "🚫" if user_data and user_data[2] else "✅"
            text += f"{status} {name} (ID: {user_id})\n"
        
        await message.answer(text, parse_mode="HTML", reply_markup=get_admin_main_keyboard())
    
    elif message.text == "🧹 Очистить старые заявки":
        deleted = Database.cleanup_old_tickets()
        await message.answer(
            f"🧹 Удалено старых заявок: {deleted}",
            reply_markup=get_admin_main_keyboard()
        )
    
    else:
        await message.answer(
            "Выберите действие из меню:",
            reply_markup=get_admin_main_keyboard()
        )

# ===== ОБРАБОТЧИКИ ИНЛАЙН КНОПОК =====
@dp.callback_query()
async def process_callback(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("⛔ У вас нет прав")
        return
    
    action, ticket_id = callback.data.split('_')
    ticket_id = int(ticket_id)
    
    ticket = Database.get_ticket(ticket_id)
    if not ticket:
        await callback.message.edit_text("❌ Заявка не найдена")
        await callback.answer()
        return
    
    if action == "accept":
        if Database.accept_ticket(ticket_id):
            # Уведомление пользователю
            try:
                await bot.send_message(
                    ticket[1],
                    f"✅ {hbold('Заявка принята!')}\n\n"
                    f"🎫 #{ticket_id}\n"
                    f"❓ {ticket[4]}\n\n"
                    f"👨‍💻 Сисадмин направляется!",
                    parse_mode="HTML"
                )
            except:
                pass
            
            await callback.message.edit_text(
                f"✅ Заявка #{ticket_id} ПРИНЯТА!",
                reply_markup=None
            )
            await callback.answer("✅ Принято!")
    
    elif action == "resolve":
        if Database.resolve_ticket(ticket_id):
            # Уведомление пользователю
            try:
                await bot.send_message(
                    ticket[1],
                    f"✅ {hbold('Заявка решена!')}\n\n"
                    f"🎫 #{ticket_id}\n"
                    f"❓ {ticket[4]}\n\n"
                    f"Спасибо за обращение!",
                    parse_mode="HTML"
                )
            except:
                pass
            
            await callback.message.edit_text(
                f"✅ Заявка #{ticket_id} РЕШЕНА!",
                reply_markup=None
            )
            await callback.answer("✅ Решено!")
    
    elif action == "refresh":
        fresh_ticket = Database.get_ticket(ticket_id)
        if fresh_ticket:
            await callback.message.edit_text(
                format_ticket_info(fresh_ticket),
                reply_markup=get_ticket_action_keyboard(ticket_id) if fresh_ticket[5] in ['new', 'accepted'] else None,
                parse_mode="HTML"
            )
            await callback.answer("🔄 Обновлено")

# ===== УНИВЕРСАЛЬНЫЙ ОБРАБОТЧИК =====
@dp.message()
async def handle_unknown(message: types.Message, state: FSMContext):
    """Обработчик для любых других сообщений"""
    user_id = message.from_user.id
    current_state = await state.get_state()
    
    # Проверяем пользователя
    user = Database.get_user(user_id)
    
    if not user:
        await start_registration(message, state)
        return
    
    # Если нет состояния - показываем меню
    if current_state is None:
        if user_id == ADMIN_ID:
            await show_admin_panel(message, state)
        else:
            await show_main_menu(message, state, user)
        return
    
    # Если состояние есть - напоминаем
    state_hints = {
        Form.name: "✏️ Введите ваше имя",
        Form.workplace: "📍 Выберите рабочее место из списка",
        Form.problem: "❓ Выберите проблему из списка",
        Form.edit_choice: "📋 Выберите действие из меню",
        Form.edit_profile: "⚙️ Выберите, что хотите изменить",
        Form.edit_name: "✏️ Введите новое имя",
        Form.edit_workplace: "📍 Выберите новое место из списка",
        AdminStates.choosing_action: "👑 Выберите действие из меню админа"
    }
    
    if current_state in state_hints:
        keyboard = get_appropriate_keyboard(current_state)
        await message.answer(f"⚠️ {state_hints[current_state]}", reply_markup=keyboard)

def get_appropriate_keyboard(state):
    """Возвращает клавиатуру для состояния"""
    keyboards = {
        Form.workplace: get_workplace_keyboard(),
        Form.problem: get_problem_keyboard(),
        Form.edit_choice: get_main_menu_keyboard(),
        Form.edit_profile: get_edit_profile_keyboard(),
        Form.edit_workplace: get_workplace_keyboard(),
        AdminStates.choosing_action: get_admin_main_keyboard()
    }
    return keyboards.get(state, ReplyKeyboardRemove())

# ===== ЗАПУСК БОТА =====
async def main():
    print("="*60)
    print("🚀 БОТ ДЛЯ ВЫЗОВА СИСАДМИНА")
    print("✅ Без переспросов при регистрации")
    print(f"👤 Админ ID: {ADMIN_ID}")
    print(f"📁 База данных: {DB_PATH}")
    print(f"🧹 Автоочистка: каждые {CLEANUP_INTERVAL_HOURS}ч")
    print("="*60)
    
    # Запускаем фоновую очистку
    asyncio.create_task(periodic_cleanup())
    
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("🛑 Бот остановлен")