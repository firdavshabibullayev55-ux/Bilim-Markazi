import os
import asyncio
import logging
import sqlite3
import io
import re
import urllib.parse
import json
import html
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Optional

import aiohttp
from aiohttp import web

from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import (
    Message,
    CallbackQuery,
    ReplyKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardRemove,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from aiogram.enums import ChatMemberStatus

try:
    from ddgs import DDGS
except ImportError:
    DDGS = None

try:
    from pypdf import PdfReader
except ImportError:
    PdfReader = None

try:
    from openai import AsyncOpenAI
except ImportError:
    AsyncOpenAI = None


# =========================================================
# CONFIG
# =========================================================
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_IDS = {int(x.strip()) for x in os.getenv("ADMIN_IDS", os.getenv("ADMIN_ID", "0")).split(",") if x.strip().isdigit()}
ADMIN_ID = next(iter(ADMIN_IDS), 0)

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

async def send_admins(*args, **kwargs):
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, *args, **kwargs)
        except Exception:
            pass

CHANNEL_ID_RAW = os.getenv("CHANNEL_ID", "").strip()
CHANNEL_URL = os.getenv("CHANNEL_URL", "").strip()
AI_API_KEY = os.getenv("AI_API_KEY", "").strip()
AI_BASE_URL = os.getenv("AI_BASE_URL", "https://openrouter.ai/api/v1").strip()
AI_MODEL = os.getenv("AI_MODEL", "openai/gpt-4o-mini").strip()
DB_PATH = os.getenv("DB_PATH", "education.db").strip()
MAX_PDF_MB = int(os.getenv("MAX_PDF_MB", "25") or 25)
AI_TIMEOUT = int(os.getenv("AI_TIMEOUT", "60") or 60)
AI_CONCURRENCY = int(os.getenv("AI_CONCURRENCY", "8") or 8)
SEARCH_CONCURRENCY = int(os.getenv("SEARCH_CONCURRENCY", "4") or 4)
APP_TZ = ZoneInfo(os.getenv("APP_TZ", "Asia/Tashkent"))
PORT = int(os.getenv("PORT", "8080") or 8080)

# Fan-specific learning channels. These are NOT mandatory-subscription channels.
# Set each *_ID to the Telegram channel ID (recommended for automatic announcements)
# and *_URL to its public/invite link for the learner button.
FAN_SUBJECTS = [
    ("➗ Algebra", "Algebra", "FAN_CHANNEL_ALGEBRA_ID", "FAN_CHANNEL_ALGEBRA_URL"),
    ("📖 Ona tili", "Ona tili", "FAN_CHANNEL_ONA_TILI_ID", "FAN_CHANNEL_ONA_TILI_URL"),
    ("🌍 Geografiya", "Geografiya", "FAN_CHANNEL_GEOGRAFIYA_ID", "FAN_CHANNEL_GEOGRAFIYA_URL"),
    ("🇬🇧 Ingliz tili", "Ingliz tili", "FAN_CHANNEL_INGLIZ_ID", "FAN_CHANNEL_INGLIZ_URL"),
    ("⚛️ Fizika", "Fizika", "FAN_CHANNEL_FIZIKA_ID", "FAN_CHANNEL_FIZIKA_URL"),
    ("🧪 Kimyo", "Kimyo", "FAN_CHANNEL_KIMYO_ID", "FAN_CHANNEL_KIMYO_URL"),
    ("🧬 Biologiya", "Biologiya", "FAN_CHANNEL_BIOLOGIYA_ID", "FAN_CHANNEL_BIOLOGIYA_URL"),
]

def fan_channel_id(subject):
    for _, name, id_key, _ in FAN_SUBJECTS:
        if name == subject:
            raw = os.getenv(id_key, "").strip()
            if not raw:
                return None
            try:
                return int(raw)
            except ValueError:
                return raw
    return None

def fan_channel_url(subject):
    for _, name, _, url_key in FAN_SUBJECTS:
        if name == subject:
            return os.getenv(url_key, "").strip()
    return ""

def all_fan_channel_ids():
    out = []
    for _, subject, _, _ in FAN_SUBJECTS:
        cid = fan_channel_id(subject)
        if cid:
            out.append((subject, cid))
    return out

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN .env faylida topilmadi.")
if not CHANNEL_ID_RAW:
    raise RuntimeError("CHANNEL_ID .env faylida topilmadi.")

try:
    CHANNEL_ID = int(CHANNEL_ID_RAW)
except ValueError:
    # Username ham ishlashi uchun qoldirildi, lekin -100... ID tavsiya etiladi.
    CHANNEL_ID = CHANNEL_ID_RAW

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("bilim_markazi")


# =========================================================
# DATABASE
# =========================================================
def db():
    db_dir = os.path.dirname(os.path.abspath(DB_PATH))
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=30, isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=30000")
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    return con


def init_db():
    con = db()
    con.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        first_name TEXT,
        last_name TEXT DEFAULT '',
        age INTEGER,
        created_at TEXT NOT NULL,
        last_seen TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS user_subjects (
        user_id INTEGER,
        subject TEXT,
        grade INTEGER,
        PRIMARY KEY(user_id, subject)
    );

    CREATE TABLE IF NOT EXISTS ai_chats (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        title TEXT DEFAULT 'Yangi suhbat',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS ai_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id INTEGER NOT NULL,
        role TEXT NOT NULL,
        content TEXT NOT NULL,
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS support_tickets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        message TEXT NOT NULL,
        status TEXT DEFAULT 'open',
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS quiz_questions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        question TEXT NOT NULL,
        option_a TEXT NOT NULL,
        option_b TEXT NOT NULL,
        option_c TEXT NOT NULL,
        option_d TEXT NOT NULL,
        correct TEXT NOT NULL,
        explanation TEXT DEFAULT '',
        difficulty TEXT DEFAULT 'hard'
    );

    CREATE TABLE IF NOT EXISTS competitions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        started_at TEXT,
        active INTEGER DEFAULT 0,
        finished INTEGER DEFAULT 0,
        duration_minutes INTEGER DEFAULT 10
    );

    CREATE TABLE IF NOT EXISTS competition_questions (
        competition_id INTEGER,
        question_id INTEGER,
        position INTEGER,
        PRIMARY KEY(competition_id, question_id)
    );

    CREATE TABLE IF NOT EXISTS competition_answers (
        competition_id INTEGER,
        user_id INTEGER,
        question_id INTEGER,
        answer TEXT,
        correct INTEGER DEFAULT 0,
        answered_at TEXT,
        PRIMARY KEY(competition_id, user_id, question_id)
    );

    CREATE TABLE IF NOT EXISTS book_sources (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        subject TEXT NOT NULL,
        grade INTEGER NOT NULL,
        title TEXT NOT NULL,
        url TEXT NOT NULL,
        snippet TEXT DEFAULT '',
        content_text TEXT DEFAULT '',
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS user_books (
        user_id INTEGER PRIMARY KEY,
        book_id INTEGER NOT NULL,
        lesson_index INTEGER DEFAULT 1,
        updated_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS book_topics (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        book_id INTEGER NOT NULL,
        position INTEGER NOT NULL,
        title TEXT NOT NULL,
        UNIQUE(book_id, position)
    );

    CREATE TABLE IF NOT EXISTS progress (
        user_id INTEGER NOT NULL,
        book_id INTEGER NOT NULL,
        topic_position INTEGER NOT NULL,
        status TEXT DEFAULT 'current',
        score REAL DEFAULT 0,
        attempts INTEGER DEFAULT 0,
        updated_at TEXT NOT NULL,
        PRIMARY KEY(user_id, book_id, topic_position)
    );

    CREATE TABLE IF NOT EXISTS quiz_results (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        subject TEXT DEFAULT '',
        grade INTEGER,
        score INTEGER DEFAULT 0,
        total INTEGER DEFAULT 0,
        quiz_type TEXT DEFAULT 'personal',
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS quiz_sessions (
        user_id INTEGER PRIMARY KEY,
        competition_id INTEGER NOT NULL,
        current_position INTEGER DEFAULT 1,
        score INTEGER DEFAULT 0,
        finished INTEGER DEFAULT 0,
        started_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS fan_test_participants (
        user_id INTEGER NOT NULL,
        subject TEXT NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY(user_id, subject)
    );

    CREATE TABLE IF NOT EXISTS fan_test_question_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        subject TEXT NOT NULL,
        question TEXT NOT NULL,
        question_key TEXT NOT NULL UNIQUE,
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS fan_test_questions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        subject TEXT NOT NULL,
        question TEXT NOT NULL,
        option_a TEXT NOT NULL,
        option_b TEXT NOT NULL,
        option_c TEXT NOT NULL,
        option_d TEXT NOT NULL,
        correct TEXT NOT NULL,
        explanation TEXT DEFAULT '',
        difficulty TEXT DEFAULT 'very_hard',
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS fan_test_competitions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        subject TEXT NOT NULL,
        title TEXT NOT NULL,
        started_at TEXT,
        active INTEGER DEFAULT 0,
        finished INTEGER DEFAULT 0,
        duration_minutes INTEGER DEFAULT 10,
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS fan_test_competition_questions (
        competition_id INTEGER NOT NULL,
        question_id INTEGER NOT NULL,
        position INTEGER NOT NULL,
        PRIMARY KEY(competition_id, question_id)
    );

    CREATE TABLE IF NOT EXISTS fan_test_answers (
        competition_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        question_id INTEGER NOT NULL,
        answer TEXT NOT NULL,
        correct INTEGER DEFAULT 0,
        answered_at TEXT NOT NULL,
        PRIMARY KEY(competition_id, user_id, question_id)
    );

    CREATE TABLE IF NOT EXISTS fan_test_sessions (
        user_id INTEGER PRIMARY KEY,
        competition_id INTEGER NOT NULL,
        current_position INTEGER DEFAULT 1,
        score INTEGER DEFAULT 0,
        finished INTEGER DEFAULT 0,
        started_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS app_locks (
        lock_name TEXT PRIMARY KEY,
        lock_value TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    """)

    # Safe migrations for existing databases.
    existing_comp = {r[1] for r in con.execute("PRAGMA table_info(competitions)").fetchall()}
    if "scheduled_date" not in existing_comp:
        con.execute("ALTER TABLE competitions ADD COLUMN scheduled_date TEXT")
    if "duration_minutes" not in existing_comp:
        con.execute("ALTER TABLE competitions ADD COLUMN duration_minutes INTEGER DEFAULT 10")
    con.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_competitions_scheduled_date ON competitions(scheduled_date)")

    existing = {r[1] for r in con.execute("PRAGMA table_info(users)").fetchall()}
    if "interests" not in existing:
        con.execute("ALTER TABLE users ADD COLUMN interests TEXT DEFAULT ''")
    if "goal" not in existing:
        con.execute("ALTER TABLE users ADD COLUMN goal TEXT DEFAULT ''")

    existing_ai = {r[1] for r in con.execute("PRAGMA table_info(ai_chats)").fetchall()}
    if "updated_at" not in existing_ai:
        con.execute("ALTER TABLE ai_chats ADD COLUMN updated_at TEXT")
        con.execute("UPDATE ai_chats SET updated_at=created_at WHERE updated_at IS NULL")

    con.commit()
    con.close()


# =========================================================
# BOT / DISPATCHER
# =========================================================
bot = Bot(BOT_TOKEN)
dp = Dispatcher()

# Temporary states for the current process.
# Important persistent data is stored in SQLite.
states = {}
# Quiz state is persisted in SQLite; this dict is intentionally not used for quiz progress.



# =========================================================
# KEYBOARDS
# =========================================================
def main_menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🤖 AI yordamchi"), KeyboardButton(text="📚 Fanlar")],
            [KeyboardButton(text="📺 Fan kanallari"), KeyboardButton(text="🧪 Fan bo‘yicha test")],
            [KeyboardButton(text="📝 Mashqlar"), KeyboardButton(text="🏆 Katta Quiz")],
            [KeyboardButton(text="📊 Mening natijalarim")],
            [KeyboardButton(text="🕘 AI tarixi"), KeyboardButton(text="🔎 Internetdan izlash")],
            [KeyboardButton(text="🆘 Adminga murojaat")],
        ],
        resize_keyboard=True,
        is_persistent=False,
        input_field_placeholder="Xabar yozing...",
    )


def admin_menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="⚙️ Admin panel")],
            [KeyboardButton(text="👥 O‘quvchilar"), KeyboardButton(text="📊 Statistika")],
            [KeyboardButton(text="🏆 Katta Quiz boshqaruvi")],
            [KeyboardButton(text="🧪 Fan testlari"), KeyboardButton(text="📚 Darsliklar")],
            [KeyboardButton(text="📝 Savollar")],
            [KeyboardButton(text="🆘 Murojaatlar")],
            [KeyboardButton(text="🔙 Asosiy menyu")],
        ],
        resize_keyboard=True,
        is_persistent=False,
    )


def admin_panel_inline():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🏆 Katta Quiz", callback_data="admin_quiz")],
            [InlineKeyboardButton(text="🧪 Fan testlari", callback_data="admin_fan_tests")],
            [InlineKeyboardButton(text="👥 O‘quvchilar", callback_data="admin_users"),
             InlineKeyboardButton(text="📊 Statistika", callback_data="admin_stats")],
            [InlineKeyboardButton(text="📝 Savollar", callback_data="admin_questions")],
            [InlineKeyboardButton(text="🆘 Murojaatlar", callback_data="admin_support")],
        ]
    )


def quiz_admin_inline():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="➕ Yangi musobaqa", callback_data="quiz_new")],
            [InlineKeyboardButton(text="🧠 Savollar yaratish", callback_data="quiz_seed")],
            [InlineKeyboardButton(text="🚀 BOSHLASH", callback_data="quiz_start")],
            [InlineKeyboardButton(text="📢 Kanalga e'lon", callback_data="quiz_announce")],
            [InlineKeyboardButton(text="📊 Natijalar", callback_data="quiz_results")],
        ]
    )


def quiz_generate_inline():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🤖 AI — 40 ta", callback_data="qgen:40"),
             InlineKeyboardButton(text="🤖 AI — 60 ta", callback_data="qgen:60")],
            [InlineKeyboardButton(text="🤖 AI — 80 ta", callback_data="qgen:80"),
             InlineKeyboardButton(text="🤖 AI — 100 ta", callback_data="qgen:100")],
            [InlineKeyboardButton(text="🔙 Quiz boshqaruvi", callback_data="admin_quiz")],
        ]
    )

def fan_subject_inline(prefix="fan"):
    rows = []
    for label, subject, _, _ in FAN_SUBJECTS:
        rows.append([InlineKeyboardButton(text=label, callback_data=f"{prefix}:{subject}")])
    rows.append([InlineKeyboardButton(text="🔙 Asosiy menyu", callback_data="back_main_inline")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def fan_admin_inline():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🧠 Savol yaratish", callback_data="fanqgen_menu")],
        [InlineKeyboardButton(text="🚀 Boshlash", callback_data="fantest_start_menu")],
        [InlineKeyboardButton(text="📊 Natijalar", callback_data="fantest_results_menu")],
        [InlineKeyboardButton(text="🗑 Savollarni o‘chirish", callback_data="fantest_delete_menu")],
        [InlineKeyboardButton(text="🔙 Admin panel", callback_data="admin_back")],
    ])

# =========================================================
# HELPERS
# =========================================================
def now_local():
    return datetime.now(APP_TZ)


def now_iso():
    return now_local().isoformat(timespec="seconds")


async def hide_reply_keyboard(message: Message):
    """Remove the persistent ReplyKeyboard before showing inline menus."""
    try:
        await message.answer(" ", reply_markup=ReplyKeyboardRemove())
    except Exception:
        logger.exception("Reply keyboard removal failed")


def safe_html(text: str) -> str:
    return html.escape(str(text or ""))


AI_SEMAPHORE = asyncio.Semaphore(AI_CONCURRENCY)
SEARCH_SEMAPHORE = asyncio.Semaphore(SEARCH_CONCURRENCY)


async def run_ai_call(client, messages, temperature=0.2):
    async with AI_SEMAPHORE:
        return await asyncio.wait_for(
            client.chat.completions.create(
            model=AI_MODEL,
            messages=messages,
                temperature=temperature,
            ),
            timeout=AI_TIMEOUT,
        )

async def is_subscribed(user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(CHANNEL_ID, user_id)
        return member.status in {
            ChatMemberStatus.MEMBER,
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.CREATOR,
        }
    except Exception as e:
        logger.warning("Obuna tekshiruvi xatosi: %s", e)
        return False


async def subscription_gate(message: Message) -> bool:
    if await is_subscribed(message.from_user.id):
        return True

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📢 Kanalga obuna bo‘lish", url=CHANNEL_URL)],
            [InlineKeyboardButton(text="✅ Obunani tekshirish", callback_data="check_sub")],
        ]
    )
    await message.answer(
        "🔒 Botdan foydalanish uchun avval <b>Bilim Markazi | Rasmiy</b> kanaliga obuna bo‘ling.",
        reply_markup=kb,
        parse_mode="HTML",
    )
    return False


def save_user(user):
    now = now_iso()
    con = db()
    con.execute(
        """INSERT INTO users(user_id, first_name, last_name, created_at, last_seen)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(user_id) DO UPDATE SET
             first_name=CASE WHEN users.first_name IS NULL OR users.first_name='' THEN excluded.first_name ELSE users.first_name END,
             last_name=CASE WHEN users.last_name IS NULL OR users.last_name='' THEN excluded.last_name ELSE users.last_name END,
             last_seen=excluded.last_seen""",
        (user.id, user.first_name or "", user.last_name or "", now, now),
    )
    con.commit()
    con.close()


def get_user_subjects(user_id: int):
    con = db()
    rows = con.execute(
        "SELECT subject, grade FROM user_subjects WHERE user_id=? ORDER BY subject",
        (user_id,),
    ).fetchall()
    con.close()
    return rows


def latest_competition():
    con = db()
    row = con.execute(
        "SELECT * FROM competitions ORDER BY id DESC LIMIT 1"
    ).fetchone()
    con.close()
    return row


def competition_question_ids(comp_id: int):
    con = db()
    rows = con.execute(
        """SELECT q.* FROM competition_questions cq
           JOIN quiz_questions q ON q.id=cq.question_id
           WHERE cq.competition_id=? ORDER BY cq.position""",
        (comp_id,),
    ).fetchall()
    con.close()
    return rows


# =========================================================
# REGISTRATION / SUBJECTS / TEXTBOOK SEARCH
# =========================================================
SUBJECTS = [
    ("📐 Matematika", "Matematika"),
    ("➗ Algebra", "Algebra"),
    ("📏 Geometriya", "Geometriya"),
    ("⚛️ Fizika", "Fizika"),
    ("🧪 Kimyo", "Kimyo"),
    ("🧬 Biologiya", "Biologiya"),
    ("🌍 Geografiya", "Geografiya"),
    ("🇺🇿 O‘zbekiston tarixi", "O‘zbekiston tarixi"),
    ("🌎 Jahon tarixi", "Jahon tarixi"),
    ("📖 Ona tili", "Ona tili"),
    ("📚 Adabiyot", "Adabiyot"),
    ("💻 Informatika", "Informatika"),
    ("🇬🇧 Ingliz tili", "Ingliz tili"),
    ("🇷🇺 Rus tili", "Rus tili"),
]


def profile_complete(user_id: int) -> bool:
    con = db()
    row = con.execute(
        "SELECT first_name, last_name, age FROM users WHERE user_id=?", (user_id,)
    ).fetchone()
    con.close()
    return bool(row and row["first_name"] and row["last_name"] and row["age"])


def update_profile(user_id: int, **fields):
    if not fields:
        return
    con = db()
    allowed = {"first_name", "last_name", "age", "interests", "goal"}
    fields = {k: v for k, v in fields.items() if k in allowed}
    if fields:
        set_sql = ", ".join(f"{k}=?" for k in fields)
        con.execute(f"UPDATE users SET {set_sql} WHERE user_id=?", (*fields.values(), user_id))
        con.commit()
    con.close()


@dp.message(CommandStart())
async def start(message: Message):
    save_user(message.from_user)
    states.pop(message.from_user.id, None)

    if not await subscription_gate(message):
        return

    if not profile_complete(message.from_user.id):
        states[message.from_user.id] = {"mode": "register", "step": "first_name"}
        await message.answer(
            "🎓 <b>Bilim Markazi</b>ga xush kelibsiz!\n\n"
            "Ro‘yxatdan o‘tish uchun avval <b>ismingizni</b> yozing:",
            parse_mode="HTML",
        )
        return

    # Telegram deep-links for large and subject-specific tests.
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) == 2 and parts[1].startswith("fantest_"):
        try:
            comp_id = int(parts[1].split("_", 1)[1])
            con = db(); comp = con.execute("SELECT * FROM fan_test_competitions WHERE id=? AND active=1", (comp_id,)).fetchone(); con.close()
            if comp:
                now = now_iso(); con=db(); con.execute("INSERT INTO fan_test_sessions(user_id,competition_id,current_position,score,finished,started_at,updated_at) VALUES(?,?,1,0,0,?,?) ON CONFLICT(user_id) DO UPDATE SET competition_id=excluded.competition_id,current_position=1,score=0,finished=0,started_at=excluded.started_at,updated_at=excluded.updated_at", (message.from_user.id,comp_id,now,now)); con.commit(); con.close(); await message.answer(f"🧪 <b>{safe_html(comp['subject'])} testi boshlandi!</b>", parse_mode="HTML"); await send_fan_question(message.from_user.id); return
        except Exception: logger.exception("Fan test deep-link error")

    # Telegram deep-link: /start quiz_123
    if len(parts) == 2 and parts[1].startswith("quiz_"):
        try:
            comp_id = int(parts[1].split("_", 1)[1])
            questions = competition_question_ids(comp_id)
            if questions:
                con = db()
                con.execute("""INSERT INTO quiz_sessions(user_id,competition_id,current_position,score,finished,started_at,updated_at)
                               VALUES(?,?,1,0,0,?,?)
                               ON CONFLICT(user_id) DO UPDATE SET competition_id=excluded.competition_id,current_position=1,score=0,finished=0,updated_at=excluded.updated_at""",
                            (message.from_user.id, comp_id, now_iso(), now_iso()))
                con.commit(); con.close()
                await message.answer("🏆 Musobaqa boshlandi!")
                await send_quiz_question(message.from_user.id)
                return
        except Exception:
            logger.exception("Quiz deep-link xatosi")

    if is_admin(message.from_user.id):
        await message.answer(
            "🎓 <b>Bilim Markazi</b>ga xush kelibsiz, admin!",
            reply_markup=admin_menu(), parse_mode="HTML"
        )
    else:
        await message.answer(
            "🎓 <b>Bilim Markazi</b>ga xush kelibsiz!\n\n"
            "📚 Fan → sinf → internetdan topilgan darslik → darslar ketma-ketligi.",
            reply_markup=main_menu(), parse_mode="HTML"
        )


@dp.callback_query(F.data == "check_sub")
async def check_sub(callback: CallbackQuery):
    if await is_subscribed(callback.from_user.id):
        await callback.answer("✅ Obuna tasdiqlandi")
        await callback.message.answer("Endi /start ni bosing.")
    else:
        await callback.answer("❌ Hali kanalga obuna bo‘lmagansiz.", show_alert=True)


@dp.message(F.text == "📚 Fanlar")
async def subjects(message: Message):
    if not await subscription_gate(message):
        return
    if not profile_complete(message.from_user.id):
        await message.answer("Avval /start orqali ro‘yxatdan o‘ting.")
        return
    states.pop(message.from_user.id, None)
    await hide_reply_keyboard(message)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=label, callback_data=f"subj:{value}")]
            for label, value in SUBJECTS
        ]
    )
    await message.answer("📚 <b>Fanni tanlang:</b>", reply_markup=kb, parse_mode="HTML")


@dp.callback_query(F.data.startswith("subj:"))
async def choose_subject(callback: CallbackQuery):
    await callback.answer()
    subject = callback.data.split(":", 1)[1]
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"{g}-sinf", callback_data=f"grade:{subject}:{g}")]
            for g in range(5, 12)
        ] + [[InlineKeyboardButton(text="🔙 Fanlar", callback_data="back_subjects")]]
    )
    await callback.message.edit_text(
        f"📚 <b>{html.escape(subject)}</b>\n\n🎓 Sinfni tanlang:",
        reply_markup=kb, parse_mode="HTML"
    )


async def _probe_book_source(url: str):
    """Return a usable direct PDF URL when possible.

    Search engines often return Wikipedia, portal pages, or HTML landing pages
    instead of the actual textbook PDF. We probe the public URL and, when the
    page contains a PDF link, follow that link. No login/paywall/DRM is bypassed.
    """
    bad_domains = (
        "wikipedia.org", "youtube.com", "youtu.be", "facebook.com",
        "instagram.com", "tiktok.com", "x.com", "twitter.com"
    )
    low = url.lower()
    if any(d in low for d in bad_domains):
        return None

    timeout = aiohttp.ClientTimeout(total=10, connect=5, sock_read=7)
    headers = {"User-Agent": "BilimMarkaziBot/1.0"}
    try:
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            async with session.get(url, allow_redirects=True, max_redirects=5) as resp:
                if resp.status != 200:
                    return None
                final_url = str(resp.url)
                ctype = (resp.headers.get("Content-Type") or "").lower()
                if "application/pdf" in ctype or final_url.lower().split("?")[0].endswith(".pdf"):
                    return final_url
                if "text/html" not in ctype and "text/plain" not in ctype:
                    return None
                data = await resp.read()
                if len(data) > 2 * 1024 * 1024:
                    data = data[:2 * 1024 * 1024]
                page = data.decode("utf-8", errors="ignore")
                hrefs = re.findall(r"href\s*=\s*[\"']([^\"']+\.pdf(?:\?[^\"']*)?)[\"']", page, flags=re.I)
                if not hrefs:
                    hrefs = re.findall(r"(?:https?:)?//[^\s\"'<>]+\.pdf(?:\?[^\s\"'<>]*)?", page, flags=re.I)
                for href in hrefs:
                    candidate = urllib.parse.urljoin(final_url, html.unescape(href))
                    if not any(d in candidate.lower() for d in bad_domains):
                        return candidate
    except Exception:
        return None
    return None


async def search_textbooks(subject: str, grade: int):
    if DDGS is None:
        return []

    if grade:
        queries = [
            f'"{grade}-sinf" "{subject}" darslik filetype:pdf O\'zbekiston',
            f'"{grade}-sinf" "{subject}" elektron darslik PDF',
            f'"{grade} sinf" "{subject}" darslik pdf uzbek',
            f'"{subject}" "{grade}-sinf" darslik PDF site:edu.uz',
            f'"{subject}" "{grade}-sinf" darslik PDF site:exujjat.uznpu.uz',
        ]
    else:
        queries = [subject]

    def do_search():
        results = []
        seen = set()
        with DDGS() as ddgs:
            for query in queries:
                try:
                    for item in ddgs.text(query, max_results=10):
                        url = (item.get("href") or item.get("url") or "").strip()
                        title = (item.get("title") or "").strip()
                        snippet = (item.get("body") or item.get("snippet") or "").strip()
                        low = url.lower()
                        if not url or any(d in low for d in (
                            "wikipedia.org", "youtube.com", "youtu.be", "facebook.com",
                            "instagram.com", "tiktok.com", "x.com", "twitter.com"
                        )):
                            continue
                        key = url.lower().rstrip("/")
                        if key not in seen:
                            seen.add(key)
                            results.append((title, url, snippet))
                except Exception:
                    logger.exception("Darslik qidirish xatosi: %s", query)
        return results

    async with SEARCH_SEMAPHORE:
        raw = await asyncio.to_thread(do_search)

    async def validate(item):
        title, url, snippet = item
        resolved = await _probe_book_source(url)
        if not resolved:
            return None
        return (title, resolved, snippet)

    validated = []
    for i in range(0, len(raw), 4):
        batch = raw[i:i + 4]
        checked = await asyncio.gather(*(validate(x) for x in batch), return_exceptions=True)
        for x in checked:
            if isinstance(x, tuple):
                validated.append(x)
            if len(validated) >= 8:
                break
        if len(validated) >= 8:
            break

    final = []
    seen = set()
    for title, url, snippet in validated:
        key = url.lower().rstrip("/")
        if key not in seen:
            seen.add(key)
            final.append((title, url, snippet))
    return final[:8]


async def save_book_results(subject: str, grade: int, results):
    """Save search results without deleting a book currently used by a student."""
    con = db()
    ids = []
    now = now_iso()
    try:
        for title, url, snippet in results:
            existing = con.execute(
                "SELECT id FROM book_sources WHERE subject=? AND grade=? AND url=? ORDER BY id DESC LIMIT 1",
                (subject, grade, url),
            ).fetchone()
            if existing:
                ids.append(existing["id"])
                continue
            cur = con.execute(
                "INSERT INTO book_sources(subject,grade,title,url,snippet,created_at) VALUES(?,?,?,?,?,?)",
                (subject, grade, title[:300] or url[:300], url, snippet[:1000], now),
            )
            ids.append(cur.lastrowid)
        con.commit()
        return ids
    finally:
        con.close()


@dp.callback_query(F.data.startswith("grade:"))
async def choose_grade(callback: CallbackQuery):
    await callback.answer("🔎 Darsliklar qidirilmoqda...")
    _, subject, grade_s = callback.data.split(":", 2)
    grade = int(grade_s)

    con = db()
    try:
        # DELETE + INSERT avoids SQLite ON CONFLICT syntax problems in old
        # manually-edited copies of this project.
        con.execute("BEGIN IMMEDIATE")
        con.execute("DELETE FROM user_subjects WHERE user_id=? AND subject=?", (callback.from_user.id, subject))
        con.execute(
            "INSERT INTO user_subjects (user_id, subject, grade) VALUES (?, ?, ?)",
            (callback.from_user.id, subject, grade),
        )
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()

    await callback.message.edit_text(
        f"✅ <b>{html.escape(subject)} — {grade}-sinf</b> tanlandi.\n\n"
        "🔎 <b>Internetdan izlash</b> avtomatik ishga tushdi...",
        parse_mode="HTML",
    )

    results = await search_textbooks(subject, grade)
    if not results:
        await callback.message.answer(
            "⚠️ Darslik topilmadi yoki qidiruv xizmati ishlamayapti.\n\n"
            "Keyinroq qayta urinib ko‘ring yoki admin darslikni qo‘lda qo‘shishi mumkin."
        )
        return

    ids = await save_book_results(subject, grade, results)
    buttons = []
    for i, (book_id, item) in enumerate(zip(ids, results), 1):
        title = item[0] or item[1]
        buttons.append([
            InlineKeyboardButton(text=f"{i}. {title[:45]}", callback_data=f"book:{book_id}"),
            InlineKeyboardButton(text="📄 Ochish", url=item[1]),
        ])
    buttons.append([InlineKeyboardButton(text="🔙 Fanlar", callback_data="back_subjects")])

    await callback.message.answer(
        f"📚 <b>{html.escape(subject)} — {grade}-sinf</b>\n\n"
        "Internetdan topilgan darslik/manbalar:\n"
        "Quyidagilardan keraklisini tanlang. Tanlangan manba asosida dars boshlanadi.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        parse_mode="HTML",
    )


def get_book(book_id: int):
    con = db()
    row = con.execute("SELECT * FROM book_sources WHERE id=?", (book_id,)).fetchone()
    con.close()
    return row


def set_user_book(user_id: int, book_id: int, lesson_index: int = 1):
    con = db()
    con.execute(
        """INSERT INTO user_books(user_id,book_id,lesson_index,updated_at) VALUES(?,?,?,?)
           ON CONFLICT(user_id) DO UPDATE SET book_id=excluded.book_id,
           lesson_index=excluded.lesson_index, updated_at=excluded.updated_at""",
        (user_id, book_id, lesson_index, now_iso()),
    )
    con.commit()
    con.close()


async def fetch_book_content(url: str):
    """Fetch public textbook content.

    Direct PDFs are extracted with pypdf. If the URL is an HTML landing page,
    a direct PDF link is discovered first. We intentionally do not bypass
    authentication, paywalls, DRM, or access controls.
    """
    timeout = aiohttp.ClientTimeout(total=35, connect=8, sock_read=25)
    headers = {"User-Agent": "BilimMarkaziBot/1.0"}

    async def read_response(session, target):
        async with session.get(target, allow_redirects=True, max_redirects=5) as resp:
            if resp.status != 200:
                return ""
            data = await resp.read()
            if len(data) > MAX_PDF_MB * 1024 * 1024:
                return ""
            ctype = (resp.headers.get("Content-Type") or "").lower()
            final_url = str(resp.url)
            is_pdf = "application/pdf" in ctype or final_url.lower().split("?")[0].endswith(".pdf")
            if is_pdf:
                if PdfReader is None:
                    return ""
                try:
                    reader = PdfReader(io.BytesIO(data))
                    parts = []
                    for page in reader.pages:
                        try:
                            text = page.extract_text() or ""
                            if text.strip():
                                parts.append(text)
                        except Exception:
                            pass
                    return "\n".join(parts).strip()[:250000]
                except Exception:
                    logger.exception("PDF parsing failed: %s", target)
                    return ""

            if "text/html" in ctype or not ctype:
                page = data.decode("utf-8", errors="ignore")
                hrefs = re.findall(r"href\s*=\s*[\"']([^\"']+\.pdf(?:\?[^\"']*)?)[\"']", page, flags=re.I)
                if not hrefs:
                    hrefs = re.findall(r"(?:https?:)?//[^\s\"'<>]+\.pdf(?:\?[^\s\"'<>]*)?", page, flags=re.I)
                for href in hrefs[:5]:
                    pdf_url = urllib.parse.urljoin(final_url, html.unescape(href))
                    try:
                        result = await read_response(session, pdf_url)
                        if result:
                            return result
                    except Exception:
                        continue
                text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", page, flags=re.I | re.S)
                text = re.sub(r"<[^>]+>", " ", text)
                return re.sub(r"\s+", " ", html.unescape(text)).strip()[:150000]
            return ""

    try:
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            return await read_response(session, url)
    except Exception:
        logger.exception("Darslik manbasini olish xatosi")
        return ""


async def generate_book_topics(book_id: int, book_text: str):
    client = ai_client()
    if client is None or not book_text.strip():
        return []
    prompt = (
        "Quyidagi maktab darsligi matnidan uning mavzular ketma-ketligini aniqlang. "
        "Faqat JSON array qaytaring, masalan [{\"position\":1,\"title\":\"...\"}]. "
        "Mavzularni o'zingizcha almashtirmang; matndagi bob/mavzu sarlavhalariga imkon qadar yaqin yozing. "
        "Ko'pi bilan 30 ta mavzu.\n\nDarslik matni:\n" + book_text[:40000]
    )
    try:
        response = await run_ai_call(client, [
                {"role": "system", "content": "Siz darslik tuzilmasini ajratuvchi yordamchisiz."},
                {"role": "user", "content": prompt},
            ], temperature=0)
        raw = response.choices[0].message.content or "[]"
        raw = raw.replace("```json", "").replace("```", "").strip()
        data = json.loads(raw)
        topics = [(int(x["position"]), str(x["title"]).strip()) for x in data if x.get("title")]
        return topics[:80]
    except Exception:
        logger.exception("Darslik mavzularini ajratish xatosi")
        return []


@dp.callback_query(F.data.startswith("book:"))
async def choose_book(callback: CallbackQuery):
    book_id = int(callback.data.split(":", 1)[1])
    book = get_book(book_id)
    if not book:
        return await callback.answer("Darslik topilmadi.", show_alert=True)

    await callback.answer("📥 Darslik manbasi tayyorlanmoqda...")
    set_user_book(callback.from_user.id, book_id, 1)

    content = await fetch_book_content(book["url"])
    con = db()
    con.execute("UPDATE book_sources SET content_text=? WHERE id=?", (content, book_id))
    con.commit()
    con.close()

    if content:
        topics = await generate_book_topics(book_id, content)
        con = db()
        con.execute("DELETE FROM book_topics WHERE book_id=?", (book_id,))
        for seq, (_, title) in enumerate(topics, 1):
            con.execute("INSERT OR IGNORE INTO book_topics(book_id,position,title) VALUES(?,?,?)", (book_id,seq,title))
        con.commit()
        con.close()

        await callback.message.edit_text(
            f"📖 <b>{html.escape(book['title'])}</b>\n\n"
            "✅ Darslik manbasi olindi.\n"
            "📚 Mavzular ketma-ketligi tayyorlanmoqda.\n\n"
            "🎓 1-darsni boshlaymiz.", parse_mode="HTML"
        )
        await send_current_lesson(callback.from_user.id)
    else:
        await callback.message.edit_text(
            f"📖 <b>{html.escape(book['title'])}</b>\n\n"
            "⚠️ Ushbu manbaning matnini bot o‘qiy olmadi.\n"
            "🔗 Manbani ochib ko‘rishingiz mumkin:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📄 Darslikni ochish", url=book["url"])],
                [InlineKeyboardButton(text="🔎 Boshqa darslik izlash", callback_data=f"searchbook:{book['subject']}:{book['grade']}")],
                [InlineKeyboardButton(text="📚 Fanlar", callback_data="back_subjects")],
            ]), parse_mode="HTML"
        )


@dp.callback_query(F.data.startswith("searchbook:"))
async def searchbook_again(callback: CallbackQuery):
    _, subject, grade_s = callback.data.split(":", 2)
    await callback.answer("🔎 Qayta qidirilmoqda...")
    results = await search_textbooks(subject, int(grade_s))
    ids = await save_book_results(subject, int(grade_s), results)
    buttons = []
    for i, (bid, x) in enumerate(zip(ids, results), 1):
        buttons.append([
            InlineKeyboardButton(text=f"{i}. {(x[0] or x[1])[:45]}", callback_data=f"book:{bid}"),
            InlineKeyboardButton(text="📄 Ochish", url=x[1]),
        ])
    buttons.append([InlineKeyboardButton(text="🔙 Fanlar", callback_data="back_subjects")])
    await callback.message.answer(
        "📚 Topilgan manbalar:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons) if results else None
    )


async def get_current_book(user_id: int):
    con = db()
    row = con.execute(
        """SELECT ub.*, b.* FROM user_books ub JOIN book_sources b ON b.id=ub.book_id
           WHERE ub.user_id=?""", (user_id,)
    ).fetchone()
    con.close()
    return row


async def send_current_lesson(user_id: int):
    book = await get_current_book(user_id)
    if not book:
        return await bot.send_message(user_id, "Avval 📚 Fanlar orqali fan va sinfni tanlang.")

    con = db()
    topic = con.execute(
        "SELECT * FROM book_topics WHERE book_id=? AND position=?",
        (book["book_id"], book["lesson_index"]),
    ).fetchone()
    max_topic = con.execute(
        "SELECT COUNT(*) c FROM book_topics WHERE book_id=?", (book["book_id"],)
    ).fetchone()["c"]
    con.close()

    title = topic["title"] if topic else f"{book['lesson_index']}-dars"
    source_text = book["content_text"] or book["snippet"]
    con = db()
    con.execute("""INSERT INTO progress(user_id,book_id,topic_position,status,updated_at)
                   VALUES(?,?,?,'current',?)
                   ON CONFLICT(user_id,book_id,topic_position) DO UPDATE SET status='current',updated_at=excluded.updated_at""",
                (user_id, book["book_id"], book["lesson_index"], now_iso()))
    con.commit(); con.close()
    client = ai_client()
    if client is None:
        return await bot.send_message(user_id, "⚠️ AI_API_KEY sozlanmagan.")

    prompt = (
        f"Siz Bilim Markazi o'qituvchisiz. {book['subject']} fanidan {book['grade']}-sinf darsligi asosida dars o'ting.\n"
        f"Hozirgi mavzu: {title}\n"
        "Darsni o'zbek tilida sodda va ilmiy aniq tushuntiring. 1) maqsad 2) tushuntirish 3) misol 4) 3 ta mashq. "
        "Darslikdagi ma'lumotga tayaning; matnda yo'q faktni darslikdan olingandek ko'rsatmang.\n\n"
        f"Darslik manbasi matni:\n{source_text[:50000]}"
    )
    try:
        response = await run_ai_call(client, [
            {"role":"system","content":"Aniq, sabrli maktab o'qituvchisi."},
            {"role":"user","content":prompt},
        ], temperature=0.2)
        lesson = response.choices[0].message.content or "Dars tayyorlanmadi."
    except Exception:
        logger.exception("Lesson generation error")
        lesson = "❌ Darsni tayyorlashda vaqtinchalik xatolik yuz berdi. Birozdan so‘ng qayta urinib ko‘ring."

    next_pos = book["lesson_index"] + 1
    buttons = []
    if max_topic and next_pos <= max_topic:
        buttons.append([InlineKeyboardButton(text="➡️ Keyingi dars", callback_data="next_lesson")])
    buttons.append([InlineKeyboardButton(text="📚 Fanlar", callback_data="back_subjects")])
    await bot.send_message(
        user_id,
        f"📖 <b>{html.escape(book['subject'])} — {book['grade']}-sinf</b>\n"
        f"🧩 <b>{html.escape(title)}</b>\n\n{lesson}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode="HTML"
    )


@dp.callback_query(F.data == "next_lesson")
async def next_lesson(callback: CallbackQuery):
    book = await get_current_book(callback.from_user.id)
    if not book:
        return await callback.answer("Darslik tanlanmagan.", show_alert=True)
    con = db()
    count = con.execute("SELECT COUNT(*) c FROM book_topics WHERE book_id=?", (book["book_id"],)).fetchone()["c"]
    if book["lesson_index"] >= count:
        con.close()
        return await callback.answer("🎉 Darslikdagi barcha mavzular tugadi!", show_alert=True)
    con.execute("UPDATE progress SET status='completed', updated_at=? WHERE user_id=? AND book_id=? AND topic_position=?",
                (now_iso(), callback.from_user.id, book["book_id"], book["lesson_index"]))
    con.execute(
        "UPDATE user_books SET lesson_index=lesson_index+1, updated_at=? WHERE user_id=?",
        (now_iso(), callback.from_user.id),
    )
    con.commit(); con.close()
    await callback.answer("➡️ Keyingi dars")
    await send_current_lesson(callback.from_user.id)


@dp.callback_query(F.data == "back_subjects")
async def back_subjects(callback: CallbackQuery):
    await callback.answer()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=label, callback_data=f"subj:{value}")] for label, value in SUBJECTS
    ])
    states.pop(callback.from_user.id, None)
    try:
        await callback.message.edit_text("📚 <b>Fanni tanlang:</b>", reply_markup=kb, parse_mode="HTML")
    except Exception:
        await callback.message.answer("📚 <b>Fanni tanlang:</b>", reply_markup=kb, parse_mode="HTML")


# =========================================================
# REGISTRATION INPUTS
# =========================================================
async def handle_registration_input(message: Message) -> bool:
    user_id = message.from_user.id
    st = states.get(user_id, {})
    if st.get("mode") != "register":
        return False
    text = (message.text or "").strip()
    if not text:
        await message.answer("Matn ko‘rinishida yozing.")
        return True
    step = st.get("step")
    if step == "first_name":
        update_profile(user_id, first_name=text[:80])
        st["step"] = "last_name"
        await message.answer("👤 Endi <b>familiyangizni</b> yozing:", parse_mode="HTML")
    elif step == "last_name":
        update_profile(user_id, last_name=text[:80])
        st["step"] = "age"
        await message.answer("🎂 Yoshingizni faqat son bilan yozing. Masalan: <b>15</b>", parse_mode="HTML")
    elif step == "age":
        try:
            age = int(text)
        except ValueError:
            await message.answer("❗ Yoshni son bilan yozing. Masalan: 15")
            return True
        if not 5 <= age <= 100:
            await message.answer("❗ Yosh 5–100 oralig‘ida bo‘lsin.")
            return True
        update_profile(user_id, age=age)
        states.pop(user_id, None)
        await message.answer(
            "✅ <b>Ro‘yxatdan o‘tish tugadi!</b>\n\nEndi 📚 Fanlar bo‘limidan fan va sinfni tanlang.",
            reply_markup=admin_menu() if is_admin(user_id) else main_menu(), parse_mode="HTML"
        )
    return True


# =========================================================
# AI
# =========================================================
def ai_client():
    if not AI_API_KEY or AsyncOpenAI is None:
        return None
    return AsyncOpenAI(api_key=AI_API_KEY, base_url=AI_BASE_URL)


def get_or_create_ai_chat(user_id: int, title: str = "Yangi suhbat") -> int:
    con = db()
    try:
        now = now_iso()
        row = con.execute(
            "SELECT id FROM ai_chats WHERE user_id=? ORDER BY updated_at DESC, id DESC LIMIT 1",
            (user_id,),
        ).fetchone()
        if row:
            con.execute("UPDATE ai_chats SET updated_at=? WHERE id=?", (now, row["id"]))
            con.commit()
            return row["id"]
        cur = con.execute(
            "INSERT INTO ai_chats(user_id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (user_id, title[:60], now, now),
        )
        con.commit()
        return cur.lastrowid
    finally:
        con.close()


def get_ai_history(chat_id: int, limit: int = 12):
    con = db()
    try:
        rows = con.execute(
            "SELECT role, content FROM ai_messages WHERE chat_id=? ORDER BY id DESC LIMIT ?",
            (chat_id, limit),
        ).fetchall()
        return list(reversed(rows))
    finally:
        con.close()


def save_ai_message(chat_id: int, role: str, content: str):
    con = db()
    try:
        now = now_iso()
        con.execute(
            "INSERT INTO ai_messages(chat_id, role, content, created_at) VALUES (?, ?, ?, ?)",
            (chat_id, role, content, now),
        )
        con.execute("UPDATE ai_chats SET updated_at=? WHERE id=?", (now, chat_id))
        con.commit()
    finally:
        con.close()


async def ask_ai(user_id: int, text: str) -> str:
    client = ai_client()
    if client is None:
        return (
            "⚠️ AI hozir sozlanmagan.\n\n"
            ".env faylida AI_API_KEY ni tekshiring."
        )

    subjects = get_user_subjects(user_id)
    profile = ", ".join(f"{r['subject']} ({r['grade']}-sinf)" for r in subjects)
    if not profile:
        profile = "fan/sinf hali tanlanmagan"

    book = await get_current_book(user_id)
    book_context = ""
    if book:
        topic = book["lesson_index"]
        book_context = (
            f"\nO‘quvchining tanlangan darsligi: {book['title']} ({book['subject']}, {book['grade']}-sinf). "
            f"Hozirgi dars raqami: {topic}. Agar savol darslikka oid bo‘lsa, quyidagi manba matniga tayaning:\n"
            f"{(book['content_text'] or book['snippet'])[:25000]}"
        )

    system = (
        "Siz Bilim Markazi AI ustozisiz. O‘zbek tilida aniq, muloyim va tushunarli "
        "javob bering. O‘quvchini shunchaki javob bilan emas, tushuntirish va amaliyot "
        "bilan o‘qiting. Foydalanuvchining tanlangan fan/sinf ma’lumotlari: "
        f"{profile}. Bilmagan narsangizni to‘qib chiqarmang." + book_context
    )

    # Suhbat xotirasi: oldingi foydalanuvchi/AI xabarlarini modelga ham yuboramiz.
    chat_id = get_or_create_ai_chat(user_id, text)
    history = get_ai_history(chat_id, limit=12)
    messages = [{"role": "system", "content": system}]
    for row in history:
        role = row["role"]
        if role in ("user", "assistant") and row["content"]:
            messages.append({"role": role, "content": row["content"]})
    messages.append({"role": "user", "content": text})

    try:
        response = await run_ai_call(client, messages, temperature=0.2)
        return response.choices[0].message.content or "Javob olinmadi."
    except Exception as e:
        logger.exception("AI xatosi")
        return "❌ AI bilan bog‘lanishda vaqtinchalik xatolik yuz berdi. Birozdan so‘ng qayta urinib ko‘ring."


@dp.message(F.text == "🤖 AI yordamchi")
async def ai_mode(message: Message):
    if not await subscription_gate(message):
        return
    states[message.from_user.id] = {"mode": "ai"}
    await hide_reply_keyboard(message)
    await message.answer(
        "🤖 <b>AI yordamchi</b>\n\n"
        "Savolingizni yozing. Masala, dars, kod yoki boshqa mavzuni tushuntirib beraman.\n\n"
        "Chiqish uchun pastdagi <b>🔙 Asosiy menyu</b> tugmasini bosing yoki /start yuboring.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Asosiy menyu", callback_data="back_main_inline")]
        ]),
        parse_mode="HTML",
    )


# =========================================================
# EXERCISES / STATS / HISTORY
# =========================================================
@dp.message(F.text == "📝 Mashqlar")
async def exercises(message: Message):
    if not await subscription_gate(message):
        return
    await message.answer(
        "📝 <b>Mashqlar</b>\n\n"
        "Avval 📚 Fanlar bo‘limidan fan va sinfni tanlang. "
        "Keyin shu yo‘nalishga mos mashqlar ochiladi.",
        parse_mode="HTML",
    )


@dp.message(F.text == "📊 Mening natijalarim")
async def my_stats(message: Message):
    if not await subscription_gate(message):
        return

    con = db()
    user = con.execute(
        "SELECT * FROM users WHERE user_id=?", (message.from_user.id,)
    ).fetchone()
    rows = con.execute(
        "SELECT subject, grade FROM user_subjects WHERE user_id=?",
        (message.from_user.id,),
    ).fetchall()
    con.close()

    subjects_text = "\n".join(
        f"• {r['subject']} — {r['grade']}-sinf" for r in rows
    ) or "Hali tanlanmagan"

    await message.answer(
        f"📊 <b>Mening natijalarim</b>\n\n"
        f"👤 {user['first_name'] if user else message.from_user.first_name}\n\n"
        f"📚 Tanlangan yo‘nalishlar:\n{subjects_text}",
        parse_mode="HTML",
    )


@dp.message(F.text == "🕘 AI tarixi")
async def ai_history(message: Message):
    if not await subscription_gate(message):
        return
    con = db()
    rows = con.execute(
        "SELECT id, title, created_at FROM ai_chats WHERE user_id=? ORDER BY updated_at DESC, id DESC LIMIT 10",
        (message.from_user.id,),
    ).fetchall()
    con.close()

    if not rows:
        await message.answer("🕘 Hozircha AI tarixi bo‘sh.")
        return

    text = "🕘 <b>AI tarixi</b>\n\n"
    for r in rows:
        text += f"#{r['id']} — {r['title']}\n"
    await message.answer(text, parse_mode="HTML")


# =========================================================
# SUPPORT
# =========================================================
@dp.message(F.text == "🔎 Internetdan izlash")
async def web_search_start(message: Message):
    if not await subscription_gate(message):
        return
    states[message.from_user.id] = {"mode": "web_search"}
    await hide_reply_keyboard(message)
    await message.answer(
        "🔎 <b>Internetdan izlash</b>\n\n"
        "Qidiruv so‘rovingizni yozing. Masalan:\n"
        "<code>6-sinf matematika darsligi PDF</code>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Asosiy menyu", callback_data="back_main_inline")]
        ])
    )


@dp.message(F.text == "🆘 Adminga murojaat")
async def support_start(message: Message):
    if not await subscription_gate(message):
        return
    states[message.from_user.id] = {"mode": "support"}
    await hide_reply_keyboard(message)
    await message.answer(
        "🆘 Muammo yoki savolingizni bitta xabar qilib yozing.\n"
        "U to‘g‘ridan-to‘g‘ri adminga yuboriladi.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Asosiy menyu", callback_data="back_main_inline")]
        ])
    )


@dp.callback_query(F.data == "back_main_inline")
async def back_main_inline(callback: CallbackQuery):
    states.pop(callback.from_user.id, None)
    await callback.answer()
    try:
        await callback.message.edit_text("🏠 <b>Asosiy menyu</b>", parse_mode="HTML")
    except Exception:
        pass
    await callback.message.answer(
        "Kerakli bo‘limni tanlang:",
        reply_markup=admin_menu() if is_admin(callback.from_user.id) else main_menu(),
    )


# =========================================================
# ADMIN PANEL
# =========================================================
async def admin_only(message: Message) -> bool:
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Bu bo‘lim faqat admin uchun.")
        return False
    return True


@dp.message(F.text == "⚙️ Admin panel")
async def admin_panel(message: Message):
    if not await admin_only(message):
        return
    await hide_reply_keyboard(message)
    await message.answer(
        "⚙️ <b>ADMIN PANEL</b>\n\nKerakli bo‘limni tanlang:",
        reply_markup=admin_panel_inline(),
        parse_mode="HTML",
    )


async def render_admin_users(callback: CallbackQuery, page: int = 1):
    if not is_admin(callback.from_user.id):
        return await callback.answer("⛔ Ruxsat yo‘q", show_alert=True)
    page = max(1, page)
    per_page = 40
    con = db()
    total = con.execute("SELECT COUNT(*) c FROM users WHERE first_name IS NOT NULL AND first_name != ''").fetchone()["c"]
    rows = con.execute("""SELECT first_name,last_name,age FROM users
                          WHERE first_name IS NOT NULL AND first_name != ''
                          ORDER BY first_name COLLATE NOCASE, last_name COLLATE NOCASE
                          LIMIT ? OFFSET ?""", (per_page, (page-1)*per_page)).fetchall()
    con.close()
    if not rows:
        text = "👥 <b>O‘quvchilar</b>\n\nHozircha o‘quvchilar yo‘q."
    else:
        text = f"👥 <b>O‘quvchilar — {total} ta</b>\n📄 Sahifa: {page}/{max(1,(total+per_page-1)//per_page)}\n\n"
        current_letter = None
        for r in rows:
            name = (r["first_name"] + " " + (r["last_name"] or "")).strip()
            letter = name[0].upper() if name else "#"
            if letter != current_letter:
                current_letter = letter
                text += f"\n<b>{safe_html(letter)}</b>\n"
            age = f" — {r['age']} yosh" if r["age"] else ""
            text += f"• {safe_html(name)}{age}\n"
    max_page = max(1, (total+per_page-1)//per_page)
    nav=[]
    if page>1: nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"admin_users_page:{page-1}"))
    nav.append(InlineKeyboardButton(text=f"{page}/{max_page}", callback_data="noop"))
    if page<max_page: nav.append(InlineKeyboardButton(text="➡️", callback_data=f"admin_users_page:{page+1}"))
    kb=[nav, [InlineKeyboardButton(text="🔙 Admin panel", callback_data="admin_back")]] if nav else [[InlineKeyboardButton(text="🔙 Admin panel", callback_data="admin_back")]]
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="HTML")


@dp.callback_query(F.data == "admin_users")
async def admin_users(callback: CallbackQuery):
    await callback.answer()
    await render_admin_users(callback, 1)


@dp.callback_query(F.data.startswith("admin_users_page:"))
async def admin_users_page(callback: CallbackQuery):
    await callback.answer()
    await render_admin_users(callback, int(callback.data.split(":",1)[1]))


@dp.callback_query(F.data == "noop")
async def noop(callback: CallbackQuery):
    await callback.answer()


@dp.callback_query(F.data == "admin_stats")
async def admin_stats(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return await callback.answer("⛔ Ruxsat yo‘q", show_alert=True)

    con = db()
    users = con.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
    active_24_cutoff = (now_local() - timedelta(days=1)).isoformat(timespec="seconds")
    active_7_cutoff = (now_local() - timedelta(days=7)).isoformat(timespec="seconds")
    active_24 = con.execute("SELECT COUNT(*) c FROM users WHERE last_seen >= ?", (active_24_cutoff,)).fetchone()["c"]
    active_7 = con.execute("SELECT COUNT(*) c FROM users WHERE last_seen >= ?", (active_7_cutoff,)).fetchone()["c"]
    subjects = con.execute("SELECT COUNT(*) c FROM user_subjects").fetchone()["c"]
    books = con.execute("SELECT COUNT(*) c FROM book_sources").fetchone()["c"]
    topics = con.execute("SELECT COUNT(*) c FROM book_topics").fetchone()["c"]
    questions = con.execute("SELECT COUNT(*) c FROM quiz_questions").fetchone()["c"]
    comps = con.execute("SELECT COUNT(*) c FROM competitions").fetchone()["c"]
    answers = con.execute("SELECT COUNT(*) c FROM competition_answers").fetchone()["c"]
    tickets = con.execute("SELECT COUNT(*) c FROM support_tickets WHERE status='open'").fetchone()["c"]
    top_subjects = con.execute("SELECT subject, COUNT(*) c FROM user_subjects GROUP BY subject ORDER BY c DESC LIMIT 5").fetchall()
    con.close()
    top = "\n".join(f"• {safe_html(r['subject'])}: {r['c']}" for r in top_subjects) or "• Hali yo‘q"
    text = ("📊 <b>Bilim Markazi — Pro statistika</b>\n\n"
        f"👥 Jami o‘quvchilar: <b>{users}</b>\n"
        f"🟢 Faol 24 soat: <b>{active_24}</b>\n"
        f"🔵 Faol 7 kun: <b>{active_7}</b>\n"
        f"📚 Tanlangan yo‘nalishlar: <b>{subjects}</b>\n"
        f"📕 Topilgan darsliklar: <b>{books}</b>\n"
        f"🧩 Mavzular: <b>{topics}</b>\n"
        f"📝 Savollar: <b>{questions}</b>\n"
        f"💬 Quiz javoblari: <b>{answers}</b>\n"
        f"🏆 Musobaqalar: <b>{comps}</b>\n"
        f"🆘 Ochiq murojaatlar: <b>{tickets}</b>\n\n"
        f"📌 <b>Eng ko‘p tanlangan fanlar</b>\n{top}")
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Admin panel", callback_data="admin_back")]]), parse_mode="HTML")


@dp.callback_query(F.data == "admin_back")
async def admin_back(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return await callback.answer("⛔ Ruxsat yo‘q", show_alert=True)
    await callback.answer()
    await callback.message.edit_text("⚙️ <b>ADMIN PANEL</b>\n\nKerakli bo‘limni tanlang:", reply_markup=admin_panel_inline(), parse_mode="HTML")


@dp.callback_query(F.data == "admin_questions")
async def admin_questions(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return await callback.answer("⛔ Ruxsat yo‘q", show_alert=True)

    con = db()
    count = con.execute("SELECT COUNT(*) c FROM quiz_questions").fetchone()["c"]
    con.close()

    await callback.message.edit_text(
        f"📝 <b>Savollar bazasi</b>\n\nHozir: <b>{count}</b> ta savol.\n\n"
        "AI orqali yangi savollar qo‘shishingiz mumkin.",
        reply_markup=quiz_generate_inline(),
        parse_mode="HTML",
    )


@dp.callback_query(F.data == "admin_support")
async def admin_support(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return await callback.answer("⛔ Ruxsat yo‘q", show_alert=True)

    con = db()
    rows = con.execute(
        """SELECT s.id, s.user_id, s.message, s.created_at, u.first_name
           FROM support_tickets s LEFT JOIN users u ON u.user_id=s.user_id
           WHERE s.status='open' ORDER BY s.id DESC LIMIT 20"""
    ).fetchall()
    con.close()

    if not rows:
        text = "🆘 Ochiq murojaatlar yo‘q."
    else:
        text = "🆘 <b>Ochiq murojaatlar</b>\n\n"
        for r in rows:
            text += (
                f"#{r['id']} — {r['first_name'] or 'Noma’lum'} "
                f"(<code>{r['user_id']}</code>)\n{r['message'][:300]}\n\n"
            )

    await callback.message.edit_text(text, parse_mode="HTML")


# =========================================================
# FAN CHANNELS + SUBJECT-SPECIFIC TESTS
# =========================================================
def fan_channel_menu():
    rows = []
    for label, subject, _, _ in FAN_SUBJECTS:
        url = fan_channel_url(subject)
        if url:
            rows.append([InlineKeyboardButton(text=label, url=url)])
        else:
            rows.append([InlineKeyboardButton(text=label, callback_data=f"fan_channel_missing:{subject}")])
    rows.append([InlineKeyboardButton(text="🔙 Asosiy menyu", callback_data="back_main_inline")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

@dp.message(F.text == "📺 Fan kanallari")
async def fan_channels(message: Message):
    if not await subscription_gate(message):
        return
    if not profile_complete(message.from_user.id):
        await message.answer("Avval /start orqali ro‘yxatdan o‘ting.")
        return
    states.pop(message.from_user.id, None)
    await hide_reply_keyboard(message)
    await message.answer(
        "📺 <b>Fan kanallari</b>\n\n"
        "Kerakli fanni tanlang va shu fan bo‘yicha alohida darslar, videolar va materiallar kanaliga o‘ting.",
        reply_markup=fan_channel_menu(), parse_mode="HTML"
    )

@dp.callback_query(F.data.startswith("fan_channel_missing:"))
async def fan_channel_missing(callback: CallbackQuery):
    await callback.answer("⚠️ Bu fan kanali hali sozlanmagan.", show_alert=True)

@dp.message(F.text == "🧪 Fan bo‘yicha test")
async def fan_test_entry(message: Message):
    if not await subscription_gate(message):
        return
    if not profile_complete(message.from_user.id):
        await message.answer("Avval /start orqali ro‘yxatdan o‘ting.")
        return
    states.pop(message.from_user.id, None)
    await hide_reply_keyboard(message)
    await message.answer(
        "🧪 <b>Fan bo‘yicha test</b>\n\n"
        "Kerakli fanni tanlang. Avval shu fan bo‘yicha qatnashayotgan boshqa o‘quvchilar ro‘yxatini ko‘rasiz.\n\n"
        "⏰ Testlar katta kunlik Quizdan keyin admin tomonidan ishga tushiriladi.",
        reply_markup=fan_subject_inline("fantest_subject"), parse_mode="HTML"
    )

@dp.callback_query(F.data.startswith("fantest_subject:"))
async def fan_test_subject(callback: CallbackQuery):
    subject = callback.data.split(":", 1)[1]
    user_id = callback.from_user.id
    now = now_iso()
    con = db()
    con.execute(
        "INSERT INTO fan_test_participants(user_id,subject,created_at) VALUES(?,?,?) ON CONFLICT(user_id,subject) DO UPDATE SET created_at=excluded.created_at",
        (user_id, subject, now),
    )
    rows = con.execute(
        """SELECT u.first_name,u.last_name FROM fan_test_participants p
           JOIN users u ON u.user_id=p.user_id WHERE p.subject=?
           ORDER BY u.first_name COLLATE NOCASE,u.last_name COLLATE NOCASE""",
        (subject,),
    ).fetchall()
    con.commit(); con.close()
    if rows:
        rivals = []
        for i, r in enumerate(rows, 1):
            name = ((r["first_name"] or "Noma’lum") + " " + (r["last_name"] or "")).strip()
            rivals.append(f"{i}. {safe_html(name)}")
        rival_text = "\n".join(rivals[:100])
    else:
        rival_text = "Hali hech kim tanlamagan."
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👀 Ko‘rib chiqdim", callback_data=f"fantest_seen:{subject}")],
        [InlineKeyboardButton(text="🔙 Fanlar", callback_data="fan_test_back")],
    ])
    await callback.answer()
    await callback.message.edit_text(
        f"🧪 <b>{safe_html(subject)} testi</b>\n\n"
        f"👥 <b>Raqiblaringiz:</b>\n{rival_text}\n\n"
        "📌 Ro‘yxatdan o‘tdingiz. Test admin tomonidan boshlanganda shu yerda qatnashasiz.\n\n"
        "⏰ <b>Katta Quiz — har kuni 20:00.</b>\n"
        "Fan testi esa undan keyin admin tomonidan ishga tushiriladi.",
        reply_markup=kb, parse_mode="HTML"
    )

@dp.callback_query(F.data.startswith("fantest_seen:"))
async def fan_test_seen(callback: CallbackQuery):
    subject = callback.data.split(":", 1)[1]
    await callback.answer("Rahmat! Test boshlanishi bilan sizga xabar beriladi.")
    await callback.message.edit_text(
        f"✅ <b>{safe_html(subject)} testi</b>\n\n"
        "Rahmat! Siz qatnashchilar ro‘yxatiga qo‘shildingiz.\n"
        "🚀 Test admin tomonidan ishga tushirilganda sizga avtomatik yuboriladi.",
        parse_mode="HTML"
    )

@dp.callback_query(F.data == "fan_test_back")
async def fan_test_back(callback: CallbackQuery):
    await callback.answer()
    await callback.message.edit_text("🧪 <b>Fan bo‘yicha test</b>\n\nFanni tanlang:", reply_markup=fan_subject_inline("fantest_subject"), parse_mode="HTML")

async def generate_ai_fan_questions(subject: str, count: int = 35):
    if count != 35:
        raise ValueError("Fan testi uchun aynan 35 ta savol yaratiladi.")
    client = ai_client()
    if client is None:
        raise RuntimeError("AI_API_KEY sozlanmagan yoki openai kutubxonasi o‘rnatilmagan.")
    con = db()
    active = con.execute("SELECT id FROM fan_test_competitions WHERE subject=? AND active=1 LIMIT 1", (subject,)).fetchone()
    if active:
        con.close()
        raise RuntimeError(f"{subject} testi hozir faol. Avval test tugashini kuting.")
    history = {r["question_key"] for r in con.execute("SELECT question_key FROM fan_test_question_history WHERE subject=?", (subject,)).fetchall()}
    con.execute("DELETE FROM fan_test_questions WHERE subject=?", (subject,))
    con.commit(); con.close()
    all_rows, seen = [], set(history)
    attempts = 0
    while len(all_rows) < count and attempts < 8:
        need = min(10, count - len(all_rows))
        prompt = f"""
Bilim Markazi uchun {subject} fanidan {need} ta yangi, juda murakkab test savoli yarating.
Savollar O'zbek tilida bo'lsin va 5-11-sinf bilimlari orasidan, lekin oddiy yodlashdan ko'ra fikrlash,
qo'llash, taqqoslash, sabab-oqibat va ilmiy mantiqni ko'proq talab qilsin.
- Fanga qat'iy rioya qiling: {subject}dan tashqariga chiqmayin.
- Bir-birini takrorlamang va avvalgi savollarga o'xshash formulani qaytarmang.
- Har savolda A/B/C/D 4 ta aniq variant va faqat bitta to'g'ri javob bo'lsin.
- Faktlar ishonchli va ilmiy jihatdan aniq bo'lsin.
- Ingliz tili bo'lsa, savollar o'zbekcha izoh bilan, test materiali esa ingliz tili grammatikasi, vocabulary, reading yoki usagega oid bo'lsin.
Faqat JSON array qaytaring:
[{{"question":"...","option_a":"...","option_b":"...","option_c":"...","option_d":"...","correct":"A","explanation":"...","difficulty":"very_hard"}}]
"""
        attempts += 1
        try:
            response = await run_ai_call(client, [
                {"role":"system","content":"Siz juda aniq va murakkab fan testlari generatorisiz. Faqat JSON qaytaring."},
                {"role":"user","content":prompt},
            ], temperature=0.25)
            raw = (response.choices[0].message.content or "[]").strip()
            raw = raw.replace("```json", "").replace("```", "").strip()
            data = json.loads(raw)
        except Exception:
            logger.exception("Fan testi AI batch xatosi: %s", subject)
            continue
        for item in data if isinstance(data, list) else []:
            if not isinstance(item, dict):
                continue
            q = str(item.get("question","")).strip()
            opts = [str(item.get(k,"")).strip() for k in ("option_a","option_b","option_c","option_d")]
            correct = str(item.get("correct","")).strip().upper()
            explanation = str(item.get("explanation","")).strip()
            if not q or any(not x for x in opts) or correct not in {"A","B","C","D"} or not explanation:
                continue
            qkey = re.sub(r"\s+", " ", q.casefold()).strip()
            if len(set(x.casefold() for x in opts)) != 4 or qkey in seen:
                continue
            seen.add(qkey)
            all_rows.append((subject,q,*opts,correct,explanation,"very_hard",now_iso()))
            if len(all_rows) >= count:
                break
    if len(all_rows) < count:
        raise RuntimeError(f"{subject} fanidan 35 ta to'liq savol yaratilmadi. {len(all_rows)} ta yaroqli savol olindi.")
    con = db()
    con.executemany("""INSERT INTO fan_test_questions(subject,question,option_a,option_b,option_c,option_d,correct,explanation,difficulty,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)""", all_rows)
    con.executemany("INSERT OR IGNORE INTO fan_test_question_history(subject,question,question_key,created_at) VALUES(?,?,?,?)", [(r[0], r[1], re.sub(r"\s+", " ", r[1].casefold()).strip(), r[-1]) for r in all_rows])
    con.commit(); con.close()
    return len(all_rows)

async def fan_test_questions(subject):
    con = db()
    rows = con.execute("SELECT * FROM fan_test_questions WHERE subject=? ORDER BY id", (subject,)).fetchall()
    con.close()
    return rows

async def publish_fan_test_announcement(comp_id: int, subject: str):
    me = await bot.get_me()
    url = f"https://t.me/{me.username}?start=fantest_{comp_id}"
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🧪 Testga kirish", url=url)]])
    text = (f"🧪 <b>{safe_html(subject)} bo‘yicha TEST boshlandi!</b>\n\n"
            "🔥 Murakkab va ilmiy savollar\n"
            "⏱ Vaqt: 10 daqiqa\n"
            "🏆 Natijalar test tugagach aniqlanadi.\n\n"
            "🚀 Testga kirish uchun tugmani bosing!")
    targets = [("Rasmiy kanal", CHANNEL_ID)] + all_fan_channel_ids()
    sent = set()
    for _, cid in targets:
        if not cid or str(cid) in sent:
            continue
        try:
            await bot.send_message(cid, text, reply_markup=kb, parse_mode="HTML")
            sent.add(str(cid))
        except Exception:
            logger.exception("Fan testi kanal e'loni yuborilmadi: %s", cid)

@dp.callback_query(F.data.startswith("fantestqgen:"))
async def fantest_qgen(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return await callback.answer("⛔ Ruxsat yo‘q", show_alert=True)
    subject = callback.data.split(":",1)[1]
    await callback.answer("🧠 Savollar yaratilmoqda...")
    await callback.message.edit_text(f"⏳ <b>{safe_html(subject)}</b> fanidan 35 ta murakkab savol tayyorlanmoqda...", parse_mode="HTML")
    try:
        n = await generate_ai_fan_questions(subject,35)
        await callback.message.edit_text(f"✅ <b>{safe_html(subject)}</b> fanidan {n} ta yangi savol tayyor.\n\nEndi 🚀 Boshlash orqali testni o‘zingiz ishga tushirasiz.", reply_markup=fan_admin_inline(), parse_mode="HTML")
    except Exception as e:
        logger.exception("Fan test question generation failed")
        await callback.message.edit_text(f"❌ Savol yaratishda xatolik:\n<code>{safe_html(str(e)[:500])}</code>", reply_markup=fan_admin_inline(), parse_mode="HTML")

@dp.callback_query(F.data == "admin_fan_tests")
async def admin_fan_tests(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return await callback.answer("⛔ Ruxsat yo‘q", show_alert=True)
    await callback.answer()
    await callback.message.edit_text("🧪 <b>Fan testlari boshqaruvi</b>\n\nKerakli bo‘limni tanlang:", reply_markup=fan_admin_inline(), parse_mode="HTML")

@dp.message(F.text == "🧪 Fan testlari")
async def admin_fan_tests_text(message: Message):
    if not await admin_only(message):
        return
    await message.answer("🧪 <b>Fan testlari boshqaruvi</b>", reply_markup=fan_admin_inline(), parse_mode="HTML")

@dp.callback_query(F.data == "fanqgen_menu")
async def fanqgen_menu(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return await callback.answer("⛔ Ruxsat yo‘q", show_alert=True)
    await callback.answer()
    await callback.message.edit_text("🧠 <b>Qaysi fan uchun 35 ta savol yaratiladi?</b>", reply_markup=fan_subject_inline("fantestqgen"), parse_mode="HTML")

async def create_fan_competition(subject: str):
    con = db()
    qrows = con.execute("SELECT id FROM fan_test_questions WHERE subject=? ORDER BY RANDOM()", (subject,)).fetchall()
    if len(qrows) != 35:
        con.close(); raise RuntimeError(f"{subject} fanida 35 ta savol tayyor emas.")
    con.execute("UPDATE fan_test_competitions SET active=0, finished=1 WHERE active=1")
    cur = con.execute("INSERT INTO fan_test_competitions(subject,title,started_at,active,finished,duration_minutes,created_at) VALUES(?,?,NULL,0,0,10,?)", (subject, f"🧪 {subject} bo‘yicha test", now_iso()))
    comp_id = cur.lastrowid
    for pos,q in enumerate(qrows,1):
        con.execute("INSERT INTO fan_test_competition_questions(competition_id,question_id,position) VALUES(?,?,?)", (comp_id,q["id"],pos))
    con.commit(); con.close()
    return comp_id

@dp.callback_query(F.data == "fantest_start_menu")
async def fantest_start_menu(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return await callback.answer("⛔ Ruxsat yo‘q", show_alert=True)
    await callback.answer()
    await callback.message.edit_text("🚀 <b>Qaysi fan testini boshlaysiz?</b>", reply_markup=fan_subject_inline("fanteststart"), parse_mode="HTML")

@dp.callback_query(F.data.startswith("fanteststart:"))
async def fantest_start(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return await callback.answer("⛔ Ruxsat yo‘q", show_alert=True)
    subject = callback.data.split(":",1)[1]
    try:
        comp_id = await create_fan_competition(subject)
        await start_fan_competition(comp_id)
        await publish_fan_test_announcement(comp_id, subject)
        await notify_fan_participants(comp_id, subject)
        await callback.answer("🚀 Test boshlandi!", show_alert=True)
        await callback.message.edit_text(f"🚀 <b>{safe_html(subject)} testi boshlandi!</b>\n\n📢 Barcha sozlangan kanallarga xabar yuborildi.\n⏱ 10 daqiqa.", reply_markup=fan_admin_inline(), parse_mode="HTML")
    except Exception as e:
        logger.exception("Fan test start failed")
        await callback.answer(str(e)[:180], show_alert=True)

@dp.callback_query(F.data == "fantest_results_menu")
async def fantest_results_menu(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return await callback.answer("⛔ Ruxsat yo‘q", show_alert=True)
    await callback.answer()
    await callback.message.edit_text("📊 <b>Natijalar uchun fan tanlang:</b>", reply_markup=fan_subject_inline("fantestresults"), parse_mode="HTML")

@dp.callback_query(F.data.startswith("fantestresults:"))
async def fantest_results(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return await callback.answer("⛔ Ruxsat yo‘q", show_alert=True)
    subject = callback.data.split(":",1)[1]
    con = db()
    comp = con.execute("SELECT * FROM fan_test_competitions WHERE subject=? ORDER BY id DESC LIMIT 1", (subject,)).fetchone()
    if not comp:
        con.close(); return await callback.answer("Bu fan uchun test hali bo‘lmagan.", show_alert=True)
    total = con.execute("SELECT COUNT(*) c FROM fan_test_competition_questions WHERE competition_id=?", (comp["id"],)).fetchone()["c"]
    rows = con.execute("""SELECT s.user_id,u.first_name,u.last_name,s.score,
               (SELECT COUNT(*) FROM fan_test_answers a WHERE a.competition_id=s.competition_id AND a.user_id=s.user_id) answered
               FROM fan_test_sessions s LEFT JOIN users u ON u.user_id=s.user_id
               WHERE s.competition_id=? ORDER BY s.score DESC, answered DESC""", (comp["id"],)).fetchall()
    con.close()
    text = f"📊 <b>{safe_html(subject)} — natijalar</b>\n📝 {total} ta savol\n\n"
    if not rows:
        text += "Hali natijalar yo‘q."
    else:
        for i,r in enumerate(rows,1):
            name=((r["first_name"] or "Noma’lum")+" "+(r["last_name"] or "")).strip()
            text += f"{i}. {safe_html(name)} — <b>{r['score']}/{total}</b> (yechilgan {r['answered']}/{total})\n"
    await callback.message.edit_text(text, reply_markup=fan_admin_inline(), parse_mode="HTML")

@dp.callback_query(F.data == "fantest_delete_menu")
async def fantest_delete_menu(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return await callback.answer("⛔ Ruxsat yo‘q", show_alert=True)
    await callback.answer()
    await callback.message.edit_text("🗑 <b>Qaysi fan savollarini o‘chirasiz?</b>", reply_markup=fan_subject_inline("fantestdelete"), parse_mode="HTML")

@dp.callback_query(F.data.startswith("fantestdelete:"))
async def fantest_delete(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return await callback.answer("⛔ Ruxsat yo‘q", show_alert=True)
    subject=callback.data.split(":",1)[1]
    con=db()
    active=con.execute("SELECT id FROM fan_test_competitions WHERE subject=? AND active=1 LIMIT 1",(subject,)).fetchone()
    if active:
        con.close(); return await callback.answer("⛔ Bu fan testi hali faol. Avval test tugasin.", show_alert=True)
    cur=con.execute("DELETE FROM fan_test_questions WHERE subject=?",(subject,)); n=cur.rowcount
    con.commit(); con.close()
    await callback.answer(f"🗑 {n} ta savol o‘chirildi.", show_alert=True)
    await callback.message.edit_text(f"🗑 <b>{safe_html(subject)}</b> savollari o‘chirildi.\n\nYana savol yaratishingiz mumkin.", reply_markup=fan_admin_inline(), parse_mode="HTML")

async def start_fan_competition(comp_id: int):
    con=db(); con.execute("UPDATE fan_test_competitions SET active=0 WHERE active=1"); con.execute("UPDATE fan_test_competitions SET active=1,finished=0,started_at=? WHERE id=?",(now_iso(),comp_id)); con.commit(); con.close()

async def notify_fan_participants(comp_id:int, subject:str):
    con=db(); rows=con.execute("SELECT user_id FROM fan_test_participants WHERE subject=?",(subject,)).fetchall(); con.close()
    for r in rows:
        try:
            await bot.send_message(r["user_id"], f"🚀 <b>{safe_html(subject)} bo‘yicha test boshlandi!</b>\n⏱ 10 daqiqa.\n\nTestga kirish uchun tugmani bosing.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🧪 Testga kirish", callback_data=f"join_fantest:{comp_id}")]]), parse_mode="HTML")
        except Exception:
            logger.exception("Fan test participant notification failed: %s", r["user_id"])

async def expire_fan_competition(comp_id:int):
    con=db(); comp=con.execute("SELECT * FROM fan_test_competitions WHERE id=? AND active=1",(comp_id,)).fetchone()
    if not comp: con.close(); return
    total=con.execute("SELECT COUNT(*) c FROM fan_test_competition_questions WHERE competition_id=?",(comp_id,)).fetchone()["c"]
    sessions=con.execute("""SELECT s.user_id,s.score,u.first_name,u.last_name,
               (SELECT COUNT(*) FROM fan_test_answers a WHERE a.competition_id=s.competition_id AND a.user_id=s.user_id) answered
               FROM fan_test_sessions s LEFT JOIN users u ON u.user_id=s.user_id WHERE s.competition_id=? AND s.finished=0 ORDER BY s.score DESC,answered DESC""",(comp_id,)).fetchall()
    con.execute("UPDATE fan_test_competitions SET active=0,finished=1 WHERE id=?",(comp_id,))
    for r in sessions:
        con.execute("UPDATE fan_test_sessions SET finished=1,updated_at=? WHERE user_id=? AND competition_id=?",(now_iso(),r["user_id"],comp_id))
    con.commit(); con.close()
    # Keep result history; delete question bank only when admin presses the delete button.
    con=db(); report=con.execute("""SELECT s.user_id,u.first_name,u.last_name,s.score,
           (SELECT COUNT(*) FROM fan_test_answers a WHERE a.competition_id=s.competition_id AND a.user_id=s.user_id) answered
           FROM fan_test_sessions s LEFT JOIN users u ON u.user_id=s.user_id WHERE s.competition_id=? ORDER BY s.score DESC,answered DESC""",(comp_id,)).fetchall(); con.close()
    for r in sessions:
        try: await bot.send_message(r["user_id"], f"⏰ <b>{safe_html(comp['subject'])} testi tugadi!</b>\n🎯 Natija: <b>{r['score']}/{total}</b>\n📝 Yechilgan: <b>{r['answered']}/{total}</b>", parse_mode="HTML", reply_markup=main_menu())
        except Exception: pass
    lines=[f"⏰ <b>{safe_html(comp['subject'])} testi yakunlandi</b>",f"📝 Jami: <b>{total}</b>",f"👥 Qatnashchilar: <b>{len(report)}</b>",""]
    for i,r in enumerate(report[:50],1):
        name=((r["first_name"] or "Noma’lum")+" "+(r["last_name"] or "")).strip(); lines.append(f"{i}. {safe_html(name)} — <b>{r['score']}/{total}</b> (yechilgan {r['answered']}/{total})")
    try: await send_admins("\n".join(lines),parse_mode="HTML")
    except Exception: pass

async def expire_active_fan_competitions():
    con=db(); rows=con.execute("SELECT * FROM fan_test_competitions WHERE active=1").fetchall(); con.close()
    for comp in rows:
        try:
            started=datetime.fromisoformat(comp["started_at"])
            if now_local() >= started+timedelta(minutes=int(comp["duration_minutes"] or 10)):
                await expire_fan_competition(comp["id"])
        except Exception: logger.exception("Fan test expiry error")

async def send_fan_question(user_id:int):
    con=db(); state=con.execute("SELECT * FROM fan_test_sessions WHERE user_id=?",(user_id,)).fetchone(); con.close()
    if not state or state["finished"]: return
    con=db(); comp=con.execute("SELECT * FROM fan_test_competitions WHERE id=?",(state["competition_id"],)).fetchone(); questions=con.execute("SELECT q.* FROM fan_test_competition_questions cq JOIN fan_test_questions q ON q.id=cq.question_id WHERE cq.competition_id=? ORDER BY cq.position",(state["competition_id"],)).fetchall(); con.close()
    if not comp or not comp["active"]: return
    try:
        if now_local() >= datetime.fromisoformat(comp["started_at"])+timedelta(minutes=int(comp["duration_minutes"] or 10)):
            await expire_fan_competition(comp["id"]); return
    except Exception: pass
    idx=state["current_position"]-1
    if idx>=len(questions):
        con=db(); con.execute("UPDATE fan_test_sessions SET finished=1,updated_at=? WHERE user_id=?",(now_iso(),user_id)); con.commit(); con.close()
        await bot.send_message(user_id,f"🏁 <b>{safe_html(comp['subject'])} testi tugadi!</b>\n🎯 Natija: <b>{state['score']}/{len(questions)}</b>",parse_mode="HTML",reply_markup=main_menu()); return
    q=questions[idx]
    kb=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=f"A) {q['option_a']}",callback_data=f"fanans:{q['id']}:A")],[InlineKeyboardButton(text=f"B) {q['option_b']}",callback_data=f"fanans:{q['id']}:B")],[InlineKeyboardButton(text=f"C) {q['option_c']}",callback_data=f"fanans:{q['id']}:C")],[InlineKeyboardButton(text=f"D) {q['option_d']}",callback_data=f"fanans:{q['id']}:D")]])
    await bot.send_message(user_id,f"🧪 <b>{safe_html(comp['subject'])}</b>\n\n🧠 Savol {idx+1}/{len(questions)}\n\n{safe_html(q['question'])}",reply_markup=kb,parse_mode="HTML")

@dp.callback_query(F.data.startswith("join_fantest:"))
async def join_fantest(callback: CallbackQuery):
    comp_id=int(callback.data.split(":",1)[1]); con=db(); comp=con.execute("SELECT * FROM fan_test_competitions WHERE id=? AND active=1",(comp_id,)).fetchone(); con.close()
    if not comp: return await callback.answer("Test faol emas.",show_alert=True)
    now=now_iso(); con=db(); con.execute("""INSERT INTO fan_test_sessions(user_id,competition_id,current_position,score,finished,started_at,updated_at) VALUES(?,?,1,0,0,?,?) ON CONFLICT(user_id) DO UPDATE SET competition_id=excluded.competition_id,current_position=1,score=0,finished=0,started_at=excluded.started_at,updated_at=excluded.updated_at""",(callback.from_user.id,comp_id,now,now)); con.commit(); con.close(); await callback.answer("🚀 Test boshlandi!"); await send_fan_question(callback.from_user.id)

@dp.callback_query(F.data.startswith("fanans:"))
async def answer_fan_question(callback:CallbackQuery):
    _,qid_s,answer=callback.data.split(":",2); qid=int(qid_s); uid=callback.from_user.id
    con=db(); state=con.execute("SELECT * FROM fan_test_sessions WHERE user_id=?",(uid,)).fetchone(); comp=con.execute("SELECT * FROM fan_test_competitions WHERE id=?",(state["competition_id"],)).fetchone() if state else None; q=con.execute("SELECT q.* FROM fan_test_competition_questions cq JOIN fan_test_questions q ON q.id=cq.question_id WHERE cq.competition_id=? AND q.id=?",(state["competition_id"],qid)).fetchone() if state else None; con.close()
    if not state or state["finished"] or not comp or not comp["active"] or not q: return await callback.answer("Test faol emas.",show_alert=True)
    try:
        if now_local() >= datetime.fromisoformat(comp["started_at"])+timedelta(minutes=int(comp["duration_minutes"] or 10)):
            await expire_fan_competition(comp["id"]); return await callback.answer("⏰ Vaqt tugadi.",show_alert=True)
    except Exception: pass
    con=db(); current=con.execute("SELECT q.* FROM fan_test_competition_questions cq JOIN fan_test_questions q ON q.id=cq.question_id WHERE cq.competition_id=? ORDER BY cq.position",(state["competition_id"],)).fetchall(); idx=state["current_position"]-1
    if idx<0 or idx>=len(current) or current[idx]["id"]!=qid: con.close(); return await callback.answer("Bu savol endi faol emas.",show_alert=True)
    exists=con.execute("SELECT 1 FROM fan_test_answers WHERE competition_id=? AND user_id=? AND question_id=?",(state["competition_id"],uid,qid)).fetchone()
    if exists: con.close(); return await callback.answer("Bu savolga javob berilgansiz.")
    correct=int(answer==q["correct"]); now=now_iso(); con.execute("INSERT INTO fan_test_answers(competition_id,user_id,question_id,answer,correct,answered_at) VALUES(?,?,?,?,?,?)",(state["competition_id"],uid,qid,answer,correct,now)); con.execute("UPDATE fan_test_sessions SET current_position=current_position+1,score=score+?,updated_at=? WHERE user_id=? AND competition_id=? AND finished=0",(correct,now,uid,state["competition_id"])); con.commit(); con.close(); await callback.answer("✅ To‘g‘ri!" if correct else "❌ Noto‘g‘ri"); await send_fan_question(uid)

# =========================================================
# LARGE DAILY QUIZ
# =========================================================
SAMPLE_QUESTIONS = [
    (
        "Yer atmosferasida eng ko‘p uchraydigan gaz qaysi?",
        "Kislorod", "Azot", "Karbonat angidrid", "Vodorod", "B",
        "Atmosferaning asosiy qismini azot tashkil qiladi.", "hard"
    ),
    (
        "Agar 2x + 6 = 18 bo‘lsa, x nechaga teng?",
        "4", "5", "6", "7", "C",
        "2x=12, demak x=6.", "medium"
    ),
    (
        "Elektr energiyasini mexanik energiyaga aylantiruvchi qurilma?",
        "Generator", "Elektr dvigatel", "Transformator", "Rezistor", "B",
        "Elektr dvigatel elektr energiyasini mexanik harakatga aylantiradi.", "hard"
    ),
    (
        "Fotosintez jarayonida o‘simliklar asosan qaysi gazni yutadi?",
        "Kislorod", "Azot", "Karbonat angidrid", "Vodorod", "C",
        "Fotosintezda CO₂ yutilib, organik moddalar hosil qilinadi.", "medium"
    ),
    (
        "GPS tizimi joylashuvni aniqlashda asosan nimadan foydalanadi?",
        "Sun’iy yo‘ldosh signallari", "Magnit", "Wi-Fi kabeli", "Faqat kamera", "A",
        "GPS sun’iy yo‘ldoshlardan keladigan signallar asosida ishlaydi.", "hard"
    ),
]


async def clear_daily_quiz_question_bank():
    """Delete old daily-competition questions before a fresh generation."""
    con = db()
    con.execute("DELETE FROM quiz_questions")
    con.commit()
    con.close()


async def _generate_ai_quiz_batch(batch_size=10, subject="Aralash", grade=None, difficulty="hard", existing=None):
    client = ai_client()
    if client is None:
        raise RuntimeError("AI_API_KEY sozlanmagan yoki openai kutubxonasi o‘rnatilmagan.")

    existing = existing or set()
    prompt = f"""
Siz Bilim Markazi uchun professional kunlik katta QUIZ savollari generatorisiz.

{batch_size} ta MUTLAQO YANGI multiple-choice savol yarating.
Mavzu: {subject}
Sinf: {grade or "turli sinflar"}
Daraja: {difficulty}

TALABLAR:
- Savollar asosan ilmiy bo‘lsin: fizika, kimyo, biologiya, astronomiya, matematika,
  informatika, texnologiya, geografiya, tibbiyot/fiziologiya asoslari, ekologiya va
  boshqa tabiiy-ilmiy mavzular.
- Shuningdek hayotiy-ilmiy vaziyatlar, mantiq va real hayotdagi fan qo‘llanilishidan foydalaning.
- Savollar murakkab, fikrlashni talab qiladigan va kattalar uchun ham qiziqarli bo‘lsin.
- Har bir savolda aynan 4 ta variant: A, B, C, D.
- Faqat BITTA to‘g‘ri javob bo‘lsin.
- Faktlar ilmiy jihatdan aniq bo‘lsin; taxminiy yoki bahsli faktlardan foydalanmang.
- Bir-biriga o‘xshash savollarni takrorlamang.
- Tayyor savolni aynan ko‘chirmang.
- Savol matni va variantlar o‘zbek tilida bo‘lsin.
- Faqat JSON array qaytaring. Markdown, izoh yoki boshqa matn yozmang.

Format:
[{{"question":"...","option_a":"...","option_b":"...","option_c":"...","option_d":"...","correct":"A","explanation":"...","difficulty":"very_hard"}}]
"""

    response = await run_ai_call(client, [
        {"role": "system", "content": "Siz aniq, murakkab va ishonchli ilmiy testlar generatorisiz. Faqat JSON qaytaring."},
        {"role": "user", "content": prompt},
    ], temperature=0.35)

    raw = (response.choices[0].message.content or "[]").strip()
    if raw.startswith("```json"):
        raw = raw[7:]
    elif raw.startswith("```"):
        raw = raw[3:]
    if raw.endswith("```"):
        raw = raw[:-3]
    data = json.loads(raw.strip())
    if not isinstance(data, list):
        raise ValueError("AI JSON array qaytarmadi.")

    rows = []
    seen = set(existing)
    for item in data:
        if not isinstance(item, dict):
            continue
        q = str(item.get("question", "")).strip()
        opts = [str(item.get(k, "")).strip() for k in ("option_a", "option_b", "option_c", "option_d")]
        correct = str(item.get("correct", "")).strip().upper()
        explanation = str(item.get("explanation", "")).strip()
        diff = str(item.get("difficulty", difficulty)).strip().lower()

        if not q or any(not x for x in opts):
            continue
        if correct not in {"A", "B", "C", "D"} or not explanation:
            continue
        if len(set(x.casefold() for x in opts)) != 4:
            continue
        if q.casefold() in seen:
            continue
        if diff not in {"medium", "hard", "very_hard"}:
            diff = difficulty

        rows.append((q, *opts, correct, explanation, diff))
        seen.add(q.casefold())

    return rows


async def generate_ai_quiz_questions(count=10, subject="Aralash", grade=None, difficulty="very_hard"):
    """Generate exactly count fresh questions in small batches and save them."""
    if count not in {40, 60, 80, 100}:
        raise ValueError("Faqat 40, 60, 80 yoki 100 ta savol tanlanishi mumkin.")

    # Every generation starts from a clean question bank.
    await clear_daily_quiz_question_bank()

    all_rows = []
    seen = set()
    no_progress = 0
    target = count

    while len(all_rows) < target and no_progress < 4:
        need = min(10, target - len(all_rows))
        try:
            rows = await _generate_ai_quiz_batch(
                batch_size=need,
                subject=subject,
                grade=grade,
                difficulty=difficulty,
                existing=seen,
            )
        except Exception:
            logger.exception("AI quiz batch generation failed")
            rows = []

        # Keep only new questions.
        fresh = []
        for row in rows:
            key = row[0].casefold()
            if key not in seen:
                seen.add(key)
                fresh.append(row)

        if fresh:
            all_rows.extend(fresh[:target - len(all_rows)])
            no_progress = 0
        else:
            no_progress += 1

    if len(all_rows) < target:
        # Never leave a partial set in the database.
        raise RuntimeError(
            f"AI {target} ta to‘liq yangi savol yarata olmadi. "
            f"Faqat {len(all_rows)} ta yaroqli savol olindi."
        )

    con = db()
    con.executemany(
        """INSERT INTO quiz_questions
        (question, option_a, option_b, option_c, option_d, correct, explanation, difficulty)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        all_rows,
    )
    con.commit()
    con.close()
    return len(all_rows)


def seed_questions():
    # Daily quiz questions are generated only by the admin.
    # No hard-coded sample questions are inserted, so the old 5-question set
    # can never reappear after a restart/deploy.
    return


@dp.message(F.text == "🏆 Katta Quiz boshqaruvi")
async def quiz_admin_button(message: Message):
    if not await admin_only(message):
        return
    await message.answer(
        "🏆 <b>Katta Quiz boshqaruvi</b>\n\n"
        "Har kuni 20:00 uchun musobaqani shu yerdan boshqarasiz.",
        reply_markup=quiz_admin_inline(),
        parse_mode="HTML",
    )


@dp.message(F.text == "🏆 Katta Quiz")
async def big_quiz(message: Message):
    if not await subscription_gate(message):
        return

    comp = latest_competition()
    if comp and comp["active"]:
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🚀 Musobaqaga kirish", callback_data=f"join_quiz:{comp['id']}")]
            ]
        )
        await message.answer(
            f"🏆 <b>{comp['title']}</b>\n\n"
            "Musobaqa hozir faol. Quyidagi tugma orqali kiring:",
            reply_markup=kb,
            parse_mode="HTML",
        )
    else:
        await message.answer(
            "🏆 <b>Katta Quiz</b>\n\n"
            "Hozircha faol musobaqa yo‘q.\n"
            "📅 Har kuni 20:00 da yangi musobaqa boshlanadi.",
            parse_mode="HTML",
        )


@dp.callback_query(F.data == "admin_quiz")
async def admin_quiz(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return await callback.answer("⛔ Ruxsat yo‘q", show_alert=True)
    await callback.message.edit_text(
        "🏆 <b>Katta Quiz boshqaruvi</b>",
        reply_markup=quiz_admin_inline(),
        parse_mode="HTML",
    )


@dp.callback_query(F.data == "quiz_seed")
async def quiz_seed(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return await callback.answer("⛔ Ruxsat yo‘q", show_alert=True)
    await callback.answer()
    await callback.message.edit_text(
        "🧠 <b>AI orqali yangi savollar yaratish</b>\n\n"
        "Kerakli miqdorni tanlang. Savollar bazadagi mavjud savollar bilan solishtirilib, "
        "takrorlari saqlanmaydi.",
        reply_markup=quiz_generate_inline(),
        parse_mode="HTML",
    )


@dp.callback_query(F.data.startswith("qgen:"))
async def quiz_generate_callback(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return await callback.answer("⛔ Ruxsat yo‘q", show_alert=True)
    try:
        count = int(callback.data.split(":", 1)[1])
        if count not in {40, 60, 80, 100}:
            raise ValueError("Noto‘g‘ri miqdor")
    except Exception:
        return await callback.answer("❌ Miqdor noto‘g‘ri.", show_alert=True)

    await callback.answer("🤖 AI savollar yaratmoqda...")
    await callback.message.edit_text(
        f"⏳ <b>{count} ta yangi savol tayyorlanmoqda...</b>\n\n"
        "Eski savollar o‘chiriladi. AI yangi, takrorlanmaydigan va asosan ilmiy savollarni yaratadi.",
        parse_mode="HTML",
    )
    try:
        added = await generate_ai_quiz_questions(count=count)
        con = db()
        total = con.execute("SELECT COUNT(*) c FROM quiz_questions").fetchone()["c"]
        con.close()
        await callback.message.edit_text(
            f"✅ <b>AI savollar tayyor!</b>\n\n"
            f"➕ Qo‘shilgan: <b>{added}</b> ta\n"
            f"📚 Bazadagi jami: <b>{total}</b> ta\n\n"
            f"⏱ Musobaqa davomiyligi: <b>10 daqiqa</b>\n\n"
            "Endi ➕ Yangi musobaqa orqali quiz yaratishingiz mumkin.",
            reply_markup=quiz_admin_inline(),
            parse_mode="HTML",
        )
    except Exception as e:
        logger.exception("AI quiz generation failed")
        await callback.message.edit_text(
            "❌ <b>AI savol yaratishda xatolik</b>\n\n"
            f"<code>{safe_html(str(e)[:500])}</code>\n\n"
            "AI_API_KEY, AI_MODEL va OpenRouter sozlamalarini tekshiring.",
            reply_markup=quiz_generate_inline(),
            parse_mode="HTML",
        )


@dp.callback_query(F.data == "quiz_new")
async def quiz_new(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return await callback.answer("⛔ Ruxsat yo‘q", show_alert=True)

    con = db()
    qcount = con.execute("SELECT COUNT(*) c FROM quiz_questions").fetchone()["c"]
    if qcount not in {40, 60, 80, 100}:
        con.close()
        return await callback.answer(
            "Avval 40, 60, 80 yoki 100 ta yangi savol yarating.",
            show_alert=True,
        )

    cur = con.execute(
        "INSERT INTO competitions(title, started_at, active, finished, duration_minutes) VALUES (?, NULL, 0, 0, 10)",
        (f"🏆 Bilim Markazi — Kunlik katta quiz {datetime.now():%d.%m.%Y}",),
    )
    comp_id = cur.lastrowid
    qrows = con.execute(
        "SELECT id FROM quiz_questions ORDER BY RANDOM()"
    ).fetchall()
    for pos, q in enumerate(qrows, 1):
        con.execute(
            "INSERT INTO competition_questions(competition_id, question_id, position) VALUES (?, ?, ?)",
            (comp_id, q["id"], pos),
        )
    con.commit()
    con.close()

    await callback.message.edit_text(
        f"✅ <b>Musobaqa tayyor!</b>\n\nID: <code>{comp_id}</code>\n"
        "⏱ Davomiyligi: <b>10 daqiqa</b>\n"
        "Keyingi qadam: 📢 Kanalga e'lon yoki 🚀 BOSHLASH.",
        reply_markup=quiz_admin_inline(),
        parse_mode="HTML",
    )


async def publish_quiz_announcement(comp_id: int):
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(
                text="🏆 Musobaqaga kirish",
                url=f"https://t.me/{(await bot.get_me()).username}?start=quiz_{comp_id}"
            )]
        ]
    )
    await bot.send_message(
        CHANNEL_ID,
        "🏆 <b>Bilim Markazi — Katta Quiz</b>\n\n"
        "🧠 Ilmiy, mantiqiy va murakkab savollar.\n"
        "🚀 Musobaqaga kirish uchun tugmani bosing!",
        reply_markup=kb,
        parse_mode="HTML",
    )


@dp.callback_query(F.data == "quiz_announce")
async def quiz_announce(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return await callback.answer("⛔ Ruxsat yo‘q", show_alert=True)

    comp = latest_competition()
    if not comp:
        return await callback.answer("Avval ➕ Yangi musobaqa yarating.", show_alert=True)

    try:
        await publish_quiz_announcement(comp["id"])
        await callback.answer("✅ Kanalga e’lon yuborildi.", show_alert=True)
    except Exception as e:
        logger.exception("Kanalga post xatosi")
        await callback.answer(f"❌ Kanalga yuborilmadi: {e}", show_alert=True)


async def start_competition(comp_id: int):
    con = db()
    con.execute("UPDATE competitions SET active=0 WHERE active=1")
    con.execute("UPDATE quiz_sessions SET finished=1, updated_at=? WHERE finished=0 AND competition_id IN (SELECT id FROM competitions WHERE active=0)", (now_iso(),))
    con.execute(
        "UPDATE competitions SET active=1, finished=0, started_at=? WHERE id=?",
        (now_iso(), comp_id),
    )
    con.commit()
    con.close()


@dp.callback_query(F.data == "quiz_start")
async def quiz_start(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return await callback.answer("⛔ Ruxsat yo‘q", show_alert=True)

    comp = latest_competition()
    if not comp:
        return await callback.answer("Avval ➕ Yangi musobaqa yarating.", show_alert=True)

    await start_competition(comp["id"])
    try:
        await publish_quiz_announcement(comp["id"])
    except Exception:
        logger.exception("Avtomatik kanal e’loni yuborilmadi")

    await callback.answer("🚀 Musobaqa boshlandi!", show_alert=True)
    await callback.message.edit_text(
        f"🚀 <b>{comp['title']}</b>\n\n"
        "Musobaqa faol.\n"
        "Kanalga e’lon ham yuborildi.",
        parse_mode="HTML",
    )


@dp.callback_query(F.data.startswith("join_quiz:"))
async def join_quiz(callback: CallbackQuery):
    comp_id = int(callback.data.split(":", 1)[1])
    if not await is_subscribed(callback.from_user.id):
        return await callback.answer("Avval kanalga obuna bo‘ling.", show_alert=True)
    con = db()
    comp = con.execute("SELECT * FROM competitions WHERE id=? AND active=1", (comp_id,)).fetchone()
    qcount = con.execute("SELECT COUNT(*) c FROM competition_questions WHERE competition_id=?", (comp_id,)).fetchone()["c"]
    con.close()
    if not comp or not qcount:
        return await callback.answer("Musobaqa hozir faol emas.", show_alert=True)
    now = now_iso()
    con = db()
    con.execute("""INSERT INTO quiz_sessions(user_id, competition_id, current_position, score, finished, started_at, updated_at)
                   VALUES(?,?,1,0,0,?,?)
                   ON CONFLICT(user_id) DO UPDATE SET
                     competition_id=excluded.competition_id,
                     current_position=CASE WHEN quiz_sessions.finished=1 OR quiz_sessions.competition_id<>excluded.competition_id THEN 1 ELSE quiz_sessions.current_position END,
                     score=CASE WHEN quiz_sessions.finished=1 OR quiz_sessions.competition_id<>excluded.competition_id THEN 0 ELSE quiz_sessions.score END,
                     finished=0, updated_at=excluded.updated_at""", (callback.from_user.id, comp_id, now, now))
    con.commit(); con.close()
    remaining = max(1, int((datetime.fromisoformat(comp["started_at"]) + timedelta(minutes=int(comp["duration_minutes"] or 10)) - now_local()).total_seconds() // 60))
    await callback.answer(f"🏆 Quiz boshlandi! Vaqt: {remaining} daqiqa")
    await send_quiz_question(callback.from_user.id)


def competition_has_expired(comp) -> bool:
    if not comp or not comp["active"] or not comp["started_at"]:
        return False
    try:
        started = datetime.fromisoformat(comp["started_at"])
        duration = int(comp["duration_minutes"] or 10)
        return now_local() >= started + timedelta(minutes=duration)
    except Exception:
        logger.exception("Competition expiry check failed")
        return False


async def expire_competition(comp_id: int):
    """Finish a timed competition, save partial results, notify users/admin, then clear its question bank."""
    con = db()
    comp = con.execute("SELECT * FROM competitions WHERE id=? AND active=1", (comp_id,)).fetchone()
    if not comp:
        con.close()
        return

    total = con.execute(
        "SELECT COUNT(*) c FROM competition_questions WHERE competition_id=?",
        (comp_id,),
    ).fetchone()["c"]

    sessions = con.execute(
        """SELECT qs.user_id, qs.score, u.first_name, u.last_name,
                  (SELECT COUNT(*) FROM competition_answers ca
                   WHERE ca.competition_id=qs.competition_id AND ca.user_id=qs.user_id) AS answered
           FROM quiz_sessions qs
           LEFT JOIN users u ON u.user_id=qs.user_id
           WHERE qs.competition_id=? AND qs.finished=0
           ORDER BY qs.score DESC, answered DESC""",
        (comp_id,),
    ).fetchall()

    # Mark the competition and all unfinished sessions finished.
    con.execute(
        "UPDATE competitions SET active=0, finished=1 WHERE id=?",
        (comp_id,),
    )
    for r in sessions:
        con.execute(
            """UPDATE quiz_sessions
               SET finished=1, updated_at=?
               WHERE user_id=? AND competition_id=? AND finished=0""",
            (now_iso(), r["user_id"], comp_id),
        )
        con.execute(
            """INSERT INTO quiz_results(user_id,subject,grade,score,total,quiz_type,created_at)
               VALUES(?,?,?,?,?,?,?)""",
            (r["user_id"], comp["title"], None, r["score"], total, "competition", now_iso()),
        )

    # Keep answer/result history, but remove the daily question bank.
    con.execute("DELETE FROM competition_questions WHERE competition_id=?", (comp_id,))
    con.execute("DELETE FROM quiz_questions")
    con.commit()
    con.close()

    # Notify unfinished participants.
    for r in sessions:
        try:
            await bot.send_message(
                r["user_id"],
                f"⏰ <b>Vaqt tugadi!</b>\\n\\n"
                f"🏆 {safe_html(comp['title'])}\\n"
                f"🎯 Natija: <b>{r['score']}/{total}</b>\\n"
                f"📝 Yechilgan: <b>{r['answered']}/{total}</b>",
                parse_mode="HTML",
                reply_markup=main_menu(),
            )
        except Exception:
            logger.exception("Quiz timeout notification failed for user %s", r["user_id"])

    # Send automatic admin report.
    con = db()
    report = con.execute(
        """SELECT u.first_name, u.last_name,
                  qs.score,
                  (SELECT COUNT(*) FROM competition_answers ca
                   WHERE ca.competition_id=qs.competition_id AND ca.user_id=qs.user_id) AS answered
           FROM quiz_sessions qs
           LEFT JOIN users u ON u.user_id=qs.user_id
           WHERE qs.competition_id=?
           ORDER BY qs.score DESC, answered DESC""",
        (comp_id,),
    ).fetchall()
    con.close()

    lines = [
        f"⏰ <b>Katta Quiz yakunlandi</b>",
        f"🏆 {safe_html(comp['title'])}",
        f"📝 Jami savollar: <b>{total}</b>",
        f"👥 Qatnashchilar: <b>{len(report)}</b>",
        "",
    ]
    if report:
        for i, r in enumerate(report[:20], 1):
            name = (r["first_name"] or "Noma’lum") + (f" {r['last_name']}" if r["last_name"] else "")
            lines.append(
                f"{i}. {safe_html(name)} — <b>{r['score']}/{total}</b> "
                f"(yechilgan: {r['answered']}/{total})"
            )
    else:
        lines.append("Hali hech kim qatnashmadi.")

    try:
        await send_admins( "\n".join(lines), parse_mode="HTML")
    except Exception:
        logger.exception("Admin quiz timeout report failed")


async def expire_active_competitions():
    con = db()
    active = con.execute("SELECT * FROM competitions WHERE active=1").fetchall()
    con.close()
    for comp in active:
        if competition_has_expired(comp):
            await expire_competition(comp["id"])


async def get_quiz_session(user_id: int):
    con = db()
    row = con.execute("SELECT * FROM quiz_sessions WHERE user_id=?", (user_id,)).fetchone()
    con.close()
    return row


async def send_quiz_question(user_id: int):
    state = await get_quiz_session(user_id)
    if not state or state["finished"]:
        return

    con = db()
    comp = con.execute("SELECT * FROM competitions WHERE id=?", (state["competition_id"],)).fetchone()
    con.close()
    if not comp or not comp["active"]:
        return
    if competition_has_expired(comp):
        await expire_competition(comp["id"])
        return

    questions = competition_question_ids(state["competition_id"])
    idx = state["current_position"] - 1
    if idx >= len(questions):
        con = db()
        con.execute("UPDATE quiz_sessions SET finished=1, updated_at=? WHERE user_id=?", (now_iso(), user_id))
        comp = con.execute("SELECT title FROM competitions WHERE id=?", (state["competition_id"],)).fetchone()
        con.execute("INSERT INTO quiz_results(user_id,subject,grade,score,total,quiz_type,created_at) VALUES(?,?,?,?,?,?,?)",
                    (user_id, comp["title"] if comp else "", None, state["score"], len(questions), "competition", now_iso()))
        con.commit(); con.close()
        total = len(questions)
        await bot.send_message(user_id, f"🏁 <b>Musobaqa tugadi!</b>\n\n🎯 Natija: <b>{state['score']}/{total}</b>\n\nNatijangiz saqlandi.", parse_mode="HTML", reply_markup=main_menu())
        return
    q = questions[idx]
    buttons = [
        [InlineKeyboardButton(text=f"A) {q['option_a']}", callback_data=f"ans:{q['id']}:A")],
        [InlineKeyboardButton(text=f"B) {q['option_b']}", callback_data=f"ans:{q['id']}:B")],
        [InlineKeyboardButton(text=f"C) {q['option_c']}", callback_data=f"ans:{q['id']}:C")],
        [InlineKeyboardButton(text=f"D) {q['option_d']}", callback_data=f"ans:{q['id']}:D")],
    ]
    await bot.send_message(user_id, f"🧠 <b>Savol {idx+1}/{len(questions)}</b>\n\n{safe_html(q['question'])}", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode="HTML")


@dp.callback_query(F.data.startswith("ans:"))
async def answer_quiz(callback: CallbackQuery):
    user_id = callback.from_user.id
    _, qid_s, answer = callback.data.split(":", 2)
    qid = int(qid_s)
    state = await get_quiz_session(user_id)
    if not state or state["finished"]:
        return await callback.answer("Bu quiz faol emas.", show_alert=True)

    con = db()
    comp = con.execute("SELECT * FROM competitions WHERE id=?", (state["competition_id"],)).fetchone()
    con.close()
    if not comp or not comp["active"]:
        return await callback.answer("Bu quiz yakunlangan.", show_alert=True)
    if competition_has_expired(comp):
        await expire_competition(comp["id"])
        return await callback.answer("⏰ Vaqt tugadi.", show_alert=True)

    questions = competition_question_ids(state["competition_id"])
    idx = state["current_position"] - 1
    if idx >= len(questions) or questions[idx]["id"] != qid:
        return await callback.answer("Bu savol endi faol emas.", show_alert=True)
    q = questions[idx]
    correct = 1 if answer == q["correct"] else 0
    now = now_iso()
    con = db()
    try:
        con.execute("BEGIN IMMEDIATE")
        exists = con.execute("SELECT 1 FROM competition_answers WHERE competition_id=? AND user_id=? AND question_id=?", (state["competition_id"], user_id, qid)).fetchone()
        if exists:
            con.rollback(); con.close()
            return await callback.answer("Bu savolga javob berilgansiz.")
        con.execute("INSERT INTO competition_answers(competition_id,user_id,question_id,answer,correct,answered_at) VALUES(?,?,?,?,?,?)", (state["competition_id"], user_id, qid, answer, correct, now))
        con.execute("UPDATE quiz_sessions SET current_position=current_position+1, score=score+?, updated_at=? WHERE user_id=? AND competition_id=? AND finished=0", (correct, now, user_id, state["competition_id"]))
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()
    await callback.answer("✅ To‘g‘ri!" if correct else "❌ Noto‘g‘ri")
    await send_quiz_question(user_id)


@dp.callback_query(F.data == "quiz_results")
async def quiz_results(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return await callback.answer("⛔ Ruxsat yo‘q", show_alert=True)

    comp = latest_competition()
    if not comp:
        return await callback.answer("Musobaqa yo‘q.", show_alert=True)

    con = db()
    rows = con.execute(
        """SELECT ca.user_id, u.first_name, u.last_name, SUM(ca.correct) score
           FROM competition_answers ca LEFT JOIN users u ON u.user_id=ca.user_id
           WHERE ca.competition_id=?
           GROUP BY ca.user_id, u.first_name, u.last_name
           ORDER BY score DESC LIMIT 10""",
        (comp["id"],),
    ).fetchall()
    con.close()

    if not rows:
        text = "🏆 Hozircha natijalar yo‘q."
    else:
        text = f"🏆 <b>Top 10 — {comp['title']}</b>\n\n"
        for i, r in enumerate(rows, 1):
            name = (r["first_name"] or "Noma’lum") + (f" {r['last_name']}" if r["last_name"] else "")
            text += f"{i}. {safe_html(name)} — <b>{r['score']}</b> ball\n"

    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Quiz boshqaruvi", callback_data="admin_quiz")]]), parse_mode="HTML")


# =========================================================
# ADMIN TEXT SHORTCUTS
# =========================================================
@dp.message(F.text == "👥 O‘quvchilar")
async def admin_users_text(message: Message):
    if not await admin_only(message):
        return
    await admin_panel(message)


@dp.message(F.text == "📊 Statistika")
async def admin_stats_text(message: Message):
    if not await admin_only(message):
        return
    await admin_panel(message)


@dp.message(F.text == "📝 Savollar")
async def admin_questions_text(message: Message):
    if not await admin_only(message):
        return
    await admin_panel(message)


@dp.message(F.text == "📚 Darsliklar")
async def admin_textbooks(message: Message):
    if not await admin_only(message):
        return
    await message.answer(
        "📚 <b>Darsliklar</b>\n\n"
        "O‘quvchi Fan → Sinf tanlaganda bot Internetdan darsliklarni avtomatik qidiradi. "
        "Tanlangan manbaning ochiq PDF/web matni olinib, AI shu manba asosida ketma-ket dars beradi.",
        parse_mode="HTML"
    )


@dp.message(F.text == "🆘 Murojaatlar")
async def admin_support_text(message: Message):
    if not await admin_only(message):
        return
    await admin_panel(message)


@dp.message(F.text == "🔙 Asosiy menyu")
async def back_main(message: Message):
    states.pop(message.from_user.id, None)
    if is_admin(message.from_user.id):
        await message.answer("🏠 Asosiy admin menyu:", reply_markup=admin_menu())
    else:
        await message.answer("🏠 Asosiy menyu:", reply_markup=main_menu())


# =========================================================
# GENERAL TEXT HANDLER
# =========================================================
@dp.message()
async def all_messages(message: Message):
    save_user(message.from_user)
    user_id = message.from_user.id
    text = message.text or ""

    if await handle_registration_input(message):
        return

    # Web search mode
    if states.get(user_id, {}).get("mode") == "web_search":
        states.pop(user_id, None)
        if DDGS is None:
            await message.answer("⚠️ Internet qidiruvi uchun ddgs kutubxonasi o‘rnatilmagan.")
            return
        results = await search_textbooks(text, 0)
        if not results:
            await message.answer("🔎 Hech narsa topilmadi.")
            return
        out = "🔎 <b>Qidiruv natijalari</b>\n\n"
        for i, (title, url, snippet) in enumerate(results[:8], 1):
            out += f"<b>{i}. {html.escape(title[:120] or url)}</b>\n{html.escape(snippet[:250])}\n"
            out += f"🔗 {html.escape(url)}\n\n"
        await message.answer(out, parse_mode="HTML", disable_web_page_preview=True)
        return

    # AI mode
    if states.get(user_id, {}).get("mode") == "ai":
        if text.startswith("/"):
            return
        answer = await ask_ai(user_id, text)

        chat_id = get_or_create_ai_chat(user_id, text)
        save_ai_message(chat_id, "user", text)
        save_ai_message(chat_id, "assistant", answer)

        await message.answer(answer)
        return

    # Support mode
    if states.get(user_id, {}).get("mode") == "support":
        states.pop(user_id, None)
        con = db()
        cur = con.execute(
            """INSERT INTO support_tickets(user_id, message, status, created_at)
               VALUES (?, ?, 'open', ?)""",
            (user_id, text, now_iso()),
        )
        ticket_id = cur.lastrowid
        con.commit()
        con.close()

        try:
            await bot.send_message(
                ADMIN_ID,
                f"🆘 <b>Yangi murojaat #{ticket_id}</b>\n\n"
                f"👤 {message.from_user.full_name}\n"
                f"🆔 <code>{user_id}</code>\n\n"
                f"💬 {text}",
                parse_mode="HTML",
            )
        except Exception as e:
            logger.warning("Admin notification failed: %s", e)

        await message.answer(
            "✅ Murojaatingiz adminga yuborildi.\nTez orada javob beriladi.",
            reply_markup=main_menu(),
        )
        return

    # Unknown text
    await message.answer(
        "👇 Menyudan kerakli bo‘limni tanlang yoki 🤖 AI yordamchiga kiring.",
        reply_markup=admin_menu() if is_admin(user_id) else main_menu(),
    )


# =========================================================
# DAILY 20:00 SCHEDULER
# =========================================================
async def daily_scheduler():
    last_started_date = None

    while True:
        try:
            await expire_active_competitions()
            await expire_active_fan_competitions()

            now = now_local()
            today = now.date().isoformat()

            if (now.hour == 20 and now.minute == 0 or now.hour > 20) and last_started_date != today:
                con = db()
                existing = con.execute("SELECT id FROM competitions WHERE scheduled_date=?", (today,)).fetchone()
                if existing:
                    con.close()
                    last_started_date = today
                    await asyncio.sleep(20)
                    continue
                qcount = con.execute("SELECT COUNT(*) c FROM quiz_questions").fetchone()["c"]
                if qcount not in {40, 60, 80, 100}:
                    con.close()
                    try:
                        await bot.send_message(
                            ADMIN_ID,
                            f"⚠️ <b>20:00 Quiz ishga tushmadi.</b>\n\n"
                            f"Bazada {qcount} ta savol bor. 40/60/80/100 ta yangi savol yaratish kerak.",
                            parse_mode="HTML",
                        )
                    except Exception:
                        logger.exception("Admin missing-question alert failed")
                    last_started_date = today
                    await asyncio.sleep(20)
                    continue

                cur = con.execute(
                    "INSERT INTO competitions(title, started_at, active, finished, scheduled_date, duration_minutes) VALUES (?, ?, 1, 0, ?, 10)",
                    (f"🏆 Bilim Markazi — Kunlik katta quiz {now:%d.%m.%Y}", now.isoformat(timespec="seconds"), today),
                )
                comp_id = cur.lastrowid
                qrows = con.execute(
                    "SELECT id FROM quiz_questions ORDER BY RANDOM()"
                ).fetchall()
                for pos, q in enumerate(qrows, 1):
                    con.execute(
                        "INSERT INTO competition_questions(competition_id, question_id, position) VALUES (?, ?, ?)",
                        (comp_id, q["id"], pos),
                    )
                con.commit()
                con.close()

                try:
                    await publish_quiz_announcement(comp_id)
                except Exception:
                    logger.exception("20:00 kanal e'loni yuborilmadi")

                last_started_date = today

            await asyncio.sleep(20)
        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("Scheduler error")
            await asyncio.sleep(20)


# =========================================================
# RENDER / UPTIMEROBOT HEALTH SERVER
# =========================================================
async def health_handler(request):
    return web.Response(text="OK", status=200)


async def start_health_server():
    app = web.Application()
    app.router.add_get("/", health_handler)
    app.router.add_get("/health", health_handler)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logger.info("Health server ishga tushdi: port %s", PORT)
    return runner


# =========================================================
# MAIN
# =========================================================
async def main():
    init_db()
    seed_questions()

    health_runner = await start_health_server()

    me = await bot.get_me()
    logger.info("Bot ishga tushdi: @%s", me.username)

    scheduler_task = asyncio.create_task(daily_scheduler())

    try:
        await dp.start_polling(bot)
    finally:
        scheduler_task.cancel()
        await health_runner.cleanup()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
