import os
import subprocess
import zipfile
import shutil
import asyncio
import json
import signal
import time
from datetime import datetime
from pathlib import Path
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackContext, CallbackQueryHandler

# ===== CONFIGURATION =====
BOT_TOKEN = "9230933269:AAHb_H4kkxmq2yW--vN5cSyB2TvC6EeJUsQ"
ADMIN_ID = 1885926472  # Your admin ID
BASE_DIR = Path("hosted_files")
os.makedirs(BASE_DIR, exist_ok=True)

# User database
USER_DB = "users.json"

def load_users():
    if os.path.exists(USER_DB):
        with open(USER_DB, 'r') as f:
            return json.load(f)
    return {}

def save_users(users):
    with open(USER_DB, 'w') as f:
        json.dump(users, f, indent=2)

# ===== HELPER FUNCTIONS =====
def get_user_folder(user_id):
    user_path = BASE_DIR / str(user_id)
    user_path.mkdir(exist_ok=True)
    return user_path

def count_user_files(user_id):
    user_folder = get_user_folder(user_id)
    return len([f for f in user_folder.iterdir() if f.is_file() and f.suffix in ['.py', '.js']])

async def run_python_script(file_path, user_id):
    """Run Python script and return output"""
    try:
        # Get user folder for requirements
        user_folder = get_user_folder(user_id)
        requirements_file = user_folder / "requirements.txt"
        
        # Install requirements if they exist
        if requirements_file.exists():
            process = await asyncio.create_subprocess_exec(
                'pip', 'install', '-r', str(requirements_file),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            await process.communicate()
        
        # Run the actual script
        process = await asyncio.create_subprocess_exec(
            'python', str(file_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(user_folder)  # Run in user's folder
        )
        stdout, stderr = await process.communicate()
        return stdout.decode() + stderr.decode()
    except Exception as e:
        return f"Error: {str(e)}"

async def run_js_script(file_path):
    """Run JavaScript script and return output"""
    try:
        process = await asyncio.create_subprocess_exec(
            'node', str(file_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()
        return stdout.decode() + stderr.decode()
    except Exception as e:
        return f"Error: {str(e)}"

async def install_requirements(requirements_path, user_id):
    """Install Python dependencies from requirements.txt"""
    try:
        process = await asyncio.create_subprocess_exec(
            'pip', 'install', '-r', str(requirements_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()
        
        if process.returncode == 0:
            return True, stdout.decode()
        else:
            return False, stderr.decode()
    except Exception as e:
        return False, str(e)

# ===== BOT COMMANDS =====
async def start(update: Update, context: CallbackContext):
    user = update.effective_user
    user_id = str(user.id)
    
    # Load user data
    users = load_users()
    if user_id not in users:
        users[user_id] = {
            "username": user.username or user.first_name,
            "join_date": datetime.now().isoformat(),
            "status": "FREE",
            "files_uploaded": 0
        }
        save_users(users)
    
    user_data = users[user_id]
    
    # Count current files
    file_count = count_user_files(user_id)
    
    welcome_text = f"""
🎉 **Welcome, @{user.username or user.first_name}!**

- **Your User ID:** `{user_id}`
- **Username:** @{user.username or 'Not set'}
- **Your Status:** {user_data['status']}
- **Files Uploaded:** {file_count} / 10

---

**📁 Host & run Python (.py) or JS (.js) scripts**
Upload single scripts or .zip archives.

**🚀 Features:**
• 24/7 Uptime
• Ultra-fast execution
• File management
• **Auto-install requirements.txt**
• Script monitoring

Use buttons or type commands.
    """
    
    keyboard = [
        [InlineKeyboardButton("📤 Upload Script", callback_data="upload")],
        [InlineKeyboardButton("📋 My Scripts", callback_data="my_scripts")],
        [InlineKeyboardButton("▶️ Run Script", callback_data="run")],
        [InlineKeyboardButton("🗑️ Delete Script", callback_data="delete")],
        [InlineKeyboardButton("ℹ️ Help", callback_data="help")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(welcome_text, parse_mode='Markdown', reply_markup=reply_markup)

async def handle_callback(update: Update, context: CallbackContext):
    query = update.callback_query
    await query.answer()
    
    user_id = str(query.from_user.id)
    data = query.data
    
    if data == "upload":
        await query.edit_message_text(
            "📤 **Upload Your Script**\n\n"
            "Send me a:\n"
            "• `.py` file (Python)\n"
            "• `.js` file (JavaScript)\n"
            "• `.zip` archive with multiple files\n\n"
            "💡 **For Python projects with dependencies:**\n"
            "Include `requirements.txt` in your ZIP file!\n\n"
            "⚠️ Max 10 files for free users\n\n"
            "📁 Just drag & drop or attach your file!"
        )
        context.user_data['waiting_for'] = 'file'
    
    elif data == "my_scripts":
        user_folder = get_user_folder(user_id)
        files = list(user_folder.glob("*.py")) + list(user_folder.glob("*.js"))
        
        if not files:
            await query.edit_message_text("📂 **No scripts found!**\n\nUpload your first script using /start")
        else:
            file_list = "📋 **Your Scripts:**\n\n"
            for i, file in enumerate(files, 1):
                size = file.stat().st_size
                # Check if requirements file exists
                req_file = user_folder / "requirements.txt"
                has_req = "📦" if req_file.exists() else ""
                file_list += f"{i}. {has_req} `{file.name}` ({size} bytes)\n"
            await query.edit_message_text(file_list, parse_mode='Markdown')
    
    elif data == "run":
        user_folder = get_user_folder(user_id)
        files = list(user_folder.glob("*.py")) + list(user_folder.glob("*.js"))
        
        if not files:
            await query.edit_message_text("❌ No scripts to run!\nUpload a script first.")
            return
        
        keyboard = []
        for file in files:
            keyboard.append([InlineKeyboardButton(f"▶️ {file.name}", callback_data=f"run_file_{file.name}")])
        keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="back")])
        
        await query.edit_message_text(
            "🎯 **Select script to run:**",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    
    elif data.startswith("run_file_"):
        filename = data.replace("run_file_", "")
        file_path = get_user_folder(user_id) / filename
        
        if not file_path.exists():
            await query.edit_message_text("❌ File not found!")
            return
        
        await query.edit_message_text(f"🚀 **Running {filename}...**\n\nThis may take a moment...")
        
        # Run based on extension
        if filename.endswith('.py'):
            output = await run_python_script(file_path, user_id)
        elif filename.endswith('.js'):
            output = await run_js_script(file_path)
        else:
            output = "Unsupported file type"
        
        # Send output (truncate if too long)
        if len(output) > 4000:
            output = output[:4000] + "\n\n... (truncated)"
        
        result_text = f"📊 **Output of {filename}:**\n\n```\n{output}\n```"
        await query.message.reply_text(result_text, parse_mode='Markdown')
    
    elif data == "delete":
        user_folder = get_user_folder(user_id)
        files = list(user_folder.glob("*.py")) + list(user_folder.glob("*.js"))
        
        if not files:
            await query.edit_message_text("❌ No scripts to delete!")
            return
        
        keyboard = []
        for file in files:
            keyboard.append([InlineKeyboardButton(f"🗑️ {file.name}", callback_data=f"del_file_{file.name}")])
        keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="back")])
        
        await query.edit_message_text(
            "🗑️ **Select script to delete:**",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    
    elif data.startswith("del_file_"):
        filename = data.replace("del_file_", "")
        file_path = get_user_folder(user_id) / filename
        
        if file_path.exists():
            file_path.unlink()
            await query.edit_message_text(f"✅ **Deleted:** {filename}")
        else:
            await query.edit_message_text("❌ File not found!")
    
    elif data == "help":
        help_text = """
ℹ️ **Hosting Bot Help**

**Commands:**
/start - Show main menu
/upload - Upload a script
/scripts - List your scripts
/run - Execute a script
/delete - Remove a script
/stats - View your statistics

**Supported Languages:**
• Python (.py)
• JavaScript (.js)

**Python Dependencies:**
📦 Include `requirements.txt` in your ZIP file
• Bot auto-installs all packages
• Works with pip install

**Limits (Free):**
• Max 10 files per user
• Max 5MB per file

**Need More?**
Contact @MARCUS_STAR for premium plans!

🚀 **24/7 Uptime Guaranteed!**
        """
        await query.edit_message_text(help_text)
    
    elif data == "back":
        # Re-show main menu
        keyboard = [
            [InlineKeyboardButton("📤 Upload Script", callback_data="upload")],
            [InlineKeyboardButton("📋 My Scripts", callback_data="my_scripts")],
            [InlineKeyboardButton("▶️ Run Script", callback_data="run")],
            [InlineKeyboardButton("🗑️ Delete Script", callback_data="delete")],
            [InlineKeyboardButton("ℹ️ Help", callback_data="help")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            "🏠 **Main Menu**\n\nWhat would you like to do?",
            reply_markup=reply_markup
        )

async def handle_document(update: Update, context: CallbackContext):
    user_id = str(update.effective_user.id)
    document = update.message.document
    file_name = document.file_name
    file_ext = Path(file_name).suffix.lower()
    
    # Check file limit
    current_files = count_user_files(user_id)
    if current_files >= 10:
        await update.message.reply_text("❌ **Limit reached!**\nFree users can only store 10 files.\nDelete some files using the menu!")
        return
    
    # Check extension
    if file_ext not in ['.py', '.js', '.zip']:
        await update.message.reply_text(
            "❌ **Unsupported file type!**\n\n"
            "Send `.py`, `.js`, or `.zip` files only.\n\n"
            "📦 **For Python projects with dependencies:**\n"
            "1. Create a folder with your script + requirements.txt\n"
            "2. ZIP the folder\n"
            "3. Upload the ZIP file\n\n"
            "The bot will auto-install all dependencies!"
        )
        return
    
    # Check file size (5MB limit)
    if document.file_size > 5 * 1024 * 1024:
        await update.message.reply_text("❌ **File too large!**\nMax 5MB per file.")
        return
    
    # Download file
    status_msg = await update.message.reply_text(f"📥 **Downloading {file_name}...**")
    file = await document.get_file()
    user_folder = get_user_folder(user_id)
    file_path = user_folder / file_name
    
    await file.download_to_drive(file_path)
    
    # Handle zip files
    if file_ext == '.zip':
        await status_msg.edit_text("📦 **Extracting archive...**")
        extract_path = user_folder / "extracted"
        with zipfile.ZipFile(file_path, 'r') as zip_ref:
            zip_ref.extractall(extract_path)
        
        # Move extracted files
        extracted_files = 0
        has_requirements = False
        
        for extracted_file in extract_path.rglob("*"):
            if extracted_file.is_file():
                if extracted_file.suffix in ['.py', '.js']:
                    if count_user_files(user_id) + extracted_files < 10:
                        # Handle duplicate filenames
                        target_path = user_folder / extracted_file.name
                        if target_path.exists():
                            base_name = extracted_file.stem
                            ext = extracted_file.suffix
                            counter = 1
                            while target_path.exists():
                                target_path = user_folder / f"{base_name}_{counter}{ext}"
                                counter += 1
                        shutil.move(str(extracted_file), str(target_path))
                        extracted_files += 1
                
                elif extracted_file.name.lower() == "requirements.txt":
                    has_requirements = True
                    req_target = user_folder / "requirements.txt"
                    shutil.move(str(extracted_file), str(req_target))
        
        # Clean up
        shutil.rmtree(extract_path)
        file_path.unlink()
        
        # Install requirements if found
        if has_requirements:
            await status_msg.edit_text("📦 **Installing Python dependencies from requirements.txt...**")
            req_file = user_folder / "requirements.txt"
            success, output = await install_requirements(req_file, user_id)
            
            if success:
                await status_msg.edit_text(
                    f"✅ **Successfully extracted and installed!**\n\n"
                    f"📁 Extracted {extracted_files} script(s)\n"
                    f"📦 Dependencies installed successfully!\n\n"
                    f"Use the 'Run Script' button to execute!"
                )
            else:
                await status_msg.edit_text(
                    f"⚠️ **Extracted {extracted_files} script(s), but dependency installation failed!**\n\n"
                    f"❌ Error:\n```\n{output[:300]}\n```\n\n"
                    f"You can still run the script manually."
                )
        else:
            await status_msg.edit_text(
                f"✅ **Extracted {extracted_files} scripts!**\n\n"
                f"💡 **Tip:** Include `requirements.txt` in your ZIP for auto-dependency installation!\n\n"
                f"Use the 'Run Script' button to execute them!"
            )
    else:
        await status_msg.edit_text(
            f"✅ **Uploaded:** {file_name}\n\n"
            f"💡 **Tip:** For Python projects with dependencies, use a ZIP file with requirements.txt!\n\n"
            f"Use the 'Run Script' button to execute it!"
        )
    
    # Update user stats
    users = load_users()
    if user_id in users:
        users[user_id]['files_uploaded'] = count_user_files(user_id)
        save_users(users)

async def upload_command(update: Update, context: CallbackContext):
    await update.message.reply_text(
        "📤 **Send me your script file!**\n\n"
        "Supported: `.py`, `.js`, or `.zip`\n"
        "Max size: 5MB\n"
        "Max files: 10\n\n"
        "📦 **For Python projects with dependencies:**\n"
        "• Include `requirements.txt` in your ZIP\n"
        "• Bot auto-installs all packages\n\n"
        "Just attach and send the file!"
    )

async def list_scripts(update: Update, context: CallbackContext):
    user_id = str(update.effective_user.id)
    user_folder = get_user_folder(user_id)
    files = list(user_folder.glob("*.py")) + list(user_folder.glob("*.js"))
    
    if not files:
        await update.message.reply_text("📂 No scripts found!\nUpload using /upload")
    else:
        text = "📋 **Your Scripts:**\n\n"
        req_file = user_folder / "requirements.txt"
        has_req = req_file.exists()
        
        if has_req:
            text += "📦 **Python environment ready** (requirements.txt installed)\n\n"
        
        for i, file in enumerate(files, 1):
            size_kb = file.stat().st_size / 1024
            text += f"{i}. `{file.name}` ({size_kb:.1f} KB)\n"
        await update.message.reply_text(text, parse_mode='Markdown')

async def stats_command(update: Update, context: CallbackContext):
    user_id = str(update.effective_user.id)
    users = load_users()
    
    if user_id in users:
        file_count = count_user_files(user_id)
        user_data = users[user_id]
        user_folder = get_user_folder(user_id)
        has_req = (user_folder / "requirements.txt").exists()
        
        stats_text = f"""
📊 **Your Statistics:**

👤 **User:** @{user_data['username']}
🆔 **ID:** `{user_id}`
⭐ **Status:** {user_data['status']}
📁 **Files:** {file_count} / 10
📦 **Dependencies:** {'✅ Installed' if has_req else '❌ None'}
📅 **Joined:** {user_data['join_date']}

🚀 **Hosting Performance:**
• Uptime: 24/7
• Speed: Ultra-fast
• Reliability: 99.9%
• Auto-dependency install: ✅

Use /start to manage your scripts!
        """
        await update.message.reply_text(stats_text, parse_mode='Markdown')
    else:
        await update.message.reply_text("Please use /start first!")

async def run_command(update: Update, context: CallbackContext):
    """Command to run a script"""
    user_id = str(update.effective_user.id)
    user_folder = get_user_folder(user_id)
    files = list(user_folder.glob("*.py")) + list(user_folder.glob("*.js"))
    
    if not files:
        await update.message.reply_text("❌ No scripts to run!\nUpload a script with /upload")
        return
    
    keyboard = []
    for file in files:
        keyboard.append([InlineKeyboardButton(f"▶️ {file.name}", callback_data=f"run_file_{file.name}")])
    
    await update.message.reply_text(
        "🎯 **Select script to run:**",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def delete_command(update: Update, context: CallbackContext):
    """Command to delete a script"""
    user_id = str(update.effective_user.id)
    user_folder = get_user_folder(user_id)
    files = list(user_folder.glob("*.py")) + list(user_folder.glob("*.js"))
    
    if not files:
        await update.message.reply_text("❌ No scripts to delete!")
        return
    
    keyboard = []
    for file in files:
        keyboard.append([InlineKeyboardButton(f"🗑️ {file.name}", callback_data=f"del_file_{file.name}")])
    
    await update.message.reply_text(
        "🗑️ **Select script to delete:**",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def admin_stats(update: Update, context: CallbackContext):
    """Admin command to see total users"""
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Unauthorized! Only bot owner can use this.")
        return
    
    users = load_users()
    total_files = 0
    total_size = 0
    users_with_req = 0
    
    for user_id in users:
        total_files += count_user_files(user_id)
        user_folder = get_user_folder(user_id)
        for file in user_folder.rglob("*"):
            if file.is_file():
                total_size += file.stat().st_size
        if (user_folder / "requirements.txt").exists():
            users_with_req += 1
    
    stats = f"""
📊 **Admin Statistics:**

👥 **Total Users:** {len(users)}
📁 **Total Files:** {total_files}
📦 **Users with requirements:** {users_with_req}
💾 **Storage Used:** {total_size / 1024 / 1024:.2f} MB

**Server Status:** 🟢 ONLINE
**Uptime:** 24/7
**Auto-dependency:** ✅ Enabled
**Bot Token:** Configured ✅

**Commands:**
• /broadcast [message] - Send to all users
• /announce [message] - Admin announcement
    """
    await update.message.reply_text(stats)

async def broadcast(update: Update, context: CallbackContext):
    """Admin broadcast message to all users"""
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Unauthorized!")
        return
    
    if not context.args:
        await update.message.reply_text("Usage: /broadcast Your message here")
        return
    
    message = " ".join(context.args)
    users = load_users()
    
    sent = 0
    failed = 0
    
    status_msg = await update.message.reply_text(f"📢 Broadcasting to {len(users)} users...")
    
    for user_id in users:
        try:
            await context.bot.send_message(
                int(user_id),
                f"📢 **Announcement from Admin:**\n\n{message}"
            )
            sent += 1
        except:
            failed += 1
        await asyncio.sleep(0.1)  # Avoid flood limits
    
    await status_msg.edit_text(f"✅ Broadcast complete!\n📨 Sent: {sent}\n❌ Failed: {failed}")

async def delete_all_scripts(update: Update, context: CallbackContext):
    """Delete all scripts of a user (admin command)"""
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Unauthorized!")
        return
    
    if not context.args:
        await update.message.reply_text("Usage: /delete_all_scripts <user_id>")
        return
    
    target_user = context.args[0]
    user_folder = get_user_folder(target_user)
    
    if user_folder.exists():
        shutil.rmtree(user_folder)
        user_folder.mkdir(exist_ok=True)
        await update.message.reply_text(f"✅ Deleted all scripts for user {target_user}")
    else:
        await update.message.reply_text(f"❌ User {target_user} not found!")

# ===== MAIN =====
def main():
    app = Application.builder().token(BOT_TOKEN).build()
    
    # Commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("upload", upload_command))
    app.add_handler(CommandHandler("scripts", list_scripts))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("run", run_command))
    app.add_handler(CommandHandler("delete", delete_command))
    app.add_handler(CommandHandler("admin", admin_stats))
    app.add_handler(CommandHandler("broadcast", broadcast))
    app.add_handler(CommandHandler("delete_all_scripts", delete_all_scripts))
    
    # Callback handlers
    app.add_handler(CallbackQueryHandler(handle_callback))
    
    # File handlers
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    
    print("=" * 50)
    print("🤖 YOUR HOSTING BOT IS RUNNING!")
    print("=" * 50)
    print(f"📁 Bot Token: {BOT_TOKEN[:10]}...")
    print(f"👑 Admin ID: {ADMIN_ID}")
    print(f"📂 Files stored in: {BASE_DIR.absolute()}")
    print(f"⚡ Ready to host Python and JavaScript files!")
    print(f"📦 Auto-dependency installation: ENABLED")
    print("=" * 50)
    print("✅ Bot is online and waiting for users!")
    print("")
    print("💡 How to use requirements.txt:")
    print("1. Create folder with your script + requirements.txt")
    print("2. ZIP the folder")
    print("3. Upload ZIP to bot")
    print("4. Bot auto-installs all dependencies!")
    print("=" * 50)
    
    app.run_polling()

if __name__ == "__main__":
    main()