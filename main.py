# otpsell.py
"""
Full OTP shop bot with real Telethon upload/sign-in and one-time OTP monitor for chat 777000.
Author: adapted for Ajay
"""

import os
import json
import asyncio
import logging
import re
from datetime import datetime, timedelta
from typing import Optional, Dict, List

import aiosqlite
from zoneinfo import ZoneInfo
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (ApplicationBuilder, ContextTypes, CommandHandler,
                          CallbackQueryHandler, MessageHandler, filters)
from telegram.request import HTTPXRequest

from telethon import TelegramClient, events
from telethon.errors import SessionPasswordNeededError, PhoneCodeInvalidError, PhoneNumberInvalidError

# ============================
# CONFIG — set your values here
# ============================


def get_required_env(key: str) -> str:
    value = os.getenv(key)
    if not value:
        raise ValueError(
            f"Required environment variable '{key}' is not set. Please add it to Replit Secrets."
        )
    return value


CONFIG = {
    "BOT_TOKEN":
    get_required_env("BOT_TOKEN"),
    "API_ID":
    int(get_required_env("API_ID")),
    "API_HASH":
    get_required_env("API_HASH"),
    "TWO_FA_PASSWORD":
    os.getenv("TWO_FA_PASSWORD"),
    "ADMIN_IDS": [
        int(x.strip())
        for x in os.getenv("ADMIN_IDS", "8251818467,6936153954,6167484733").split(",")
        if x.strip()
    ],
    "FORCE_JOIN_USERNAME":
    os.getenv("FORCE_JOIN_USERNAME", "@abouttechyrajput"),
    "FORCE_JOIN_CHAT_ID":
    int(os.getenv("FORCE_JOIN_CHAT_ID", "-1002731834108")),
    "DATABASE_PATH":
    os.getenv("DATABASE_PATH", "shop.db"),
    "SESSION_DIR":
    os.getenv("SESSION_DIR", "sessions"),
    "COUNTRY_PRICES": {
        "US": 40.0,
        "ET": 35.0,
        "VN": 35.0,
        "IN": 40.0,
        "NP": 40.0,
        "SV": 55.0,
        "PH": 80.0,
        "CN": 80.0
    },
    "OWNER_HANDLE":
    os.getenv("OWNER_HANDLE", "choudhary_ji600"),
    "DEVELOPER_CREDITS":
    os.getenv("DEVELOPER_CREDITS", "🤖 Developed by @BSRAJPUT0"),
    "RESERVE_MINUTES":
    int(os.getenv("RESERVE_MINUTES", "10")),
    # timeouts (seconds)
    "HTTP_CONNECT_TIMEOUT":
    float(os.getenv("HTTP_CONNECT_TIMEOUT", "20.0")),
    "HTTP_READ_TIMEOUT":
    float(os.getenv("HTTP_READ_TIMEOUT", "60.0")),
    "HTTP_WRITE_TIMEOUT":
    float(os.getenv("HTTP_WRITE_TIMEOUT", "60.0")),
    "HTTP_POOL_TIMEOUT":
    float(os.getenv("HTTP_POOL_TIMEOUT", "10.0")),
}
# ============================

IST = ZoneInfo("Asia/Kolkata")
DB_PATH = CONFIG["DATABASE_PATH"]
SESSION_DIR = CONFIG["SESSION_DIR"]
os.makedirs(SESSION_DIR, exist_ok=True)

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("shopbot")

# Prevent sensitive data from being logged by httpx and telegram libraries
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)
logging.getLogger("telegram.ext").setLevel(logging.INFO)

# In-memory state for interactive admin upload sign-in
# PENDING_UPLOADS[admin_id] = { phone, phone_code_hash, session_fname, country, step }
PENDING_UPLOADS: Dict[int, Dict] = {}

# ---------- Schema ----------
SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY,
  username TEXT,
  balance REAL DEFAULT 0,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS bans (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER,
  reason TEXT,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS accounts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  country_code TEXT NOT NULL,
  phone_number TEXT NOT NULL,
  session_file TEXT,
  two_fa_password TEXT,
  uploaded_by INTEGER,
  status TEXT NOT NULL DEFAULT 'available',
  price REAL,
  metadata TEXT,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS transactions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER,
  account_id INTEGER,
  amount REAL,
  type TEXT,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY(user_id) REFERENCES users(id),
  FOREIGN KEY(account_id) REFERENCES accounts(id)
);

CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT
);
"""


# ---------- Helpers ----------
def country_flag(code: str) -> str:
    code = (code or "").upper()
    if len(code) != 2:
        return "🏳️"
    base = 127397
    return chr(ord(code[0]) + base) + chr(ord(code[1]) + base)


def now_iso() -> str:
    return datetime.now(IST).isoformat()


def minutes_from_now(mins: int) -> str:
    return (datetime.now(IST) + timedelta(minutes=mins)).isoformat()


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(SCHEMA_SQL)
        await db.commit()


async def get_user(user_id: int, username: Optional[str]):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT id, username, balance FROM users WHERE id=?", (user_id, ))
        row = await cur.fetchone()
        if row:
            return {"id": row[0], "username": row[1], "balance": row[2]}
        await db.execute("INSERT INTO users (id, username) VALUES (?, ?)",
                         (user_id, username))
        await db.commit()
        return {"id": user_id, "username": username, "balance": 0.0}


async def check_force_join(user_id: int, app) -> bool:
    chat_id = CONFIG["FORCE_JOIN_CHAT_ID"]
    try:
        member = await app.bot.get_chat_member(chat_id=chat_id,
                                               user_id=user_id)
        if member.status in ("left", "kicked", "restricted"):
            return False
    except Exception as e:
        logger.warning("Force-join check failed for %s: %s", chat_id, e)
        return False
    return True


def join_buttons() -> InlineKeyboardMarkup:
    uname = CONFIG["FORCE_JOIN_USERNAME"]
    if uname and uname.startswith("@"):
        return InlineKeyboardMarkup([[
            InlineKeyboardButton(f"✅ Join {uname}",
                                 url=f"https://t.me/{uname[1:]}")
        ]])
    return InlineKeyboardMarkup([])


# Admin decorator
def admin_only(func):

    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        uid = update.effective_user.id
        if uid not in CONFIG["ADMIN_IDS"]:
            # consistent style for unauthorized
            await update.message.reply_text(
                "❌ Unauthorized. This command is for admins only.")
            return
        return await func(update, context)

    return wrapper


# ---------- Telethon helpers (for convenience) ----------
async def create_telethon_client(session_path: str):
    client = TelegramClient(session_path, CONFIG["API_ID"], CONFIG["API_HASH"])
    await client.connect()
    return client


async def check_session_active(session_path: str) -> bool:
    client = TelegramClient(session_path, CONFIG["API_ID"], CONFIG["API_HASH"])
    try:
        await client.start()
        me = await client.get_me()
        return me is not None
    except Exception:
        return False
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass


# ---------- APScheduler tick ----------
async def release_expired_reservations_tick():
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT id, metadata FROM accounts WHERE status='reserved'")
        rows = await cur.fetchall()
        changed = 0
        for acc_id, meta in rows:
            try:
                m = json.loads(meta) if meta else {}
                ru = m.get("reserved_until")
                if not ru:
                    continue
                ru_dt = datetime.fromisoformat(ru)
                if ru_dt.tzinfo is None:
                    ru_dt = ru_dt.replace(tzinfo=IST)
                if ru_dt < datetime.now(IST):
                    await db.execute(
                        "UPDATE accounts SET status='available', metadata=NULL WHERE id=?",
                        (acc_id, ))
                    changed += 1
            except Exception:
                await db.execute(
                    "UPDATE accounts SET status='available', metadata=NULL WHERE id=?",
                    (acc_id, ))
                changed += 1

        if changed:
            await db.commit()


# ---------- Bot flows ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await get_user(user.id, user.username)
    await show_main_menu(update, context)


async def verify_join_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not await check_force_join(q.from_user.id, context.application):
        await q.edit_message_text(
            "❌ **You haven't joined the channel yet!**\n\nPlease join and then verify.",
            reply_markup=InlineKeyboardMarkup(
                [[
                    InlineKeyboardButton(
                        "📢 Join Channel",
                        url=f"https://t.me/{CONFIG['FORCE_JOIN_USERNAME'][1:]}"
                    )
                ],
                 [
                     InlineKeyboardButton("✅ Verify Join",
                                          callback_data="verify_join")
                 ]]),
            parse_mode="Markdown")
        return
    await show_main_menu_cb(update, context)


async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    is_admin = user.id in CONFIG["ADMIN_IDS"]
    welcome_text = f"""
✨ Welcome to Telegram Accounts Shop! ✨

🤖 Your trusted source for premium Telegram accounts

{CONFIG['DEVELOPER_CREDITS']}

💎 Features:
• Instant OTP Delivery
• Premium Quality Accounts  
• 24/7 Support
• Secure & Reliable

📊 Available Countries:
🇺🇸 US - ₹40      🇪🇹 Ethiopia - ₹35
🇻🇳 Vietnam - ₹35  🇮🇳 India - ₹40  
🇳🇵 Nepal - ₹40    🇸🇻 El Salvador - ₹55
🇵🇭 Philippines - ₹80  🇨🇳 China - ₹80
    """
    kb = []
    kb.append(
        [InlineKeyboardButton("🛒 Buy Accounts", callback_data="buy_accounts")])
    kb.append([
        InlineKeyboardButton("💰 Check Balance", callback_data="check_balance")
    ])
    if is_admin:
        kb.append([
            InlineKeyboardButton("⚡ Admin Panel", callback_data="admin_panel")
        ])
    kb.append([
        InlineKeyboardButton("📞 Contact Support",
                             url=f"https://t.me/{CONFIG['OWNER_HANDLE']}")
    ])
    if update.message:
        await update.message.reply_text(welcome_text,
                                        reply_markup=InlineKeyboardMarkup(kb),
                                        parse_mode="Markdown")
    else:
        await update.callback_query.edit_message_text(
            welcome_text,
            reply_markup=InlineKeyboardMarkup(kb),
            parse_mode="Markdown")


async def show_main_menu_cb(update: Update,
                            context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if q:
        await q.answer()
    await show_main_menu(update, context)


async def main_menu_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    user = q.from_user
    is_admin = user.id in CONFIG["ADMIN_IDS"]
    welcome_text = f"""
✨ Welcome to Telegram Accounts Shop! ✨

🤖 Your trusted source for premium Telegram accounts

{CONFIG['DEVELOPER_CREDITS']}

💎 Features:
• Instant OTP Delivery
• Premium Quality Accounts  
• 24/7 Support
• Secure & Reliable

📊 Available Countries:
🇺🇸 US - ₹40      🇪🇹 Ethiopia - ₹35
🇻🇳 Vietnam - ₹35  🇮🇳 India - ₹40  
🇳🇵 Nepal - ₹40    🇸🇻 El Salvador - ₹55
🇵🇭 Philippines - ₹80  🇨🇳 China - ₹80
    """
    kb = []
    kb.append(
        [InlineKeyboardButton("🛒 Buy Accounts", callback_data="buy_accounts")])
    kb.append([
        InlineKeyboardButton("💰 Check Balance", callback_data="check_balance")
    ])
    if is_admin:
        kb.append([
            InlineKeyboardButton("⚡ Admin Panel", callback_data="admin_panel")
        ])
    kb.append([
        InlineKeyboardButton("📞 Contact Support",
                             url=f"https://t.me/{CONFIG['OWNER_HANDLE']}")
    ])
    await q.edit_message_text(welcome_text,
                              reply_markup=InlineKeyboardMarkup(kb),
                              parse_mode="Markdown")


async def check_balance_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    user = await get_user(q.from_user.id, q.from_user.username)
    kb = [[InlineKeyboardButton("🔙 Back to Main", callback_data="main_menu")]]
    owner_handle = CONFIG['OWNER_HANDLE'].replace('_', '\\_')
    await q.edit_message_text(
        f"💰 **Your Balance**\n\n"
        f"Current Balance: ₹{user['balance']:.2f}\n\n"
        f"Need to add balance? Contact @{owner_handle}",
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode="Markdown")


async def buy_accounts_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    countries = [("US", "United States", 40), ("ET", "Ethiopia", 35),
                 ("VN", "Vietnam", 35), ("IN", "India", 40),
                 ("NP", "Nepal", 40), ("SV", "El Salvador", 55),
                 ("PH", "Philippines", 80), ("CN", "China", 80)]
    kb = []
    for i in range(0, len(countries), 2):
        row = []
        for j in range(2):
            if i + j < len(countries):
                country_code, country_name, price = countries[i + j]
                row.append(
                    InlineKeyboardButton(
                        f"{country_flag(country_code)} {country_name} - ₹{price}",
                        callback_data=f"country_{country_code}"))
        kb.append(row)
    kb.append(
        [InlineKeyboardButton("🔙 Back to Main", callback_data="main_menu")])
    await q.edit_message_text(
        "🌍 **Choose a Country**\n\nSelect your preferred country:",
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode="Markdown")


async def country_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    _, cc = q.data.split("_", 1)
    user = await get_user(q.from_user.id, q.from_user.username)
    price = CONFIG["COUNTRY_PRICES"].get(cc, 40.0)
    if user['balance'] < price:
        owner_handle = CONFIG['OWNER_HANDLE'].replace('_', '\\_')
        await q.edit_message_text(
            f"❌ **Insufficient Balance**\n\nRequired: ₹{price}\nYour Balance: ₹{user['balance']}\n\nContact @{owner_handle} to add balance.",
            parse_mode="Markdown")
        return
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT id, phone_number, price, session_file FROM accounts WHERE country_code=? AND status='available' LIMIT 1",
            (cc, ))
        row = await cur.fetchone()
    if not row:
        await q.edit_message_text(
            f"❌ **No {country_flag(cc)} {cc} numbers available**\n\nPlease check back later.",
            parse_mode="Markdown")
        return
    acc_id, phone, price_db, session_file = row
    if not session_file:
        owner_handle = CONFIG['OWNER_HANDLE'].replace('_', '\\_')
        await q.edit_message_text(
            f"❌ **Session file missing for this account**\n\nPlease contact @{owner_handle}.",
            parse_mode="Markdown")
        return
    meta = {
        "reserved_by": q.from_user.id,
        "reserved_at": now_iso(),
        "reserved_until": minutes_from_now(CONFIG["RESERVE_MINUTES"])
    }
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE accounts SET status='reserved', metadata=? WHERE id=?",
            (json.dumps(meta), acc_id))
        await db.commit()
    session_path = os.path.join(SESSION_DIR, session_file)
    kb = [[
        InlineKeyboardButton("🔄 Get New OTP",
                             callback_data=f"getotp_{acc_id}"),
        InlineKeyboardButton("✅ Done", callback_data=f"done_{acc_id}")
    ],
          [
              InlineKeyboardButton("🔙 Choose Another Country",
                                   callback_data="buy_accounts")
          ]]
    await q.edit_message_text(
        f"📱 **TRY LOGIN**\n\n📞 **Number:** `{phone}`\n\nInstructions:\n1. Copy the number\n2. Paste in Telegram app\n\n🔄 **Monitoring for OTP...**\n\n_When you receive OTP it will be forwarded here automatically._\n\n_WHEN YOU HAVE SUCCESSFULLY LOGGED IN TAP DONE ✅_",
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode="Markdown")
    # start monitor in background
    asyncio.create_task(
        monitor_telegram_messages(context, q.from_user.id, acc_id, phone,
                                  session_path))


# ---------- Admin panel and helpers ----------
@admin_only
async def admin_panel_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if q:
        await q.answer()
    text = ("⚙️ **Admin Panel**\n\n"
            "Available admin commands:\n"
            "• /upload - Add new accounts (interactive upload via Telethon)\n"
            "• /stats - View detailed statistics\n"
            "• /accounts - Manage accounts list\n"
            "• /balance - View/set user balance\n"
            "• /broadcast - Send message to all users\n"
            "• /ban - Ban user\n"
            "• /unban - Unban user\n"
            "• /addcoins - Add coins to user\n"
            "• /deductcoin - Deduct coins from user\n"
            "• /clearstats - Clear all sales statistics\n\n"
            "Tap an action below:")
    kb = [[
        InlineKeyboardButton("📥 Upload Account", callback_data="admin_upload")
    ],
          [
              InlineKeyboardButton("📊 Stats", callback_data="admin_stats"),
              InlineKeyboardButton("📇 Accounts",
                                   callback_data="admin_accounts")
          ],
          [
              InlineKeyboardButton("💳 Manage Balance",
                                   callback_data="check_balance")
          ],
          [
              InlineKeyboardButton("📣 Broadcast",
                                   callback_data="admin_broadcast")
          ], [InlineKeyboardButton("🔙 Back", callback_data="main_menu")]]
    if q:
        await q.edit_message_text(text,
                                  reply_markup=InlineKeyboardMarkup(kb),
                                  parse_mode="Markdown")
    else:
        await update.message.reply_text(text,
                                        reply_markup=InlineKeyboardMarkup(kb),
                                        parse_mode="Markdown")


@admin_only
async def admin_upload_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if q:
        await q.answer()
    countries = [("US", "United States"), ("ET", "Ethiopia"),
                 ("VN", "Vietnam"), ("IN", "India"), ("NP", "Nepal"),
                 ("SV", "El Salvador"), ("PH", "Philippines"), ("CN", "China")]
    kb = []
    for i in range(0, len(countries), 2):
        row = []
        for j in range(2):
            if i + j < len(countries):
                country_code, country_name = countries[i + j]
                row.append(
                    InlineKeyboardButton(
                        f"{country_flag(country_code)} {country_name}",
                        callback_data=f"admin_country_{country_code}"))
        kb.append(row)
    kb.append([InlineKeyboardButton("🔙 Back", callback_data="admin_panel")])
    text = "📥 **Upload Account**\n\nChoose country for the account you want to upload:"
    if q:
        await q.edit_message_text(text,
                                  reply_markup=InlineKeyboardMarkup(kb),
                                  parse_mode="Markdown")
    else:
        await update.message.reply_text(text,
                                        reply_markup=InlineKeyboardMarkup(kb),
                                        parse_mode="Markdown")


@admin_only
async def admin_country_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    # data like admin_country_US
    parts = q.data.split("_")
    cc = parts[-1] if parts else "US"
    context.user_data['upload_country'] = cc
    context.user_data['upload_step'] = 'phone'
    await q.edit_message_text(
        f"📥 **Upload Account — {cc}**\n\nEnter the phone number (international, e.g. +911234567890) for the account. You'll receive a code in Telegram; paste it here.",
        parse_mode="Markdown")


# ---------- Upload flow with real Telethon sign-in ----------
async def cmd_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Command wrapper for admin_upload_cb"""
    await admin_upload_cb(update, context)


async def handle_phone_number(update: Update,
                              context: ContextTypes.DEFAULT_TYPE):
    """Admin sends phone number to start Telethon sign-in flow."""
    admin_id = update.effective_user.id
    text = update.message.text.strip()
    phone = text
    if not phone.startswith("+") or len(phone) < 8:
        await update.message.reply_text(
            "Please send a valid phone number in international format (e.g. +911234567890)."
        )
        return

    # Prepare session filename
    safe_phone = phone.replace("+", "").replace(" ", "").replace("-", "")
    session_fname = f"{safe_phone}.session"
    session_path = os.path.join(SESSION_DIR, session_fname)

    # create client and send code
    client = TelegramClient(session_path, CONFIG["API_ID"], CONFIG["API_HASH"])
    try:
        await client.connect()
        sent = await client.send_code_request(phone)
        phone_code_hash = getattr(sent, "phone_code_hash", None)
    except PhoneNumberInvalidError:
        await update.message.reply_text("❌ Invalid phone number for Telegram.")
        await client.disconnect()
        return
    except Exception as e:
        logger.exception("Failed to send code request: %s", e)
        await update.message.reply_text(f"❌ Failed to send code request: {e}")
        try:
            await client.disconnect()
        except Exception:
            pass
        return

    # store pending
    PENDING_UPLOADS[admin_id] = {
        "phone": phone,
        "phone_code_hash": phone_code_hash,
        "session_fname": session_fname,
        "session_path": session_path,
        "country": context.user_data.get('upload_country', 'US'),
        "step": "waiting_otp"
    }

    await update.message.reply_text(
        f"✅ Code sent to {phone}\n\n📱 Please check:\n• Telegram app notifications\n• SMS messages\n• Other Telegram logged-in devices\n\nSend the OTP code you received:"
    )


async def handle_otp_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Admin sends OTP back to bot to finish sign-in (sanitizes OTP and limits retries).
    This version sets pending['step']="waiting_2fa" and a sent_2fa_prompt flag so we don't re-prompt.
    """
    admin_id = update.effective_user.id
    if admin_id not in PENDING_UPLOADS or PENDING_UPLOADS[admin_id].get(
            "step") not in ("waiting_otp", ):
        await update.message.reply_text(
            "No pending phone verification. Use /upload to start.")
        return

    raw_otp = (update.message.text or "").strip()
    import re
    otp = re.sub(r"\D", "", raw_otp)  # normalize digits only

    if not otp:
        await update.message.reply_text(
            "Please send the numeric OTP you received (e.g. 45441).")
        return

    pending = PENDING_UPLOADS[admin_id]
    pending.setdefault("otp_attempts", 0)
    pending["otp_attempts"] += 1
    if pending["otp_attempts"] > 5:
        # too many OTP attempts — cancel and cleanup
        session_path = pending.get("session_path")
        try:
            if session_path and os.path.exists(session_path):
                os.remove(session_path)
        except Exception:
            pass
        PENDING_UPLOADS.pop(admin_id, None)
        await update.message.reply_text(
            "❌ Too many invalid OTP attempts. Upload cancelled and session removed."
        )
        return

    phone = pending["phone"]
    phone_code_hash = pending.get("phone_code_hash")
    session_path = pending["session_path"]
    session_fname = pending["session_fname"]

    await update.message.reply_text("⏳ Verifying OTP...")

    client = TelegramClient(session_path, CONFIG["API_ID"], CONFIG["API_HASH"])
    try:
        await client.connect()
        try:
            # attempt sign in with code
            await client.sign_in(phone=phone,
                                 code=otp,
                                 phone_code_hash=phone_code_hash)
        except SessionPasswordNeededError:
            # account requires 2FA password
            # Mark authoritative server-side state so message_handler routes 2FA replies here
            pending["step"] = "waiting_2fa"
            pending.setdefault("2fa_attempts", 0)

            # Avoid sending duplicate 2FA prompt: only send if not already sent
            if not pending.get("sent_2fa_prompt"):
                pending["sent_2fa_prompt"] = True
                # set context user flag for UI logic as well
                context.user_data['waiting_2fa'] = True
                context.user_data['pending_session_path'] = session_path
                context.user_data['pending_phone'] = phone

                await update.message.reply_text(
                    "🔒 This account requires a 2FA password.\n\n"
                    "Please **send the 2FA password** now, or send 'cancel' to abort."
                )
            else:
                # already prompted — give short acknowledgement
                await update.message.reply_text(
                    "🔒 Awaiting 2FA password (previously requested).")
            try:
                await client.disconnect()
            except Exception:
                pass
            return

        except PhoneCodeInvalidError:
            await update.message.reply_text(
                "❌ Invalid OTP code. Please try again.")
            try:
                await client.disconnect()
            except Exception:
                pass
            return

        # If sign_in succeeded without 2FA:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT INTO accounts (country_code, phone_number, session_file, uploaded_by, status, price, metadata) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (pending.get("country", "US"), phone, session_fname, admin_id,
                 "available", CONFIG["COUNTRY_PRICES"].get(
                     pending.get("country", "US"), 40.0), json.dumps({})))
            await db.commit()
            cur = await db.execute("SELECT last_insert_rowid()")
            row = await cur.fetchone()
            acc_id = row[0] if row else None

        # clear pending and set post-otp UI flags
        PENDING_UPLOADS.pop(admin_id, None)
        context.user_data['upload_after_otp_acc_id'] = acc_id
        context.user_data['upload_after_otp_session'] = session_fname
        context.user_data['upload_after_otp_phone'] = phone
        context.user_data['upload_after_otp_waiting_choice'] = True

        await update.message.reply_text(
            f"✅ **Login Successful!**\n\n📱 **Number:** `{phone}`\n\n"
            "Does this account have 2FA password?\n\nPlease choose:\n"
            "• Send the 2FA password now, OR\n"
            "• Send 'skip' if no 2FA password\n"
            "• Send 'cancel' to abort",
            parse_mode="Markdown")
        try:
            await client.disconnect()
        except Exception:
            pass
        return

    except PhoneCodeInvalidError:
        await update.message.reply_text("❌ Invalid OTP code. Please try again."
                                        )
        try:
            await client.disconnect()
        except Exception:
            pass
        return
    except Exception as e:
        logger.exception("OTP sign-in failed: %s", e)
        await update.message.reply_text(f"❌ OTP verification failed: {e}")
        try:
            await client.disconnect()
        except Exception:
            pass
        return


async def handle_2fa_password_input(update: Update,
                                    context: ContextTypes.DEFAULT_TYPE):
    """
    Finish sign-in when a SessionPasswordNeededError occurred, or handle the post-OTP choice stage.
    Uses server-side PENDING_UPLOADS[admin_id]['step'] == 'waiting_2fa' as authoritative.
    Ensures the 2FA prompt is not repeated.
    """
    admin_id = update.effective_user.id
    txt = (update.message.text or "").strip()

    # First: handle the 'post-OTP' choice stage (already-added account asking for storing 2FA)
    if context.user_data.get('upload_after_otp_waiting_choice'):
        if txt.lower() == "skip":
            acc_id = context.user_data.get('upload_after_otp_acc_id')
            phone = context.user_data.get('upload_after_otp_phone')
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute(
                    "UPDATE accounts SET two_fa_password=NULL WHERE id=?",
                    (acc_id, ))
                await db.commit()
            context.user_data.pop('upload_after_otp_waiting_choice', None)
            await update.message.reply_text(
                f"✅ Account Added Successfully!\n\n📱 Number: {phone}\n🔒 2FA: No password set\n\nThe account is now available for sale! 🎊"
            )
            return
        elif txt.lower() == "cancel":
            acc_id = context.user_data.get('upload_after_otp_acc_id')
            session_fname = context.user_data.get('upload_after_otp_session')
            session_path = os.path.join(
                SESSION_DIR, session_fname) if session_fname else None
            async with aiosqlite.connect(DB_PATH) as db:
                if acc_id:
                    await db.execute("DELETE FROM accounts WHERE id=?",
                                     (acc_id, ))
                    await db.commit()
            try:
                if session_path and os.path.exists(session_path):
                    os.remove(session_path)
            except Exception:
                pass
            context.user_data.pop('upload_after_otp_waiting_choice', None)
            await update.message.reply_text(
                "✖️ Upload canceled and session removed.")
            return
        else:
            # save provided 2FA for the newly added account
            password = txt
            acc_id = context.user_data.get('upload_after_otp_acc_id')
            phone = context.user_data.get('upload_after_otp_phone')
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute(
                    "UPDATE accounts SET two_fa_password=? WHERE id=?",
                    (password, acc_id))
                await db.commit()
            context.user_data.pop('upload_after_otp_waiting_choice', None)
            await update.message.reply_text(
                f"✅ Account Added Successfully!\n\n📱 Number: {phone}\n🔒 2FA: Password set\n\nThe account is now available for sale! 🎊"
            )
            return

    # Authoritative: check server-side pending
    pending = PENDING_UPLOADS.get(admin_id)
    if pending and pending.get("step") == "waiting_2fa":
        # allow cancel
        if txt.lower() == "cancel":
            # cleanup pending and session file
            session_path = pending.get("session_path")
            try:
                if session_path and os.path.exists(session_path):
                    os.remove(session_path)
            except Exception:
                pass
            PENDING_UPLOADS.pop(admin_id, None)
            context.user_data.pop('waiting_2fa', None)
            await update.message.reply_text(
                "✖️ Sign-in aborted. Session removed.")
            return

        # process password
        password = txt
        pending.setdefault("2fa_attempts", 0)
        pending["2fa_attempts"] += 1
        if pending["2fa_attempts"] > 5:
            # too many tries -> cleanup
            session_path = pending.get("session_path")
            try:
                if session_path and os.path.exists(session_path):
                    os.remove(session_path)
            except Exception:
                pass
            PENDING_UPLOADS.pop(admin_id, None)
            context.user_data.pop('waiting_2fa', None)
            await update.message.reply_text(
                "❌ Too many incorrect 2FA attempts. Upload cancelled and session removed."
            )
            return

        phone = pending.get("phone")
        session_path = pending.get("session_path")
        session_fname = pending.get("session_fname")

        client = TelegramClient(session_path, CONFIG["API_ID"],
                                CONFIG["API_HASH"])
        try:
            await client.connect()
            try:
                await client.sign_in(password=password)
            except Exception as exc:
                logger.exception("2FA sign-in failed: %s", exc)
                # reply with single clear error — do NOT re-send initial 2FA prompt
                await update.message.reply_text(
                    "❌ Incorrect 2FA password. Please try again or send 'cancel' to abort."
                )
                try:
                    await client.disconnect()
                except Exception:
                    pass
                return

            # success -> persist account with 2FA in DB
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute(
                    "INSERT INTO accounts (country_code, phone_number, session_file, two_fa_password, uploaded_by, status, price, metadata) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (pending.get("country",
                                 "US"), phone, session_fname, password,
                     admin_id, "available", CONFIG["COUNTRY_PRICES"].get(
                         pending.get("country", "US"), 40.0), json.dumps({})))
                await db.commit()
                cur = await db.execute("SELECT last_insert_rowid()")
                row = await cur.fetchone()
                acc_id = row[0] if row else None

            # cleanup and confirmation
            PENDING_UPLOADS.pop(admin_id, None)
            # clear the sent_2fa_prompt flag if present (defensive)
            pending.pop("sent_2fa_prompt", None)
            context.user_data.pop('waiting_2fa', None)
            await update.message.reply_text(
                f"✅ **Login Successful!**\n\n📱 **Number:** `{phone}`\n\n✅ Account Added Successfully!\n\n📱 Number: {phone}\n🔒 2FA: Password set\n\nThe account is now available for sale! 🎊",
                parse_mode="Markdown")
            try:
                await client.disconnect()
            except Exception:
                pass
            return
        except Exception as e:
            logger.exception("Error finishing 2FA sign-in: %s", e)
            await update.message.reply_text(
                f"❌ Error finishing 2FA sign-in: {e}")
            try:
                await client.disconnect()
            except Exception:
                pass
            return

    # fallback
    await update.message.reply_text(
        "I didn't understand that. Use /upload to start an account upload.")


# ---------- One-time OTP monitor (listens to chat 777000 both directions) ----------
async def monitor_telegram_messages(context: ContextTypes.DEFAULT_TYPE, user_id: int, acc_id: int, phone: str, session_path: str):
    """
    Drop-in replacement monitor:
    - Monitors messages related to TARGET_UID (777000) both incoming and outgoing.
    - Extracts OTPs in formats:
        * 'Login Code: 45441'
        * '4 5 4 4 1'
        * '45441'
    - Forwards once with Get New OTP / Done buttons, then stops.
    """
    TARGET_UID = 777000
    client = TelegramClient(session_path, CONFIG["API_ID"], CONFIG["API_HASH"])

    try:
        await client.start()
    except Exception as e:
        logger.error("Monitor: failed to start Telethon client for %s: %s", session_path, e)
        try:
            await client.disconnect()
        except Exception:
            pass
        return

    # regexes
    primary_re = re.compile(r'Login Code:\s*([\d\s\-]{5,20})', re.IGNORECASE)
    spaced_five_re = re.compile(r'((?:\d[\s\-]*){5})')
    fallback_re = re.compile(r'\b(\d{5})\b')

    forwarded_event = asyncio.Event()

    async def _handler(event):
        try:
            msg = event.message
            if not msg:
                return

            # extract text
            text = None
            if getattr(msg, "message", None) is not None:
                text = msg.message
            else:
                try:
                    text = msg.raw_text
                except Exception:
                    text = str(msg)

            # determine relevance (from/to TARGET_UID)
            sender = getattr(msg, "sender_id", None)
            is_from_target = (sender == TARGET_UID)

            is_to_target = False
            try:
                to_id = getattr(msg, "to_id", None)
                if to_id is not None and getattr(to_id, "user_id", None) == TARGET_UID:
                    is_to_target = True
                else:
                    try:
                        if to_id and str(to_id).find(str(TARGET_UID)) != -1:
                            is_to_target = True
                    except Exception:
                        pass
            except Exception:
                pass

            if getattr(msg, "out", False) and not is_to_target:
                try:
                    peer = getattr(event, "peer_id", None)
                    if peer is not None and hasattr(peer, "user_id") and peer.user_id == TARGET_UID:
                        is_to_target = True
                except Exception:
                    pass

            if not (is_from_target or is_to_target):
                return

            logger.info("Monitor matched message (from=%s to=%s) text=%r", is_from_target, is_to_target, text)

            # extract OTP
            otp = None
            m = primary_re.search(text or "")
            if m:
                candidate = re.sub(r'\D', '', m.group(1) or "")
                if len(candidate) >= 5:
                    otp = candidate[:5]

            if not otp:
                m2 = spaced_five_re.search(text or "")
                if m2:
                    candidate = re.sub(r'\D', '', m2.group(1) or "")
                    if len(candidate) >= 5:
                        otp = candidate[:5]

            if not otp:
                m3 = fallback_re.search(text or "")
                if m3:
                    otp = m3.group(1)

            if not otp:
                return

            # fetch two_fa if stored
            two_fa = None
            try:
                async with aiosqlite.connect(DB_PATH) as db:
                    cur = await db.execute("SELECT two_fa_password FROM accounts WHERE id=?", (acc_id,))
                    row = await cur.fetchone()
                    if row:
                        two_fa = row[0]
            except Exception as e:
                logger.warning("Monitor DB read failed: %s", e)

            send_text = f"🔐 **OTP Received**\n\n📱 **Number:** `{phone}`\n🔢 **OTP Code:** `{otp}`\n"
            if two_fa:
                send_text += f"\n🔐 **2FA Password:** `{two_fa}`\n"
            send_text += "\nUse this code in Telegram to continue login."

            kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Get New OTP", callback_data=f"getotp_{acc_id}"),
                                        InlineKeyboardButton("✅ Done", callback_data=f"done_{acc_id}")]])
            try:
                await context.bot.send_message(chat_id=user_id, text=send_text, parse_mode="Markdown", reply_markup=kb)
                logger.info("Monitor forwarded OTP %s for acc %s -> user %s", otp, acc_id, user_id)
            except Exception as e:
                logger.error("Monitor failed to forward OTP: %s", e)

            forwarded_event.set()

        except Exception as e:
            logger.exception("Exception in monitor handler: %s", e)

    client.add_event_handler(_handler, events.NewMessage)

    try:
        await asyncio.wait_for(forwarded_event.wait(), timeout=600)
    except asyncio.TimeoutError:
        logger.info("Monitor timed out for acc %s", acc_id)
    finally:
        try:
            client.remove_event_handler(_handler, events.NewMessage)
        except Exception:
            pass
        try:
            await client.disconnect()
        except Exception:
            pass


# ---------- get_otp and done callbacks ----------
async def get_otp_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    _, acc_id_s = q.data.split("_", 1)
    acc_id = int(acc_id_s)
    # get account
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT phone_number, session_file, two_fa_password FROM accounts WHERE id=?",
            (acc_id, ))
        row = await cur.fetchone()
    if not row:
        await q.answer("Account not found!", show_alert=True)
        return
    phone, session_file, two_fa_password = row
    if not session_file:
        await q.answer("Session file missing!", show_alert=True)
        return
    session_path = os.path.join(SESSION_DIR, session_file)
    # update UI
    kb = [[InlineKeyboardButton("✅ Done", callback_data=f"done_{acc_id}")]]
    await q.edit_message_text(
        f"📱 **TRY LOGIN**\n\n📞 **Number:** `{phone}`\n\nSteps:\n1. Paste number in Telegram app\n2. Tap Continue\n\n🔄 **Monitoring for OTP...**\n\n_When you receive OTP it will be forwarded here._",
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode="Markdown")
    # start monitor in background
    asyncio.create_task(
        monitor_telegram_messages(context, q.from_user.id, acc_id, phone,
                                  session_path))


async def done_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    _, acc_id_s = q.data.split("_", 1)
    acc_id = int(acc_id_s)
    user = await get_user(q.from_user.id, q.from_user.username)
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT phone_number, price, session_file, country_code FROM accounts WHERE id=?",
            (acc_id, ))
        row = await cur.fetchone()
    if not row:
        await q.answer("Account not found!", show_alert=True)
        return
    phone, price, session_file, country = row
    # check balance
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT balance FROM users WHERE id=?",
                               (q.from_user.id, ))
        row = await cur.fetchone()
        bal = (row[0] if row else 0.0)
    if bal < price:
        # release reservation in case it was reserved
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "UPDATE accounts SET status='available', metadata=NULL WHERE id=?",
                (acc_id, ))
            await db.commit()
        await q.edit_message_text(
            f"❌ **Insufficient Balance**\n\nRequired: ₹{price}\nYour Balance: ₹{bal}\n\nAccount released. Please add balance and try again.",
            parse_mode="Markdown")
        return
    # deduct and mark as sold
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET balance = balance - ? WHERE id=?",
                         (price, q.from_user.id))
        await db.execute(
            "UPDATE accounts SET status='sold', metadata=NULL WHERE id=?",
            (acc_id, ))
        await db.execute(
            "INSERT INTO transactions (user_id, account_id, amount, type) VALUES (?, ?, ?, 'purchase')",
            (q.from_user.id, acc_id, price))
        await db.commit()
    # notify admins
    for admin_id in CONFIG["ADMIN_IDS"]:
        try:
            await context.bot.send_message(
                admin_id,
                f"💰 New sale: Buyer {user['username'] or user['id']}\nNumber: {phone}\nAmount: ₹{price}",
                parse_mode="Markdown")
        except Exception:
            pass
    kb = [[
        InlineKeyboardButton("🛒 Buy Another", callback_data="buy_accounts")
    ]]
    await q.edit_message_text(
        f"🎉 **Purchase Successful!**\n\n📱 **Number:** `{phone}`\n💰 **Amount Paid:** ₹{price}\n\nThank you for your purchase!",
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode="Markdown")


# ---------- Admin commands (complete) ----------
@admin_only
async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT status, COUNT(*) FROM accounts GROUP BY status")
        status_rows = await cur.fetchall()
        cur = await db.execute(
            "SELECT country_code, status, COUNT(*) FROM accounts GROUP BY country_code, status ORDER BY country_code, status"
        )
        country_rows = await cur.fetchall()
        cur = await db.execute("SELECT COUNT(*) FROM users")
        user_count = (await cur.fetchone())[0]
        cur = await db.execute(
            "SELECT SUM(amount) FROM transactions WHERE type='purchase'")
        revenue = (await cur.fetchone())[0] or 0
    text = "📊 **Bot Statistics**\n\n"
    text += f"👥 **Total Users:** {user_count}\n💰 **Total Revenue:** ₹{revenue}\n\n**Account Status:**\n"
    for status, count in status_rows:
        text += f"• {status.title()}: {count}\n"
    text += "\n**Country-wise:**\n"
    curr = None
    for country, status, count in country_rows:
        if country != curr:
            text += f"\n{country_flag(country)} {country}:\n"
            curr = country
        text += f"  └ {status}: {count}\n"
    
    if update.callback_query:
        kb = [[InlineKeyboardButton("🔙 Back to Admin", callback_data="admin_panel")]]
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
    else:
        await update.message.reply_text(text, parse_mode="Markdown")


@admin_only
async def cmd_accounts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT id, country_code, phone_number, status, price FROM accounts ORDER BY status, country_code"
        )
        rows = await cur.fetchall()
    if not rows:
        if update.callback_query:
            kb = [[InlineKeyboardButton("🔙 Back to Admin", callback_data="admin_panel")]]
            await update.callback_query.edit_message_text("No accounts found.", reply_markup=InlineKeyboardMarkup(kb))
        else:
            await update.message.reply_text("No accounts found.")
        return
    res = "📱 **Accounts**\n\n"
    for r in rows[:200]:
        acc_id, cc, phone, status, price = r
        res += f"• ID {acc_id} | {country_flag(cc)} {cc} | {phone} | {status} | ₹{price}\n"
    
    if update.callback_query:
        kb = [[InlineKeyboardButton("🔙 Back to Admin", callback_data="admin_panel")]]
        await update.callback_query.edit_message_text(res, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
    else:
        await update.message.reply_text(res, parse_mode="Markdown")


_owner_escaped = CONFIG['OWNER_HANDLE'].replace('_', '\\_')
FOOTER = f"\n\n{CONFIG['DEVELOPER_CREDITS']} • @{_owner_escaped}"


async def send_admin_reply(update: Update, text: str):
    text_with_footer = f"{text}{FOOTER}"
    if update.callback_query:
        try:
            await update.callback_query.edit_message_text(
                text_with_footer, parse_mode="Markdown")
            return
        except Exception:
            pass
    if update.message:
        await update.message.reply_text(text_with_footer,
                                        parse_mode="Markdown")
    else:
        await update.effective_chat.send_message(text_with_footer,
                                                 parse_mode="Markdown")


@admin_only
async def cmd_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args or []
    if not args:
        await send_admin_reply(
            update,
            "Usage:\n/balance <user_id_or_username>\n/balance set <user_id_or_username> <amount>"
        )
        return
    if args[0].lower() == "set":
        if len(args) < 3:
            await send_admin_reply(
                update, "Usage: /balance set <user_id_or_username> <amount>")
            return
        target = args[1]
        amount = args[2]
        try:
            amt = float(amount)
        except Exception:
            await send_admin_reply(update, "Invalid amount.")
            return
        user_id = None
        if target.isdigit():
            user_id = int(target)
        else:
            username = target.lstrip("@")
            async with aiosqlite.connect(DB_PATH) as db:
                cur = await db.execute("SELECT id FROM users WHERE username=?",
                                       (username, ))
                r = await cur.fetchone()
                if r:
                    user_id = r[0]
        if not user_id:
            await send_admin_reply(update, f"User not found: {target}")
            return
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("UPDATE users SET balance=? WHERE id=?",
                             (amt, user_id))
            await db.commit()
            cur = await db.execute("SELECT balance FROM users WHERE id=?",
                                   (user_id, ))
            new = (await cur.fetchone())[0]
        await send_admin_reply(update,
                               f"✅ Balance set for user {user_id}: ₹{new}")
        return
    # show balance
    target = args[0]
    user_id = None
    if target.isdigit():
        user_id = int(target)
    else:
        username = target.lstrip("@")
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute("SELECT id FROM users WHERE username=?",
                                   (username, ))
            r = await cur.fetchone()
            if r: user_id = r[0]
    if not user_id:
        await send_admin_reply(update, f"User not found: {target}")
        return
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT balance FROM users WHERE id=?",
                               (user_id, ))
        row = await cur.fetchone()
        bal = (row[0] if row else 0.0)
    await send_admin_reply(update, f"💳 Balance for user {user_id}: ₹{bal}")


@admin_only
async def cmd_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args or []
    if not args:
        await send_admin_reply(update, "Usage: /broadcast <message>")
        return
    msg = " ".join(args)
    sent = 0
    failed = 0
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT id FROM users")
        rows = await cur.fetchall()
    user_ids = [r[0] for r in rows]
    for uid in user_ids:
        try:
            await context.bot.send_message(uid,
                                           f"{msg}{FOOTER}",
                                           parse_mode="Markdown")
            sent += 1
            await asyncio.sleep(0.05)
        except Exception:
            failed += 1
    await send_admin_reply(update,
                           f"Broadcast done. Sent: {sent}, Failed: {failed}")


@admin_only
async def cmd_ban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /ban <username_or_userid>")
        return
    target = context.args[0]
    user_id = None
    if target.isdigit():
        user_id = int(target)
    else:
        username = target.lstrip("@")
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute("SELECT id FROM users WHERE username=?",
                                   (username, ))
            r = await cur.fetchone()
            if r: user_id = r[0]
    if not user_id:
        await update.message.reply_text("User not found.")
        return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO bans (user_id, reason) VALUES (?, ?)",
                         (user_id, "Admin ban"))
        await db.commit()
    await update.message.reply_text(f"✅ User {target} banned.")


@admin_only
async def cmd_unban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /unban <username_or_userid>")
        return
    target = context.args[0]
    user_id = None
    if target.isdigit(): user_id = int(target)
    else:
        username = target.lstrip("@")
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute("SELECT id FROM users WHERE username=?",
                                   (username, ))
            r = await cur.fetchone()
            if r: user_id = r[0]
    if not user_id:
        await update.message.reply_text("User not found.")
        return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM bans WHERE user_id=?", (user_id, ))
        await db.commit()
    await update.message.reply_text(f"✅ User {target} unbanned.")


@admin_only
async def cmd_addcoins(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text(
            "Usage: /addcoins <username_or_userid> <amount>")
        return
    target = context.args[0]
    try:
        amount = float(context.args[1])
    except Exception:
        await update.message.reply_text("Invalid amount.")
        return
    user_id = None
    if target.isdigit(): user_id = int(target)
    else:
        username = target.lstrip("@")
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute("SELECT id FROM users WHERE username=?",
                                   (username, ))
            r = await cur.fetchone()
            if r: user_id = r[0]
    if not user_id:
        await update.message.reply_text("User not found.")
        return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET balance = balance + ? WHERE id=?",
                         (amount, user_id))
        await db.execute(
            "INSERT INTO transactions (user_id, amount, type) VALUES (?, ?, 'admin_topup')",
            (user_id, amount))
        await db.commit()
        cur = await db.execute("SELECT balance FROM users WHERE id=?",
                               (user_id, ))
        new = (await cur.fetchone())[0]
    await update.message.reply_text(
        f"✅ Added ₹{amount} to user {target}\nNew balance: ₹{new}")


@admin_only
async def cmd_deductcoin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text(
            "Usage: /deductcoin <username_or_userid> <amount>")
        return
    target = context.args[0]
    try:
        amount = float(context.args[1])
    except Exception:
        await update.message.reply_text("Invalid amount.")
        return
    user_id = None
    if target.isdigit(): user_id = int(target)
    else:
        username = target.lstrip("@")
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute("SELECT id FROM users WHERE username=?",
                                   (username, ))
            r = await cur.fetchone()
            if r: user_id = r[0]
    if not user_id:
        await update.message.reply_text("User not found.")
        return
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT balance FROM users WHERE id=?",
                               (user_id, ))
        current = (await cur.fetchone())[0]
        if current < amount:
            await update.message.reply_text(
                f"User has only ₹{current}, cannot deduct ₹{amount}.")
            return
        await db.execute("UPDATE users SET balance = balance - ? WHERE id=?",
                         (amount, user_id))
        await db.execute(
            "INSERT INTO transactions (user_id, amount, type) VALUES (?, ?, 'admin_deduction')",
            (user_id, -amount))
        await db.commit()
        cur = await db.execute("SELECT balance FROM users WHERE id=?",
                               (user_id, ))
        new = (await cur.fetchone())[0]
    await update.message.reply_text(
        f"✅ Deducted ₹{amount} from {target}\nNew balance: ₹{new}")


@admin_only
async def cmd_clearstats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Clear all transaction records and sales statistics (admin only)"""
    async with aiosqlite.connect(DB_PATH) as db:
        # Get count before deletion
        cur = await db.execute("SELECT COUNT(*) FROM transactions")
        count = (await cur.fetchone())[0]
        
        # Delete all transactions
        await db.execute("DELETE FROM transactions")
        await db.commit()
    
    await send_admin_reply(
        update,
        f"🗑️ **Statistics Cleared**\n\n✅ Deleted {count} transaction records\n💰 Revenue stats reset to zero\n\n_Note: User balances and accounts remain unchanged._"
    )


# ---------- Message handler: coordinate flows ----------
async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Central message handler. Order here is important:
      1) If admin has a server-side pending upload waiting_2fa -> handle 2FA input.
      2) If admin has a post-OTP 'choice' stage -> handle that.
      3) If admin has pending upload waiting_otp -> handle OTP input (normalize).
      4) If admin is in interactive phone step (upload_step == 'phone') -> handle phone.
      5) Otherwise fallback.
    """
    uid = update.effective_user.id
    txt = (update.message.text or "").strip()

    # 1) authoritative server-side waiting_2fa (finish Telethon sign_in)
    pending = PENDING_UPLOADS.get(uid)
    if pending and pending.get("step") == "waiting_2fa":
        # forward to our 2FA handler
        await handle_2fa_password_input(update, context)
        return

    # 2) post-OTP choice stage (save 2FA / skip / cancel)
    if context.user_data.get('upload_after_otp_waiting_choice'):
        await handle_2fa_password_input(update, context)
        return

    # 3) if admin has pending upload and step waiting_otp -> handle OTP (normalize)
    if uid in PENDING_UPLOADS and PENDING_UPLOADS[uid].get(
            "step") == "waiting_otp":
        await handle_otp_code(update, context)
        return

    # 4) phone entry step for admin upload
    if uid in CONFIG["ADMIN_IDS"] and context.user_data.get(
            'upload_step') == 'phone':
        await handle_phone_number(update, context)
        context.user_data.pop('upload_step', None)
        return

    # default fallback
    await update.message.reply_text(
        "Please use the menu buttons or /start to begin.",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("🏠 Start", callback_data="main_menu")]]))


# ---------- Callback router ----------
async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = update.callback_query.data
    try:
        if data == "buy_accounts":
            await buy_accounts_cb(update, context)
        elif data == "check_balance":
            await check_balance_cb(update, context)
        elif data == "main_menu":
            await show_main_menu_cb(update, context)
        elif data == "admin_panel":
            await admin_panel_cb(update, context)
        elif data.startswith("country_"):
            await country_cb(update, context)
        elif data.startswith("getotp_"):
            await get_otp_cb(update, context)
        elif data.startswith("done_"):
            await done_cb(update, context)
        elif data == "admin_upload":
            await admin_upload_cb(update, context)
        elif data.startswith("admin_country_"):
            await admin_country_cb(update, context)
        elif data == "admin_stats":
            await cmd_stats(update, context)
        elif data == "admin_accounts":
            await cmd_accounts(update, context)
        elif data == "verify_join":
            await verify_join_cb(update, context)
        elif data == "admin_broadcast":
            await update.callback_query.answer(
                "Use /broadcast <message> to send a broadcast.",
                show_alert=True)
        else:
            await update.callback_query.answer("Unknown action.")
    except Exception as e:
        logger.exception("Callback error: %s", e)
        try:
            await update.callback_query.answer("Error occurred!",
                                               show_alert=True)
        except Exception:
            pass


# ---------- Main entrypoint ----------
async def main():
    await init_db()
    http_request = HTTPXRequest(
        connect_timeout=CONFIG["HTTP_CONNECT_TIMEOUT"],
        read_timeout=CONFIG["HTTP_READ_TIMEOUT"],
        write_timeout=CONFIG["HTTP_WRITE_TIMEOUT"],
        pool_timeout=CONFIG["HTTP_POOL_TIMEOUT"],
    )
    global app
    app = ApplicationBuilder().token(
        CONFIG["BOT_TOKEN"]).request(http_request).build()

    # Register handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(callback_router))
    app.add_handler(
        MessageHandler(filters.TEXT & (~filters.COMMAND), message_handler))

    # Admin commands (registered as commands too)
    app.add_handler(CommandHandler("upload", cmd_upload))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("accounts", cmd_accounts))
    app.add_handler(CommandHandler("balance", cmd_balance))
    app.add_handler(CommandHandler("broadcast", cmd_broadcast))
    app.add_handler(CommandHandler("ban", cmd_ban))
    app.add_handler(CommandHandler("unban", cmd_unban))
    app.add_handler(CommandHandler("addcoins", cmd_addcoins))
    app.add_handler(CommandHandler("deductcoin", cmd_deductcoin))
    app.add_handler(CommandHandler("clearstats", cmd_clearstats))

    # Scheduler
    scheduler = AsyncIOScheduler(timezone=IST)
    scheduler.add_job(release_expired_reservations_tick,
                      "interval",
                      minutes=1,
                      coalesce=True,
                      max_instances=1)
    scheduler.start()

    # Start bot
    await app.initialize()
    await app.start()

    # Delete webhook to prevent conflicts with polling
    try:
        await app.bot.delete_webhook(drop_pending_updates=True)
        logger.info("Webhook deleted successfully")
    except Exception as e:
        logger.warning("Failed to delete webhook: %s", e)

    logger.info("Bot started")
    await app.updater.start_polling()

    try:
        await asyncio.Future()
    except asyncio.CancelledError:
        pass
    finally:
        try:
            stop = getattr(app.updater, "stop", None)
            if callable(stop):
                await stop()
        except Exception:
            pass
        await app.stop()
        await app.shutdown()


if __name__ == "__main__":
    try:
        print("🚀 Starting redesigned OTP Bot…")
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n🛑 Shutting down (Ctrl+C).")
    except Exception as e:
        print(f"❌ Fatal error: {e}")
        raise
