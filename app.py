# ==================== TPUBLISH PRO — GURUHLARGA XABAR YUBORUVCHI BOT ====================
# Free + 6 pullik tarif | HUMO avto-to'lov | Telethon multi-akkaunt | Render + Supabase
# To'lov sozlashda faqat nomer + kod + 2FA (API ID/Hash faqat .env da)
# =====================================================================

import os
import re
import time
import json
import asyncio
import base64
import hashlib
import secrets
import traceback
from datetime import datetime, timedelta, time as dtime
from threading import Thread, Lock
from functools import wraps

from flask import Flask, request
import telebot
from telebot import types
import psycopg2
from psycopg2.extras import RealDictCursor
from psycopg2 import pool
from dotenv import load_dotenv
import pytz

from cryptography.fernet import Fernet
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.errors import (
    SessionPasswordNeededError, PhoneCodeInvalidError, PhoneCodeExpiredError,
    FloodWaitError, ChatWriteForbiddenError, UserBannedInChannelError,
    SlowModeWaitError, ChannelPrivateError, AuthKeyError
)

load_dotenv()

# ==================== ENV ====================
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_IDS = [int(x.strip()) for x in os.getenv("ADMIN_IDS", "0").split(",") if x.strip().isdigit()]
DATABASE_URL = os.getenv("DATABASE_URL", "")
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "").rstrip("/")
WEBHOOK_PATH = os.getenv("WEBHOOK_PATH", "/webhook")
PORT = int(os.getenv("PORT", "10000"))

API_ID = int(os.getenv("API_ID", "0") or 0)
API_HASH = os.getenv("API_HASH", "")
ENCRYPT_KEY = os.getenv("ENCRYPT_KEY", "")
HUMO_BOT_USERNAME = os.getenv("HUMO_BOT_USERNAME", "HUMOcardbot")

TASHKENT = pytz.timezone("Asia/Tashkent")

# ==================== DEFAULT TARIFLAR ====================
DEFAULT_TARIFFS = [
    {"code": "free",     "name": "🆓 Free",     "price": 0,      "accounts": 1,  "groups": 2,    "daily_limit": 10,   "interval_min": 5,  "is_active": 1},
    {"code": "start",    "name": "🌱 Start",    "price": 29000,  "accounts": 1,  "groups": 10,   "daily_limit": 50,   "interval_min": 3,  "is_active": 1},
    {"code": "basic",    "name": "⭐ Basic",    "price": 49000,  "accounts": 1,  "groups": 25,   "daily_limit": 100,  "interval_min": 2,  "is_active": 1},
    {"code": "standard", "name": "🔥 Standard", "price": 79000,  "accounts": 2,  "groups": 50,   "daily_limit": 250,  "interval_min": 1,  "is_active": 1},
    {"code": "pro",      "name": "💎 Pro",      "price": 119000, "accounts": 3,  "groups": 100,  "daily_limit": 500,  "interval_min": 1,  "is_active": 1},
    {"code": "premium",  "name": "👑 Premium",  "price": 179000, "accounts": 5,  "groups": 200,  "daily_limit": 1000, "interval_min": 1,  "is_active": 1},
    {"code": "vip",      "name": "🚀 VIP",      "price": 299000, "accounts": 10, "groups": 9999, "daily_limit": 3000, "interval_min": 1,  "is_active": 1},
]

DEFAULT_SETTINGS = {
    "payment_time_minutes": 15,
    "card_number": os.getenv("CARD_NUMBER", "8600123456789012"),
    "card_owner": os.getenv("CARD_OWNER", "HUMO CARD"),
    "trial_days": 3,
    "remind_days_before": 3,
    "bot_active": True,
    "maintenance": False,
    "welcome_text": (
        "👋 <b>Assalomu alaykum!</b>\n\n"
        "🚀 <b>Tpublish Pro</b> — guruhlarga avtomatik xabar yuboruvchi bot.\n\n"
        "✅ Akkaunt ulang\n"
        "✅ Guruhlarni tanlang\n"
        "✅ Xabar yozing\n"
        "✅ Boshlash tugmasini bosing\n\n"
        "Quyidagi menyudan foydalaning 👇"
    ),
    "force_subscribe": False,
    "force_channel": "",
    "referral_bonus_days": 3,
    "referral_bonus_money": 5000,   # do'st kelganda refererga beriladigan pul (so'm)
    "referral_percent": 10,         # do'st to'lov qilsa % bonus
    "min_interval_seconds": 30,
    # Pastki reklama (xabar tagida)
    "ad_enabled": True,
    "ad_text": "",  # bo'sh = bot username avtomatik: 🤖 @BotUsername orqali yuborildi
    "ad_tariffs": "free,start,basic",  # vergul bilan: shu tariflarda reklama chiqadi
}

# ==================== GLOBAL ====================
bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML", threaded=True)
app = Flask(__name__)

_db_pool = None
_settings_cache = {}
_settings_cache_time = 0
_SETTINGS_TTL = 30
_admin_cache = set(ADMIN_IDS)
_recent_humo = {}
_user_states = {}  # user_id -> {step, data}
_user_states_lock = Lock()
_running_clients = {}  # account_id -> TelegramClient
_clients_lock = Lock()
_sender_running = False
_humo_client = None
_fernet = None

if ENCRYPT_KEY:
    try:
        _fernet = Fernet(ENCRYPT_KEY.encode() if isinstance(ENCRYPT_KEY, str) else ENCRYPT_KEY)
    except Exception:
        try:
            _fernet = Fernet(base64.urlsafe_b64encode(hashlib.sha256(ENCRYPT_KEY.encode()).digest()))
        except Exception as e:
            print(f"⚠️ ENCRYPT_KEY xato: {e}")

def now_tz():
    return datetime.now(TASHKENT)

def now_str():
    return now_tz().strftime("%Y-%m-%d %H:%M:%S")

def today_str():
    return now_tz().strftime("%Y-%m-%d")

def format_money(n):
    try:
        n = int(float(n))
        return f"{n:,}".replace(",", " ") + " so'm"
    except Exception:
        return str(n)


_bot_username_cache = None

def get_bot_username():
    """Bot username ni bir marta olib keshlaydi"""
    global _bot_username_cache
    if _bot_username_cache:
        return _bot_username_cache
    try:
        me = bot.get_me()
        _bot_username_cache = me.username or ""
    except Exception:
        _bot_username_cache = ""
    return _bot_username_cache


def append_ad_footer(text: str, tariff_code: str) -> str:
    """Agar tarif reklamali ro'yxatda bo'lsa, xabar tagiga reklama qo'shadi.
    ad_text bo'sh bo'lsa — bot username avtomatik qo'yiladi.
    """
    if not get_setting("ad_enabled", True):
        return text
    ad_tariffs = str(get_setting("ad_tariffs", "free,start,basic") or "")
    allowed = {x.strip().lower() for x in ad_tariffs.split(",") if x.strip()}
    if (tariff_code or "free").lower() not in allowed:
        return text
    ad = str(get_setting("ad_text", "") or "").strip()
    if not ad:
        uname = get_bot_username()
        if uname:
            ad = f"🤖 @{uname} orqali yuborildi"
        else:
            return text
    return (text or "").rstrip() + "\n\n" + ad

# ==================== ENCRYPT / DECRYPT SESSION ====================
def encrypt_session(s: str) -> str:
    if not _fernet or not s:
        return s or ""
    return _fernet.encrypt(s.encode()).decode()

def decrypt_session(s: str) -> str:
    if not _fernet or not s:
        return s or ""
    try:
        return _fernet.decrypt(s.encode()).decode()
    except Exception:
        return ""

# ==================== DB ====================
def init_pool():
    global _db_pool
    if _db_pool is None and DATABASE_URL:
        _db_pool = pool.ThreadedConnectionPool(
            minconn=1, maxconn=12, dsn=DATABASE_URL, cursor_factory=RealDictCursor
        )
        print("✅ DB pool tayyor")

def get_db():
    if _db_pool is None:
        init_pool()
    conn = _db_pool.getconn()
    conn.autocommit = False
    return conn

def put_db(conn):
    if not conn:
        return
    try:
        _db_pool.putconn(conn)
    except Exception:
        try:
            conn.close()
        except Exception:
            pass

def init_db():
    conn = get_db()
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id BIGINT PRIMARY KEY,
            username TEXT,
            full_name TEXT,
            lang TEXT DEFAULT 'uz',
            balance REAL DEFAULT 0,
            referred_by BIGINT DEFAULT 0,
            referrals_count INTEGER DEFAULT 0,
            is_blocked INTEGER DEFAULT 0,
            trial_used INTEGER DEFAULT 0,
            joined_at TEXT,
            last_active TEXT
        )
    """)
    try:
        c.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS balance REAL DEFAULT 0")
    except Exception:
        pass

    c.execute("""
        CREATE TABLE IF NOT EXISTS tariffs (
            id SERIAL PRIMARY KEY,
            code TEXT UNIQUE,
            name TEXT,
            price REAL DEFAULT 0,
            accounts INTEGER DEFAULT 1,
            groups_limit INTEGER DEFAULT 2,
            daily_limit INTEGER DEFAULT 10,
            interval_min INTEGER DEFAULT 5,
            duration_days INTEGER DEFAULT 30,
            is_active INTEGER DEFAULT 1,
            sort_order INTEGER DEFAULT 0
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS subscriptions (
            id SERIAL PRIMARY KEY,
            user_id BIGINT,
            tariff_code TEXT,
            starts_at TEXT,
            expires_at TEXT,
            is_active INTEGER DEFAULT 1,
            source TEXT DEFAULT 'payment',
            created_at TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS accounts (
            id SERIAL PRIMARY KEY,
            user_id BIGINT,
            phone TEXT,
            session_enc TEXT,
            status TEXT DEFAULT 'active',
            last_error TEXT DEFAULT '',
            daily_sent INTEGER DEFAULT 0,
            last_reset TEXT,
            is_running INTEGER DEFAULT 0,
            work_from TEXT DEFAULT '09:00',
            work_to TEXT DEFAULT '21:00',
            interval_sec INTEGER DEFAULT 300,
            created_at TEXT,
            updated_at TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS groups (
            id SERIAL PRIMARY KEY,
            account_id INTEGER,
            user_id BIGINT,
            chat_id TEXT,
            title TEXT,
            folder TEXT DEFAULT 'Asosiy',
            is_active INTEGER DEFAULT 1,
            last_sent TEXT,
            error_count INTEGER DEFAULT 0,
            created_at TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id SERIAL PRIMARY KEY,
            user_id BIGINT,
            account_id INTEGER DEFAULT 0,
            title TEXT DEFAULT 'Xabar',
            text TEXT,
            media_type TEXT DEFAULT 'text',
            media_file_id TEXT DEFAULT '',
            variables TEXT DEFAULT '',
            is_active INTEGER DEFAULT 1,
            sort_order INTEGER DEFAULT 0,
            created_at TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS send_log (
            id SERIAL PRIMARY KEY,
            user_id BIGINT,
            account_id INTEGER,
            group_id INTEGER,
            chat_id TEXT,
            message_id INTEGER,
            status TEXT,
            error TEXT DEFAULT '',
            sent_at TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS daily_usage (
            id SERIAL PRIMARY KEY,
            user_id BIGINT,
            account_id INTEGER,
            day TEXT,
            sent_count INTEGER DEFAULT 0,
            UNIQUE(user_id, account_id, day)
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS payments (
            id SERIAL PRIMARY KEY,
            user_id BIGINT,
            tariff_code TEXT,
            amount REAL,
            unique_amount REAL,
            status TEXT DEFAULT 'pending',
            method TEXT DEFAULT 'humo',
            created_at TEXT,
            paid_at TEXT,
            expires_at TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS promo_codes (
            id SERIAL PRIMARY KEY,
            code TEXT UNIQUE,
            tariff_code TEXT,
            days INTEGER DEFAULT 30,
            max_uses INTEGER DEFAULT 1,
            used_count INTEGER DEFAULT 0,
            is_active INTEGER DEFAULT 1,
            created_at TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS tickets (
            id SERIAL PRIMARY KEY,
            user_id BIGINT,
            subject TEXT,
            message TEXT,
            status TEXT DEFAULT 'open',
            admin_reply TEXT DEFAULT '',
            created_at TEXT,
            replied_at TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS admins (
            user_id BIGINT PRIMARY KEY
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS admin_log (
            id SERIAL PRIMARY KEY,
            admin_id BIGINT,
            action TEXT,
            details TEXT,
            created_at TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS humo_session (
            id INTEGER PRIMARY KEY DEFAULT 1,
            session_enc TEXT,
            phone TEXT,
            status TEXT DEFAULT 'disconnected',
            updated_at TEXT
        )
    """)

    # Default tariffs
    for i, t in enumerate(DEFAULT_TARIFFS):
        c.execute("""
            INSERT INTO tariffs (code, name, price, accounts, groups_limit, daily_limit, interval_min, duration_days, is_active, sort_order)
            VALUES (%s,%s,%s,%s,%s,%s,%s,30,%s,%s)
            ON CONFLICT (code) DO NOTHING
        """, (t["code"], t["name"], t["price"], t["accounts"], t["groups"], t["daily_limit"], t["interval_min"], t["is_active"], i))

    for k, v in DEFAULT_SETTINGS.items():
        c.execute("INSERT INTO settings (key, value) VALUES (%s, %s) ON CONFLICT (key) DO NOTHING", (k, str(v)))

    for a in ADMIN_IDS:
        c.execute("INSERT INTO admins (user_id) VALUES (%s) ON CONFLICT DO NOTHING", (a,))

    conn.commit()
    put_db(conn)
    print("✅ DB jadvallari tayyor")

# ==================== SETTINGS ====================
def _pv(val):
    if val is None:
        return None
    if str(val).lower() in ("true", "false"):
        return str(val).lower() == "true"
    try:
        return float(val) if "." in str(val) else int(val)
    except Exception:
        return val

def get_setting(key, default=None):
    global _settings_cache, _settings_cache_time
    if time.time() - _settings_cache_time > _SETTINGS_TTL or not _settings_cache:
        conn = get_db()
        try:
            c = conn.cursor()
            c.execute("SELECT key, value FROM settings")
            _settings_cache = {r["key"]: r["value"] for r in c.fetchall()}
            _settings_cache_time = time.time()
        finally:
            put_db(conn)
    v = _settings_cache.get(key)
    return default if v is None else _pv(v)

def set_setting(key, value):
    global _settings_cache, _settings_cache_time
    conn = get_db()
    try:
        c = conn.cursor()
        c.execute(
            "INSERT INTO settings (key, value) VALUES (%s, %s) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
            (key, str(value))
        )
        conn.commit()
        _settings_cache[key] = str(value)
        _settings_cache_time = time.time()
    finally:
        put_db(conn)

def is_admin(uid):
    return uid in _admin_cache or uid in ADMIN_IDS

def log_admin(admin_id, action, details=""):
    conn = get_db()
    try:
        c = conn.cursor()
        c.execute(
            "INSERT INTO admin_log (admin_id, action, details, created_at) VALUES (%s,%s,%s,%s)",
            (admin_id, action, details, now_str())
        )
        conn.commit()
    finally:
        put_db(conn)

# ==================== USER HELPERS ====================
def ensure_user(uid, username=None, full_name=None):
    conn = get_db()
    try:
        c = conn.cursor()
        c.execute("SELECT user_id FROM users WHERE user_id = %s", (uid,))
        if not c.fetchone():
            c.execute(
                "INSERT INTO users (user_id, username, full_name, joined_at, last_active) VALUES (%s,%s,%s,%s,%s)",
                (uid, username or "", full_name or "", now_str(), now_str())
            )
        else:
            c.execute(
                "UPDATE users SET username=%s, full_name=%s, last_active=%s WHERE user_id=%s",
                (username or "", full_name or "", now_str(), uid)
            )
        conn.commit()
    finally:
        put_db(conn)

def get_user(uid):
    conn = get_db()
    try:
        c = conn.cursor()
        c.execute("SELECT * FROM users WHERE user_id = %s", (uid,))
        return c.fetchone()
    finally:
        put_db(conn)

def get_active_subscription(uid):
    conn = get_db()
    try:
        c = conn.cursor()
        c.execute("""
            SELECT s.*, t.name as tariff_name, t.accounts, t.groups_limit, t.daily_limit, t.interval_min, t.price
            FROM subscriptions s
            JOIN tariffs t ON t.code = s.tariff_code
            WHERE s.user_id = %s AND s.is_active = 1 AND s.expires_at > %s
            ORDER BY s.expires_at DESC LIMIT 1
        """, (uid, now_str()))
        return c.fetchone()
    finally:
        put_db(conn)

def get_tariff(code):
    conn = get_db()
    try:
        c = conn.cursor()
        c.execute("SELECT * FROM tariffs WHERE code = %s", (code,))
        return c.fetchone()
    finally:
        put_db(conn)

def get_all_tariffs(active_only=True):
    conn = get_db()
    try:
        c = conn.cursor()
        if active_only:
            c.execute("SELECT * FROM tariffs WHERE is_active = 1 ORDER BY sort_order, price")
        else:
            c.execute("SELECT * FROM tariffs ORDER BY sort_order, price")
        return c.fetchall()
    finally:
        put_db(conn)

def get_user_limits(uid):
    """Foydalanuvchi limitlarini qaytaradi (tarif yoki free)"""
    sub = get_active_subscription(uid)
    if sub:
        return {
            "tariff_code": sub["tariff_code"],
            "tariff_name": sub["tariff_name"],
            "accounts": sub["accounts"],
            "groups": sub["groups_limit"],
            "daily_limit": sub["daily_limit"],
            "interval_min": sub["interval_min"],
            "expires_at": sub["expires_at"],
            "is_paid": True,
        }
    # Free
    free = get_tariff("free")
    return {
        "tariff_code": "free",
        "tariff_name": free["name"] if free else "🆓 Free",
        "accounts": free["accounts"] if free else 1,
        "groups": free["groups_limit"] if free else 2,
        "daily_limit": free["daily_limit"] if free else 10,
        "interval_min": free["interval_min"] if free else 5,
        "expires_at": None,
        "is_paid": False,
    }

def activate_subscription(uid, tariff_code, days=None, source="payment"):
    t = get_tariff(tariff_code)
    if not t:
        return False
    days = days or t.get("duration_days", 30)
    starts = now_str()
    expires = (now_tz() + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    conn = get_db()
    try:
        c = conn.cursor()
        c.execute("UPDATE subscriptions SET is_active = 0 WHERE user_id = %s AND is_active = 1", (uid,))
        c.execute("""
            INSERT INTO subscriptions (user_id, tariff_code, starts_at, expires_at, is_active, source, created_at)
            VALUES (%s,%s,%s,%s,1,%s,%s)
        """, (uid, tariff_code, starts, expires, source, now_str()))
        conn.commit()
        return True
    finally:
        put_db(conn)

# ==================== STATE MACHINE ====================
def set_state(uid, step, data=None):
    with _user_states_lock:
        _user_states[uid] = {"step": step, "data": data or {}}

def get_state(uid):
    with _user_states_lock:
        return _user_states.get(uid)

def clear_state(uid):
    with _user_states_lock:
        _user_states.pop(uid, None)

# ==================== KEYBOARDS ====================
def kb_main(uid=None):
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.row("📱 Akkauntlarim", "👥 Guruhlarim")
    kb.row("✉️ Xabarlarim", "⏰ Rejalashtirish")
    kb.row("▶️ Boshlash", "⏹ To'xtatish")
    kb.row("📊 Statistika", "💎 Tariflar")
    kb.row("🎁 Referal", "💵 Balans")
    kb.row("📖 Qo'llanma", "🆘 Yordam")
    kb.row("⚙️ Sozlamalar")
    if uid and is_admin(uid):
        kb.row("🛠 Admin panel")
    return kb

def kb_back():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(types.KeyboardButton("🔙 Orqaga"))
    return kb

def kb_cancel():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(types.KeyboardButton("❌ Bekor"), types.KeyboardButton("🔙 Orqaga"))
    return kb

def kb_admin():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add("📊 Dashboard", "👥 Foydalanuvchilar")
    kb.add("💎 Tariflar sozlash", "💳 To'lov sozlash")
    kb.add("🧾 To'lovlar", "📱 Akkauntlar monitoring")
    kb.add("📢 Ommaviy xabar", "🎟 Promokod")
    kb.add("🔔 Majburiy obuna", "🆘 Murojaatlar")
    kb.add("📣 Pastki reklama", "👮 Adminlar")
    kb.add("⚙️ Bot sozlamalari", "💾 Zaxira")
    kb.add("🔙 Orqaga")
    return kb

def kb_tariffs_inline():
    tariffs = get_all_tariffs()
    kb = types.InlineKeyboardMarkup(row_width=1)
    for t in tariffs:
        if t["code"] == "free":
            continue
        price = format_money(t["price"])
        kb.add(types.InlineKeyboardButton(
            f"{t['name']} — {price}",
            callback_data=f"buy_{t['code']}"
        ))
    kb.add(types.InlineKeyboardButton("🎁 Promokod kiritish", callback_data="promo_enter"))
    return kb

# ==================== HUMO PAYMENT ====================
def get_unique_amount(desired):
    """Noyob summa yaratish (desired + random 1-99)"""
    conn = get_db()
    try:
        c = conn.cursor()
        for _ in range(50):
            extra = secrets.randbelow(99) + 1
            unique = float(desired) + extra
            c.execute(
                "SELECT id FROM payments WHERE status='pending' AND unique_amount=%s AND expires_at > %s",
                (unique, now_str())
            )
            if not c.fetchone():
                return unique
        return float(desired) + secrets.randbelow(900) + 100
    finally:
        put_db(conn)

def create_payment(uid, tariff_code, amount):
    unique = get_unique_amount(amount)
    mins = int(get_setting("payment_time_minutes", 15))
    expires = (now_tz() + timedelta(minutes=mins)).strftime("%Y-%m-%d %H:%M:%S")
    conn = get_db()
    try:
        c = conn.cursor()
        c.execute("""
            INSERT INTO payments (user_id, tariff_code, amount, unique_amount, status, method, created_at, expires_at)
            VALUES (%s,%s,%s,%s,'pending','humo',%s,%s) RETURNING id
        """, (uid, tariff_code, amount, unique, now_str(), expires))
        pid = c.fetchone()["id"]
        conn.commit()
        return pid, unique, expires
    finally:
        put_db(conn)

def parse_humo_amount(text: str):
    """HUMO xabaridan summani ajratib olish"""
    if not text:
        return None
    text = text.replace("\u00a0", " ").replace(",", " ")
    # Turli formatlar: 29 047,00 | 29047 | 29 047 so'm
    patterns = [
        r"(?:summa|amount|tushdi|keldi|o'tkazma)[^\d]*([\d\s]+(?:[.,]\d{2})?)",
        r"([\d\s]{4,}(?:[.,]\d{2})?)\s*(?:so['ʻ']?m|UZS|сум)",
        r"(?:^|\s)([\d]{1,3}(?:\s\d{3})+(?:[.,]\d{2})?)(?:\s|$)",
        r"(?:^|\s)(\d{4,}(?:[.,]\d{2})?)(?:\s|$)",
    ]
    for p in patterns:
        m = re.search(p, text, re.IGNORECASE)
        if m:
            raw = m.group(1).replace(" ", "").replace(",", ".")
            try:
                val = float(raw)
                if val >= 1000:
                    return round(val)
            except Exception:
                pass
    # Fallback: barcha raqamlarni yig'ish
    nums = re.findall(r"\d[\d\s]*\d|\d{4,}", text)
    for n in nums:
        try:
            val = float(n.replace(" ", ""))
            if 1000 <= val <= 10_000_000:
                return round(val)
        except Exception:
            pass
    return None

def process_humo(amount, src="HUMO"):
    """Avto-tasdiqlash"""
    global _recent_humo
    now_ts = time.time()
    amount = round(float(amount))

    if amount in _recent_humo and now_ts - _recent_humo[amount] < 30:
        print(f"⏭ Takroriy HUMO: {amount}")
        return False
    _recent_humo[amount] = now_ts
    for k in list(_recent_humo.keys()):
        if now_ts - _recent_humo[k] > 300:
            del _recent_humo[k]

    conn = get_db()
    try:
        c = conn.cursor()
        # Muddati o'tganlarni bekor qilish
        c.execute(
            "UPDATE payments SET status='expired' WHERE status='pending' AND expires_at < %s",
            (now_str(),)
        )
        # Aniq moslik
        c.execute("""
            SELECT id, user_id, tariff_code, unique_amount FROM payments
            WHERE status='pending' AND unique_amount = %s
            ORDER BY created_at LIMIT 1
        """, (float(amount),))
        row = c.fetchone()
        if not row:
            # ±1 farq
            c.execute("""
                SELECT id, user_id, tariff_code, unique_amount FROM payments
                WHERE status='pending' AND unique_amount BETWEEN %s AND %s
                ORDER BY created_at LIMIT 1
            """, (amount - 1, amount + 1))
            row = c.fetchone()
        if not row:
            print(f"❌ HUMO summa topilmadi: {amount}")
            conn.commit()
            return False

        pid = row["id"]
        uid = row["user_id"]
        tcode = row["tariff_code"]
        unique = row["unique_amount"]

        c.execute(
            "UPDATE payments SET status='paid', paid_at=%s WHERE id=%s",
            (now_str(), pid)
        )
        conn.commit()
    finally:
        put_db(conn)

    # Tarifni yoqish
    ok = activate_subscription(uid, tcode, source="humo")

    # Referalga % bonus
    try:
        u = get_user(uid)
        if u and u.get("referred_by"):
            ref_id = u["referred_by"]
            pct = float(get_setting("referral_percent", 10))
            if pct > 0:
                bonus = round(float(unique) * pct / 100)
                conn2 = get_db()
                try:
                    c2 = conn2.cursor()
                    c2.execute("UPDATE users SET balance = balance + %s WHERE user_id=%s", (bonus, ref_id))
                    conn2.commit()
                finally:
                    put_db(conn2)
                try:
                    bot.send_message(
                        ref_id,
                        f"💰 Do'stingiz to'lov qildi!\n"
                        f"Sizga <b>{format_money(bonus)}</b> ({pct}%) bonus qo'shildi."
                    )
                except Exception:
                    pass
    except Exception as e:
        print(f"Ref percent error: {e}")

    try:
        bot.send_message(
            uid,
            f"✅ <b>To'lov qabul qilindi!</b>\n\n"
            f"💰 Summa: <b>{format_money(unique)}</b>\n"
            f"💎 Tarif: <b>{get_tariff(tcode)['name'] if get_tariff(tcode) else tcode}</b>\n\n"
            f"Tarifingiz faollashtirildi. Foydalanishni boshlashingiz mumkin! 🚀",
            reply_markup=kb_main(uid)
        )
    except Exception:
        pass
    for aid in ADMIN_IDS:
        try:
            bot.send_message(
                aid,
                f"✅ Avto-to'lov\n👤 {uid}\n💰 {format_money(unique)}\n💎 {tcode}"
            )
        except Exception:
            pass
    print(f"✅ [{src}] Avto-to'lov OK: user={uid} amount={unique} pay=#{pid}")
    return True

# ==================== TELETHON HUMO LISTENER ====================
def start_humo_listener():
    """@HUMOcardbot xabarlarini kuzatish"""
    global _humo_client
    if not API_ID or not API_HASH:
        print("⚠️ API_ID/API_HASH yo'q — HUMO listener ishlamaydi")
        return

    conn = get_db()
    try:
        c = conn.cursor()
        c.execute("SELECT session_enc, phone, status FROM humo_session WHERE id=1")
        row = c.fetchone()
    finally:
        put_db(conn)

    if not row or not row["session_enc"] or row["status"] != "connected":
        print("⚠️ HUMO sessiya ulanmagan")
        return

    session_str = decrypt_session(row["session_enc"])
    if not session_str:
        print("⚠️ HUMO sessiya dekodlanmadi")
        return

    async def _run():
        global _humo_client
        client = TelegramClient(StringSession(session_str), API_ID, API_HASH)
        await client.connect()
        if not await client.is_user_authorized():
            print("❌ HUMO sessiya yaroqsiz")
            return
        _humo_client = client
        print("✅ HUMO Telethon listener ishga tushdi")

        @client.on(events.NewMessage(from_users=HUMO_BOT_USERNAME))
        async def handler(event):
            text = event.message.message or ""
            amount = parse_humo_amount(text)
            if amount:
                process_humo(amount, src="HUMO-listener")

        await client.run_until_disconnected()

    def _thread():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(_run())
        except Exception as e:
            print(f"HUMO listener xato: {e}")

    Thread(target=_thread, daemon=True).start()

# ==================== USER ACCOUNT (TELETHON) ====================
async def login_phone_step(phone):
    """Yangi sessiya boshlash — kod so'rash"""
    client = TelegramClient(StringSession(), API_ID, API_HASH)
    await client.connect()
    result = await client.send_code_request(phone)
    return client, result.phone_code_hash

# ==================== BOT HANDLERS ====================
@bot.message_handler(commands=["start"])
def cmd_start(m):
    uid = m.from_user.id
    ensure_user(uid, m.from_user.username, m.from_user.full_name)
    clear_state(uid)

    # Referal — pul + kun bonus
    parts = (m.text or "").split()
    if len(parts) > 1 and parts[1].startswith("ref"):
        try:
            ref_id = int(parts[1][3:])
            if ref_id != uid:
                conn = get_db()
                try:
                    c = conn.cursor()
                    c.execute("SELECT referred_by FROM users WHERE user_id=%s", (uid,))
                    u = c.fetchone()
                    if u and not u.get("referred_by"):
                        c.execute("UPDATE users SET referred_by=%s WHERE user_id=%s", (ref_id, uid))
                        c.execute("UPDATE users SET referrals_count = referrals_count + 1 WHERE user_id=%s", (ref_id,))
                        bonus = float(get_setting("referral_bonus_money", 5000))
                        if bonus > 0:
                            c.execute("UPDATE users SET balance = balance + %s WHERE user_id=%s", (bonus, ref_id))
                        conn.commit()
                        try:
                            bot.send_message(
                                ref_id,
                                f"🎉 Yangi do'st qo'shildi!\n"
                                f"💰 Sizga <b>{format_money(bonus)}</b> bonus berildi.\n"
                                f"👥 Jami referallar: ko'ring 🎁 Referal bo'limida."
                            )
                        except Exception:
                            pass
                finally:
                    put_db(conn)
        except Exception:
            pass

    # Trial
    u = get_user(uid)
    if u and not u["trial_used"]:
        days = int(get_setting("trial_days", 3))
        activate_subscription(uid, "start", days=days, source="trial")
        conn = get_db()
        try:
            c = conn.cursor()
            c.execute("UPDATE users SET trial_used=1 WHERE user_id=%s", (uid,))
            conn.commit()
        finally:
            put_db(conn)
        bot.send_message(
            uid,
            f"🎁 Sizga <b>{days} kunlik Start tarif</b> sinov sifatida berildi!\n"
            f"Muddat tugagach Free tarifga o'tasiz.",
            reply_markup=kb_main(uid)
        )
        return

    text = get_setting("welcome_text", DEFAULT_SETTINGS["welcome_text"])
    limits = get_user_limits(uid)
    text += f"\n\n📊 Sizning tarif: <b>{limits['tariff_name']}</b>"
    if limits["expires_at"]:
        text += f"\n⏳ Muddat: {limits['expires_at'][:10]}"
    text += f"\n📱 Akkaunt: {limits['accounts']} | 👥 Guruh: {limits['groups']} | ✉️ Kuniga: {limits['daily_limit']}"
    bot.send_message(uid, text, reply_markup=kb_main(uid))

@bot.message_handler(commands=["admin"])
def cmd_admin(m):
    if not is_admin(m.from_user.id):
        return
    bot.send_message(m.chat.id, "🛠 Admin panel", reply_markup=kb_admin())

# ---------- ASOSIY MENYU ----------
@bot.message_handler(func=lambda m: m.text == "🔙 Orqaga")
def msg_back(m):
    clear_state(m.from_user.id)
    bot.send_message(m.chat.id, "Asosiy menyu", reply_markup=kb_main(m.from_user.id))

@bot.message_handler(func=lambda m: m.text == "💎 Tariflar")
def msg_tariffs(m):
    uid = m.from_user.id
    limits = get_user_limits(uid)
    text = f"💎 <b>Tariflar</b>\n\nHozirgi: <b>{limits['tariff_name']}</b>\n"
    if limits["expires_at"]:
        text += f"Muddat: {limits['expires_at'][:16]}\n"
    text += f"\n📱 {limits['accounts']} akkaunt | 👥 {limits['groups']} guruh | ✉️ {limits['daily_limit']}/kun\n\n"
    text += "Quyidagi tariflardan birini tanlang:"
    bot.send_message(uid, text, reply_markup=kb_tariffs_inline())

@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("buy_"))
def cb_buy(c):
    uid = c.from_user.id
    code = c.data[4:]
    t = get_tariff(code)
    if not t or not t["is_active"]:
        bot.answer_callback_query(c.id, "Tarif topilmadi")
        return
    if t["price"] <= 0:
        bot.answer_callback_query(c.id, "Bu bepul tarif")
        return

    pid, unique, expires = create_payment(uid, code, t["price"])
    card = get_setting("card_number", "8600...")
    owner = get_setting("card_owner", "")
    mins = get_setting("payment_time_minutes", 15)

    text = (
        f"💳 <b>To'lov</b>\n\n"
        f"Tarif: <b>{t['name']}</b>\n"
        f"Asosiy narx: {format_money(t['price'])}\n"
        f"🔸 <b>To'lov summasi: {format_money(unique)}</b>\n\n"
        f"📌 <b>Muhim:</b> aynan shu summani o'tkazing (oxirgi raqamlar farq qiladi)!\n\n"
        f"💳 Karta: <code>{card}</code>\n"
        f"👤 Egasi: {owner}\n\n"
        f"⏱ Vaqt: {mins} daqiqa ({expires[11:16]} gacha)\n\n"
        f"To'lov qilgach bot avtomatik tasdiqlaydi."
    )
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("❌ Bekor qilish", callback_data=f"cancelpay_{pid}"))
    bot.edit_message_text(text, c.message.chat.id, c.message.message_id, reply_markup=kb)
    bot.answer_callback_query(c.id)

@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("cancelpay_"))
def cb_cancel_pay(c):
    pid = int(c.data.split("_")[1])
    conn = get_db()
    try:
        c_db = conn.cursor()
        c_db.execute("UPDATE payments SET status='cancelled' WHERE id=%s AND user_id=%s AND status='pending'",
                     (pid, c.from_user.id))
        conn.commit()
    finally:
        put_db(conn)
    bot.edit_message_text("❌ To'lov bekor qilindi.", c.message.chat.id, c.message.message_id)
    bot.answer_callback_query(c.id)

# ---------- AKKAUNTLAR ----------
@bot.message_handler(func=lambda m: m.text == "📱 Akkauntlarim")
def msg_accounts(m):
    uid = m.from_user.id
    limits = get_user_limits(uid)
    conn = get_db()
    try:
        c = conn.cursor()
        c.execute("SELECT * FROM accounts WHERE user_id=%s ORDER BY id", (uid,))
        accs = c.fetchall()
    finally:
        put_db(conn)

    text = f"📱 <b>Akkauntlarim</b> ({len(accs)}/{limits['accounts']})\n\n"
    if not accs:
        text += "Hali akkaunt ulanmagan.\n➕ tugmasi orqali qo'shing."
    else:
        for a in accs:
            st = {"active": "🟢", "error": "🔴", "limit": "⚠️"}.get(a["status"], "⚪")
            run = "▶️" if a["is_running"] else "⏸"
            text += f"{st}{run} <b>{a['phone']}</b> (#{a['id']})\n"
            if a["last_error"]:
                text += f"   ⚠️ {a['last_error'][:40]}\n"
            text += f"   ✉️ bugun: {a['daily_sent']} | interval: {a['interval_sec']}s\n"

    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add("➕ Yangi akkaunt")
    if accs:
        kb.add("🔄 Qayta ulash", "❌ Uzish")
        kb.add("⚙️ Akkaunt sozlamalari")
    kb.add("🔙 Orqaga")
    bot.send_message(uid, text, reply_markup=kb)

@bot.message_handler(func=lambda m: m.text == "➕ Yangi akkaunt")
def msg_add_account(m):
    uid = m.from_user.id
    limits = get_user_limits(uid)
    conn = get_db()
    try:
        c = conn.cursor()
        c.execute("SELECT COUNT(*) as n FROM accounts WHERE user_id=%s", (uid,))
        n = c.fetchone()["n"]
    finally:
        put_db(conn)
    if n >= limits["accounts"]:
        bot.send_message(uid, f"❌ Limit: {limits['accounts']} akkaunt. Tarifni yangilang.", reply_markup=kb_main(uid))
        return
    if not API_ID or not API_HASH:
        bot.send_message(uid, "⚠️ Serverda API_ID/API_HASH sozlanmagan. Admin bilan bog'laning.")
        return
    set_state(uid, "acc_phone")
    bot.send_message(
        uid,
        "📱 <b>Yangi akkaunt ulash</b>\n\n"
        "Telefon raqamni xalqaro formatda yuboring:\n"
        "<code>+998901234567</code>\n\n"
        "API ID/Hash so'ralmaydi — faqat raqam, kod va 2FA.",
        reply_markup=kb_cancel()
    )

@bot.message_handler(func=lambda m: get_state(m.from_user.id) and get_state(m.from_user.id)["step"] == "acc_phone")
def proc_acc_phone(m):
    uid = m.from_user.id
    if m.text in ("❌ Bekor", "🔙 Orqaga"):
        clear_state(uid)
        bot.send_message(uid, "Bekor.", reply_markup=kb_main(uid))
        return
    phone = m.text.strip().replace(" ", "")
    if not re.match(r"^\+\d{10,15}$", phone):
        bot.send_message(uid, "❌ Noto'g'ri format. Masalan: +998901234567")
        return

    def _do():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            client, phone_code_hash = loop.run_until_complete(login_phone_step(phone))
            set_state(uid, "acc_code", {
                "phone": phone,
                "phone_code_hash": phone_code_hash,
                "session": client.session.save()
            })
            bot.send_message(uid, f"✅ Kod yuborildi: <b>{phone}</b>\n\nTelegramdan kelgan kodni yuboring:", reply_markup=kb_cancel())
        except Exception as e:
            clear_state(uid)
            bot.send_message(uid, f"❌ Xato: {e}", reply_markup=kb_main(uid))
        finally:
            loop.close()

    Thread(target=_do, daemon=True).start()

@bot.message_handler(func=lambda m: get_state(m.from_user.id) and get_state(m.from_user.id)["step"] == "acc_code")
def proc_acc_code(m):
    uid = m.from_user.id
    st = get_state(uid)
    if m.text in ("❌ Bekor", "🔙 Orqaga"):
        clear_state(uid)
        bot.send_message(uid, "Bekor.", reply_markup=kb_main(uid))
        return
    code = m.text.strip().replace(" ", "")
    phone = st["data"]["phone"]
    phone_code_hash = st["data"]["phone_code_hash"]
    session_str = st["data"]["session"]

    def _do():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            client = TelegramClient(StringSession(session_str), API_ID, API_HASH)
            loop.run_until_complete(client.connect())
            try:
                loop.run_until_complete(client.sign_in(phone, code, phone_code_hash=phone_code_hash))
            except SessionPasswordNeededError:
                set_state(uid, "acc_2fa", {"phone": phone, "session": client.session.save()})
                bot.send_message(uid, "🔐 2FA parolni yuboring:", reply_markup=kb_cancel())
                return
            except (PhoneCodeInvalidError, PhoneCodeExpiredError) as e:
                clear_state(uid)
                bot.send_message(uid, f"❌ Kod xato yoki muddati o'tgan: {e}", reply_markup=kb_main(uid))
                return

            # Muvaffaqiyat
            sess = client.session.save()
            enc = encrypt_session(sess)
            conn = get_db()
            try:
                c = conn.cursor()
                c.execute("""
                    INSERT INTO accounts (user_id, phone, session_enc, status, last_reset, created_at, updated_at)
                    VALUES (%s,%s,%s,'active',%s,%s,%s)
                """, (uid, phone, enc, today_str(), now_str(), now_str()))
                conn.commit()
            finally:
                put_db(conn)
            clear_state(uid)
            bot.send_message(uid, f"✅ Akkaunt ulandi: <b>{phone}</b>", reply_markup=kb_main(uid))
            loop.run_until_complete(client.disconnect())
        except Exception as e:
            clear_state(uid)
            bot.send_message(uid, f"❌ Xato: {e}", reply_markup=kb_main(uid))
        finally:
            loop.close()

    Thread(target=_do, daemon=True).start()

@bot.message_handler(func=lambda m: get_state(m.from_user.id) and get_state(m.from_user.id)["step"] == "acc_2fa")
def proc_acc_2fa(m):
    uid = m.from_user.id
    st = get_state(uid)
    if m.text in ("❌ Bekor", "🔙 Orqaga"):
        clear_state(uid)
        bot.send_message(uid, "Bekor.", reply_markup=kb_main(uid))
        return
    password = m.text.strip()
    phone = st["data"]["phone"]
    session_str = st["data"]["session"]

    def _do():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            client = TelegramClient(StringSession(session_str), API_ID, API_HASH)
            loop.run_until_complete(client.connect())
            loop.run_until_complete(client.sign_in(password=password))
            sess = client.session.save()
            enc = encrypt_session(sess)
            conn = get_db()
            try:
                c = conn.cursor()
                c.execute("""
                    INSERT INTO accounts (user_id, phone, session_enc, status, last_reset, created_at, updated_at)
                    VALUES (%s,%s,%s,'active',%s,%s,%s)
                """, (uid, phone, enc, today_str(), now_str(), now_str()))
                conn.commit()
            finally:
                put_db(conn)
            clear_state(uid)
            bot.send_message(uid, f"✅ Akkaunt ulandi (2FA): <b>{phone}</b>", reply_markup=kb_main(uid))
            loop.run_until_complete(client.disconnect())
        except Exception as e:
            clear_state(uid)
            bot.send_message(uid, f"❌ 2FA xato: {e}", reply_markup=kb_main(uid))
        finally:
            loop.close()

    Thread(target=_do, daemon=True).start()

# ---------- GURUHLAR ----------
@bot.message_handler(func=lambda m: m.text == "👥 Guruhlarim")
def msg_groups(m):
    uid = m.from_user.id
    limits = get_user_limits(uid)
    conn = get_db()
    try:
        c = conn.cursor()
        c.execute("SELECT COUNT(*) as n FROM groups WHERE user_id=%s AND is_active=1", (uid,))
        n = c.fetchone()["n"]
        c.execute("""
            SELECT g.*, a.phone FROM groups g
            LEFT JOIN accounts a ON a.id = g.account_id
            WHERE g.user_id=%s ORDER BY g.is_active DESC, g.folder, g.id LIMIT 40
        """, (uid,))
        groups = c.fetchall()
        c.execute("SELECT id, phone FROM accounts WHERE user_id=%s AND status='active'", (uid,))
        accs = c.fetchall()
    finally:
        put_db(conn)

    text = (
        f"👥 <b>Guruhlarim</b>\n"
        f"Faol: <b>{n}</b> / limit: <b>{limits['groups']}</b>\n\n"
    )
    if not groups:
        text += (
            "Hali guruh tanlanmagan.\n\n"
            "👇 <b>Qanday qo'shiladi?</b>\n"
            "1️⃣ «📥 Akkauntdan yuklash» — akkauntingizdagi barcha guruhlar chiqadi, ✅ bilan tanlaysiz\n"
            "2️⃣ «➕ Qo'lda qo'shish» — chat ID yoki @username yozasiz"
        )
    else:
        for g in groups[:25]:
            st = "✅" if g["is_active"] else "⬜"
            text += f"{st} {g['title'] or g['chat_id']}\n"
        if len(groups) > 25:
            text += f"\n... va yana {len(groups)-25} ta"

    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    if accs:
        kb.add("📥 Akkauntdan yuklash")
    kb.add("➕ Qo'lda qo'shish", "🗑 O'chirish")
    kb.add("✅ Hammasini yoqish", "⬜ Hammasini o'chirish")
    kb.add("🔙 Orqaga")
    bot.send_message(uid, text, reply_markup=kb)


@bot.message_handler(func=lambda m: m.text == "📥 Akkauntdan yuklash")
def msg_load_groups_from_account(m):
    """Akkauntdagi barcha guruh/kanallarni olib, ✅ bilan tanlash"""
    uid = m.from_user.id
    limits = get_user_limits(uid)
    conn = get_db()
    try:
        c = conn.cursor()
        c.execute("SELECT id, phone, session_enc FROM accounts WHERE user_id=%s AND status='active' ORDER BY id", (uid,))
        accs = c.fetchall()
        c.execute("SELECT chat_id FROM groups WHERE user_id=%s", (uid,))
        already = {str(r["chat_id"]) for r in c.fetchall()}
    finally:
        put_db(conn)

    if not accs:
        bot.send_message(uid, "❌ Avval 📱 Akkauntlarim bo'limidan akkaunt ulang.", reply_markup=kb_main(uid))
        return

    bot.send_message(uid, "⏳ Akkauntdagi guruhlar yuklanmoqda... biroz kuting.")

    def _do():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        all_dialogs = []
        try:
            for acc in accs:
                sess = decrypt_session(acc["session_enc"])
                if not sess:
                    continue
                client = TelegramClient(StringSession(sess), API_ID, API_HASH)
                try:
                    loop.run_until_complete(client.connect())
                    if not loop.run_until_complete(client.is_user_authorized()):
                        continue
                    dialogs = loop.run_until_complete(client.get_dialogs(limit=200))
                    for d in dialogs:
                        if not d.is_group and not d.is_channel:
                            continue
                        chat_id = str(d.id)
                        title = d.title or chat_id
                        all_dialogs.append({
                            "chat_id": chat_id,
                            "title": title,
                            "account_id": acc["id"],
                            "phone": acc["phone"],
                            "already": chat_id in already,
                        })
                except Exception as e:
                    print(f"Dialog load error: {e}")
                finally:
                    try:
                        loop.run_until_complete(client.disconnect())
                    except Exception:
                        pass
        finally:
            loop.close()

        if not all_dialogs:
            bot.send_message(uid, "❌ Guruh topilmadi yoki akkaunt ulanmagan.", reply_markup=kb_main(uid))
            return

        # Unique by chat_id
        seen = set()
        unique = []
        for d in all_dialogs:
            if d["chat_id"] not in seen:
                seen.add(d["chat_id"])
                unique.append(d)

        set_state(uid, "grp_select", {"dialogs": unique, "page": 0, "selected": set()})
        _send_group_select_page(uid)

    Thread(target=_do, daemon=True).start()


def _send_group_select_page(uid):
    st = get_state(uid)
    if not st or st["step"] != "grp_select":
        return
    dialogs = st["data"]["dialogs"]
    page = st["data"].get("page", 0)
    selected = st["data"].get("selected", set())
    per_page = 8
    total_pages = max(1, (len(dialogs) + per_page - 1) // per_page)
    page = max(0, min(page, total_pages - 1))
    st["data"]["page"] = page
    chunk = dialogs[page * per_page:(page + 1) * per_page]

    limits = get_user_limits(uid)
    text = (
        f"📥 <b>Akkauntdagi guruhlar</b>\n"
        f"Sahifa {page+1}/{total_pages} · Jami {len(dialogs)} ta\n"
        f"Tanlangan: <b>{len(selected)}</b> · Limit: {limits['groups']}\n\n"
        f"Kerakli guruhlarni ✅ bilan belgilang, keyin «💾 Saqlash» bosing."
    )
    kb = types.InlineKeyboardMarkup(row_width=1)
    for d in chunk:
        mark = "✅" if d["chat_id"] in selected else "⬜"
        already = " (qo'shilgan)" if d["already"] else ""
        title = (d["title"] or d["chat_id"])[:40]
        kb.add(types.InlineKeyboardButton(
            f"{mark} {title}{already}",
            callback_data=f"gsel_{d['chat_id']}"
        ))
    nav = []
    if page > 0:
        nav.append(types.InlineKeyboardButton("⬅️", callback_data="gpage_prev"))
    nav.append(types.InlineKeyboardButton(f"{page+1}/{total_pages}", callback_data="gpage_noop"))
    if page < total_pages - 1:
        nav.append(types.InlineKeyboardButton("➡️", callback_data="gpage_next"))
    if nav:
        kb.row(*nav)
    kb.add(types.InlineKeyboardButton("✅ Hammasini tanlash", callback_data="gsel_all"))
    kb.add(types.InlineKeyboardButton("💾 Saqlash", callback_data="gsel_save"))
    kb.add(types.InlineKeyboardButton("❌ Bekor", callback_data="gsel_cancel"))
    bot.send_message(uid, text, reply_markup=kb)


@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("gsel_"))
def cb_group_select(c):
    uid = c.from_user.id
    st = get_state(uid)
    if not st or st["step"] != "grp_select":
        bot.answer_callback_query(c.id, "Sessiya tugagan, qayta boshlang")
        return
    data = st["data"]
    selected = data.get("selected", set())
    if not isinstance(selected, set):
        selected = set(selected)

    action = c.data[5:]  # after gsel_
    if action == "all":
        for d in data["dialogs"]:
            selected.add(d["chat_id"])
        data["selected"] = selected
        bot.answer_callback_query(c.id, f"✅ {len(selected)} ta tanlandi")
        try:
            bot.delete_message(c.message.chat.id, c.message.message_id)
        except Exception:
            pass
        _send_group_select_page(uid)
        return
    if action == "save":
        limits = get_user_limits(uid)
        conn = get_db()
        try:
            c_db = conn.cursor()
            c_db.execute("SELECT COUNT(*) as n FROM groups WHERE user_id=%s AND is_active=1", (uid,))
            current = c_db.fetchone()["n"]
            added = 0
            for d in data["dialogs"]:
                if d["chat_id"] not in selected:
                    continue
                if current + added >= limits["groups"]:
                    break
                c_db.execute("SELECT id FROM groups WHERE user_id=%s AND chat_id=%s", (uid, d["chat_id"]))
                if c_db.fetchone():
                    c_db.execute("UPDATE groups SET is_active=1, title=%s WHERE user_id=%s AND chat_id=%s",
                                 (d["title"], uid, d["chat_id"]))
                else:
                    c_db.execute("""
                        INSERT INTO groups (account_id, user_id, chat_id, title, folder, is_active, created_at)
                        VALUES (%s,%s,%s,%s,'Asosiy',1,%s)
                    """, (d["account_id"], uid, d["chat_id"], d["title"], now_str()))
                    added += 1
            conn.commit()
        finally:
            put_db(conn)
        clear_state(uid)
        bot.answer_callback_query(c.id, "Saqlandi!")
        try:
            bot.edit_message_text(
                f"✅ <b>{added}</b> ta yangi guruh qo'shildi / yangilandi.\n"
                f"Endi ▶️ Boshlash orqali yuborishingiz mumkin.",
                c.message.chat.id, c.message.message_id
            )
        except Exception:
            bot.send_message(uid, f"✅ Guruhlar saqlandi ({added} ta).", reply_markup=kb_main(uid))
        return
    if action == "cancel":
        clear_state(uid)
        bot.answer_callback_query(c.id, "Bekor")
        try:
            bot.edit_message_text("❌ Bekor qilindi.", c.message.chat.id, c.message.message_id)
        except Exception:
            pass
        bot.send_message(uid, "Asosiy menyu", reply_markup=kb_main(uid))
        return

    # Toggle single
    chat_id = action
    if chat_id in selected:
        selected.discard(chat_id)
    else:
        selected.add(chat_id)
    data["selected"] = selected
    bot.answer_callback_query(c.id, "✅" if chat_id in selected else "⬜")
    try:
        bot.delete_message(c.message.chat.id, c.message.message_id)
    except Exception:
        pass
    _send_group_select_page(uid)


@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("gpage_"))
def cb_group_page(c):
    uid = c.from_user.id
    st = get_state(uid)
    if not st or st["step"] != "grp_select":
        bot.answer_callback_query(c.id)
        return
    action = c.data[6:]
    if action == "prev":
        st["data"]["page"] = max(0, st["data"].get("page", 0) - 1)
    elif action == "next":
        st["data"]["page"] = st["data"].get("page", 0) + 1
    bot.answer_callback_query(c.id)
    try:
        bot.delete_message(c.message.chat.id, c.message.message_id)
    except Exception:
        pass
    _send_group_select_page(uid)


@bot.message_handler(func=lambda m: m.text == "➕ Qo'lda qo'shish")
def msg_add_group(m):
    uid = m.from_user.id
    limits = get_user_limits(uid)
    conn = get_db()
    try:
        c = conn.cursor()
        c.execute("SELECT COUNT(*) as n FROM groups WHERE user_id=%s AND is_active=1", (uid,))
        n = c.fetchone()["n"]
        c.execute("SELECT id, phone FROM accounts WHERE user_id=%s AND status='active'", (uid,))
        accs = c.fetchall()
    finally:
        put_db(conn)
    if n >= limits["groups"]:
        bot.send_message(uid, f"❌ Guruh limiti tugadi ({limits['groups']}).\n💎 Tariflar orqali yangilang.", reply_markup=kb_main(uid))
        return
    if not accs:
        bot.send_message(uid, "❌ Avval 📱 Akkauntlarim bo'limidan akkaunt ulang.", reply_markup=kb_main(uid))
        return
    set_state(uid, "grp_chatid", {"accs": [a["id"] for a in accs]})
    bot.send_message(
        uid,
        "➕ <b>Guruhni qo'lda qo'shish</b>\n\n"
        "Chat ID yoki @username yuboring:\n"
        "• <code>-1001234567890</code>\n"
        "• <code>@mygroup</code>\n\n"
        "💡 Chat ID olish: guruhga @getidsbot ni qo'shing.",
        reply_markup=kb_cancel()
    )


@bot.message_handler(func=lambda m: get_state(m.from_user.id) and get_state(m.from_user.id)["step"] == "grp_chatid")
def proc_grp_chatid(m):
    uid = m.from_user.id
    if m.text in ("❌ Bekor", "🔙 Orqaga"):
        clear_state(uid)
        bot.send_message(uid, "Bekor qilindi.", reply_markup=kb_main(uid))
        return
    chat_raw = m.text.strip()
    st = get_state(uid)
    acc_id = st["data"]["accs"][0]
    title = chat_raw
    conn = get_db()
    try:
        c = conn.cursor()
        c.execute("SELECT id FROM groups WHERE user_id=%s AND chat_id=%s", (uid, chat_raw))
        if c.fetchone():
            c.execute("UPDATE groups SET is_active=1 WHERE user_id=%s AND chat_id=%s", (uid, chat_raw))
        else:
            c.execute("""
                INSERT INTO groups (account_id, user_id, chat_id, title, folder, is_active, created_at)
                VALUES (%s,%s,%s,%s,'Asosiy',1,%s)
            """, (acc_id, uid, chat_raw, title, now_str()))
        conn.commit()
    finally:
        put_db(conn)
    clear_state(uid)
    bot.send_message(uid, f"✅ Guruh qo'shildi: <b>{title}</b>", reply_markup=kb_main(uid))


@bot.message_handler(func=lambda m: m.text == "✅ Hammasini yoqish")
def msg_enable_all_groups(m):
    uid = m.from_user.id
    conn = get_db()
    try:
        c = conn.cursor()
        c.execute("UPDATE groups SET is_active=1 WHERE user_id=%s", (uid,))
        n = c.rowcount
        conn.commit()
    finally:
        put_db(conn)
    bot.send_message(uid, f"✅ {n} ta guruh yoqildi.", reply_markup=kb_main(uid))


@bot.message_handler(func=lambda m: m.text == "⬜ Hammasini o'chirish")
def msg_disable_all_groups(m):
    uid = m.from_user.id
    conn = get_db()
    try:
        c = conn.cursor()
        c.execute("UPDATE groups SET is_active=0 WHERE user_id=%s", (uid,))
        n = c.rowcount
        conn.commit()
    finally:
        put_db(conn)
    bot.send_message(uid, f"⬜ {n} ta guruh o'chirildi (vaqtincha).", reply_markup=kb_main(uid))


@bot.message_handler(func=lambda m: m.text == "🗑 O'chirish")
def msg_delete_group_prompt(m):
    uid = m.from_user.id
    conn = get_db()
    try:
        c = conn.cursor()
        c.execute("SELECT id, title, chat_id, is_active FROM groups WHERE user_id=%s ORDER BY id DESC LIMIT 20", (uid,))
        groups = c.fetchall()
    finally:
        put_db(conn)
    if not groups:
        bot.send_message(uid, "Guruh yo'q.", reply_markup=kb_main(uid))
        return
    kb = types.InlineKeyboardMarkup(row_width=1)
    for g in groups:
        kb.add(types.InlineKeyboardButton(
            f"🗑 {g['title'] or g['chat_id']}",
            callback_data=f"gdel_{g['id']}"
        ))
    kb.add(types.InlineKeyboardButton("❌ Bekor", callback_data="gdel_cancel"))
    bot.send_message(uid, "O'chirish uchun guruhni tanlang:", reply_markup=kb)


@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("gdel_"))
def cb_delete_group(c):
    uid = c.from_user.id
    if c.data == "gdel_cancel":
        bot.answer_callback_query(c.id)
        try:
            bot.edit_message_text("Bekor.", c.message.chat.id, c.message.message_id)
        except Exception:
            pass
        return
    gid = int(c.data.split("_")[1])
    conn = get_db()
    try:
        c_db = conn.cursor()
        c_db.execute("DELETE FROM groups WHERE id=%s AND user_id=%s", (gid, uid))
        conn.commit()
    finally:
        put_db(conn)
    bot.answer_callback_query(c.id, "O'chirildi")
    try:
        bot.edit_message_text("✅ Guruh o'chirildi.", c.message.chat.id, c.message.message_id)
    except Exception:
        pass

# ---------- XABARLAR ----------
@bot.message_handler(func=lambda m: m.text == "✉️ Xabarlarim")
def msg_messages(m):
    uid = m.from_user.id
    conn = get_db()
    try:
        c = conn.cursor()
        c.execute("SELECT * FROM messages WHERE user_id=%s ORDER BY sort_order, id", (uid,))
        msgs = c.fetchall()
    finally:
        put_db(conn)

    text = "✉️ <b>Xabarlarim</b>\n\n"
    if not msgs:
        text += "Hali xabar yo'q. ➕ orqali qo'shing.\n"
        text += "O'zgaruvchilar: {nom}, {sana}, {vaqt}"
    else:
        for i, msg in enumerate(msgs, 1):
            preview = (msg["text"] or "")[:40]
            text += f"{i}. {msg['title']}: {preview}...\n"

    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add("➕ Xabar qo'shish", "✏️ Tahrirlash")
    kb.add("🗑 O'chirish", "🔙 Orqaga")
    bot.send_message(uid, text, reply_markup=kb)

@bot.message_handler(func=lambda m: m.text == "➕ Xabar qo'shish")
def msg_add_message(m):
    uid = m.from_user.id
    set_state(uid, "msg_text")
    bot.send_message(
        uid,
        "✉️ Xabar matnini yuboring.\n\n"
        "Qo'llab-quvvatlanadi: matn, rasm, video.\n"
        "O'zgaruvchilar: <code>{nom}</code>, <code>{sana}</code>, <code>{vaqt}</code>",
        reply_markup=kb_cancel()
    )

@bot.message_handler(func=lambda m: get_state(m.from_user.id) and get_state(m.from_user.id)["step"] == "msg_text",
                     content_types=["text", "photo", "video", "document"])
def proc_msg_text(m):
    uid = m.from_user.id
    if m.text in ("❌ Bekor", "🔙 Orqaga"):
        clear_state(uid)
        bot.send_message(uid, "Bekor.", reply_markup=kb_main(uid))
        return

    text = m.text or m.caption or ""
    media_type = "text"
    media_file_id = ""
    if m.photo:
        media_type = "photo"
        media_file_id = m.photo[-1].file_id
    elif m.video:
        media_type = "video"
        media_file_id = m.video.file_id
    elif m.document:
        media_type = "document"
        media_file_id = m.document.file_id

    conn = get_db()
    try:
        c = conn.cursor()
        c.execute("""
            INSERT INTO messages (user_id, title, text, media_type, media_file_id, created_at)
            VALUES (%s,%s,%s,%s,%s,%s)
        """, (uid, "Xabar", text, media_type, media_file_id, now_str()))
        conn.commit()
    finally:
        put_db(conn)
    clear_state(uid)
    bot.send_message(uid, "✅ Xabar saqlandi!", reply_markup=kb_main(uid))

# ---------- REJALASHTIRISH ----------
@bot.message_handler(func=lambda m: m.text == "⏰ Rejalashtirish")
def msg_schedule(m):
    uid = m.from_user.id
    conn = get_db()
    try:
        c = conn.cursor()
        c.execute("SELECT id, phone, work_from, work_to, interval_sec FROM accounts WHERE user_id=%s", (uid,))
        accs = c.fetchall()
    finally:
        put_db(conn)

    text = "⏰ <b>Rejalashtirish</b>\n\n"
    text += "Har bir akkaunt uchun ish vaqti va interval (daqiqa/soniya).\n\n"
    if not accs:
        text += "Avval akkaunt ulang."
    else:
        for a in accs:
            text += f"📱 {a['phone']}\n   🕐 {a['work_from']}–{a['work_to']} | ⏱ {a['interval_sec']}s\n"

    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add("⏱ Interval belgilash", "🕐 Ish vaqti")
    kb.add("🔙 Orqaga")
    bot.send_message(uid, text, reply_markup=kb)

@bot.message_handler(func=lambda m: m.text == "⏱ Interval belgilash")
def msg_set_interval(m):
    uid = m.from_user.id
    set_state(uid, "set_interval")
    bot.send_message(
        uid,
        "⏱ Intervalni yuboring (soniyada yoki daqiqada).\n"
        "Masalan: <code>60</code> (1 daqiqa) yoki <code>5m</code> (5 daqiqa)\n"
        "Minimal: 30 soniya (tarifga qarab).",
        reply_markup=kb_cancel()
    )

@bot.message_handler(func=lambda m: get_state(m.from_user.id) and get_state(m.from_user.id)["step"] == "set_interval")
def proc_set_interval(m):
    uid = m.from_user.id
    if m.text in ("❌ Bekor", "🔙 Orqaga"):
        clear_state(uid)
        bot.send_message(uid, "Bekor.", reply_markup=kb_main(uid))
        return
    raw = m.text.strip().lower()
    sec = 0
    if raw.endswith("m"):
        try:
            sec = int(raw[:-1]) * 60
        except Exception:
            pass
    else:
        try:
            sec = int(raw)
        except Exception:
            pass
    if sec < 30:
        bot.send_message(uid, "❌ Minimal 30 soniya.")
        return
    limits = get_user_limits(uid)
    min_sec = max(30, int(limits.get("interval_min", 1)) * 60) if limits.get("interval_min", 1) > 1 else 30
    # Tarif interval_min daqiqada — lekin foydalanuvchi minut/soniya tanlay oladi
    conn = get_db()
    try:
        c = conn.cursor()
        c.execute("UPDATE accounts SET interval_sec=%s, updated_at=%s WHERE user_id=%s", (sec, now_str(), uid))
        conn.commit()
    finally:
        put_db(conn)
    clear_state(uid)
    bot.send_message(uid, f"✅ Interval: <b>{sec} soniya</b> ({sec // 60} daqiqa {sec % 60} s)", reply_markup=kb_main(uid))

@bot.message_handler(func=lambda m: m.text == "🕐 Ish vaqti")
def msg_set_worktime(m):
    uid = m.from_user.id
    set_state(uid, "set_worktime")
    bot.send_message(
        uid,
        "🕐 Ish vaqtini yuboring (Toshkent vaqti).\n"
        "Format: <code>09:00-21:00</code> yoki <code>08:30-22:00</code>",
        reply_markup=kb_cancel()
    )

@bot.message_handler(func=lambda m: get_state(m.from_user.id) and get_state(m.from_user.id)["step"] == "set_worktime")
def proc_set_worktime(m):
    uid = m.from_user.id
    if m.text in ("❌ Bekor", "🔙 Orqaga"):
        clear_state(uid)
        bot.send_message(uid, "Bekor.", reply_markup=kb_main(uid))
        return
    raw = m.text.strip().replace(" ", "")
    m_ = re.match(r"(\d{1,2}:\d{2})-(\d{1,2}:\d{2})", raw)
    if not m_:
        bot.send_message(uid, "❌ Format: 09:00-21:00")
        return
    wf, wt = m_.group(1), m_.group(2)
    conn = get_db()
    try:
        c = conn.cursor()
        c.execute("UPDATE accounts SET work_from=%s, work_to=%s, updated_at=%s WHERE user_id=%s",
                  (wf, wt, now_str(), uid))
        conn.commit()
    finally:
        put_db(conn)
    clear_state(uid)
    bot.send_message(uid, f"✅ Ish vaqti: <b>{wf}–{wt}</b>", reply_markup=kb_main(uid))

# ---------- BOSHLASH / TO'XTATISH ----------
@bot.message_handler(func=lambda m: m.text == "▶️ Boshlash")
def msg_start_sending(m):
    uid = m.from_user.id
    conn = get_db()
    try:
        c = conn.cursor()
        c.execute("UPDATE accounts SET is_running=1 WHERE user_id=%s AND status='active'", (uid,))
        n = c.rowcount
        conn.commit()
    finally:
        put_db(conn)
    bot.send_message(uid, f"▶️ {n} ta akkaunt ishga tushirildi.\nYuborish fon rejimida davom etadi.", reply_markup=kb_main(uid))

@bot.message_handler(func=lambda m: m.text == "⏹ To'xtatish")
def msg_stop_sending(m):
    uid = m.from_user.id
    conn = get_db()
    try:
        c = conn.cursor()
        c.execute("UPDATE accounts SET is_running=0 WHERE user_id=%s", (uid,))
        conn.commit()
    finally:
        put_db(conn)
    bot.send_message(uid, "⏹ Barcha akkauntlar to'xtatildi.", reply_markup=kb_main(uid))

# ---------- STATISTIKA ----------
@bot.message_handler(func=lambda m: m.text == "📊 Statistika")
def msg_stats(m):
    uid = m.from_user.id
    limits = get_user_limits(uid)
    today = today_str()
    conn = get_db()
    try:
        c = conn.cursor()
        c.execute("SELECT COALESCE(SUM(sent_count),0) as s FROM daily_usage WHERE user_id=%s AND day=%s", (uid, today))
        today_sent = c.fetchone()["s"]
        c.execute("SELECT COUNT(*) as n FROM groups WHERE user_id=%s AND is_active=1", (uid,))
        groups_n = c.fetchone()["n"]
        c.execute("SELECT COUNT(*) as n FROM accounts WHERE user_id=%s", (uid,))
        acc_n = c.fetchone()["n"]
        c.execute("SELECT COUNT(*) as n FROM send_log WHERE user_id=%s AND status='error' AND sent_at > %s",
                  (uid, (now_tz() - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")))
        errors = c.fetchone()["n"]
    finally:
        put_db(conn)

    text = (
        f"📊 <b>Statistika</b>\n\n"
        f"💎 Tarif: {limits['tariff_name']}\n"
        f"📱 Akkaunt: {acc_n}/{limits['accounts']}\n"
        f"👥 Guruh: {groups_n}/{limits['groups']}\n"
        f"✉️ Bugun yuborilgan: <b>{today_sent}</b> / {limits['daily_limit']}\n"
        f"⚠️ So'nggi 24 soat xato: {errors}\n"
    )
    if limits["expires_at"]:
        text += f"\n⏳ Muddat: {limits['expires_at'][:16]}"
    bot.send_message(uid, text, reply_markup=kb_main(uid))

# ---------- REFERAL ----------
@bot.message_handler(func=lambda m: m.text == "🎁 Referal")
def msg_referral(m):
    uid = m.from_user.id
    u = get_user(uid)
    me = bot.get_me()
    link = f"https://t.me/{me.username}?start=ref{uid}"
    days = get_setting("referral_bonus_days", 3)
    money = get_setting("referral_bonus_money", 5000)
    percent = get_setting("referral_percent", 10)
    bal = float(u["balance"]) if u and u.get("balance") is not None else 0
    refs = u["referrals_count"] if u else 0
    text = (
        f"🎁 <b>Referal dasturi</b>\n\n"
        f"Do'stingizni taklif qiling va pul ishlang!\n\n"
        f"💰 Do'st start bosganda: <b>{format_money(money)}</b>\n"
        f"📊 Do'st to'lov qilsa: <b>{percent}%</b> bonus\n"
        f"⏳ Qo'shimcha: {days} kun tarif bonusi (ixtiyoriy)\n\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"💵 Sizning balansi: <b>{format_money(bal)}</b>\n"
        f"👥 Taklif qilganlaringiz: <b>{refs}</b>\n\n"
        f"🔗 <b>Sizning havolangiz:</b>\n<code>{link}</code>\n\n"
        f"Havolani do'stlaringizga yuboring 👆"
    )
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("📤 Havolani ulashish", switch_inline_query=f"Tpublish Pro bot — {link}"))
    bot.send_message(uid, text, reply_markup=kb)
    bot.send_message(uid, "Asosiy menyu", reply_markup=kb_main(uid))

# ---------- QO'LLANMA & YORDAM ----------
@bot.message_handler(func=lambda m: m.text == "💵 Balans")
def msg_balance(m):
    uid = m.from_user.id
    u = get_user(uid)
    bal = float(u["balance"]) if u and u.get("balance") is not None else 0
    text = (
        f"💵 <b>Sizning balansingiz</b>\n\n"
        f"💰 Mavjud: <b>{format_money(bal)}</b>\n\n"
        f"Balans referal orqali to'ldiriladi:\n"
        f"• Do'st start bosganda — pul bonus\n"
        f"• Do'st to'lov qilsa — % bonus\n\n"
        f"Tez orada balansdan tarif sotib olish va yechib olish qo'shiladi."
    )
    bot.send_message(uid, text, reply_markup=kb_main(uid))


@bot.message_handler(func=lambda m: m.text == "📖 Qo'llanma")
def msg_guide(m):
    text = (
        "📖 <b>Botdan foydalanish — 5 qadam</b>\n\n"
        "1️⃣ <b>Akkaunt ulash</b>\n"
        "📱 Akkauntlarim → ➕ Yangi akkaunt\n"
        "Raqam → SMS kod → 2FA (API so'ralmaydi)\n\n"
        "2️⃣ <b>Guruhlarni tanlash</b>\n"
        "👥 Guruhlarim → 📥 Akkauntdan yuklash\n"
        "Barcha guruhlar chiqadi → ✅ bilan belgilang → 💾 Saqlash\n\n"
        "3️⃣ <b>Xabar yozish</b>\n"
        "✉️ Xabarlarim → ➕ → matn / rasm / video\n"
        "O'zgaruvchilar: {nom} {sana} {vaqt}\n\n"
        "4️⃣ <b>Vaqt sozlash</b>\n"
        "⏰ Interval: 60 yoki 5m\n"
        "Ish vaqti: 09:00-21:00\n\n"
        "5️⃣ <b>Boshlash</b>\n"
        "▶️ Boshlash — avtomatik yuboradi\n\n"
        "━━━━━━━━━━━━━━━━\n"
        "⚠️ <b>Maslahatlar</b>\n"
        "• Intervalni 60 soniyadan past qilmang\n"
        "• Faqat o'zingiz a'zo guruhlarga yuboring\n"
        "• 2FA yoqilgan akkaunt ishlating\n"
        "• Kunlik limitni kuzating (📊 Statistika)"
    )
    bot.send_message(m.chat.id, text, reply_markup=kb_main(m.from_user.id))

@bot.message_handler(func=lambda m: m.text == "🆘 Yordam")
def msg_help(m):
    set_state(m.from_user.id, "ticket")
    bot.send_message(m.chat.id, "🆘 Murojaatingizni yozing (admin javob beradi):", reply_markup=kb_cancel())

@bot.message_handler(func=lambda m: get_state(m.from_user.id) and get_state(m.from_user.id)["step"] == "ticket")
def proc_ticket(m):
    uid = m.from_user.id
    if m.text in ("❌ Bekor", "🔙 Orqaga"):
        clear_state(uid)
        bot.send_message(uid, "Bekor.", reply_markup=kb_main(uid))
        return
    conn = get_db()
    try:
        c = conn.cursor()
        c.execute(
            "INSERT INTO tickets (user_id, subject, message, status, created_at) VALUES (%s,%s,%s,'open',%s)",
            (uid, "Yordam", m.text, now_str())
        )
        conn.commit()
    finally:
        put_db(conn)
    clear_state(uid)
    bot.send_message(uid, "✅ Murojaat yuborildi. Tez orada javob beramiz.", reply_markup=kb_main(uid))
    for aid in ADMIN_IDS:
        try:
            bot.send_message(aid, f"🆘 Yangi ticket\n👤 {uid}\n{m.text[:200]}")
        except Exception:
            pass

@bot.message_handler(func=lambda m: m.text == "⚙️ Sozlamalar")
def msg_settings(m):
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add("🌐 Til: UZ / RU", "🔔 Eslatmalar")
    kb.add("🔙 Orqaga")
    bot.send_message(m.chat.id, "⚙️ Sozlamalar", reply_markup=kb)

# ==================== ADMIN PANEL ====================
@bot.message_handler(func=lambda m: m.text == "🛠 Admin panel" and is_admin(m.from_user.id))
def msg_admin_panel(m):
    bot.send_message(m.chat.id, "🛠 Admin panel", reply_markup=kb_admin())

@bot.message_handler(func=lambda m: m.text == "📊 Dashboard" and is_admin(m.from_user.id))
def adm_dashboard(m):
    conn = get_db()
    try:
        c = conn.cursor()
        c.execute("SELECT COUNT(*) as n FROM users")
        users = c.fetchone()["n"]
        c.execute("SELECT COUNT(*) as n FROM subscriptions WHERE is_active=1 AND expires_at > %s", (now_str(),))
        paid = c.fetchone()["n"]
        c.execute("SELECT COUNT(*) as n FROM accounts")
        accs = c.fetchone()["n"]
        today = today_str()
        c.execute("SELECT COALESCE(SUM(sent_count),0) as s FROM daily_usage WHERE day=%s", (today,))
        sent = c.fetchone()["s"]
        c.execute("SELECT COALESCE(SUM(unique_amount),0) as s FROM payments WHERE status='paid' AND paid_at LIKE %s", (today + "%",))
        today_money = c.fetchone()["s"]
        c.execute("SELECT COALESCE(SUM(unique_amount),0) as s FROM payments WHERE status='paid' AND paid_at >= %s",
                  (now_tz().replace(day=1).strftime("%Y-%m-%d"),))
        month_money = c.fetchone()["s"]
    finally:
        put_db(conn)
    text = (
        f"📊 <b>Dashboard</b>\n\n"
        f"👥 Foydalanuvchilar: {users}\n"
        f"💎 Pullik obuna: {paid}\n"
        f"📱 Ulangan akkauntlar: {accs}\n"
        f"✉️ Bugun yuborilgan: {sent}\n"
        f"💰 Bugungi tushum: {format_money(today_money)}\n"
        f"💰 Oylik tushum: {format_money(month_money)}"
    )
    bot.send_message(m.chat.id, text, reply_markup=kb_admin())

@bot.message_handler(func=lambda m: m.text == "💳 To'lov sozlash" and is_admin(m.from_user.id))
def adm_payment_settings(m):
    card = get_setting("card_number")
    owner = get_setting("card_owner")
    mins = get_setting("payment_time_minutes")
    conn = get_db()
    try:
        c = conn.cursor()
        c.execute("SELECT phone, status FROM humo_session WHERE id=1")
        humo = c.fetchone()
    finally:
        put_db(conn)
    status = humo["status"] if humo else "disconnected"
    phone = humo["phone"] if humo else "—"
    text = (
        f"💳 <b>To'lov sozlamalari</b>\n\n"
        f"Karta: <code>{card}</code>\n"
        f"Egasi: {owner}\n"
        f"Vaqt: {mins} daqiqa\n\n"
        f"HUMO akkaunt: {phone}\n"
        f"Holat: {'🟢 ulangan' if status == 'connected' else '🔴 uzilgan'}"
    )
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add("🔗 HUMO ulash", "💳 Karta o'zgartirish")
    kb.add("⏱ To'lov vaqti", "🔙 Orqaga")
    bot.send_message(m.chat.id, text, reply_markup=kb)

@bot.message_handler(func=lambda m: m.text == "🔗 HUMO ulash" and is_admin(m.from_user.id))
def adm_humo_connect(m):
    if not API_ID or not API_HASH:
        bot.send_message(m.chat.id, "❌ API_ID va API_HASH .env da bo'lishi shart.")
        return
    set_state(m.from_user.id, "humo_phone")
    bot.send_message(
        m.chat.id,
        "🔗 <b>HUMO akkauntini ulash</b>\n\n"
        "Telefon raqamni yuboring (+998...):\n"
        "Faqat raqam, SMS kod va 2FA so'raladi.\n"
        "API ID/Hash so'ralmaydi.",
        reply_markup=kb_cancel()
    )

@bot.message_handler(func=lambda m: get_state(m.from_user.id) and get_state(m.from_user.id)["step"] == "humo_phone" and is_admin(m.from_user.id))
def proc_humo_phone(m):
    uid = m.from_user.id
    if m.text in ("❌ Bekor", "🔙 Orqaga"):
        clear_state(uid)
        bot.send_message(uid, "Bekor.", reply_markup=kb_admin())
        return
    phone = m.text.strip().replace(" ", "")
    if not re.match(r"^\+\d{10,15}$", phone):
        bot.send_message(uid, "❌ Format: +998901234567")
        return

    def _do():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            client, phone_code_hash = loop.run_until_complete(login_phone_step(phone))
            set_state(uid, "humo_code", {
                "phone": phone,
                "phone_code_hash": phone_code_hash,
                "session": client.session.save()
            })
            bot.send_message(uid, f"✅ Kod yuborildi. Kodni yuboring:", reply_markup=kb_cancel())
        except Exception as e:
            clear_state(uid)
            bot.send_message(uid, f"❌ {e}", reply_markup=kb_admin())
        finally:
            loop.close()
    Thread(target=_do, daemon=True).start()

@bot.message_handler(func=lambda m: get_state(m.from_user.id) and get_state(m.from_user.id)["step"] == "humo_code" and is_admin(m.from_user.id))
def proc_humo_code(m):
    uid = m.from_user.id
    st = get_state(uid)
    if m.text in ("❌ Bekor", "🔙 Orqaga"):
        clear_state(uid)
        bot.send_message(uid, "Bekor.", reply_markup=kb_admin())
        return
    code = m.text.strip()
    phone = st["data"]["phone"]
    phone_code_hash = st["data"]["phone_code_hash"]
    session_str = st["data"]["session"]

    def _do():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            client = TelegramClient(StringSession(session_str), API_ID, API_HASH)
            loop.run_until_complete(client.connect())
            try:
                loop.run_until_complete(client.sign_in(phone, code, phone_code_hash=phone_code_hash))
            except SessionPasswordNeededError:
                set_state(uid, "humo_2fa", {"phone": phone, "session": client.session.save()})
                bot.send_message(uid, "🔐 2FA parolni yuboring:", reply_markup=kb_cancel())
                return
            sess = client.session.save()
            enc = encrypt_session(sess)
            conn = get_db()
            try:
                c = conn.cursor()
                c.execute("""
                    INSERT INTO humo_session (id, session_enc, phone, status, updated_at)
                    VALUES (1,%s,%s,'connected',%s)
                    ON CONFLICT (id) DO UPDATE SET session_enc=EXCLUDED.session_enc, phone=EXCLUDED.phone,
                    status='connected', updated_at=EXCLUDED.updated_at
                """, (enc, phone, now_str()))
                conn.commit()
            finally:
                put_db(conn)
            clear_state(uid)
            bot.send_message(uid, f"✅ HUMO akkaunt ulandi: {phone}\nListener qayta ishga tushiriladi.", reply_markup=kb_admin())
            loop.run_until_complete(client.disconnect())
            start_humo_listener()
        except Exception as e:
            clear_state(uid)
            bot.send_message(uid, f"❌ {e}", reply_markup=kb_admin())
        finally:
            loop.close()
    Thread(target=_do, daemon=True).start()

@bot.message_handler(func=lambda m: get_state(m.from_user.id) and get_state(m.from_user.id)["step"] == "humo_2fa" and is_admin(m.from_user.id))
def proc_humo_2fa(m):
    uid = m.from_user.id
    st = get_state(uid)
    if m.text in ("❌ Bekor", "🔙 Orqaga"):
        clear_state(uid)
        bot.send_message(uid, "Bekor.", reply_markup=kb_admin())
        return
    password = m.text.strip()
    phone = st["data"]["phone"]
    session_str = st["data"]["session"]

    def _do():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            client = TelegramClient(StringSession(session_str), API_ID, API_HASH)
            loop.run_until_complete(client.connect())
            loop.run_until_complete(client.sign_in(password=password))
            sess = client.session.save()
            enc = encrypt_session(sess)
            conn = get_db()
            try:
                c = conn.cursor()
                c.execute("""
                    INSERT INTO humo_session (id, session_enc, phone, status, updated_at)
                    VALUES (1,%s,%s,'connected',%s)
                    ON CONFLICT (id) DO UPDATE SET session_enc=EXCLUDED.session_enc, phone=EXCLUDED.phone,
                    status='connected', updated_at=EXCLUDED.updated_at
                """, (enc, phone, now_str()))
                conn.commit()
            finally:
                put_db(conn)
            clear_state(uid)
            bot.send_message(uid, f"✅ HUMO (2FA) ulandi: {phone}", reply_markup=kb_admin())
            loop.run_until_complete(client.disconnect())
            start_humo_listener()
        except Exception as e:
            clear_state(uid)
            bot.send_message(uid, f"❌ {e}", reply_markup=kb_admin())
        finally:
            loop.close()
    Thread(target=_do, daemon=True).start()

@bot.message_handler(func=lambda m: m.text == "💳 Karta o'zgartirish" and is_admin(m.from_user.id))
def adm_card_change(m):
    set_state(m.from_user.id, "adm_card")
    bot.send_message(m.chat.id, "Yangi karta raqamini yuboring (8600...):", reply_markup=kb_cancel())

@bot.message_handler(func=lambda m: get_state(m.from_user.id) and get_state(m.from_user.id)["step"] == "adm_card" and is_admin(m.from_user.id))
def proc_adm_card(m):
    uid = m.from_user.id
    if m.text in ("❌ Bekor", "🔙 Orqaga"):
        clear_state(uid)
        bot.send_message(uid, "Bekor.", reply_markup=kb_admin())
        return
    card = m.text.strip().replace(" ", "")
    set_setting("card_number", card)
    set_state(uid, "adm_card_owner")
    bot.send_message(uid, "Karta egasi F.I.Sh ni yuboring:")

@bot.message_handler(func=lambda m: get_state(m.from_user.id) and get_state(m.from_user.id)["step"] == "adm_card_owner" and is_admin(m.from_user.id))
def proc_adm_card_owner(m):
    uid = m.from_user.id
    set_setting("card_owner", m.text.strip())
    clear_state(uid)
    bot.send_message(uid, "✅ Karta yangilandi.", reply_markup=kb_admin())

@bot.message_handler(func=lambda m: m.text == "🧾 To'lovlar" and is_admin(m.from_user.id))
def adm_payments(m):
    conn = get_db()
    try:
        c = conn.cursor()
        c.execute("""
            SELECT id, user_id, tariff_code, unique_amount, status, created_at
            FROM payments ORDER BY id DESC LIMIT 15
        """)
        rows = c.fetchall()
    finally:
        put_db(conn)
    text = "🧾 <b>So'nggi to'lovlar</b>\n\n"
    for r in rows:
        text += f"#{r['id']} {r['status']} | {r['user_id']} | {format_money(r['unique_amount'])} | {r['tariff_code']}\n"
    bot.send_message(m.chat.id, text or "Bo'sh", reply_markup=kb_admin())

@bot.message_handler(func=lambda m: m.text == "💎 Tariflar sozlash" and is_admin(m.from_user.id))
def adm_tariffs(m):
    tariffs = get_all_tariffs(active_only=False)
    text = "💎 <b>Tariflar</b>\n\n"
    for t in tariffs:
        text += (
            f"{t['name']} ({t['code']})\n"
            f"  💰 {format_money(t['price'])} | 📱{t['accounts']} 👥{t['groups_limit']} ✉️{t['daily_limit']}\n"
        )
    text += "\nTahrirlash uchun: /edittariff CODE"
    bot.send_message(m.chat.id, text, reply_markup=kb_admin())


@bot.message_handler(func=lambda m: m.text == "📣 Pastki reklama" and is_admin(m.from_user.id))
def adm_ad_footer(m):
    enabled = get_setting("ad_enabled", True)
    ad_text = get_setting("ad_text", "")
    ad_tariffs = get_setting("ad_tariffs", "free,start,basic")
    tariffs = get_all_tariffs(active_only=False)
    allowed = {x.strip().lower() for x in str(ad_tariffs).split(",") if x.strip()}

    uname = get_bot_username()
    if ad_text:
        show_ad = ad_text
        source = "qo‘lda yozilgan"
    else:
        show_ad = f"🤖 @{uname} orqali yuborildi" if uname else "(bot username topilmadi)"
        source = "avtomatik (bot manzili)"

    text = (
        f"📣 <b>Pastki reklama (xabar tagida)</b>\n\n"
        f"Holat: {'🟢 Yoqilgan' if enabled else '🔴 O‘chirilgan'}\n"
        f"Manba: {source}\n"
        f"Ko‘rinishi:\n<code>{show_ad}</code>\n\n"
        f"Qaysi tariflarda chiqadi:\n"
    )
    for t in tariffs:
        mark = "✅" if t["code"].lower() in allowed else "⬜"
        text += f"{mark} {t['name']} (<code>{t['code']}</code>)\n"
    text += (
        "\n💡 Yuqori tariflarda (Pro, Premium, VIP) odatda reklama o‘chiriladi — "
        "foydalanuvchi toza xabar yuboradi."
    )

    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton(
            "🟢 Yoqish" if not enabled else "🔴 O‘chirish",
            callback_data="ad_toggle"
        )
    )
    kb.add(types.InlineKeyboardButton("✏️ Reklama matni", callback_data="ad_edit_text"))
    row = []
    for t in tariffs:
        mark = "✅" if t["code"].lower() in allowed else "⬜"
        row.append(types.InlineKeyboardButton(
            f"{mark} {t['code']}",
            callback_data=f"ad_tar_{t['code']}"
        ))
        if len(row) == 2:
            kb.row(*row)
            row = []
    if row:
        kb.row(*row)
    bot.send_message(m.chat.id, text, reply_markup=kb)


@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("ad_"))
def cb_ad_settings(c):
    if not is_admin(c.from_user.id):
        bot.answer_callback_query(c.id, "Faqat admin")
        return
    data = c.data

    if data == "ad_toggle":
        cur = get_setting("ad_enabled", True)
        set_setting("ad_enabled", not cur)
        bot.answer_callback_query(c.id, "O‘zgartirildi")
        try:
            bot.delete_message(c.message.chat.id, c.message.message_id)
        except Exception:
            pass
        # qayta ko'rsatish
        class Fake:
            chat = c.message.chat
            from_user = c.from_user
            text = "📣 Pastki reklama"
        adm_ad_footer(Fake())
        return

    if data == "ad_edit_text":
        set_state(c.from_user.id, "adm_ad_text")
        bot.answer_callback_query(c.id)
        uname = get_bot_username() or "BotUsername"
        bot.send_message(
            c.from_user.id,
            "✏️ Yangi reklama matnini yuboring.\n\n"
            f"Bo‘sh qoldirsangiz (<code>-</code>) avtomatik bo‘ladi:\n"
            f"<code>🤖 @{uname} orqali yuborildi</code>\n\n"
            "O‘zingiz yozmoqchi bo‘lsangiz — matnni yuboring.",
            reply_markup=kb_cancel()
        )
        return

    if data.startswith("ad_tar_"):
        code = data[7:].lower()
        raw = str(get_setting("ad_tariffs", "free,start,basic") or "")
        allowed = {x.strip().lower() for x in raw.split(",") if x.strip()}
        if code in allowed:
            allowed.discard(code)
        else:
            allowed.add(code)
        set_setting("ad_tariffs", ",".join(sorted(allowed)) if allowed else "")
        bot.answer_callback_query(c.id, f"{code}: {'✅' if code in allowed else '⬜'}")
        try:
            bot.delete_message(c.message.chat.id, c.message.message_id)
        except Exception:
            pass
        class Fake:
            chat = c.message.chat
            from_user = c.from_user
            text = "📣 Pastki reklama"
        adm_ad_footer(Fake())
        return


@bot.message_handler(func=lambda m: get_state(m.from_user.id) and get_state(m.from_user.id)["step"] == "adm_ad_text" and is_admin(m.from_user.id))
def proc_adm_ad_text(m):
    uid = m.from_user.id
    if m.text in ("❌ Bekor", "🔙 Orqaga"):
        clear_state(uid)
        bot.send_message(uid, "Bekor.", reply_markup=kb_admin())
        return
    text = m.text.strip()
    if text == "-":
        text = ""
    set_setting("ad_text", text)
    clear_state(uid)
    bot.send_message(uid, f"✅ Reklama matni yangilandi:\n<code>{text or '(bo‘sh)'}</code>", reply_markup=kb_admin())

# ==================== SENDER WORKER ====================
def reset_daily_if_needed(account):
    today = today_str()
    if account.get("last_reset") != today:
        conn = get_db()
        try:
            c = conn.cursor()
            c.execute("UPDATE accounts SET daily_sent=0, last_reset=%s WHERE id=%s", (today, account["id"]))
            conn.commit()
        finally:
            put_db(conn)
        account["daily_sent"] = 0
        account["last_reset"] = today

def is_work_time(work_from, work_to):
    now = now_tz().time()
    try:
        wf = dtime(*map(int, work_from.split(":")))
        wt = dtime(*map(int, work_to.split(":")))
        if wf <= wt:
            return wf <= now <= wt
        return now >= wf or now <= wt
    except Exception:
        return True

def sender_worker():
    """Asosiy yuborish tsikli (har 20-40 soniyada)"""
    global _sender_running
    _sender_running = True
    print("✅ Sender worker ishga tushdi")
    while _sender_running:
        try:
            conn = get_db()
            try:
                c = conn.cursor()
                c.execute("""
                    SELECT a.*, u.user_id as uid FROM accounts a
                    JOIN users u ON u.user_id = a.user_id
                    WHERE a.is_running=1 AND a.status='active'
                """)
                accounts = c.fetchall()
            finally:
                put_db(conn)

            for acc in accounts:
                try:
                    reset_daily_if_needed(acc)
                    limits = get_user_limits(acc["user_id"])
                    if acc["daily_sent"] >= limits["daily_limit"]:
                        continue
                    if not is_work_time(acc.get("work_from", "09:00"), acc.get("work_to", "21:00")):
                        continue

                    # Guruhlar
                    conn = get_db()
                    try:
                        c = conn.cursor()
                        c.execute("""
                            SELECT * FROM groups WHERE account_id=%s AND is_active=1
                            ORDER BY last_sent NULLS FIRST LIMIT 5
                        """, (acc["id"],))
                        groups = c.fetchall()
                        c.execute("SELECT * FROM messages WHERE user_id=%s AND is_active=1 ORDER BY sort_order LIMIT 10",
                                  (acc["user_id"],))
                        messages = c.fetchall()
                    finally:
                        put_db(conn)

                    if not groups or not messages:
                        continue

                    # Client
                    sess = decrypt_session(acc["session_enc"])
                    if not sess:
                        continue

                    async def _send_one(group, msg, tariff_code="free"):
                        client = TelegramClient(StringSession(sess), API_ID, API_HASH)
                        await client.connect()
                        if not await client.is_user_authorized():
                            await client.disconnect()
                            return False, "session invalid"
                        try:
                            entity = await client.get_entity(int(group["chat_id"]) if group["chat_id"].lstrip("-").isdigit() else group["chat_id"])
                            text = msg["text"] or ""
                            text = text.replace("{sana}", today_str()).replace("{vaqt}", now_tz().strftime("%H:%M"))
                            text = text.replace("{nom}", group.get("title") or "")
                            # Pastki reklama (admin belgilagan tariflarda)
                            text = append_ad_footer(text, tariff_code)
                            if msg["media_type"] == "text" or not msg["media_file_id"]:
                                await client.send_message(entity, text)
                            else:
                                await client.send_message(entity, text)
                            await client.disconnect()
                            return True, ""
                        except FloodWaitError as e:
                            await client.disconnect()
                            return False, f"flood {e.seconds}s"
                        except Exception as e:
                            await client.disconnect()
                            return False, str(e)[:100]

                    for g in groups:
                        if acc["daily_sent"] >= limits["daily_limit"]:
                            break
                        msg = messages[acc["daily_sent"] % len(messages)]
                        loop = asyncio.new_event_loop()
                        ok, err = loop.run_until_complete(_send_one(g, msg, limits.get("tariff_code", "free")))
                        loop.close()

                        conn = get_db()
                        try:
                            c = conn.cursor()
                            if ok:
                                c.execute("UPDATE accounts SET daily_sent = daily_sent + 1 WHERE id=%s", (acc["id"],))
                                c.execute("UPDATE groups SET last_sent=%s, error_count=0 WHERE id=%s", (now_str(), g["id"]))
                                c.execute("""
                                    INSERT INTO daily_usage (user_id, account_id, day, sent_count)
                                    VALUES (%s,%s,%s,1)
                                    ON CONFLICT (user_id, account_id, day)
                                    DO UPDATE SET sent_count = daily_usage.sent_count + 1
                                """, (acc["user_id"], acc["id"], today_str()))
                                c.execute("""
                                    INSERT INTO send_log (user_id, account_id, group_id, chat_id, message_id, status, sent_at)
                                    VALUES (%s,%s,%s,%s,%s,'ok',%s)
                                """, (acc["user_id"], acc["id"], g["id"], g["chat_id"], msg["id"], now_str()))
                            else:
                                c.execute("UPDATE groups SET error_count = error_count + 1 WHERE id=%s", (g["id"],))
                                c.execute("""
                                    INSERT INTO send_log (user_id, account_id, group_id, chat_id, message_id, status, error, sent_at)
                                    VALUES (%s,%s,%s,%s,%s,'error',%s,%s)
                                """, (acc["user_id"], acc["id"], g["id"], g["chat_id"], msg["id"], err, now_str()))
                                if "flood" in err.lower() or "ban" in err.lower():
                                    c.execute("UPDATE accounts SET status='error', last_error=%s WHERE id=%s", (err, acc["id"]))
                            conn.commit()
                        finally:
                            put_db(conn)

                        time.sleep(max(2, acc.get("interval_sec", 60) // 10))
                except Exception as e:
                    print(f"Sender acc error: {e}")
                    traceback.print_exc()

            time.sleep(25)
        except Exception as e:
            print(f"Sender worker error: {e}")
            time.sleep(30)

# ==================== BACKGROUND TASKS ====================
def background_tasks():
    """Eslatmalar, muddati o'tgan to'lovlar, kunlik reset"""
    while True:
        try:
            # Muddati o'tgan to'lovlar
            conn = get_db()
            try:
                c = conn.cursor()
                c.execute("UPDATE payments SET status='expired' WHERE status='pending' AND expires_at < %s", (now_str(),))
                # Tarif eslatmalari
                remind = int(get_setting("remind_days_before", 3))
                future = (now_tz() + timedelta(days=remind)).strftime("%Y-%m-%d %H:%M:%S")
                c.execute("""
                    SELECT s.user_id, s.expires_at, t.name FROM subscriptions s
                    JOIN tariffs t ON t.code = s.tariff_code
                    WHERE s.is_active=1 AND s.expires_at BETWEEN %s AND %s
                """, (now_str(), future))
                for row in c.fetchall():
                    try:
                        bot.send_message(
                            row["user_id"],
                            f"⏳ Tarif <b>{row['name']}</b> muddati {row['expires_at'][:10]} da tugaydi.\n"
                            f"Uzaytirish uchun 💎 Tariflar bo'limiga o'ting."
                        )
                    except Exception:
                        pass
                # Muddati o'tgan obunalarni o'chirish
                c.execute("UPDATE subscriptions SET is_active=0 WHERE is_active=1 AND expires_at < %s", (now_str(),))
                conn.commit()
            finally:
                put_db(conn)
        except Exception as e:
            print(f"BG task error: {e}")
        time.sleep(3600)  # soatda 1 marta

# ==================== FLASK ====================
@app.route(WEBHOOK_PATH, methods=["POST"])
def webhook():
    if request.headers.get("content-type") == "application/json":
        json_str = request.get_data().decode("utf-8")
        update = telebot.types.Update.de_json(json_str)
        bot.process_new_updates([update])
        return "", 200
    return "", 403

@app.route("/health")
def health():
    return {"status": "ok", "time": now_str()}, 200

@app.route("/")
def index():
    return "Tpublish Pro is running", 200

# ==================== STARTUP ====================
def main():
    print("=" * 50)
    print("🚀 TPUBLISH PRO — START")
    print("=" * 50)
    if not BOT_TOKEN:
        print("❌ BOT_TOKEN yo'q")
        return
    init_pool()
    init_db()

    # Workers
    Thread(target=sender_worker, daemon=True).start()
    Thread(target=background_tasks, daemon=True).start()
    start_humo_listener()

    if WEBHOOK_URL:
        bot.remove_webhook()
        time.sleep(0.5)
        bot.set_webhook(url=WEBHOOK_URL + WEBHOOK_PATH)
        print(f"✅ Webhook: {WEBHOOK_URL}{WEBHOOK_PATH}")
        app.run(host="0.0.0.0", port=PORT)
    else:
        print("✅ Polling mode")
        bot.remove_webhook()
        bot.infinity_polling(timeout=60, long_polling_timeout=40)

if __name__ == "__main__":
    main()
