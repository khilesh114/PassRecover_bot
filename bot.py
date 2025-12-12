import logging
import os
import asyncio
import time
import zipfile
import rarfile
import py7zr
import itertools
import psutil
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    filters
)

# --- CONFIGURATION ---
TOKEN = "8267019373:AAGkIsBBGTP88PeSzyj8uNnU2Gz05PL5iU0"  # <--- REPLACE THIS

# Directories
TEMP_DIR = "temp_files"
EXTRACT_DIR = "extracted_files"
WORDLIST_DIR = "generated_wordlists"

# Internal Memory
DEFAULT_WORDLIST_PATH = "default_passwords.txt"
if not os.path.exists(DEFAULT_WORDLIST_PATH):
    with open(DEFAULT_WORDLIST_PATH, 'w') as f:
        f.write("123456\npassword\nadmin\n")

# Limits
MAX_FILE_SIZE = 50 * 1024 * 1024
MAX_DURATION = 600
GLOBAL_LOCK = asyncio.Semaphore(1)

# Conversation States
GEN_KEYWORDS, GEN_LIMIT = range(2)
Q1_FIRST, Q2_LAST, Q3_NICK, Q4_BIRTH, Q5_PARTNER, Q6_PET, Q7_MOBILE, Q8_EXTRA = range(2, 10)

# Setup
os.makedirs(TEMP_DIR, exist_ok=True)
os.makedirs(EXTRACT_DIR, exist_ok=True)
os.makedirs(WORDLIST_DIR, exist_ok=True)
logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)

# ==============================================================================
# 🧠 SMART GENERATION LOGIC (UPDATED)
# ==============================================================================

def leet_transform(word):
    subs = {'a': '@', 'e': '3', 'i': '1', 'o': '0', 's': '$', 't': '7'}
    yield word
    chars = list(word.lower())
    for i, c in enumerate(chars):
        if c in subs: chars[i] = subs[c]
    yield "".join(chars)
    yield "".join(chars).capitalize()

def generate_targeted_passwords(data):
    """
    Generates passwords, ignoring fields where user typed 'no' or 'skip'.
    """
    # Define what counts as skipping
    SKIP_WORDS = ['no', 'skip', 'none', 'na', 'n/a', '-', '']

    # 1. Collect Raw Inputs
    raw_keywords = [
        data['first'], data['last'], data['nick'],
        data['partner'], data['pet'], data['extra']
    ]
    
    # 2. Filter out "no/skip" inputs
    keywords = []
    for k in raw_keywords:
        if k.strip().lower() not in SKIP_WORDS:
            keywords.append(k.strip())

    # Handle Special Fields (Birth/Mobile)
    birth_year = data['birth'] if data['birth'].lower() not in SKIP_WORDS else ""
    
    # Clean Mobile (remove non-digits if user typed text)
    mobile = data['mobile'] if data['mobile'].lower() not in SKIP_WORDS else ""

    # 3. Generate Variations
    base_words = set()
    for k in keywords:
        base_words.add(k)
        base_words.add(k.lower())
        base_words.add(k.capitalize())
        for l in leet_transform(k): base_words.add(l)
            
    base_words = list(base_words)
    separators = ["", "123", "!", "@", "#", "_", ".", "@123"]
    generated = set()

    for w in base_words:
        for sep in separators:
            generated.add(f"{w}{sep}")
            generated.add(f"{sep}{w}")
            if birth_year:
                generated.add(f"{w}{sep}{birth_year}")
                generated.add(f"{w}{birth_year}{sep}")
            if mobile:
                generated.add(f"{w}{mobile}")

    if len(keywords) > 1:
        for w1, w2 in itertools.permutations(keywords, 2):
            generated.add(f"{w1}{w2}")
            generated.add(f"{w1}@{w2}")
            generated.add(f"{w1}_{w2}")

    return list(generated)

def infinite_generator(keywords):
    base_words = set()
    for k in keywords: base_words.add(k); base_words.add(k.lower()); base_words.add(k.capitalize())
    base_words = list(base_words)
    separators = ["", "123", "@", "#", "_", "."]
    for w in base_words:
        yield w
        for sep in separators: yield f"{w}{sep}"
    if len(base_words) > 1:
        for w1, w2 in itertools.permutations(base_words, 2):
            for sep in separators: yield f"{w1}{sep}{w2}"
            for i in range(100): yield f"{w1}{w2}{i}"

# ==============================================================================
# 🧠 UTILS & UNLOCKER
# ==============================================================================

def get_system_stats():
    try:
        cpu = psutil.cpu_percent(interval=None)
        process = psutil.Process(os.getpid())
        ram = process.memory_info().rss / (1024 * 1024)
        return f"🖥️ CPU: {cpu}% | 💾 RAM: {int(ram)}MB"
    except: return "CPU: ? | RAM: ?"

def check_and_extract(archive, pwd, out_dir):
    try:
        if archive.endswith('.zip'):
            with zipfile.ZipFile(archive) as zf:
                zf.extract(zf.namelist()[0], path=out_dir, pwd=pwd.encode('utf-8'))
                return os.path.join(out_dir, zf.namelist()[0])
        elif archive.endswith('.rar'):
            with rarfile.RarFile(archive) as rf:
                rf.extract(rf.namelist()[0], path=out_dir, pwd=pwd)
                return os.path.join(out_dir, rf.namelist()[0])
        elif archive.endswith('.7z'):
            with py7zr.SevenZipFile(archive, 'r', password=pwd) as zf:
                zf.extractall(path=out_dir)
                return os.path.join(out_dir, zf.getnames()[0])
    except: return None

def get_progress_bar(current, total, start_time):
    percent = current / total if total > 0 else 0
    bar = '█' * int(10 * percent) + '░' * (10 - int(10 * percent))
    elapsed = time.time() - start_time
    speed = int(current / elapsed) if elapsed > 0 else 0
    return f"[{bar}] {int(percent * 100)}%\n🚀 Speed: {speed}/s | ⏱️ {int(elapsed)}s"

# ==============================================================================
# 🤖 BOT HANDLERS
# ==============================================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 **Cyber Bot v9.0**\n\n"
        "1️⃣ **Unlocker**: Upload Archive + Wordlist.\n"
        "2️⃣ **Profiler**: `/cupp` (Smart Target List).\n"
        "3️⃣ **Generator**: `/gen` (Mass List)."
    )

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🚫 Cancelled."); return ConversationHandler.END

# --- 🏭 GENERATOR ---
async def start_gen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🏭 Keywords? (comma separated)")
    return GEN_KEYWORDS
async def gen_keywords(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['keywords'] = [k.strip() for k in update.message.text.split(',') if k.strip()]
    await update.message.reply_text("🔢 Limit? (e.g. 100000)")
    return GEN_LIMIT
async def gen_process(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try: limit = int(update.message.text.replace(',', ''))
    except: limit = 100000
    if limit > 10000000: limit = 10000000
    msg = await update.message.reply_text(f"⚙️ Generating {limit}...")
    fname = f"Pass_{update.effective_user.id}"
    tpath = os.path.join(WORDLIST_DIR, fname+".txt"); zpath = os.path.join(WORDLIST_DIR, fname+".zip")
    count = 0
    with open(tpath, 'w', encoding='utf-8') as f:
        gen = infinite_generator(context.user_data['keywords'])
        try:
            while count < limit: f.write(next(gen)+'\n'); count += 1
        except: pass
    with zipfile.ZipFile(zpath, 'w', zipfile.ZIP_DEFLATED) as zf: zf.write(tpath, arcname=fname+".txt")
    await msg.edit_text(f"✅ Generated {count}. Uploading..."); await context.bot.send_document(update.effective_chat.id, open(zpath, 'rb'))
    if os.path.exists(tpath): os.remove(tpath)
    if os.path.exists(zpath): os.remove(zpath)
    return ConversationHandler.END

# --- 🕵️‍♂️ PROFILER (CUPP) - UPDATED FOR 'NO' ---

async def start_cupp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🕵️‍♂️ **Target Profiler**\nType `no` to skip any question.\n\n1. First Name?")
    return Q1_FIRST

async def ask_last(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['first'] = update.message.text
    await update.message.reply_text("2. Surname? (Type `no` to skip)")
    return Q2_LAST

async def ask_nick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['last'] = update.message.text
    await update.message.reply_text("3. Nickname? (Type `no` to skip)")
    return Q3_NICK

async def ask_birth(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['nick'] = update.message.text
    await update.message.reply_text("4. Birth Year? (Type `no` to skip)")
    return Q4_BIRTH

async def ask_partner(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['birth'] = update.message.text
    await update.message.reply_text("5. Partner's Name? (Type `no` to skip)")
    return Q5_PARTNER

async def ask_pet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['partner'] = update.message.text
    await update.message.reply_text("6. Pet's Name? (Type `no` to skip)")
    return Q6_PET

async def ask_mobile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['pet'] = update.message.text
    await update.message.reply_text("7. Mobile Digits? (Type `no` to skip)")
    return Q7_MOBILE

async def ask_extra(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['mobile'] = update.message.text
    await update.message.reply_text("8. Extra Keyword? (City/Company) (Type `no` to skip)")
    return Q8_EXTRA

async def finish_cupp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['extra'] = update.message.text
    msg = await update.message.reply_text("⚙️ Compiling Intelligence...")
    
    passwords = generate_targeted_passwords(context.user_data)
    
    # Auto-Add to Memory
    try:
        existing = set()
        with open(DEFAULT_WORDLIST_PATH, 'r', encoding='utf-8', errors='ignore') as f:
            for l in f: existing.add(l.strip())
        new_ones = [p for p in passwords if p not in existing]
        if new_ones:
            with open(DEFAULT_WORDLIST_PATH, 'a', encoding='utf-8') as f: f.write("\n".join(new_ones) + "\n")
    except: pass

    fpath = os.path.join(WORDLIST_DIR, f"Target_{context.user_data['first']}.txt")
    with open(fpath, 'w', encoding='utf-8') as f: f.write("\n".join(passwords))
    
    await msg.edit_text(f"🎯 Generated {len(passwords)} passwords. Uploading...")
    await context.bot.send_document(update.effective_chat.id, open(fpath, 'rb'))
    if os.path.exists(fpath): os.remove(fpath)
    return ConversationHandler.END

# --- 🔓 UNLOCKER & CUSTOM UPLOAD ---
async def handle_files(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    if doc.file_size > MAX_FILE_SIZE: return await update.message.reply_text("❌ File > 50MB.")
    path = os.path.join(TEMP_DIR, f"{update.effective_user.id}_{doc.file_name}")
    await (await context.bot.get_file(doc.file_id)).download_to_drive(path)
    
    if doc.file_name.lower().endswith(('.zip','.rar','.7z')):
        context.user_data['archive_path'] = path; context.user_data['archive_name'] = doc.file_name
        msg = "📦 **Target Locked.** Upload custom `.txt` or use default."
    elif doc.file_name.lower().endswith('.txt'):
        context.user_data['wordlist_path'] = path; context.user_data['wordlist_name'] = doc.file_name
        msg = f"📄 **Payload Loaded**: `{doc.file_name}`"
    else: return
    
    archive = context.user_data.get('archive_path'); wlist = context.user_data.get('wordlist_path')
    kb = []
    if archive:
        if wlist: kb.append([InlineKeyboardButton("🚀 ATTACK (CUSTOM)", callback_data="start_custom")])
        else:
            kb.append([InlineKeyboardButton("⚡ ATTACK (DEFAULT)", callback_data="start_default")])
            kb.append([InlineKeyboardButton("📤 UPLOAD LIST", callback_data="ask_upload")])
    kb.append([InlineKeyboardButton("🗑️ RESET", callback_data="reset")])
    await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    if q.data == "ask_upload": await q.edit_message_text("📂 Upload .txt now.")
    elif q.data == "reset": context.user_data.clear(); await q.edit_message_text("🗑️ Flushed.")
    elif q.data == "stop": context.user_data['stop'] = True
    elif q.data in ["start_custom", "start_default"]:
        if q.data == "start_default": context.user_data['wordlist_path'] = DEFAULT_WORDLIST_PATH
        await run_queue(update, context)

async def run_queue(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if GLOBAL_LOCK.locked(): await q.edit_message_text("⚠️ Busy. Queueing...")
    async with GLOBAL_LOCK:
        await q.edit_message_text("💀 **INITIATING...**")
        await attack_logic(update, context)

async def attack_logic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    archive = context.user_data.get('archive_path')
    wlist = context.user_data.get('wordlist_path')
    context.user_data['stop'] = False
    
    try:
        with open(wlist, 'r', encoding='utf-8', errors='ignore') as f: pwds = [l.strip() for l in f if l.strip()]
    except: return await q.edit_message_text("❌ Payload Error.")

    await q.edit_message_text(f"🚀 Attacking with {len(pwds)} keys...", parse_mode='Markdown')
    start_t = time.time()
    
    for i, pwd in enumerate(pwds):
        if context.user_data.get('stop'): return await q.edit_message_text("🛑 **ABORTED**")
        if (time.time() - start_t) > MAX_DURATION: return await q.edit_message_text("⏱️ **TIMEOUT**")
        
        extracted = check_and_extract(archive, pwd, os.path.join(EXTRACT_DIR, str(update.effective_user.id)))
        if extracted:
            await context.bot.send_message(update.effective_chat.id, f"🎉 **ACCESS GRANTED**\n🔑: `{pwd}`", parse_mode='Markdown')
            try: await context.bot.send_document(update.effective_chat.id, open(extracted, 'rb'))
            except: pass
            return

        if i % 200 == 0 and int(time.time()) % 3 == 0:
            try:
                stats = get_progress_bar(i, len(pwds), start_t); sys = get_system_stats()
                await q.edit_message_text(f"⚔️ **Brute Force**\n{stats}\n{sys}\nTry: ||{pwd[:2]}..||", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🛑 ABORT", callback_data="stop")]]), parse_mode='MarkdownV2')
            except: pass
            await asyncio.sleep(0)
    
    await context.bot.send_message(update.effective_chat.id, "❌ Access Denied.")
    if wlist and "default" not in wlist: os.remove(wlist)
    if archive: os.remove(archive)

# --- MAIN ---
if __name__ == '__main__':
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler('gen', start_gen)],
        states={GEN_KEYWORDS: [MessageHandler(filters.TEXT, gen_keywords)], GEN_LIMIT: [MessageHandler(filters.TEXT, gen_process)]},
        fallbacks=[CommandHandler('cancel', cancel)]
    ))
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler('cupp', start_cupp)],
        states={
            Q1_FIRST: [MessageHandler(filters.TEXT, ask_last)], Q2_LAST: [MessageHandler(filters.TEXT, ask_nick)],
            Q3_NICK: [MessageHandler(filters.TEXT, ask_birth)], Q4_BIRTH: [MessageHandler(filters.TEXT, ask_partner)],
            Q5_PARTNER: [MessageHandler(filters.TEXT, ask_pet)], Q6_PET: [MessageHandler(filters.TEXT, ask_mobile)],
            Q7_MOBILE: [MessageHandler(filters.TEXT, ask_extra)], Q8_EXTRA: [MessageHandler(filters.TEXT, finish_cupp)],
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    ))
    app.add_handler(CommandHandler('start', start))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_files))
    app.add_handler(CallbackQueryHandler(button_handler))
    print("✅ SYSTEM ONLINE")
    app.run_polling()
