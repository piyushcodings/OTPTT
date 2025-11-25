import os
import json
import random
import time
import requests
from functools import wraps
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

# ============================
# CONFIG (via env vars)
# ============================
API_ID = int(os.environ.get("API_ID", "23907288"))
API_HASH = os.environ.get("API_HASH", "f9a47570ed19aebf8eb0f0a5ec1111e5")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8496569281:AAHuz4BPGlRuklpo21yYejBwxxbl59h7ao8")

# ADMIN IDs (comma separated)
ADMIN_IDS = [int(x) for x in os.environ.get("ADMIN_IDS", "5748100919,1180293826").split(",")]

# default required invites (admin can change)
DEFAULT_REQUIRED_INVITES = int(os.environ.get("DEFAULT_REQUIRED_INVITES", "15"))

# DB path (Heroku friendly directory)
DB_DIR = "/app/.data"
DB_PATH = os.path.join(DB_DIR, "database.json")

# ============================
# JSON DB helpers
# ============================
def ensure_db():
    if not os.path.exists(DB_DIR):
        os.makedirs(DB_DIR)
    if not os.path.exists(DB_PATH):
        with open(DB_PATH, "w") as f:
            base = {
                "users": {},
                "settings": {
                    "required_invites": DEFAULT_REQUIRED_INVITES,
                    "channels": [],
                    "xtg_api_key": "",
                    "xtg_dest": "https://example.com",
                    "numbers": [],
                    "otps": ["Your OTP is 1234", "Code: 5678"]
                },
                "one_time_links": {}
            }
            json.dump(base, f, indent=2)

def load_db():
    ensure_db()
    with open(DB_PATH, "r") as f:
        return json.load(f)

def save_db(db):
    ensure_db()
    with open(DB_PATH, "w") as f:
        json.dump(db, f, indent=2)

def get_user(db, uid):
    return db["users"].get(str(uid), {
        "invites": 0,
        "referred_by": None,
        "verified": False,
        "xtg_alias": None,
        "used_numbers": []
    })

def set_user(db, uid, data):
    db["users"][str(uid)] = data

# ============================
# XTGLINKS helper
# api format:
# https://xtglinks.com/api?api=API_KEY&url=DEST_URL&alias=CustomAlias
# ============================
def create_xtg_link(api_key, dest_url, alias):
    dest = dest_url
    # XTGLINKS expects the url param (not strictly encoded, but safe to encode)
    from urllib.parse import quote
    dest_enc = quote(dest, safe='')
    api_url = f"https://xtglinks.com/api?api={api_key}&url={dest_enc}&alias={alias}"
    return api_url

# ============================
# One-time link generator
# Creates a unique HV_<uid>_<timestamp>_<rand>
# Saves it in db["one_time_links"] with used:false and target uid
# Returns bot start link using your bot username
# ============================
def generate_one_time_link(app_client, uid, db):
    unique_id = f"HV_{uid}_{int(time.time())}_{random.randint(10000,99999)}"
    db.setdefault("one_time_links", {})
    db["one_time_links"][unique_id] = {
        "user_id": uid,
        "used": False,
        "created_at": int(time.time())
    }
    save_db(db)
    bot_username = app_client.get_me().username
    return f"https://t.me/{bot_username}?start={unique_id}", unique_id

# ============================
# Keyboards
# ============================
def main_menu_kb():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Get Temp Number 📱", callback_data="get_number")],
            [InlineKeyboardButton("My Invite Link 🔗", callback_data="my_invite")],
            [InlineKeyboardButton("How to Use ❓", callback_data="howto")]
        ]
    )

def number_kb(number_id):
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Get OTP 📨", callback_data=f"get_otp|{number_id}")],
            [InlineKeyboardButton("Back 🔙", callback_data="menu")]
        ]
    )

# ============================
# App
# ============================
app = Client("otpbot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# ============================
# Admin decorator
# ============================
def only_admin(f):
    @wraps(f)
    def wrapper(client, message):
        uid = message.from_user.id
        if uid not in ADMIN_IDS:
            try:
                message.reply_text("🚫 You are not an admin.")
            except:
                pass
            return
        return f(client, message)
    return wrapper

# ============================
# /start handler (handles normal starts and one-time HV starts)
# ============================
@app.on_message(filters.command("start") & filters.private)
def start_cmd(client, message):
    db = load_db()
    args = message.text.split()
    uid = message.from_user.id

    # CASE: One-time HV flow used (e.g., user visited XTGLINKS and got redirected to this start link)
    if len(args) > 1 and args[1].startswith("HV_"):
        code = args[1].strip()
        links = db.get("one_time_links", {})
        if code not in links:
            message.reply_text("❌ Invalid or unknown verification link.")
            return

        info = links[code]

        # If already used
        if info.get("used"):
            message.reply_text("❌ This verification link has expired.")
            return

        # Must be same user
        if int(info.get("user_id")) != uid:
            message.reply_text("❌ This verification link is not for you.")
            return

        # Mark used
        info["used"] = True
        db["one_time_links"][code] = info

        # Mark user verified
        user = get_user(db, uid)
        user["verified"] = True
        set_user(db, uid, user)

        # If user had a referrer, credit them +1 invite and notify
        ref = user.get("referred_by")
        if ref:
            try:
                ref = int(ref)
                ref_user = get_user(db, ref)
                ref_user["invites"] = ref_user.get("invites", 0) + 1
                set_user(db, ref, ref_user)
                # notify referrer
                try:
                    client.send_message(ref, f"✅ Your referral just completed verification! You received +1 invite. Total invites: {ref_user['invites']}")
                except Exception as e:
                    print("notify ref fail", e)
            except Exception as e:
                print("credit ref error", e)

        save_db(db)
        message.reply_text("✅ Human Verification completed. You can now use the bot if you meet invite requirements.", reply_markup=main_menu_kb())
        return

    # Normal start - possibly with referral param (a number or id)
    ref = None
    if len(args) > 1:
        ref = args[1]
        # treat numeric referral as user id
        try:
            ref_int = int(ref)
            if ref_int == uid:
                ref = None  # ignore self-referral
            else:
                ref = ref_int
        except:
            # keep as-is (e.g., username) but we prefer numeric user ids for crediting
            ref = ref

    # First time user setup
    if str(uid) not in db.get("users", {}):
        user = get_user(db, uid)
        if ref:
            user["referred_by"] = ref
        set_user(db, uid, user)
        save_db(db)

    message.reply_text("Welcome! Use the buttons below.\nYou MUST join required channels before referral counts apply.", reply_markup=main_menu_kb())

# ============================
# Callback query handler (main interactive flows)
# ============================
@app.on_callback_query()
def cb_handler(client, callback_query):
    db = load_db()
    uid = callback_query.from_user.id
    user = get_user(db, uid)
    data = callback_query.data

    # Menu
    if data == "menu":
        callback_query.message.edit_text("Main Menu", reply_markup=main_menu_kb())
        callback_query.answer()
        return

    if data == "howto":
        required = db["settings"].get("required_invites", DEFAULT_REQUIRED_INVITES)
        txt = (
            f"How to use:\n"
            f"1. Join all required channels (admin-set).\n"
            f"2. Invite users using your invite link.\n"
            f"3. Complete human verification (opens XTGLINKS ad link).\n"
            f"4. After verification and reaching {required} invites, you can get temp numbers and OTPs."
        )
        callback_query.message.edit_text(txt, reply_markup=main_menu_kb())
        callback_query.answer()
        return

    if data == "my_invite":
        required = db["settings"].get("required_invites", DEFAULT_REQUIRED_INVITES)
        invite_link = f"https://t.me/{client.get_me().username}?start={uid}"
        user = get_user(db, uid)
        txt = f"• Total Invites: {user.get('invites',0)}/{required}\n• Your invite link:\n{invite_link}"
        callback_query.message.edit_text(txt, reply_markup=main_menu_kb())
        callback_query.answer()
        return

    # GET NUMBER flow
    if data == "get_number":
        # Check required channels
        channels = db["settings"].get("channels", [])
        not_joined = []
        for ch in channels:
            try:
                member = client.get_chat_member(ch, uid)
                if member.status in ("left", "kicked"):
                    not_joined.append(ch)
            except Exception as e:
                # if we can't check, treat as not joined to be safe
                not_joined.append(ch)

        if not_joined:
            kb = InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton("Open First Channel 🔗", url=f"https://t.me/{not_joined[0].lstrip('@')}")],
                    [InlineKeyboardButton("I Joined ✅", callback_data="joined_checked")]
                ]
            )
            callback_query.message.edit_text("You must join these channels:\n" + "\n".join(not_joined), reply_markup=kb)
            callback_query.answer("Please join required channels first.")
            return

        # If not verified -> trigger human verification (XTGLINKS)
        if not user.get("verified", False):
            api_key = db["settings"].get("xtg_api_key", "")
            dest_default = db["settings"].get("xtg_dest", "https://example.com")
            if not api_key:
                callback_query.answer("Admin hasn't configured XTGLINKS API. Ask admin.")
                return

            # Generate one-time bot start link, save it, then create an XTGLINKS wrapper pointing to it
            one_time_url, unique_code = generate_one_time_link(client, uid, db)

            # Create an alias for xtg (optional)
            alias = f"v{uid}{int(time.time())}{random.randint(100,999)}"
            xtg_url = create_xtg_link(api_key, one_time_url, alias)

            # Store alias in user for debugging/lookup
            user["xtg_alias"] = alias
            set_user(db, uid, user)
            save_db(db)

            kb = InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton("Open Verification Link 🔗", url=xtg_url)],
                    [InlineKeyboardButton("I Verified ✅", callback_data="verify_done")],
                    [InlineKeyboardButton("Cancel ❌", callback_data="menu")]
                ]
            )
            callback_query.message.edit_text("To prove you're human, open the ad link and follow instructions. After that click 'I Verified'.", reply_markup=kb)
            callback_query.answer()
            return

        # If verified, check invites requirement
        required = db["settings"].get("required_invites", DEFAULT_REQUIRED_INVITES)
        if user.get("invites", 0) < required:
            callback_query.message.edit_text(f"You need {required} invites. You have {user.get('invites',0)} invites.", reply_markup=main_menu_kb())
            callback_query.answer("You don't have enough invites yet.")
            return

        # Provide a random unused number
        numbers = db["settings"].get("numbers", [])
        used = user.get("used_numbers", [])
        available = [n for n in numbers if n not in used]
        if not available:
            callback_query.message.edit_text("No temp numbers available right now. Ask admin to add numbers.", reply_markup=main_menu_kb())
            callback_query.answer("No temp numbers available.")
            return

        chosen = random.choice(available)
        user.setdefault("used_numbers", []).append(chosen)
        set_user(db, uid, user)
        save_db(db)

        kb = number_kb(chosen)
        callback_query.message.edit_text(f"Here is your temp number:\n{chosen}", reply_markup=kb)
        callback_query.answer()
        return

    # "I Joined" after channel join prompt
    if data == "joined_checked":
        callback_query.message.edit_text("Thanks — press Get Temp Number again.", reply_markup=main_menu_kb())
        callback_query.answer("Now press Get Temp Number.")
        return

    # User clicked "I Verified" after finishing XTGLINKS flow
    if data == "verify_done":
        # Mark verified, mark associated one-time link used if any
        # Find any one_time_link in db that matches this user and is unused and recently created
        links = db.get("one_time_links", {})
        found_code = None
        for code, info in links.items():
            if int(info.get("user_id")) == uid and info.get("used") is False:
                # optional: ensure not older than X minutes (skip for now)
                found_code = code
                break

        if found_code:
            links[found_code]["used"] = True
            db["one_time_links"] = links

        user["verified"] = True
        set_user(db, uid, user)

        # credit referrer if exists
        ref = user.get("referred_by")
        if ref:
            try:
                ref = int(ref)
                ref_user = get_user(db, ref)
                ref_user["invites"] = ref_user.get("invites", 0) + 1
                set_user(db, ref, ref_user)
                # notify referrer
                try:
                    client.send_message(ref, f"✅ Your referral ({uid}) completed verification! +1 invite. Total: {ref_user['invites']}")
                except Exception as e:
                    print("notify ref failed", e)
            except Exception as e:
                print("credit ref error", e)

        save_db(db)
        callback_query.message.edit_text("✅ Verification recorded. Now press Get Temp Number.", reply_markup=main_menu_kb())
        callback_query.answer()
        return

    # Get OTP
    if data.startswith("get_otp|"):
        _, number = data.split("|", 1)
        settings = db["settings"]
        otps = settings.get("otps", ["Your OTP is 1234"])
        otp_msg = random.choice(otps)
        callback_query.message.edit_text(f"OTP for {number}:\n{otp_msg}", reply_markup=main_menu_kb())
        callback_query.answer("OTP delivered.")
        return

    callback_query.answer()

# ============================
# Admin commands
# ============================
@app.on_message(filters.command("admin") & filters.private)
@only_admin
def admin_panel(client, message):
    txt = (
        "Admin Panel Commands:\n"
        "/set_invites <num>\n"
        "/add_channel <@username_or_id>\n"
        "/remove_channel <@username_or_id>\n"
        "/list_channels\n"
        "/set_xtg_api <api_key>\n"
        "/set_xtg_dest <destination_url>\n"
        "/add_number <number>\n"
        "/remove_number <number>\n"
        "/list_numbers\n"
        "/add_otp <message>\n"
        "/list_otps\n"
        "/stats\n"
        "/broadcast <text>\n"
        "/credit_invite <user_id> <count>\n"
        "/list_users <optional_limit>\n"
    )
    message.reply_text(txt)

@app.on_message(filters.command("set_invites") & filters.private)
@only_admin
def cmd_set_invites(client, message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        message.reply_text("Usage: /set_invites <number>")
        return
    try:
        n = int(args[1].strip())
    except:
        message.reply_text("Invalid number.")
        return
    db = load_db()
    db["settings"]["required_invites"] = n
    save_db(db)
    message.reply_text(f"Required invites set to {n}.")

@app.on_message(filters.command("add_channel") & filters.private)
@only_admin
def cmd_add_channel(client, message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        message.reply_text("Usage: /add_channel @channelusername")
        return
    ch = args[1].strip()
    db = load_db()
    if ch not in db["settings"]["channels"]:
        db["settings"]["channels"].append(ch)
        save_db(db)
        message.reply_text(f"Added channel {ch}.")
    else:
        message.reply_text("Channel already exists.")

@app.on_message(filters.command("remove_channel") & filters.private)
@only_admin
def cmd_remove_channel(client, message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        message.reply_text("Usage: /remove_channel @channelusername")
        return
    ch = args[1].strip()
    db = load_db()
    if ch in db["settings"]["channels"]:
        db["settings"]["channels"].remove(ch)
        save_db(db)
        message.reply_text(f"Removed channel {ch}.")
    else:
        message.reply_text("Channel not found.")

@app.on_message(filters.command("list_channels") & filters.private)
@only_admin
def cmd_list_channels(client, message):
    db = load_db()
    chs = db["settings"].get("channels", [])
    message.reply_text("Channels:\n" + ("\n".join(chs) if chs else "No channels configured."))

@app.on_message(filters.command("set_xtg_api") & filters.private)
@only_admin
def cmd_set_xtg_api(client, message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        message.reply_text("Usage: /set_xtg_api <api_key>")
        return
    key = args[1].strip()
    db = load_db()
    db["settings"]["xtg_api_key"] = key
    save_db(db)
    message.reply_text("XTGLINKS API key updated.")

@app.on_message(filters.command("set_xtg_dest") & filters.private)
@only_admin
def cmd_set_xtg_dest(client, message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        message.reply_text("Usage: /set_xtg_dest <destination_url>")
        return
    url = args[1].strip()
    db = load_db()
    db["settings"]["xtg_dest"] = url
    save_db(db)
    message.reply_text("XTGLINKS destination URL updated.")

@app.on_message(filters.command("add_number") & filters.private)
@only_admin
def cmd_add_number(client, message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        message.reply_text("Usage: /add_number <number>")
        return
    num = args[1].strip()
    db = load_db()
    db["settings"].setdefault("numbers", []).append(num)
    save_db(db)
    message.reply_text(f"Added number {num}.")

@app.on_message(filters.command("remove_number") & filters.private)
@only_admin
def cmd_remove_number(client, message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        message.reply_text("Usage: /remove_number <number>")
        return
    num = args[1].strip()
    db = load_db()
    if num in db["settings"].get("numbers", []):
        db["settings"]["numbers"].remove(num)
        save_db(db)
        message.reply_text("Removed.")
    else:
        message.reply_text("Number not found.")

@app.on_message(filters.command("list_numbers") & filters.private)
@only_admin
def cmd_list_numbers(client, message):
    db = load_db()
    nums = db["settings"].get("numbers", [])
    message.reply_text("Numbers:\n" + ("\n".join(nums) if nums else "No numbers"))

@app.on_message(filters.command("add_otp") & filters.private)
@only_admin
def cmd_add_otp(client, message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        message.reply_text("Usage: /add_otp <message>")
        return
    otp = args[1].strip()
    db = load_db()
    db["settings"].setdefault("otps", []).append(otp)
    save_db(db)
    message.reply_text("OTP template added.")

@app.on_message(filters.command("list_otps") & filters.private)
@only_admin
def cmd_list_otps(client, message):
    db = load_db()
    otps = db["settings"].get("otps", [])
    message.reply_text("OTP templates:\n" + ("\n".join(otps) if otps else "No templates"))

@app.on_message(filters.command("stats") & filters.private)
@only_admin
def cmd_stats(client, message):
    db = load_db()
    total = len(db.get("users", {}))
    verified = sum(1 for u in db["users"].values() if u.get("verified"))
    message.reply_text(f"Total users: {total}\nVerified: {verified}")

@app.on_message(filters.command("broadcast") & filters.private)
@only_admin
def cmd_broadcast(client, message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        message.reply_text("Usage: /broadcast <text>")
        return
    text = args[1]
    db = load_db()
    sent = 0
    for uid in db.get("users", {}).keys():
        try:
            client.send_message(int(uid), text)
            sent += 1
        except Exception as e:
            print("broadcast fail", uid, e)
    message.reply_text(f"Broadcast sent to {sent} users.")

@app.on_message(filters.command("credit_invite") & filters.private)
@only_admin
def cmd_credit_invite(client, message):
    args = message.text.split()
    if len(args) < 3:
        message.reply_text("Usage: /credit_invite <user_id> <count>")
        return
    try:
        target = int(args[1].strip())
        count = int(args[2].strip())
    except:
        message.reply_text("Invalid args")
        return
    db = load_db()
    u = get_user(db, target)
    u["invites"] = u.get("invites", 0) + count
    set_user(db, target, u)
    save_db(db)
    message.reply_text(f"Credited {count} invites to {target}.")

@app.on_message(filters.command("list_users") & filters.private)
@only_admin
def cmd_list_users(client, message):
    args = message.text.split(maxsplit=1)
    limit = 50
    if len(args) > 1:
        try:
            limit = int(args[1].strip())
        except:
            pass
    db = load_db()
    items = list(db.get("users", {}).items())[:limit]
    txt = "Users (id : invites : verified):\n"
    for k, v in items:
        txt += f"{k} : {v.get('invites',0)} : {v.get('verified',False)}\n"
    message.reply_text(txt if items else "No users yet.")

# ============================
# Run
# ============================
if __name__ == "__main__":
    ensure_db()
    print("Bot starting...")
    app.run()
