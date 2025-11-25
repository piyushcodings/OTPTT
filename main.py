# final bot.py
# Free OTP Bot - Heroku-safe persistent DB using Heroku Config Var (DB_DATA)
# Features:
# - ConfigVar DB (primary) with local cache fallback
# - One-time HV links + XTGLINKS shortener
# - Channel join required
# - Admin panel (reply keyboard) with full bypass
# - Country-based valid temp numbers (IN/US/UK/CA)
# - OTP templates for many apps
# - Animated "Waiting for OTP..." (3-10s) with message edits
# - Referral credited only after verification
# - No MongoDB required

import os
import json
import time
import random
import requests
from functools import wraps
from typing import Dict, Any, Optional, List

from pyrogram import Client, filters
from pyrogram.types import (
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)

# =======================
# CONFIG / ENV
# =======================
API_ID = int(os.environ.get("API_ID", "23907288"))
API_HASH = os.environ.get("API_HASH", "f9a47570ed19aebf8eb0f0a5ec1111e5")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8496569281:AAHuz4BPGlRuklpo21yYejBwxxbl59h7ao8")

# Admin IDs csv: "12345,67890"
ADMIN_IDS = [int(x) for x in os.environ.get("ADMIN_IDS", "5748100919").split(",") if x.strip().isdigit()]

# Heroku persistence (optional but recommended)
HEROKU_API_KEY = os.environ.get("HEROKU_API_KEY", "HRKU-523dd6da-a489-4adc-86d2-028b07bd7357")  # set in Heroku config if you want runtime writes to config var
HEROKU_APP_NAME = os.environ.get("HEROKU_APP_NAME", "otptt")  # your heroku app name
DB_CONFIGVAR_NAME = "DB_DATA"  # config var key to store DB JSON

# Local cache fallback (inside dyno). Will be lost on full deploy, but used as fallback
DB_CACHE_DIR = "/app/.data"
DB_CACHE_PATH = os.path.join(DB_CACHE_DIR, "db_cache.json")

# Defaults
DEFAULT_REQUIRED_INVITES = int(os.environ.get("DEFAULT_REQUIRED_INVITES", "5"))

# Basic safety checks
if not API_ID or not API_HASH or not BOT_TOKEN:
    print("WARNING: API_ID/API_HASH/BOT_TOKEN not fully set. Fill these in Heroku config vars before running.")

# =======================
# DB helpers (Config Var primary, local cache fallback)
# =======================
def _default_db() -> Dict[str, Any]:
    return {
        "users": {},  # key: str(uid) -> {verified, invites, referred_by, used_numbers}
        "settings": {
            "required_invites": DEFAULT_REQUIRED_INVITES,
            "channels": [],  # list of channel usernames or IDs
            "xtg_api_key": "",
            "numbers": [],
            "otps": []
        },
        "one_time_links": {}  # code -> {user_id, used, created_at}
    }

def ensure_cache_dir():
    try:
        if not os.path.exists(DB_CACHE_DIR):
            os.makedirs(DB_CACHE_DIR, exist_ok=True)
    except Exception as e:
        print("ensure_cache_dir failed:", e)

def load_db() -> Dict[str, Any]:
    """
    Attempt to load DB from Heroku Config Var DB_DATA.
    If not present or invalid, fall back to local cache file.
    If neither exists, return default structure.
    """
    # 1) Try config var
    db_raw = os.environ.get(DB_CONFIGVAR_NAME)
    if db_raw:
        try:
            db = json.loads(db_raw)
            if isinstance(db, dict):
                return db
            else:
                print("DB_DATA not a dict, falling back.")
        except Exception as e:
            print("Failed to parse DB_DATA env var:", e)

    # 2) Try local cache
    try:
        ensure_cache_dir()
        if os.path.exists(DB_CACHE_PATH):
            with open(DB_CACHE_PATH, "r") as f:
                db = json.load(f)
                if isinstance(db, dict):
                    return db
    except Exception as e:
        print("Failed to load local cache:", e)

    # 3) default
    return _default_db()

def save_db(db: Dict[str, Any]) -> bool:
    """
    Save DB.

    Strategy:
    - If HEROKU_API_KEY and HEROKU_APP_NAME present -> attempt to PATCH config vars via Heroku API (preferred).
    - If that fails or not configured -> write local cache file (best-effort).
    Returns True on at least one success.
    """
    saved = False
    # 1) Try Heroku API (preferred)
    if HEROKU_API_KEY and HEROKU_APP_NAME:
        try:
            url = f"https://api.heroku.com/apps/{HEROKU_APP_NAME}/config-vars"
            headers = {
                "Accept": "application/vnd.heroku+json; version=3",
                "Authorization": f"Bearer {HEROKU_API_KEY}",
                "Content-Type": "application/json",
            }
            payload = {DB_CONFIGVAR_NAME: json.dumps(db)}
            r = requests.patch(url, headers=headers, data=json.dumps(payload), timeout=15)
            if r.status_code in (200, 201):
                saved = True
                # Also update local cache for quick reads
                try:
                    ensure_cache_dir()
                    with open(DB_CACHE_PATH, "w") as f:
                        json.dump(db, f, indent=2)
                except:
                    pass
            else:
                print("Heroku API save failed code:", r.status_code, r.text)
        except Exception as e:
            print("Heroku API save exception:", e)

    # 2) Fallback: local cache file
    if not saved:
        try:
            ensure_cache_dir()
            with open(DB_CACHE_PATH, "w") as f:
                json.dump(db, f, indent=2)
            saved = True
        except Exception as e:
            print("Local cache save failed:", e)

    # 3) Also set os.environ[DB_CONFIGVAR_NAME] for the current process so future load_db() reads it
    try:
        os.environ[DB_CONFIGVAR_NAME] = json.dumps(db)
    except Exception:
        pass

    return saved

# Helper getters/setters for users
def get_user(db: Dict[str, Any], uid: int) -> Dict[str, Any]:
    s = db.get("users", {})
    return s.get(str(uid), {"verified": False, "invites": 0, "referred_by": None, "used_numbers": []})

def set_user(db: Dict[str, Any], uid: int, user_obj: Dict[str, Any]) -> None:
    db.setdefault("users", {})[str(uid)] = user_obj

# =======================
# Utility: not_joined_channels
# =======================
def not_joined_channels(client: Client, uid: int, db: Dict[str, Any]) -> List[str]:
    channels = db.get("settings", {}).get("channels", []) or []
    missing = []
    for ch in channels:
        try:
            mem = client.get_chat_member(ch, uid)
            if mem.status in ("left", "kicked"):
                missing.append(ch)
        except Exception:
            # if bot can't access channel or invalid channel, treat as not joined
            missing.append(ch)
    return missing

# =======================
# XTGLINKS helper
# =======================
def create_xtg_short(api_key: str, dest_url: str, alias: str) -> Optional[str]:
    """
    Call xtglinks.com/api?api=...&url=...&alias=...
    Return shortenedUrl on success, else None.
    """
    try:
        endpoint = "https://xtglinks.com/api"
        params = {"api": api_key, "url": dest_url, "alias": alias}
        r = requests.get(endpoint, params=params, timeout=12)
        data = r.json()
        # success structure expected per docs: {"status":"success","shortenedUrl":"https://xtglinks.com/.."}
        if data.get("status") == "success" and data.get("shortenedUrl"):
            return data.get("shortenedUrl")
        else:
            print("XTG response not success:", data)
            return None
    except Exception as e:
        print("XTG request failed:", e)
        return None

# =======================
# One-time HV link generator
# =======================
def generate_one_time_link(client: Client, uid: int, db: Dict[str, Any]) -> (str, str):
    code = f"HV_{uid}_{int(time.time())}_{random.randint(1000,9999)}"
    db.setdefault("one_time_links", {})[code] = {"user_id": uid, "used": False, "created_at": int(time.time())}
    # ensure saved immediately
    save_db(db)
    bot_username = client.get_me().username
    return f"https://t.me/{bot_username}?start={code}", code

# =======================
# Keyboards & Countries
# =======================
USER_KB = ReplyKeyboardMarkup([["📱 Get Temp Number"], ["🔗 My Invite Link"], ["❓ How to Use"]], resize_keyboard=True)
ADMIN_KB = ReplyKeyboardMarkup([
    ["🛠 Set Invites"],
    ["➕ Add Channel"],
    ["➖ Remove Channel"],
    ["📱 Add Number"],
    ["🔑 Set XTGLINKS Key"],
    ["📊 Stats"],
    ["📢 Broadcast"],
    ["⬅️ Back to Main Menu"]
], resize_keyboard=True)
JOIN_KB = ReplyKeyboardMarkup([["🔁 I Joined"]], resize_keyboard=True)

COUNTRIES = {"🇮🇳 India": "IN", "🇺🇸 USA": "US", "🇬🇧 UK": "UK", "🇨🇦 Canada": "CA"}
COUNTRY_KB = ReplyKeyboardMarkup([["🇮🇳 India", "🇺🇸 USA"], ["🇬🇧 UK", "🇨🇦 Canada"], ["⬅️ Back"]], resize_keyboard=True)

# =======================
# Number generators
# =======================
def generate_number(country_code: str) -> str:
    if country_code == "IN":
        start = random.choice(["6", "7", "8", "9"])
        rest = "".join(random.choice("0123456789") for _ in range(9))
        return "+91" + start + rest
    if country_code == "US":
        # NANP: +1 NXXNXXXXXX; ensure N not 0/1
        n1 = random.choice("23456789")
        n2 = "".join(random.choice("0123456789") for _ in range(9))
        return "+1" + n1 + n2
    if country_code == "UK":
        rest = "".join(random.choice("0123456789") for _ in range(9))
        return "+447" + rest  # mobile numbers in UK often start with 7 but we include 7 after +44
    if country_code == "CA":
        n1 = random.choice("23456789")
        nrest = "".join(random.choice("0123456789") for _ in range(9))
        return "+1" + n1 + nrest
    # default fallback
    return "+000000000000"

# =======================
# OTP utilities & templates
# =======================
def generate_otp() -> str:
    length = random.choice([5, 6])
    return "".join(random.choice("0123456789") for _ in range(length))

# Large-ish set of realistic templates (expandable)
OTP_TEMPLATES = {
    "WhatsApp": [
        "Your WhatsApp code: {otp}\nYou can also tap on this link to verify your phone: v.whatsapp.com/{otp}\nDon't share this code with others.",
        "WhatsApp verification code: {otp}\nThis code will expire in 10 minutes.",
        "{otp} is your WhatsApp verification code."
    ],
    "Telegram": [
        "Telegram code: {otp}\nYou can also tap on this link to log in:\nhttps://t.me/login/{otp}",
        "Telegram Login Code: {otp}\nDo NOT share this code.",
        "Your Telegram code is {otp}."
    ],
    "LinkedIn": [
        "LinkedIn verification code: {otp}\nEnter this code to continue.",
        "LinkedIn security code: {otp}\nKeep this code private."
    ],
    "Instagram": [
        "Instagram code: {otp}\nEnter this to continue.",
        "Instagram verification code: {otp}\nExpires in 10 minutes."
    ],
    "Google": [
        "G-{otp} is your Google verification code.",
        "Google verification code: {otp}\nDo not share with anyone."
    ],
    "Facebook": [
        "Facebook confirmation code: {otp}\nUse to continue.",
        "FB Login: {otp}\nDon't share this code."
    ],
    "Amazon": [
        "Amazon OTP: {otp}\nUse it to verify your account."
    ],
    "Microsoft": [
        "Microsoft security code: {otp}"
    ],
    "Paytm": [
        "Your Paytm verification code is {otp}. Do not disclose it."
    ],
    "PhonePe": [
        "PhonePe verification code: {otp}"
    ],
    "Swiggy": [
        "Swiggy OTP: {otp}\nEnter to complete your login."
    ],
    "Zomato": [
        "Zomato verification code: {otp}"
    ],
    "Uber": [
        "Uber verification code: {otp}"
    ],
    "Twitter": [
        "Twitter verification code: {otp}"
    ],
    "Discord": [
        "Discord login code: {otp}"
    ],
    "Netflix": [
        "Netflix code: {otp}\nUse this to finish login."
    ],
    "Apple": [
        "Apple ID verification code: {otp}"
    ],
    "Spotify": [
        "Spotify code: {otp}\nDon't share it."
    ],
    "Steam": [
        "Steam Guard code: {otp}"
    ],
    "Binance": [
        "Binance verification code: {otp}"
    ],
    "Coinbase": [
        "Coinbase OTP: {otp}"
    ],
    "Snapchat": [
        "Snapchat code: {otp}"
    ],
    "Flipkart": [
        "Flipkart OTP: {otp}"
    ],
    "Meesho": [
        "Meesho verification code: {otp}"
    ],
    "PayPal": [
        "PayPal verification: {otp}"
    ],
    "Others": [
        "{otp} is your verification code.",
        "Use {otp} to complete the login."
    ]
}

# =======================
# Waiting animation for OTP (edit message)
# =======================
def simulate_waiting_for_otp(msg):
    """
    Synchronously edit the provided message with dots for 3-10 seconds.
    This function is blocking (uses time.sleep) — acceptable in Pyrogram sync handlers.
    """
    try:
        total = random.randint(3, 10)
        elapsed = 0
        dots = 1
        while elapsed < total:
            try:
                text = "Waiting for OTP" + "." * dots
                msg.edit_text(text)
            except Exception:
                # ignore possible edit errors
                pass
            time.sleep(1)
            elapsed += 1
            dots = dots + 1
            if dots > 3:
                dots = 1
    except Exception as e:
        print("simulate_waiting_for_otp exception:", e)

def process_otp_and_send(msg, app_name):
    """
    Simulate waiting; generate OTP; send final message by editing original msg.
    """
    try:
        simulate_waiting_for_otp(msg)
    except Exception:
        pass

    otp = generate_otp()
    templates = OTP_TEMPLATES.get(app_name, OTP_TEMPLATES["Others"])
    final = random.choice(templates).format(otp=otp)

    try:
        msg.edit_text(f"📨 Incoming Message from *{app_name}*\n\n{final}", parse_mode="markdown")
    except Exception:
        try:
            msg.edit_text(f"📨 {app_name}: {final}")
        except:
            pass

# =======================
# Pyrogram app init
# =======================
app = Client("otpbot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# =======================
# START handler
# =======================
@app.on_message(filters.command("start") & filters.private)
def start_cmd(client, message):
    db = load_db()
    uid = message.from_user.id
    args = message.text.split()

    # Admin bypass: open admin panel directly
    if uid in ADMIN_IDS:
        return message.reply("Admin Panel:", reply_markup=ADMIN_KB)

    # HV auto-verify flow
    if len(args) > 1 and args[1].startswith("HV_"):
        code = args[1]
        links = db.get("one_time_links", {})
        info = links.get(code)
        if not info:
            return message.reply("❌ Invalid or expired verification link.")
        if info.get("used"):
            return message.reply("❌ This link was already used.")
        if int(info.get("user_id")) != uid:
            return message.reply("❌ This link is not for your account.")

        # mark used
        info["used"] = True
        db["one_time_links"][code] = info

        # verify user
        user = get_user(db, uid)
        user["verified"] = True

        # credit referral
        ref = user.get("referred_by")
        if ref:
            try:
                ref = int(ref)
                ref_user = get_user(db, ref)
                ref_user["invites"] = int(ref_user.get("invites", 0)) + 1
                set_user(db, ref, ref_user)
                try:
                    client.send_message(ref, f"✅ Your referral ({uid}) completed verification. +1 invite. Total: {ref_user['invites']}")
                except:
                    pass
            except Exception:
                pass

        set_user(db, uid, user)
        save_db(db)
        return message.reply("🎉 Verification completed! You can now use the bot.", reply_markup=USER_KB)

    # Normal start: capture referral
    ref = None
    if len(args) > 1:
        try:
            r = int(args[1])
            if r != uid:
                ref = r
        except:
            ref = None

    db = load_db()
    if str(uid) not in db.get("users", {}):
        user = get_user(db, uid)
        if ref:
            user["referred_by"] = ref
        set_user(db, uid, user)
        save_db(db)

    # Channel enforcement
    not_joined = not_joined_channels(client, uid, db)
    if not_joined:
        return message.reply("📛 You must join the required channels:\n" + "\n".join(not_joined), reply_markup=JOIN_KB)

    # If not verified -> create one-time link and XTGLINKS shorten
    user = get_user(db, uid)
    if not user.get("verified"):
        api_key = db.get("settings", {}).get("xtg_api_key") or ""
        if not api_key:
            return message.reply("❌ XTGLINKS API key not set by admin. Ask admin to set it.", reply_markup=USER_KB)

        one_time, code = generate_one_time_link(client, uid, db)
        alias = f"v{uid}_{int(time.time())}"
        short = create_xtg_short(api_key, one_time, alias)

        if not short or not str(short).startswith("http"):
            # cleanup dangling link
            db.get("one_time_links", {}).pop(code, None)
            save_db(db)
            return message.reply("❌ XTGLINKS API failed to produce a short URL. Ask admin to check API key.", reply_markup=USER_KB)

        return message.reply(
            "🧩 Human Verification Required\n\nClick the button below and follow instructions. You will be redirected back to the bot automatically.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Verify Now 🔗", url=short)]])
        )

    # finally show menu
    return message.reply("👋 Welcome back!", reply_markup=USER_KB)

# =======================
# USER message handler (menu, countries, OTP)
# =======================
@app.on_message(filters.text & filters.private)
def user_buttons(client, message):
    db = load_db()
    uid = message.from_user.id
    text = message.text.strip()

    # Admin bypass (admin uses same handler)
    if uid in ADMIN_IDS:
        if text == "/admin":
            return message.reply("Admin Panel:", reply_markup=ADMIN_KB)
        # admin-specific actions handled below

    else:
        # channel check
        not_joined = not_joined_channels(client, uid, db)
        if not_joined:
            return message.reply("📛 You must join required channels:\n" + "\n".join(not_joined), reply_markup=JOIN_KB)

        # verification
        user = get_user(db, uid)
        if not user.get("verified"):
            api_key = db.get("settings", {}).get("xtg_api_key") or ""
            if not api_key:
                return message.reply("❌ XTGLINKS API key not set by admin.", reply_markup=USER_KB)
            one_time, code = generate_one_time_link(client, uid, db)
            alias = f"v{uid}_{int(time.time())}"
            short = create_xtg_short(api_key, one_time, alias)
            if not short or not str(short).startswith("http"):
                db.get("one_time_links", {}).pop(code, None)
                save_db(db)
                return message.reply("❌ XTGLINKS API Error, try later.", reply_markup=USER_KB)
            return message.reply("🧩 Please verify yourself first:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Verify Now 🔗", url=short)]]))

    # at this point user is either admin or verified user
    db = load_db()
    user = get_user(db, uid)

    # ---- Main menu items ----
    if text == "❓ How to Use":
        req = db.get("settings", {}).get("required_invites", DEFAULT_REQUIRED_INVITES)
        return message.reply(f"📘 You must verify + invite {req} users to unlock temp numbers.\nUse your invite link from menu.", reply_markup=USER_KB)

    if text == "🔗 My Invite Link":
        bot_username = client.get_me().username
        link = f"https://t.me/{bot_username}?start={uid}"
        return message.reply(f"🔗 Your Invite Link:\n{link}\n\n👥 Verified Invites: {user.get('invites',0)}", reply_markup=USER_KB)

    # Get temp number -> show countries
    if text == "📱 Get Temp Number":
        req = db.get("settings", {}).get("required_invites", DEFAULT_REQUIRED_INVITES)
        if user.get("invites", 0) < req and uid not in ADMIN_IDS:
            return message.reply(f"⛔ You need {req} verified invites.\nCurrent: {user.get('invites',0)}", reply_markup=USER_KB)
        return message.reply("🌍 Select Country:", reply_markup=COUNTRY_KB)

    # Country selected
    if text in COUNTRIES:
        code = COUNTRIES[text]
        number = generate_number(code)
        # save used number for user
        user = get_user(db, uid)
        used = user.get("used_numbers", [])
        used.append(number)
        user["used_numbers"] = used
        set_user(db, uid, user)
        save_db(db)
        # show number and ask for Get OTP
        return message.reply(
            f"📱 Your Temp Number:\n`{number}`\n\nPress Get OTP to receive incoming verification messages.",
            parse_mode="markdown",
            reply_markup=ReplyKeyboardMarkup([["📨 Get OTP"], ["⬅️ Back"]], resize_keyboard=True)
        )

    # Get OTP flow
    if text == "📨 Get OTP":
        # Send initial "Waiting for OTP..." message and then edit it to final OTP
        try:
            msg = message.reply("Waiting for OTP...")
        except Exception:
            # fallback
            msg = message.reply("Waiting for OTP...")

        # pick random app
        app_name = random.choice(list(OTP_TEMPLATES.keys()))
        # process (blocking)
        process_otp_and_send(msg, app_name)
        return

    # Back
    if text == "⬅️ Back":
        return message.reply("Main Menu:", reply_markup=USER_KB)

    # ---- Admin section (reply keyboard UI) ----
    if uid in ADMIN_IDS:
        # Back to admin menu
        if text == "⬅️ Back to Main Menu":
            return message.reply("Admin Panel:", reply_markup=ADMIN_KB)

        if text == "🛠 Set Invites":
            return message.reply("Send: /setinvites 5", reply_markup=ADMIN_KB)
        if text.startswith("/setinvites"):
            try:
                n = int(text.split()[1])
                db["settings"]["required_invites"] = n
                save_db(db)
                return message.reply("Updated required invites.", reply_markup=ADMIN_KB)
            except:
                return message.reply("Format: /setinvites 5", reply_markup=ADMIN_KB)

        if text == "➕ Add Channel":
            return message.reply("Send: /addch @channelname", reply_markup=ADMIN_KB)
        if text.startswith("/addch"):
            try:
                ch = text.split()[1]
                db["settings"].setdefault("channels", []).append(ch)
                save_db(db)
                return message.reply("Added channel.", reply_markup=ADMIN_KB)
            except:
                return message.reply("Usage: /addch @channel", reply_markup=ADMIN_KB)

        if text == "➖ Remove Channel":
            return message.reply("Send: /rmch @channelname", reply_markup=ADMIN_KB)
        if text.startswith("/rmch"):
            try:
                ch = text.split()[1]
                if ch in db["settings"].get("channels", []):
                    db["settings"]["channels"].remove(ch)
                    save_db(db)
                return message.reply("Removed channel.", reply_markup=ADMIN_KB)
            except:
                return message.reply("Usage: /rmch @channel", reply_markup=ADMIN_KB)

        if text == "📱 Add Number":
            return message.reply("Send: /addnum +911234567890", reply_markup=ADMIN_KB)
        if text.startswith("/addnum"):
            try:
                num = text.split()[1]
                db["settings"].setdefault("numbers", []).append(num)
                save_db(db)
                return message.reply("Number added.", reply_markup=ADMIN_KB)
            except:
                return message.reply("Usage: /addnum <number>", reply_markup=ADMIN_KB)

        if text == "🔑 Set XTGLINKS Key":
            return message.reply("Send: /setxtg APIKEY", reply_markup=ADMIN_KB)
        if text.startswith("/setxtg"):
            try:
                key = text.split()[1]
                db["settings"]["xtg_api_key"] = key
                save_db(db)
                return message.reply("XTGLINKS API key updated.", reply_markup=ADMIN_KB)
            except:
                return message.reply("Usage: /setxtg <key>", reply_markup=ADMIN_KB)

        if text == "📊 Stats":
            total = len(db.get("users", {}))
            verified = sum(1 for u in db.get("users", {}).values() if u.get("verified"))
            return message.reply(f"Total users: {total}\nVerified: {verified}", reply_markup=ADMIN_KB)

        if text == "📢 Broadcast":
            return message.reply("Send: /bc Your message", reply_markup=ADMIN_KB)
        if text.startswith("/bc"):
            try:
                bc_msg = text.replace("/bc", "").strip()
                count = 0
                for k in db.get("users", {}).keys():
                    try:
                        client.send_message(int(k), bc_msg)
                        count += 1
                    except:
                        pass
                return message.reply(f"Broadcast sent to {count} users.", reply_markup=ADMIN_KB)
            except Exception:
                return message.reply("Broadcast failed.", reply_markup=ADMIN_KB)

    # default fallback
    return message.reply("Unknown command. Use the menu.", reply_markup=USER_KB)

# =======================
# Run
# =======================
if __name__ == "__main__":
    ensure_cache_dir()
    # ensure DB_DATA exists in env for consistency; do not overwrite if exists
    if not os.environ.get(DB_CONFIGVAR_NAME):
        # set in current process memory only; persistent save happens on save_db
        os.environ[DB_CONFIGVAR_NAME] = json.dumps(_default_db())
    print("🔥 OTP Bot (final) starting...")
    app.run()
