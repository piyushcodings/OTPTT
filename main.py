# ============================================================
# Telegram Temp Number Bot - Clean & Updated Final Version
# ============================================================

import os, json, time, random, requests
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

DEFAULT_REQUIRED_INVITES = 5

DB_DIR = "/app/.data"
DB_PATH = DB_DIR + "/database.json"

# ============================================================
# Database Helpers
# ============================================================
def ensure_db():
    if not os.path.exists(DB_DIR):
        os.makedirs(DB_DIR)
    if not os.path.exists(DB_PATH):
        with open(DB_PATH, "w") as f:
            json.dump({
                "users": {},
                "settings": {
                    "required_invites": DEFAULT_REQUIRED_INVITES,
                    "channels": [],
                    "xtg_api_key": "",
                    "numbers": [],
                    "otps": ["Your OTP is 1234"]
                },
                "one_time_links": {}
            }, f, indent=2)


def load_db():
    ensure_db()
    with open(DB_PATH, "r") as f:
        return json.load(f)


def save_db(db):
    with open(DB_PATH, "w") as f:
        json.dump(db, f, indent=2)


def get_user(db, uid):
    return db["users"].get(str(uid), {
        "verified": False,
        "referred_by": None,
        "invites": 0,
        "used_numbers": []
    })


def set_user(db, uid, data):
    db["users"][str(uid)] = data


# ============================================================
# XTGLINKS Shortener
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
# One-Time HV Link
# ============================================================
def generate_one_time_link(app, uid, db):
    code = f"HV_{uid}_{int(time.time())}_{random.randint(1000,9999)}"
    db["one_time_links"][code] = {"user_id": uid, "used": False}
    save_db(db)
    bot_username = app.get_me().username
    return f"https://t.me/{bot_username}?start={code}", code


# ============================================================
# Keyboards
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
# Helpers
# ============================================================
def not_joined_channels(client, uid, db):
    channels = db["settings"].get("channels", [])
    result = []
    for ch in channels:
        try:
            m = client.get_chat_member(ch, uid)
            if m.status in ("left", "kicked"):
                result.append(ch)
        except:
            result.append(ch)
    return result


# ============================================================
# App Init
# ============================================================
app = Client("otpbot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)


# ============================================================
# START COMMAND
# ============================================================
@app.on_message(filters.command("start") & filters.private)
def start_cmd(client, message):
    db = load_db()
    uid = message.from_user.id
    args = message.text.split()

    # ----- ADMIN BYPASS -----
    if uid in ADMIN_IDS:
        return message.reply("Admin Panel:", reply_markup=ADMIN_KB)

    # ----- HANDLE HV AUTO VERIFICATION -----
    if len(args) > 1 and args[1].startswith("HV_"):
        code = args[1]

        if code not in db["one_time_links"]:
            return message.reply("❌ Invalid verification link.")

        info = db["one_time_links"][code]

        if info["used"]:
            return message.reply("❌ Link expired.")

        if info["user_id"] != uid:
            return message.reply("❌ This link is not for your account.")

        # Mark used
        info["used"] = True
        db["one_time_links"][code] = info

        # Verify user
        user = get_user(db, uid)
        user["verified"] = True

        # Referral credit
        if user.get("referred_by"):
            ref = user["referred_by"]
            ref_user = get_user(db, ref)
            ref_user["invites"] += 1
            set_user(db, ref, ref_user)
            try:
                client.send_message(ref, f"🎉 Referral Verified!\nTotal invites: {ref_user['invites']}")
            except:
                pass

        set_user(db, uid, user)
        save_db(db)

        return message.reply("🎉 Verification Completed!", reply_markup=USER_KB)

    # ----- REFERRAL LOGIC -----
    ref = None
    if len(args) > 1:
        try:
            ref = int(args[1])
            if ref == uid:
                ref = None
        except:
            ref = None

    # Create user entry if not exists
    if str(uid) not in db["users"]:
        user = get_user(db, uid)
        if ref:
            user["referred_by"] = ref
        set_user(db, uid, user)
        save_db(db)

    # ----- CHANNEL CHECK -----
    not_join = not_joined_channels(client, uid, db)
    if not_join:
        return message.reply(
            "📛 You must join all required channels:\n" + "\n".join(not_join),
            reply_markup=JOIN_KB
        )

    # ----- VERIFICATION CHECK -----
    user = get_user(db, uid)
    if not user.get("verified"):
        api_key = db["settings"]["xtg_api_key"]
        if not api_key:
            return message.reply("❌ Admin has not set XTGLINKS API key.")

        one_time, code = generate_one_time_link(client, uid, db)
        alias = f"v{uid}_{int(time.time())}"
        short_url = create_xtg_short(api_key, one_time, alias)

        if not short_url:
            return message.reply("❌ XTGLINKS API failed.")

        return message.reply(
            "🧩 Human Verification Required\n\nClick the button below:",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("Verify Now 🔗", url=short_url)]]
            )
        )

    return message.reply("👋 Welcome!", reply_markup=USER_KB)


# ============================================================
# USER BUTTONS
# ============================================================
@app.on_message(filters.text & filters.private)
def user_buttons(client, message):
    db = load_db()
    uid = message.from_user.id
    text = message.text

    # ---- ADMIN BYPASS ----
    if uid in ADMIN_IDS:
        if text == "/admin":
            return message.reply("Admin Panel:", reply_markup=ADMIN_KB)
        # Admin functions processed later
    else:
        # ---- CHANNEL CHECK ----
        not_join = not_joined_channels(client, uid, db)
        if not_join:
            return message.reply(
                "📛 Join all channels:\n" + "\n".join(not_join),
                reply_markup=JOIN_KB
            )

        # ---- VERIFICATION CHECK ----
        user = get_user(db, uid)
        if not user.get("verified"):
            api_key = db["settings"]["xtg_api_key"]
            one_time, code = generate_one_time_link(client, uid, db)
            alias = f"v{uid}_{int(time.time())}"
            short_url = create_xtg_short(api_key, one_time, alias)

            return message.reply(
                "🧩 Please verify first:",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("Verify Now 🔗", url=short_url)]]
                )
            )

    # ==========================
    # USER MENU BUTTONS
    # ==========================
    user = get_user(db, uid)

    if text == "❓ How to Use":
        req = db["settings"]["required_invites"]
        return message.reply(
            f"📘 You must verify + invite {req} users to use this bot.",
            reply_markup=USER_KB
        )

    if text == "🔗 My Invite Link":
        bot_username = client.get_me().username
        link = f"https://t.me/{bot_username}?start={uid}"
        return message.reply(
            f"🔗 Your Invite Link:\n{link}\n\nInvites: {user['invites']}",
            reply_markup=USER_KB
        )

    if text == "📱 Get Temp Number":
        req = db["settings"]["required_invites"]
        if user["invites"] < req:
            return message.reply(
                f"⛔ Need {req} invites.\nCurrent: {user['invites']}",
                reply_markup=USER_KB
            )

        numbers = db["settings"]["numbers"]
        used = user.get("used_numbers", [])
        available = [n for n in numbers if n not in used]

        if not available:
            return message.reply("❌ No numbers left.", reply_markup=USER_KB)

        chosen = random.choice(available)
        user["used_numbers"].append(chosen)
        set_user(db, uid, user)
        save_db(db)

        otp = random.choice(db["settings"]["otps"])

        return message.reply(
            f"📱 Your Temp Number: {chosen}\n📨 OTP: {otp}",
            reply_markup=USER_KB
        )

    # ============================================================
    # ADMIN COMMANDS
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
            return message.reply("Added!", reply_markup=ADMIN_KB)

        if text == "➖ Remove Channel":
            return message.reply("Send: /rmch @channel")

        if text.startswith("/rmch"):
            ch = text.split()[1]
            if ch in db["settings"]["channels"]:
                db["settings"]["channels"].remove(ch)
                save_db(db)
            return message.reply("Removed!", reply_markup=ADMIN_KB)

        if text == "📱 Add Number":
            return message.reply("Send: /addnum +910000000")

        if text.startswith("/addnum"):
            num = text.split()[1]
            db["settings"]["numbers"].append(num)
            save_db(db)
            return message.reply("Added!", reply_markup=ADMIN_KB)

        if text == "🔑 Set XTGLINKS Key":
            return message.reply("Send: /setxtg APIKEY")

        if text.startswith("/setxtg"):
            key = text.split()[1]
            db["settings"]["xtg_api_key"] = key
            save_db(db)
            return message.reply("Updated!", reply_markup=ADMIN_KB)

        if text == "📊 Stats":
            total = len(db["users"])
            ver = sum(1 for u in db["users"].values() if u.get("verified"))
            return message.reply(
                f"Users: {total}\nVerified: {ver}",
                reply_markup=ADMIN_KB
            )

        if text == "📢 Broadcast":
            return message.reply("Send: /bc message")

        if text.startswith("/bc"):
            msg = text.replace("/bc", "").strip()
            sent = 0
            for x in db["users"].keys():
                try:
                    client.send_message(int(x), msg)
                    sent += 1
                except:
                    pass
            return message.reply(f"Sent: {sent}", reply_markup=ADMIN_KB)


# ============================================================
# RUN BOT
# ============================================================
if __name__ == "__main__":
    ensure_db()
    print("BOT ONLINE ✔")
    app.run()
