# ============================================================
# FREE OTP BOT - FINAL FULL SYSTEM
# ============================================================

# ✓ Country-wise numbers
# ✓ Valid number formats (IN/US/UK/CA)
# ✓ XTGLINKS verification
# ✓ Animated "Waiting for OTP"
# ✓ Random OTP templates (WhatsApp, Telegram, etc.)
# ✓ Referral system
# ✓ Channel join required
# ✓ Pure Reply Keyboard UI
# ✓ Admin Panel (Reply Keyboard)
# ✓ JSON DB (Heroku-safe)
# ============================================================

import os
import json
import time
import random
import requests
from functools import wraps
from pyrogram import Client, filters
from pyrogram.types import (
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton
)

# ============================================================
# ENV
# ============================================================
API_ID = int(os.environ.get("API_ID", "23907288"))
API_HASH = os.environ.get("API_HASH", "f9a47570ed19aebf8eb0f0a5ec1111e5")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8496569281:AAHuz4BPGlRuklpo21yYejBwxxbl59h7ao8")

ADMIN_IDS = [int(x) for x in os.environ.get("ADMIN_IDS", "5748100919").split(",")]

DB_DIR = "/app/.data"
DB_PATH = DB_DIR + "/database.json"

# ============================================================
# DATABASE HELPERS
# ============================================================
def ensure_db():
    if not os.path.exists(DB_DIR):
        os.makedirs(DB_DIR)
    if not os.path.exists(DB_PATH):
        with open(DB_PATH, "w") as f:
            json.dump({
                "users": {},
                "settings": {
                    "required_invites": 5,
                    "channels": [],
                    "xtg_api_key": "",
                    "numbers": [],
                    "otps": []
                },
                "one_time_links": {}
            }, f, indent=3)

def load_db():
    ensure_db()
    with open(DB_PATH, "r") as f:
        return json.load(f)

def save_db(db):
    with open(DB_PATH, "w") as f:
        json.dump(db, f, indent=3)

def get_user(db, uid):
    return db["users"].get(str(uid), {
        "verified": False,
        "invites": 0,
        "referred_by": None,
        "used_numbers": []
    })

def set_user(db, uid, data):
    db["users"][str(uid)] = data

# ============================================================
# XTGLINKS SHORTENER
# ============================================================
def create_xtg_short(api_key, dest_url, alias):
    try:
        r = requests.get("https://xtglinks.com/api", params={
            "api": api_key,
            "url": dest_url,
            "alias": alias
        })
        data = r.json()
        return data.get("shortenedUrl")
    except:
        return None
# ============================================================
# CHECK REQUIRED CHANNELS
# ============================================================
def not_joined_channels(client, uid, db):
    channels = db["settings"].get("channels", [])
    missing = []

    for ch in channels:
        try:
            member = client.get_chat_member(ch, uid)
            if member.status in ("left", "kicked"):
                missing.append(ch)
        except Exception:
            # If channel not found or bot not in channel, also treat as missing
            missing.append(ch)

    return missing
# ============================================================
# ONE-TIME VERIFICATION LINK
# ============================================================
def generate_one_time_link(app, uid, db):
    code = f"HV_{uid}_{int(time.time())}_{random.randint(1000,9999)}"
    db["one_time_links"][code] = {"user_id": uid, "used": False}
    save_db(db)
    bot_username = app.get_me().username
    return f"https://t.me/{bot_username}?start={code}", code

# ============================================================
# KEYBOARDS
# ============================================================
USER_KB = ReplyKeyboardMarkup(
    [
        ["📱 Get Temp Number"],
        ["🔗 My Invite Link"],
        ["❓ How to Use"]
    ],
    resize_keyboard=True
)

ADMIN_KB = ReplyKeyboardMarkup(
    [
        ["🛠 Set Invites"],
        ["➕ Add Channel"],
        ["➖ Remove Channel"],
        ["📱 Add Number"],
        ["🔑 Set XTGLINKS Key"],
        ["📊 Stats"],
        ["📢 Broadcast"],
        ["⬅️ Back to Main Menu"]
    ],
    resize_keyboard=True
)

JOIN_KB = ReplyKeyboardMarkup(
    [["🔁 I Joined"]],
    resize_keyboard=True
)

# ============================================================
# COUNTRIES
# ============================================================
COUNTRIES = {
    "🇮🇳 India": "IN",
    "🇺🇸 USA": "US",
    "🇬🇧 UK":  "UK",
    "🇨🇦 Canada": "CA"
}

COUNTRY_KB = ReplyKeyboardMarkup(
    [
        ["🇮🇳 India", "🇺🇸 USA"],
        ["🇬🇧 UK", "🇨🇦 Canada"],
        ["⬅️ Back"]
    ],
    resize_keyboard=True
)

# ============================================================
# VALID NUMBER GENERATORS
# ============================================================
def generate_number(country):
    if country == "IN":
        start = random.choice(["6","7","8","9"])
        rest = "".join(random.choice("0123456789") for _ in range(9))
        return "+91" + start + rest

    if country == "US":
        first = random.choice("23456789")
        second = random.choice("0123456789")
        third  = random.choice("0123456789")
        rest1 = "".join(random.choice("0123456789") for _ in range(7))
        return "+1" + first + second + third + rest1

    if country == "UK":
        rest = "".join(random.choice("0123456789") for _ in range(9))
        return "+447" + rest

    if country == "CA":
        first = random.choice("23456789")
        second = random.choice("0123456789")
        third  = random.choice("0123456789")
        rest1 = "".join(random.choice("0123456789") for _ in range(7))
        return "+1" + first + second + third + rest1

# ============================================================
# OTP GENERATION
# ============================================================
def generate_otp():
    return "".join(random.choice("0123456789") for _ in range(random.choice([5,6])))
# ===========================
# PART 2/4
# - OTP templates (many apps)
# - Waiting simulation (edit messages)
# - Pyrogram app init
# - /start handler (admin bypass + HV auto verify + channel & verification checks)
# ===========================

# ---------------------------
# OTP TEMPLATES (sample set, expand as needed)
# Each template uses {otp} placeholder
# ---------------------------
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
        "LinkedIn security code: {otp}\nKeep this code private.",
        "Use {otp} to verify your LinkedIn login."
    ],
    "Instagram": [
        "Instagram code: {otp}\nEnter this to continue.",
        "Instagram verification code: {otp}\nExpires in 10 minutes.",
        "Your Instagram login code is {otp}."
    ],
    "Google": [
        "G-{otp} is your Google verification code.",
        "Google verification code: {otp}\nDo not share with anyone.",
        "Your Google OTP is {otp}."
    ],
    "Facebook": [
        "Facebook confirmation code: {otp}\nUse to continue.",
        "FB Login: {otp}\nDon't share this code.",
        "Your Facebook code is {otp}."
    ],
    "Amazon": [
        "Amazon OTP: {otp}\nUse it to verify your account.",
        "Your Amazon security code is {otp}.",
        "Amazon verification: {otp}"
    ],
    "Microsoft": [
        "Microsoft security code: {otp}",
        "Use {otp} to sign into Microsoft services.",
        "Your Microsoft login code: {otp}"
    ],
    "Paytm": [
        "Your Paytm verification code is {otp}. Do not disclose it.",
        "Paytm OTP: {otp}",
        "{otp} is your Paytm code."
    ],
    "PhonePe": [
        "PhonePe verification code: {otp}",
        "PhonePe OTP: {otp}",
        "Use {otp} to continue on PhonePe."
    ],
    "Swiggy": [
        "Swiggy OTP: {otp}\nEnter to complete your login.",
        "Your Swiggy verification code: {otp}.",
        "Swiggy code {otp} — valid for a short time."
    ],
    "Zomato": [
        "Zomato verification code: {otp}",
        "Your Zomato OTP is {otp}",
        "{otp} — use this to verify your Zomato account"
    ],
    "Uber": [
        "Uber verification code: {otp}",
        "Use {otp} to login to Uber.",
        "Uber OTP: {otp}."
    ],
    "LinkedIn-2": [
        "Your LinkedIn code is {otp}",
        "Use {otp} to verify account on LinkedIn"
    ],
    "Twitter": [
        "Twitter verification code: {otp}",
        "Your Twitter login code: {otp}"
    ],
    "Discord": [
        "Discord login code: {otp}\nUse this to sign in.",
        "Discord verification: {otp}"
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
    "Instagram-2": [
        "Instagram login code: {otp}"
    ],
    "GMail": [
        "Gmail verification code: {otp}"
    ],
    "TikTok": [
        "TikTok code: {otp}"
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

# (You can extend OTP_TEMPLATES easily by adding keys and arrays)

# ---------------------------
# Simulate waiting for OTP
# This edits the message repeatedly: "Waiting for OTP." -> ".." -> "..." etc
# Random total wait 3-10 seconds
# ---------------------------
def simulate_waiting_for_otp(msg):
    """
    msg: a pyrogram Message instance that will be edited repeatedly.
    This is synchronous (uses time.sleep). It's OK in Pyrogram handler because
    we're running in threadpool for sync handlers. For heavy load, convert to async.
    """
    try:
        total = random.randint(3, 10)  # total wait seconds
        elapsed = 0
        dots = 1
        # initial small delay to show first message
        while elapsed < total:
            try:
                text = "Waiting for OTP" + "." * dots
                msg.edit_text(text)
            except Exception:
                # ignore edit failures (message deleted or can't edit)
                pass
            time.sleep(1)  # 1 second per edit step
            elapsed += 1
            dots = dots + 1
            if dots > 3:
                dots = 1
    except Exception:
        pass

# ---------------------------
# Initialize Pyrogram client
# ---------------------------
app = Client("otpbot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# ---------------------------
# /start handler
# - Admin bypass
# - HV one-time handling (HV_...)
# - Referral capture
# - Channel check and verification creation (XTGLINKS)
# ---------------------------
@app.on_message(filters.command("start") & filters.private)
def start_cmd(client, message):
    db = load_db()
    uid = message.from_user.id
    args = message.text.split()

    # ADMIN BYPASS: open admin panel immediately
    if uid in ADMIN_IDS:
        return message.reply("Admin Panel:", reply_markup=ADMIN_KB)

    # HV auto-verify if start param like HV_xxx
    if len(args) > 1 and args[1].startswith("HV_"):
        code = args[1]
        links = db.get("one_time_links", {})
        if code not in links:
            return message.reply("❌ Invalid or expired verification link.")
        info = links[code]
        if info.get("used"):
            return message.reply("❌ This link was already used.")
        if int(info.get("user_id")) != uid:
            return message.reply("❌ This link is not for your account.")

        # mark used and verify user
        info["used"] = True
        db["one_time_links"][code] = info

        user = get_user(db, uid)
        user["verified"] = True

        # credit referrer now (only when verification completes)
        ref = user.get("referred_by")
        if ref:
            try:
                ref = int(ref)
                ref_user = get_user(db, ref)
                ref_user["invites"] = ref_user.get("invites", 0) + 1
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

    # Normal start: capture referral if present
    ref = None
    if len(args) > 1:
        try:
            ref = int(args[1])
            if ref == uid:
                ref = None
        except:
            ref = None

    # create user record if new
    if str(uid) not in db.get("users", {}):
        user = get_user(db, uid)
        if ref:
            user["referred_by"] = ref
        set_user(db, uid, user)
        save_db(db)

    # enforce channel join
    not_joined = not_joined_channels(client, uid, db)
    if not_joined:
        return message.reply(
            "📛 You must join the required channels first:\n" + "\n".join(not_joined),
            reply_markup=JOIN_KB
        )

    # if not verified -> create one-time link + short it with XTGLINKS
    user = get_user(db, uid)
    if not user.get("verified"):
        api_key = db["settings"].get("xtg_api_key") or ""
        if not api_key:
            return message.reply("❌ XTGLINKS API key not set by admin. Ask admin to set it.", reply_markup=USER_KB)

        one_time, code = generate_one_time_link(client, uid, db)
        alias = f"v{uid}_{int(time.time())}"
        short = create_xtg_short(api_key, one_time, alias)

        # fail-safe: if short invalid, return friendly message
        if not short or not str(short).startswith("http"):
            # Clean up one_time entry to avoid dangling codes (optional)
            db["one_time_links"].pop(code, None)
            save_db(db)
            return message.reply("❌ XTGLINKS API failed to produce a short URL. Ask admin to check API key.", reply_markup=USER_KB)

        # send verification prompt WITH inline button (allowed)
        return message.reply(
            "🧩 Human Verification Required\n\nClick the button below and follow instructions. You will be redirected back to the bot automatically.",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("Verify Now 🔗", url=short)]]
            )
        )

    # finally show main menu
    return message.reply("👋 Welcome back!", reply_markup=USER_KB)
# ============================================================
# PART 3/4
# USER MENU HANDLER
# - Country selection
# - Generate valid temp number
# - OTP request (animated)
# ============================================================

@app.on_message(filters.text & filters.private)
def user_buttons(client, message):
    db = load_db()
    uid = message.from_user.id
    text = message.text

    # ----------------------------------------------------
    # ADMIN BYPASS (Admin never forced to join/verify)
    # ----------------------------------------------------
    if uid in ADMIN_IDS:
        if text == "/admin":
            return message.reply("Admin Panel:", reply_markup=ADMIN_KB)
        # Admin-specific buttons handled later in admin section

    # ----------------------------------------------------
    # CHANNEL CHECK (users only)
    # ----------------------------------------------------
    not_joined = not_joined_channels(client, uid, db)
    if not_joined and uid not in ADMIN_IDS:
        return message.reply(
            "📛 You must join the required channels:\n" + "\n".join(not_joined),
            reply_markup=JOIN_KB
        )

    # ----------------------------------------------------
    # VERIFICATION CHECK (users only)
    # ----------------------------------------------------
    user = get_user(db, uid)
    if not user.get("verified") and uid not in ADMIN_IDS:

        api_key = db["settings"].get("xtg_api_key")
        if not api_key:
            return message.reply("❌ XTGLINKS API key not set by admin.")

        # Create one-time link
        one_time, code = generate_one_time_link(client, uid, db)
        alias = f"v{uid}_{int(time.time())}"
        short = create_xtg_short(api_key, one_time, alias)

        # fail-safe
        if not short or not str(short).startswith("http"):
            db["one_time_links"].pop(code, None)
            save_db(db)
            return message.reply("❌ XTGLINKS API Error, try again later.")

        return message.reply(
            "🧩 Please verify yourself first:",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("Verify Now 🔗", url=short)]]
            )
        )

    # ============================================================
    # MAIN USER MENU OPTIONS
    # ============================================================

    if text == "❓ How to Use":
        req = db["settings"]["required_invites"]
        return message.reply(
            f"📘 You must invite {req} verified users to unlock temp numbers.\n\n"
            f"Use your invite link from menu.",
            reply_markup=USER_KB
        )

    if text == "🔗 My Invite Link":
        bot_username = client.get_me().username
        link = f"https://t.me/{bot_username}?start={uid}"

        return message.reply(
            f"🔗 Your Invite Link:\n{link}\n\n"
            f"👥 Verified Invites: {user['invites']}",
            reply_markup=USER_KB
        )

    # -------------------------------
    # 📱 Get Temp Number → Country Select
    # -------------------------------
    if text == "📱 Get Temp Number":
        req = db["settings"]["required_invites"]
        if user["invites"] < req and uid not in ADMIN_IDS:
            return message.reply(
                f"⛔ You need {req} verified invites.\n"
                f"Current: {user['invites']}",
                reply_markup=USER_KB
            )

        return message.reply(
            "🌍 Select Country:",
            reply_markup=COUNTRY_KB
        )

    # -------------------------------
    # Country Selected
    # -------------------------------
    if text in COUNTRIES:
        country_code = COUNTRIES[text]
        number = generate_number(country_code)

        # Save number to used list
        user = get_user(db, uid)
        used = user.get("used_numbers", [])
        used.append(number)
        user["used_numbers"] = used
        set_user(db, uid, user)
        save_db(db)

        # Ask user to request OTP
        return message.reply(
            f"📱 *Your Temp Number:*\n`{number}`\n\n"
            f"Press **Get OTP** to receive incoming verification messages.",
            parse_mode="markdown",
            reply_markup=ReplyKeyboardMarkup(
                [
                    ["📨 Get OTP"],
                    ["⬅️ Back"]
                ],
                resize_keyboard=True
            )
        )

    # -------------------------------
    # OTP REQUEST (main logic)
    # -------------------------------
    if text == "📨 Get OTP":
        # 1) Show waiting message
        msg = message.reply("Waiting for OTP...")

        # 2) Pick random app name
        app_name = random.choice(list(OTP_TEMPLATES.keys()))

        # 3) Simulate waiting + send final OTP
        process_otp_and_send(msg, app_name)

        return

    # -------------------------------
    # BACK BUTTON
    # -------------------------------
    if text == "⬅️ Back":
        return message.reply("Main Menu:", reply_markup=USER_KB)

    # ============================================================
    # ADMIN PANEL CONTROLS
    # ============================================================
    if uid in ADMIN_IDS:

        if text == "⬅️ Back to Main Menu":
            return message.reply("Admin Panel:", reply_markup=ADMIN_KB)

        if text == "🛠 Set Invites":
            return message.reply("Send: /setinvites 5")

        if text.startswith("/setinvites"):
            try:
                n = int(text.split()[1])
                db["settings"]["required_invites"] = n
                save_db(db)
                return message.reply("Updated!", reply_markup=ADMIN_KB)
            except:
                return message.reply("Format: /setinvites 5")

        if text == "➕ Add Channel":
            return message.reply("Send: /addch @channel")

        if text.startswith("/addch"):
            ch = text.split()[1]
            db["settings"]["channels"].append(ch)
            save_db(db)
            return message.reply("Added channel!", reply_markup=ADMIN_KB)

        if text == "➖ Remove Channel":
            return message.reply("Send: /rmch @channel")

        if text.startswith("/rmch"):
            ch = text.split()[1]
            if ch in db["settings"]["channels"]:
                db["settings"]["channels"].remove(ch)
                save_db(db)
            return message.reply("Removed!", reply_markup=ADMIN_KB)

        if text == "📱 Add Number":
            return message.reply("Send: /addnum +911234567890")

        if text.startswith("/addnum"):
            num = text.split()[1]
            db["settings"]["numbers"].append(num)
            save_db(db)
            return message.reply("Added number!", reply_markup=ADMIN_KB)

        if text == "🔑 Set XTGLINKS Key":
            return message.reply("Send: /setxtg APIKEY")

        if text.startswith("/setxtg"):
            key = text.split()[1]
            db["settings"]["xtg_api_key"] = key
            save_db(db)
            return message.reply("XTG Key saved!", reply_markup=ADMIN_KB)

        if text == "📊 Stats":
            total = len(db["users"])
            verified = sum(1 for u in db["users"].values() if u.get("verified"))
            return message.reply(
                f"👥 Total users: {total}\n"
                f"✅ Verified users: {verified}",
                reply_markup=ADMIN_KB
            )

        if text == "📢 Broadcast":
            return message.reply("Send: /bc Your message")

        if text.startswith("/bc"):
            bc_msg = text.replace("/bc", "").strip()
            sent = 0
            for u in db["users"]:
                try:
                    client.send_message(int(u), bc_msg)
                    sent += 1
                except:
                    pass

            return message.reply(f"Message sent to {sent} users!", reply_markup=ADMIN_KB)
# ============================================================
# PART 4/4 — RUN BOT
# ============================================================

if __name__ == "__main__":
    ensure_db()
    print("🔥 OTP Bot is now running…")
    app.run()
