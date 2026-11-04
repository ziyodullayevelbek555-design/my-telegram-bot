import os
import io
import json
import asyncio
import logging
import html
from datetime import datetime
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

from openai import AsyncOpenAI
from anthropic import AsyncAnthropic
from google import genai
from google.genai import types

load_dotenv()

# =========================================================
# EduMind AI v6.2 — Ultimate Next-Gen Educational Ecosystem
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
DEFAULT_PROVIDER = os.getenv("AI_DEFAULT_PROVIDER", "gemini").lower()
AUTO_FALLBACK = os.getenv("AI_AUTO_FALLBACK", "true").lower() == "true"
ADMIN_IDS = {int(x.strip()) for x in os.getenv("ADMIN_IDS", "6841567673").split(",") if x.strip().isdigit()}

MODELS = {
    "gemini": os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
    "deepseek": os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
    "groq": os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile"),
    "openai": os.getenv("OPENAI_MODEL", "gpt-4o"),
    "claude": os.getenv("CLAUDE_MODEL", "claude-3-5-sonnet-20241022"),
}

GEMINI_VISION_MODEL = os.getenv("GEMINI_VISION_MODEL", MODELS["gemini"])
GEMINI_IMAGE_MODEL = os.getenv("GEMINI_IMAGE_MODEL", "imagen-3.0-generate-002")

KEYS = {
    "gemini": os.getenv("GEMINI_API_KEY"),
    "deepseek": os.getenv("DEEPSEEK_API_KEY"),
    "groq": os.getenv("GROQ_API_KEY"),
    "openai": os.getenv("OPENAI_API_KEY"),
    "claude": os.getenv("ANTHROPIC_API_KEY") or os.getenv("CLAUDE_API_KEY"),
}

FAST_FALLBACK = ["gemini", "groq", "openai", "claude", "deepseek"]
PROVIDER_TIMEOUT = float(os.getenv("AI_PROVIDER_TIMEOUT", "22"))

BRAND_FOOTER = "✨ <i>EduMind AI — Ta'lim va Ilm Ziyo Maskani</i>\n<i>crafted by: @elbek_ziyodullayev</i>"

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
log = logging.getLogger("edumind")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN .env faylida topilmadi")

# ------------------------- Clients -------------------------

gemini_client = genai.Client(api_key=KEYS["gemini"]) if KEYS["gemini"] else None
openai_client = AsyncOpenAI(api_key=KEYS["openai"]) if KEYS["openai"] else None
deepseek_client = AsyncOpenAI(api_key=KEYS["deepseek"], base_url="https://api.deepseek.com") if KEYS["deepseek"] else None
groq_client = AsyncOpenAI(api_key=KEYS["groq"], base_url="https://api.groq.com/openai/v1") if KEYS["groq"] else None
claude_client = AsyncAnthropic(api_key=KEYS["claude"]) if KEYS["claude"] else None

PROVIDER_LABELS = {
    "gemini": "✨ Gemini",
    "deepseek": "🚀 DeepSeek",
    "groq": "⚡ Groq",
    "openai": "🧠 GPT",
    "claude": "🔵 Claude",
}

# ------------------------- Persistent Storage & Files -------------------------

DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)
USERS_FILE = DATA_DIR / "users.json"
ACTIVITIES_FILE = DATA_DIR / "activities.json"
GUIDES_FILE = DATA_DIR / "guides.json"
FEEDBACK_FILE = DATA_DIR / "feedback.json"
REQUESTS_FILE = DATA_DIR / "requests.json"
REFERRALS_LOG_FILE = DATA_DIR / "referrals_log.json"

def load_json(file_path: Path, default):
    try:
        return json.loads(file_path.read_text(encoding="utf-8")) if file_path.exists() else default
    except Exception:
        return default

users = load_json(USERS_FILE, {})
activities = load_json(ACTIVITIES_FILE, [])
guides = load_json(GUIDES_FILE, {}) 
feedbacks = load_json(FEEDBACK_FILE, [])
purchase_requests = load_json(REQUESTS_FILE, [])
referrals_log = load_json(REFERRALS_LOG_FILE, [])

active_pomodoros = {}

def save_json(file_path: Path, data):
    try:
        file_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        log.error(f"Error saving {file_path}: {e}")

def log_activity(uid: int, full_name: str, username: str, action: str, desc: str):
    try:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        act_entry = {
            "time": now, "uid": uid,
            "full_name": full_name or f"ID: {uid}",
            "username": f"@{username}" if username else "username_yo'q",
            "action": action, "desc": desc
        }
        activities.insert(0, act_entry)
        save_json(ACTIVITIES_FILE, activities[:500])
    except Exception as e:
        log.error(f"Error logging activity: {e}")

def user_state(uid: int, message_obj: Optional[Message] = None):
    key = str(uid)
    if message_obj and message_obj.from_user:
        fn = message_obj.from_user.full_name or f"ID: {uid}"
        un = message_obj.from_user.username or ""
    else:
        fn, un = f"ID: {uid}", ""

    if key not in users:
        users[key] = {
            "history": [],
            "provider": DEFAULT_PROVIDER,
            "mode": "ai_tutor",
            "lang": "uz",
            "points": 10,
            "queries_count": 0,
            "blocked": False,
            "status": "Standart",
            "last_query": "",
            "full_name": fn,
            "username": un,
            "joined_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "referred_by": None,
            "referrals_count": 0,
            "reward_claimed": False,
            "admin_auth": False,
            "admin_action": None,
            "awaiting_purchase": None,
            "awaiting_feedback": False,
            "awaiting_guide_code": False,
            "temp_guide_title": None,
        }
    else:
        users[key]["full_name"] = fn or users[key].get("full_name", f"ID: {uid}")
        users[key]["username"] = un
        if "joined_at" not in users[key]:
            users[key]["joined_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if "referrals_count" not in users[key]:
            users[key]["referrals_count"] = 0
    return users[key]

# ------------------------- Keyboards -------------------------

def get_kb(uid: int):
    is_adm = uid in ADMIN_IDS
    keyboard = [
        [KeyboardButton(text="🚀 START"), KeyboardButton(text="🧠 AI TUTOR")],
        [KeyboardButton(text="📚 FANLAR"), KeyboardButton(text="📝 NEW TEST")],
        [KeyboardButton(text="🎯 QUIZ"), KeyboardButton(text="🖼 RASM TAHLILI")],
        [KeyboardButton(text="🍅 POMODORO TIMER"), KeyboardButton(text="🎁 REFERRAL (STARS)")],
        [KeyboardButton(text="🎨 RASM YARATISH"), KeyboardButton(text="👨‍🏫 O‘QITUVCHI")],
        [KeyboardButton(text="🇬🇧 IELTS"), KeyboardButton(text="🎯 EXAM MODE")],
        [KeyboardButton(text="⭐ STARS SOTIB OLISH"), KeyboardButton(text="💎 TELEGRAM PREMIUM")],
        [KeyboardButton(text="✨ GEMINI PRO (18 OYLIK)"), KeyboardButton(text="👤 PROFIL")],
        [KeyboardButton(text="📚 QO'LLANMALAR"), KeyboardButton(text="✍️ FIKR BILDIRISH")],
        [KeyboardButton(text="👨‍💻 BOT YARATUVCHISI"), KeyboardButton(text="🎓 UNIVERSITET")],
        [KeyboardButton(text="🌐 TIL"), KeyboardButton(text="🤖 AI TANLASH")],
        [KeyboardButton(text="🔄 YANGI SUHBAT"), KeyboardButton(text="ℹ️ YORDAM")],
    ]
    if is_adm:
        keyboard.append([KeyboardButton(text="👨‍💼 ADMIN PANEL")])
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)

POMODORO_KB = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="▶️ Pomodoro Boshlash"), KeyboardButton(text="⏹️ To'xtatish")],
        [KeyboardButton(text="⬅️ MENU")],
    ],
    resize_keyboard=True,
)

AI_KB = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="✨ Gemini"), KeyboardButton(text="⚡ Groq")],
        [KeyboardButton(text="🧠 GPT"), KeyboardButton(text="🔵 Claude")],
        [KeyboardButton(text="🚀 DeepSeek")],
        [KeyboardButton(text="⬅️ MENU")],
    ],
    resize_keyboard=True,
)

IELTS_KB = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🗣 IELTS SPEAKING"), KeyboardButton(text="✍️ IELTS WRITING")],
        [KeyboardButton(text="📖 IELTS READING"), KeyboardButton(text="🎧 IELTS LISTENING")],
        [KeyboardButton(text="📊 IELTS FULL MOCK")],
        [KeyboardButton(text="⬅️ MENU")],
    ],
    resize_keyboard=True,
)

DOMAIN_KB = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🧮 OLIY MATEMATIKA"), KeyboardButton(text="💻 DASTURLASH")],
        [KeyboardButton(text="⚛️ FIZIKA"), KeyboardButton(text="🧪 KIMYO")],
        [KeyboardButton(text="🧬 BIOLOGIYA"), KeyboardButton(text="📐 GEOMETRIYA")],
        [KeyboardButton(text="📊 STATISTIKA"), KeyboardButton(text="💼 IQTISOD")],
        [KeyboardButton(text="📚 BARCHA FANLAR")],
        [KeyboardButton(text="⬅️ MENU")],
    ],
    resize_keyboard=True,
)

ADMIN_KB = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📊 STATISTIKA"), KeyboardButton(text="👥 BARCHA FOYDALANUVCHILAR")],
        [KeyboardButton(text="🚫 BLOKLAGANLAR"), KeyboardButton(text="🕒 OXIRGI FAOLIYATLAR")],
        [KeyboardButton(text="📥 XARID SO'ROVLARI"), KeyboardButton(text="🎁 REFERRAL TARIXI")],
        [KeyboardButton(text="💬 FIKR & SHARHLAR"), KeyboardButton(text="📚 PDF QO'LLANMA QO'SHISH")],
        [KeyboardButton(text="📢 BARCHAGA XABAR"), KeyboardButton(text="🔒 ADMINDAN CHIQISH")],
        [KeyboardButton(text="⬅️ MENU")],
    ],
    resize_keyboard=True,
)

LANG_KB = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🇺🇿 O'zbekcha"), KeyboardButton(text="🇷🇺 Русский"), KeyboardButton(text="🇬🇧 English")],
        [KeyboardButton(text="⬅️ MENU")],
    ],
    resize_keyboard=True,
)

def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS

# ------------------------- Domain & Pedagogy System -------------------------

DOMAIN = {
    "ai_tutor": {"provider": "gemini", "system": "Universal AI tutor. Explain clearly and precisely with utmost politeness and rich engaging emojis."},
    "subjects": {"provider": "gemini", "system": "Expert school/university subject tutor with rich emojis."},
    "math": {"provider": "gemini", "system": "Strict Expert Mathematician. Focus on precision, formulas, step-by-step logic with rich emojis."},
    "physics": {"provider": "gemini", "system": "Strict Physics Expert. Focus on laws, formulas, units, calculations with rich emojis."},
    "chemistry": {"provider": "gemini", "system": "Strict Chemistry Expert. Focus on equations, mechanisms with rich emojis."},
    "biology": {"provider": "gemini", "system": "Strict Biology Expert. Focus on terminology, processes with rich emojis."},
    "geometry": {"provider": "gemini", "system": "Strict Geometry Expert. Focus on theorems, proofs with rich emojis."},
    "statistics": {"provider": "openai", "system": "Expert Statistics tutor with rich emojis."},
    "economics": {"provider": "openai", "system": "Expert Economics tutor with rich emojis."},
    "programming": {"provider": "groq", "system": "Senior Software Engineer. Provide clean code and explanations with rich emojis."},
    "new_test": {"provider": "groq", "system": "Assessment Generator. Create premium tests with answer keys and rich emojis."},
    "quiz": {"provider": "groq", "system": "Fast Quiz Master. Ask one tailored question at a time with rich emojis."},
    "image_analysis": {"provider": "gemini", "system": "Vision Tutor. Precisely analyze diagrams with rich emojis."},
    "teacher": {"provider": "claude", "system": "Experienced Teacher and Curriculum Designer with rich emojis."},
    "ielts": {"provider": "claude", "system": "Strict IELTS Examiner. Evaluate based on official 4 criteria with rich emojis."},
    "exam": {"provider": "openai", "system": "Exam Preparation Specialist with rich emojis."},
    "profile": {"provider": "gemini", "system": "Personal Learning Coach with rich emojis."},
    "project": {"provider": "openai", "system": "Academic Project Supervisor with rich emojis."},
    "university": {"provider": "openai", "system": "University Academic Advisor with rich emojis."},
    "language": {"provider": "gemini", "system": "Multilingual Language Tutor with rich emojis."},
    "pdf": {"provider": "gemini", "system": "Document Analyst with rich emojis."},
}

def footer(text: str) -> str:
    if BRAND_FOOTER in text:
        return text
    return f"{text}\n\n{BRAND_FOOTER}"

def clean(text: str) -> str:
    return text.strip() if text else "Javob tayyorlashda xatolik yuz berdi. Hurmatli foydalanuvchi, iltimos qaytadan urinib ko'ring! 🙏✨"

def split_text(text: str, limit: int = 3900):
    text = clean(text)
    return [text[i:i+limit] for i in range(0, len(text), limit)] or [""]

async def send_long(message: Message, text: str, reply_markup=None):
    parts = split_text(footer(text))
    for i, part in enumerate(parts):
        await message.answer(
            part,
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup if i == len(parts)-1 else None,
        )

def provider_is_configured(name: str) -> bool:
    return bool(KEYS.get(name))

def make_messages(uid: int, prompt: str, system: str):
    st = user_state(uid)
    history = st.get("history", [])[-8:]
    msgs = [{"role": "system", "content": system + "\nAlways use plenty of expressive emojis, structured points, and engaging format! 🌟🧠💡🚀✨"}]
    msgs.extend(history)
    msgs.append({"role": "user", "content": prompt})
    return msgs

def remember(uid: int, user_text: str, answer: str):
    st = user_state(uid)
    st.setdefault("history", []).append({"role": "user", "content": user_text})
    st["history"].append({"role": "assistant", "content": answer})
    st["history"] = st["history"][-16:]
    st["queries_count"] = st.get("queries_count", 0) + 1
    st["last_query"] = user_text[:100]
    st["points"] = st.get("points", 10) + 10
    save_json(USERS_FILE, users)

async def call_openai_compatible(name: str, messages):
    client = {"openai": openai_client, "deepseek": deepseek_client, "groq": groq_client}.get(name)
    if not client:
        raise RuntimeError(f"{name} API key sozlanmagan")
    response = await client.chat.completions.create(
        model=MODELS[name], messages=messages, temperature=0.3, max_tokens=4000,
    )
    return response.choices[0].message.content or ""

async def call_claude(messages):
    if not claude_client:
        raise RuntimeError("Claude API key sozlanmagan")
    resp = await claude_client.messages.create(
        model=MODELS["claude"], system=messages[0]["content"], messages=messages[1:], max_tokens=4000, temperature=0.3,
    )
    return "".join(getattr(x, "text", "") for x in resp.content)

async def call_gemini(messages):
    if not gemini_client:
        raise RuntimeError("Gemini API key sozlanmagan")
    system = messages[0]["content"]
    contents = [types.Content(role="user" if m["role"] == "user" else "model", parts=[types.Part.from_text(text=m["content"])]) for m in messages[1:]]
    def run():
        return gemini_client.models.generate_content(
            model=MODELS["gemini"], contents=contents, config=types.GenerateContentConfig(system_instruction=system, temperature=0.3, max_output_tokens=4000),
        )
    result = await asyncio.to_thread(run)
    return result.text or ""

async def call_provider(name: str, messages):
    if name == "gemini": return await call_gemini(messages)
    if name in ("openai", "deepseek", "groq"): return await call_openai_compatible(name, messages)
    if name == "claude": return await call_claude(messages)
    raise RuntimeError(f"Noma'lum provider: {name}")

async def ask_ai(uid: int, prompt: str, mode: Optional[str] = None):
    st = user_state(uid)
    mode = mode or st.get("mode", "ai_tutor")
    cfg = DOMAIN.get(mode, DOMAIN["ai_tutor"])
    selected = cfg["provider"]

    candidates = [selected]
    if AUTO_FALLBACK:
        for p in FAST_FALLBACK:
            if p != selected and p not in candidates: candidates.append(p)

    system = cfg["system"] + """
You are EduMind AI. Always reply with extreme politeness, warmth, utmost clarity, and engaging rich emojis. Follow this exact 5-step didactic format:
1. 🎯 **Aniq javob (Direct Answer):** To'g'ridan-to'g'ri va aniq yechim.
2. 💡 **Sababi (The Reason):** Nima uchun aynan shundayligi haqida qisqacha asos.
3. 📖 **Tushuntirish (Detailed Explanation):** Bosqichma-bosqich o'quvchiga tushunarli bayon.
4. 🔬 **Misol (Practical Example):** Aniq real hayotiy yoki ilmiy misol.
5. ✍️ **Topshiriq (Practice Task):** Bilimni sinash uchun kichik mini-savol.
Tone: Extremely polite, respectful, warm, highly engaging, rich with expressive emojis and visual formatting.
"""
    messages = make_messages(uid, prompt, system)
    errors = []
    for name in candidates:
        if not provider_is_configured(name): continue
        try:
            answer = await asyncio.wait_for(call_provider(name, messages), timeout=PROVIDER_TIMEOUT)
            answer = clean(answer)
            if answer:
                st["last_provider"] = name
                remember(uid, prompt, answer)
                return answer, name
        except Exception as e:
            errors.append(f"{name}: {str(e)[:100]}")
    raise RuntimeError(" | ".join(errors) if errors else "AI xizmati vaqtincha ishlamayapti. Iltimos, ozgina tanaffusdan keyin urinib ko'ring! 🙏✨")

# ------------------------- Bot & Dispatcher -------------------------

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

@dp.message(CommandStart())
async def start(message: Message):
    uid = message.from_user.id
    args = message.text.split()
    st = user_state(uid, message)
    st["mode"] = "ai_tutor"
    st["blocked"] = False

    # Referral tracking with inviter details
    if len(args) > 1 and args[1].startswith("ref_"):
        try:
            inviter_id = int(args[1].replace("ref_", ""))
            if inviter_id != uid and not st.get("referred_by"):
                inviter_key = str(inviter_id)
                if inviter_key in users:
                    st["referred_by"] = inviter_id
                    users[inviter_key]["referrals_count"] = users[inviter_key].get("referrals_count", 0) + 1
                    
                    inviter_name = users[inviter_key].get("full_name", f"ID: {inviter_id}")
                    new_user_name = message.from_user.full_name or f"ID: {uid}"
                    
                    # Log referral relationship
                    ref_log_entry = {
                        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "inviter_id": inviter_id,
                        "inviter_name": inviter_name,
                        "new_user_id": uid,
                        "new_user_name": new_user_name
                    }
                    referrals_log.insert(0, ref_log_entry)
                    save_json(REFERRALS_LOG_FILE, referrals_log[-200:])
                    
                    inviter_ref_count = users[inviter_key]["referrals_count"]
                    if inviter_ref_count >= 15 and not users[inviter_key].get("reward_claimed"):
                        users[inviter_key]["reward_claimed"] = True
                        
                        for adm_id in ADMIN_IDS:
                            try:
                                await bot.send_message(
                                    adm_id,
                                    f"🎁 <b>YANGI REFERRAL MUKOFOT SO'ROVI!</b> ⭐\n\n"
                                    f"👤 Taklif qilgan: <a href='tg://user?id={inviter_id}'>{html.escape(inviter_name)}</a> (ID: <code>{inviter_id}</code>)\n"
                                    f"👥 Qo'shilgan do'stlar: <b>{inviter_ref_count} ta</b>\n"
                                    f"✨ Iltimos, foydalanuvchiga 15 ta Stars taqdim eting!"
                                )
                            except Exception:
                                pass
        except Exception:
            pass

    save_json(USERS_FILE, users)
    log_activity(uid, message.from_user.full_name, message.from_user.username, "START", "Botni ishga tushirdi /start 🚀")
    
    await send_long(
        message,
        "🌟 <b>Assalomu alaykum, hurmatli va qadrli foydalanuvchi!</b> ✨\n\n"
        "🚀 <b>EduMind AI v6.2 Super-App</b> — ta'lim va ilm ziyo maskaniga xush kelibsiz! 📚🎯\n\n"
        "🎯 Har bir savolingizga 5 qadamli mukammal tuzilmada o'ta xushmuomalalik bilan javob beramiz:\n"
        "<i>✨ Aniq javob ➔ Sababi ➔ Tushuntirish ➔ Misol ➔ Topshiriq</i> 💡\n\n"
        "👇 Marhamat, quyidagi jozibali bo'limlardan birini tanlang:",
        get_kb(uid)
    )

@dp.message(Command("help"))
async def help_cmd(message: Message):
    uid = message.from_user.id
    log_activity(uid, message.from_user.full_name, message.from_user.username, "HELP", "Yordam bo'limi ℹ️")
    await send_long(
        message,
        "ℹ️ <b>EduMind AI Yordam Markazi</b> 🌟\n\n"
        "• Savolingizni istalgan tilda oddiy matn ko'rinishida yuboring. 📝\n"
        "• Rasm, Video yoki PDF yuborsangiz — avtomatik Vision & PDF tahlil ishga tushadi! 📄🖼🎥\n"
        "• ⭐ Stars, 💎 Telegram Premium va ✨ Gemini Pro obunalarini qulay narxlarda xarid qiling. 💳\n"
        "• 🍅 Pomodoro avtomatik taymeri orqali samarali dars qiling!\n"
        "• 🎁 Referral orqali 15 ta do'st olib kelib bepul Stars yutib oling!\n"
        "• Har bir faolligingiz uchun maxsus XP ballar taqdim etiladi! 🏆✨",
        get_kb(uid)
    )

@dp.message(Command("new"))
@dp.message(F.text == "🔄 YANGI SUHBAT")
async def new_chat(message: Message):
    uid = message.from_user.id
    st = user_state(uid, message)
    st["history"] = []
    st["mode"] = "ai_tutor"
    st["awaiting_purchase"] = None
    st["awaiting_feedback"] = False
    st["awaiting_guide_code"] = False
    save_json(USERS_FILE, users)
    log_activity(uid, message.from_user.full_name, message.from_user.username, "NEW_CHAT", "Suhbat tozalandi 🔄")
    await send_long(message, "🔄 Suhbat tarixi tozalandi. Yangi savolingizni bering! 😊✨", get_kb(uid))

@dp.message(F.text == "⬅️ MENU")
async def menu(message: Message):
    uid = message.from_user.id
    st = user_state(uid, message)
    st["awaiting_purchase"] = None
    st["awaiting_feedback"] = False
    st["awaiting_guide_code"] = False
    await message.answer("🏠 Asosiy menyu ✨ Kerakli bo'limni tanlang:", reply_markup=get_kb(uid))

@dp.message(F.text == "🤖 AI TANLASH")
async def choose_ai(message: Message):
    await message.answer("🤖 <b>Sun'iy intellekt provayderini tanlang:</b> ✨", reply_markup=AI_KB)

@dp.message(F.text.in_(set(PROVIDER_LABELS.values())))
async def provider_select(message: Message):
    mapping = {"✨ Gemini": "gemini", "🚀 DeepSeek": "deepseek", "⚡ Groq": "groq", "🧠 GPT": "openai", "🔵 Claude": "claude"}
    name = mapping[message.text]
    uid = message.from_user.id
    st = user_state(uid, message)
    st["provider"] = name
    save_json(USERS_FILE, users)
    log_activity(uid, message.from_user.full_name, message.from_user.username, "AI_SELECT", f"{PROVIDER_LABELS[name]} tanlandi 🤖")
    await send_long(message, f"✅ {PROVIDER_LABELS[name]} faollashtirildi! 🚀✨", get_kb(uid))

# ================= AUTOMATIC POMODORO TIMER =================

async def pomodoro_loop(uid: int, chat_id: int):
    try:
        while True:
            await bot.send_message(chat_id, "🍅 <b>Pomodoro Fokus vaqti boshlandi! (25 daqiqa)</b> 🧠🔥\nBarcha chalg'ituvchi narsalarni yig'ishtirib, to'liq darsga e'tibor qarating!")
            await asyncio.sleep(1500)
            
            await bot.send_message(chat_id, "☕ <b>Tabriklaymiz! 25 daqiqalik fokus yakunlandi.</b> 🎉\nEndi <b>5 daqiqalik tanaffus</b> vaqti! Dam oling, biroz hordiq chiqaring 🛋️✨")
            await asyncio.sleep(300)
    except asyncio.CancelledError:
        pass
    except Exception as e:
        log.error(f"Pomodoro error for {uid}: {e}")

@dp.message(F.text == "🍅 POMODORO TIMER")
async def pomodoro_handler(message: Message):
    uid = message.from_user.id
    log_activity(uid, message.from_user.full_name, message.from_user.username, "POMODORO", "Pomodoro bo'limiga kirdi 🍅")
    text = (
        "🍅 <b>Avtomatik Pomodoro Dars Taymeri</b> ⏱️✨\n\n"
        "🧠 <b>25 daqiqa</b> — Fokus va dars qilish\n"
        "☕ <b>5 daqiqa</b> — Tanaffus va dam olish\n"
        "🔄 <i>Sikl to'xtatmaguningizcha avtomatik ravishda to'xtovsiz takrorlanaveradi!</i>\n\n"
        "👇 Taymerni boshlash yoki to'xtatish uchun quyidagi tugmalardan foydalaning:"
    )
    await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=POMODORO_KB)

@dp.message(F.text == "▶️ Pomodoro Boshlash")
async def start_pomodoro(message: Message):
    uid = message.from_user.id
    if uid in active_pomodoros and not active_pomodoros[uid].done():
        await message.answer("⚠️ Sizda allaqachon faol Pomodoro taymeri ishlamoqda! 🍅✨", reply_markup=POMODORO_KB)
        return
    
    task = asyncio.create_task(pomodoro_loop(uid, message.chat.id))
    active_pomodoros[uid] = task
    log_activity(uid, message.from_user.full_name, message.from_user.username, "POMODORO_START", "Pomodoro boshladi ▶️")
    await message.answer("🚀 <b>Pomodoro avtomatik taymeri ishga tushdi!</b> 🍅✨", parse_mode=ParseMode.HTML, reply_markup=POMODORO_KB)

@dp.message(F.text == "⏹️ To'xtatish")
async def stop_pomodoro(message: Message):
    uid = message.from_user.id
    if uid in active_pomodoros and not active_pomodoros[uid].done():
        active_pomodoros[uid].cancel()
        del active_pomodoros[uid]
        log_activity(uid, message.from_user.full_name, message.from_user.username, "POMODORO_STOP", "Pomodoro to'xtatdi ⏹️")
        await message.answer("⏹️ <b>Pomodoro taymeri to'xtatildi.</b> Yaxshi dam oling! ✨", parse_mode=ParseMode.HTML, reply_markup=POMODORO_KB)
    else:
        await message.answer("ℹ️ Hozirda faol ishlayotgan taymeringiz yo'q.", reply_markup=POMODORO_KB)

# ================= REFERRAL (STARS) =================

@dp.message(F.text == "🎁 REFERRAL (STARS)")
async def referral_handler(message: Message):
    uid = message.from_user.id
    st = user_state(uid, message)
    me = await bot.get_me()
    ref_link = f"https://t.me/{me.username}?start=ref_{uid}"
    count = st.get("referrals_count", 0)
    
    log_activity(uid, message.from_user.full_name, message.from_user.username, "REFERRAL", "Referral bo'limi 🎁")
    text = (
        "🎁 <b>Do'stingni Taklif Qil — Bepul Stars Yut!</b> ⭐✨\n\n"
        f"🔗 <b>Sizning shaxsiy taklif havolangiz:</b>\n<code>{ref_link}</code>\n\n"
        f"📊 <b>Siz taklif qilgan do'stlar soni:</b> <b>{count} / 15 ta</b>\n\n"
        "📌 <i>Havolangiz orqali <b>15 ta do'stingiz</b> botga a'zo bo'lsa, adminlarimiz sizga **15 ta Telegram Stars** taqdim etishadi!</i> 🎉"
    )
    await send_long(message, text, get_kb(uid))

# ================= CREATOR PANEL =================

@dp.message(F.text == "👨‍💻 BOT YARATUVCHISI")
async def creator_info(message: Message):
    uid = message.from_user.id
    log_activity(uid, message.from_user.full_name, message.from_user.username, "CREATOR", "Yaratuvchi haqida 👨‍💻")
    text = (
        "👨‍💻 <b>Bot yaratuvchisi va Lider haqida ma'lumot</b> ✨\n\n"
        "Men Ziyodullayev Elbek, Qashqadaryo viloyati, G'uzor tumani, 68-DIMI ning 11-sinf o'quvchisiman. 🎓\n"
        "Yosh va ishtiyoqli coder, programmerman! 💻🚀\n\n"
        "📩 <b>Aloqa uchun:</b> @elbek_ziyodullayev"
    )
    await send_long(message, text, get_kb(uid))

# ================= PURCHASE MODULES =================

@dp.message(F.text == "⭐ STARS SOTIB OLISH")
async def buy_stars_handler(message: Message):
    uid = message.from_user.id
    st = user_state(uid, message)
    st["awaiting_purchase"] = "stars"
    save_json(USERS_FILE, users)
    log_activity(uid, message.from_user.full_name, message.from_user.username, "STARS", "Stars sotib olish ⭐")
    
    text = (
        "⭐ <b>Telegram Stars Xarid qilish</b> ✨\n\n"
        "• 50 ta Stars — 13,999 so'm 🌟\n"
        "• 100 ta Stars — 23,999 so'm 🔥\n"
        "• 200 ta Stars — 46,999 so'm 💎\n\n"
        "👇 <b>Qancha stars olishni yozib yuboring:</b>"
    )
    await send_long(message, text, get_kb(uid))

@dp.message(F.text == "💎 TELEGRAM PREMIUM")
async def buy_tg_premium_handler(message: Message):
    uid = message.from_user.id
    st = user_state(uid, message)
    st["awaiting_purchase"] = "premium"
    save_json(USERS_FILE, users)
    log_activity(uid, message.from_user.full_name, message.from_user.username, "PREMIUM", "Telegram Premium 💎")
    
    text = (
        "💎 <b>Telegram Premium Obunasi</b> 🚀\n\n"
        "• 1 oylik — 45,999 so'm 🌟\n"
        "• 1 yillik — 282,999 so'm 🔥\n\n"
        "👇 <b>Tarifni tanlab yozib yuboring:</b>"
    )
    await send_long(message, text, get_kb(uid))

@dp.message(F.text == "✨ GEMINI PRO (18 OYLIK)")
async def buy_gemini_pro_handler(message: Message):
    uid = message.from_user.id
    st = user_state(uid, message)
    st["awaiting_purchase"] = "gemini_pro"
    save_json(USERS_FILE, users)
    log_activity(uid, message.from_user.full_name, message.from_user.username, "GEMINI_PRO", "Gemini Pro ✨")
    
    text = (
        "💎 <b>Google Gemini AI Pro Status (18 oylik)</b> 🔥\n\n"
        "⚠️ Dushanbadan narx 79,000 soʻmga oshadi. Atigi 8 ta link qoldi! ⏳\n"
        "💰 <b>Narx:</b> 49,000 so'm / oy 📉🔥\n\n"
        "👇 <b>Istagingizni tasdiqlab yozing:</b>"
    )
    await send_long(message, text, get_kb(uid))

# ================= IMAGE GENERATION RESTRICTION HANDLER =================

@dp.message(F.text == "🎨 RASM YARATISH")
async def image_generate_restricted(message: Message):
    uid = message.from_user.id
    st = user_state(uid, message)
    log_activity(uid, message.from_user.full_name, message.from_user.username, "IMAGE_GEN_RESTRICTED", "Rasm yaratishga urindi (Cheklangan) 🎨")
    
    text = (
        "🎨 <b>Sun'iy Intellekt Orqali Rasm Yaratish Xizmati</b> 🌟✨\n\n"
        "🔒 <i>Hurmatli foydalanuvchi, ushbu professional rasm va vizual generatsiya qilish imkoniyati faqat **Gemini Pro (18 oylik) / Premium** obunachilari uchun amal qiladi!</i> 💎\n\n"
        "✨ Cheksiz imkoniyatlarga ega bo'lish uchun hoziroq obuna bo'ling:\n"
        "🔥 **Atigi 49,999 so'mga** Premium statusni qo'lga kiriting va barcha eksklyuziv funksiyalarni oching! 🚀\n\n"
        "👇 Obuna bo'lish yoki xarid qilish uchun **✨ GEMINI PRO (18 OYLIK)** yoki **💎 TELEGRAM PREMIUM** bo'limini tanlang! 🙏😊"
    )
    await send_long(message, text, get_kb(uid))

# ================= FEEDBACK & GUIDES =================

@dp.message(F.text == "✍️ FIKR BILDIRISH")
async def feedback_start(message: Message):
    uid = message.from_user.id
    st = user_state(uid, message)
    st["awaiting_feedback"] = True
    save_json(USERS_FILE, users)
    log_activity(uid, message.from_user.full_name, message.from_user.username, "FEEDBACK_START", "Fikr bildirish ✍️")
    await send_long(
        message,
        "✍️ <b>Biz uchun har bir fikr juda muhim!</b> ✨\n\n"
        "Taklif va mulohazalaringizni yozib yuboring. 🙏😊",
        get_kb(uid)
    )

@dp.message(F.text == "📚 QO'LLANMALAR")
async def guides_user_start(message: Message):
    uid = message.from_user.id
    st = user_state(uid, message)
    st["awaiting_guide_code"] = True
    save_json(USERS_FILE, users)
    log_activity(uid, message.from_user.full_name, message.from_user.username, "GUIDES_VIEW", "Qo'llanmalar 📚")
    
    guide_list = "\n".join([f"• Kod: <code>{code}</code> — <b>{data['title']}</b>" for code, data in guides.items()]) if guides else "Hozircha qo'llanmalar qo'shilmagan. 📖"
    
    text = (
        "📚 <b>EduMind AI — PDF Qo'llanmalar Kutubxonasi</b> ✨\n\n"
        f"{guide_list}\n\n"
        "👇 Kitob kodini yuboring:"
    )
    await send_long(message, text, get_kb(uid))

# ================= Language & Modes =================

@dp.message(F.text == "🌐 TIL")
async def lang_menu(message: Message):
    await message.answer("🌐 <b>Tilni tanlang / Select Language:</b> ✨", reply_markup=LANG_KB)

@dp.message(F.text.in_({"🇺🇿 O'zbekcha", "🇷🇺 Русский", "🇬🇧 English"}))
async def set_language(message: Message):
    uid = message.from_user.id
    st = user_state(uid, message)
    lang_map = {"🇺🇿 O'zbekcha": "uz", "🇷🇺 Русский": "ru", "🇬🇧 English": "en"}
    st["lang"] = lang_map[message.text]
    save_json(USERS_FILE, users)
    log_activity(uid, message.from_user.full_name, message.from_user.username, "LANG_CHANGE", f"Til: {st['lang']} 🌐")
    await send_long(message, "✅ Til o'zgartirildi! 🙏✨", get_kb(uid))

SUBJECT_BUTTONS = {
    "🧮 OLIY MATEMATIKA": "math", "💻 DASTURLASH": "programming", "⚛️ FIZIKA": "physics",
    "🧪 KIMYO": "chemistry", "🧬 BIOLOGIYA": "biology", "📐 GEOMETRIYA": "geometry",
    "📊 STATISTIKA": "statistics", "💼 IQTISOD": "economics", "📚 BARCHA FANLAR": "subjects",
}

@dp.message(F.text == "📚 FANLAR")
async def subjects(message: Message):
    uid = message.from_user.id
    st = user_state(uid, message)
    st["mode"] = "subjects"
    save_json(USERS_FILE, users)
    await message.answer("📚 <b>Fan yo'nalishini tanlang:</b> ✨", reply_markup=DOMAIN_KB)

@dp.message(F.text.in_(set(SUBJECT_BUTTONS)))
async def subject_select(message: Message):
    mode = SUBJECT_BUTTONS[message.text]
    uid = message.from_user.id
    st = user_state(uid, message)
    st["mode"] = mode
    save_json(USERS_FILE, users)
    log_activity(uid, message.from_user.full_name, message.from_user.username, "SUBJECT_SELECT", f"Fan: {message.text} 📚")
    await send_long(message, f"📚 <b>{message.text}</b> rejimi faollashdi! ✨\nSavolingizni yuboring 👇", get_kb(uid))

MODE_BUTTONS = {
    "🧠 AI TUTOR": ("ai_tutor", "✨ Gemini"), "📝 NEW TEST": ("new_test", "⚡ Groq"),
    "🎯 QUIZ": ("quiz", "⚡ Groq"), "🖼 RASM TAHLILI": ("image_analysis", "✨ Gemini"),
    "👨‍🏫 O‘QITUVCHI": ("teacher", "🔵 Claude"),
    "🎯 EXAM MODE": ("exam", "🧠 GPT"), "👤 PROFIL": ("profile", "✨ Gemini"),
    "🎓 UNIVERSITET": ("university", "🧠 GPT"),
}

@dp.message(F.text.in_(set(MODE_BUTTONS)))
async def domain_select(message: Message):
    mode, _ = MODE_BUTTONS[message.text]
    uid = message.from_user.id
    st = user_state(uid, message)
    st["mode"] = mode
    save_json(USERS_FILE, users)
    log_activity(uid, message.from_user.full_name, message.from_user.username, "MODE_SELECT", f"Rejim: {message.text} 🎯")
    await send_long(message, f"✅ <b>{message.text}</b> rejimi yoqildi. ✨\nSavolingizni yuboring.", get_kb(uid))

@dp.message(F.text == "🚀 START")
async def start_btn(message: Message): await start(message)

@dp.message(F.text == "ℹ️ YORDAM")
async def help_btn(message: Message): await help_cmd(message)

@dp.message(F.text == "👤 PROFIL")
async def profile_btn(message: Message):
    uid = message.from_user.id
    st = user_state(uid, message)
    log_activity(uid, message.from_user.full_name, message.from_user.username, "PROFILE", "Profil 👤")
    ans = (
        f"👤 <b>Sizning shaxsiy profilingiz</b> ✨\n\n"
        f"🆔 ID: <code>{uid}</code>\n"
        f"🏷 Ism: <b>{st.get('full_name', 'Foydalanuvchi')}</b>\n"
        f"🌐 Til: <b>{st.get('lang', 'uz').upper()}</b>\n"
        f"⭐ Status: [ 👤 <b>{st.get('status', 'Standart')}</b> ]\n"
        f"💬 Jami so'rovlar: <b>{st.get('queries_count', 0)} ta</b>\n"
        f"⭐ XP Ballar: <b>{st.get('points', 10)} XP</b> 🏆\n"
        f"🎁 Taklif qilingan do'stlar: <b>{st.get('referrals_count', 0)} ta</b>\n"
        f"🕒 Qo'shilgan vaqt: <b>{st.get('joined_at', 'Noma\'lum')}</b>\n\n"
        "<i>Faol bo'ling va eng yuqori natijalarga erishing! ✨🚀</i>"
    )
    await send_long(message, ans, get_kb(uid))

# ================= IELTS Menu =================

@dp.message(F.text == "🇬🇧 IELTS")
async def ielts_menu(message: Message):
    uid = message.from_user.id
    st = user_state(uid, message)
    st["mode"] = "ielts"
    save_json(USERS_FILE, users)
    log_activity(uid, message.from_user.full_name, message.from_user.username, "IELTS_MENU", "IELTS bo'limi 🇬🇧")
    await message.answer("🇬🇧 <b>IELTS Real Examiner Mode</b> ✨\nKerakli skillni tanlang:", reply_markup=IELTS_KB)

@dp.message(F.text.in_({"🗣 IELTS SPEAKING", "✍️ IELTS WRITING", "📖 IELTS READING", "🎧 IELTS LISTENING"}))
async def ielts_skill_handler(message: Message):
    uid = message.from_user.id
    st = user_state(uid, message)
    st["mode"] = "ielts"
    save_json(USERS_FILE, users)
    log_activity(uid, message.from_user.full_name, message.from_user.username, "IELTS_SKILL", f"IELTS: {message.text} 🎯")
    await send_long(message, f"✅ <b>{message.text}</b> rejimi faollashdi. Javobingizni yoki matningizni yuboring. 🎯✨", IELTS_KB)

# ================= ADMIN PANEL =================

@dp.message(F.text == "🔒 ADMINDAN CHIQISH")
async def admin_logout(message: Message):
    uid = message.from_user.id
    if is_admin(uid):
        st = user_state(uid, message)
        st["admin_auth"] = False
        save_json(USERS_FILE, users)
        await message.answer("🔒 Admin rejimidan chiqildi. ✨", reply_markup=get_kb(uid))

@dp.message(Command("admin"))
@dp.message(F.text == "👨‍💼 ADMIN PANEL")
async def admin_panel_entry(message: Message):
    uid = message.from_user.id
    if not is_admin(uid):
        await message.answer("⛔ Kechirasiz, bu bo'lim faqat bot administratori uchun mo'ljallangan. 🙏")
        return
    
    st = user_state(uid, message)
    if st.get("admin_auth", False):
        await message.answer("👨‍💼 <b>EduMind AI — ADMIN PANEL</b> ✨\nKerakli bo'limni tanlang:", reply_markup=ADMIN_KB)
    else:
        st["admin_action"] = "awaiting_pin"
        save_json(USERS_FILE, users)
        await message.answer("🔐 <b>Admin panelga kirish uchun maxfiy PIN kodni kiriting:</b> 🔑")

@dp.message(F.text == "📊 STATISTIKA")
async def admin_stats(message: Message):
    if not is_admin(message.from_user.id): return
    st = user_state(message.from_user.id, message)
    if not st.get("admin_auth"): return
    
    total = len(users)
    active = sum(1 for u in users.values() if u.get("queries_count", 0) > 0)
    blocked = sum(1 for u in users.values() if u.get("blocked", False))
    total_q = sum(u.get("queries_count", 0) for u in users.values())
    
    txt = (
        f"📊 <b>EDUMIND AI STATISTIKASI</b> ✨\n\n"
        f"👥 Jami foydalanuvchilar: <b>{total}</b>\n"
        f"🔥 Faol foydalanuvchilar: <b>{active}</b>\n"
        f"🚫 Bloklaganlar: <b>{blocked}</b>\n"
        f"💬 Jami so'rovlar: <b>{total_q} ta</b>\n"
        f"⚙️ Tizim holati: <b>Mukammal ishlamoqda ✅</b>"
    )
    await message.answer(txt, reply_markup=ADMIN_KB)

@dp.message(F.text == "👥 BARCHA FOYDALANUVCHILAR")
async def admin_active_users(message: Message):
    if not is_admin(message.from_user.id): return
    st = user_state(message.from_user.id, message)
    if not st.get("admin_auth"): return
    
    sorted_users = sorted(users.items(), key=lambda x: x[1].get("joined_at", ""), reverse=True)
    total_users = len(users)
    chunk_size = 15
    user_items = list(sorted_users)
    
    for i in range(0, total_users, chunk_size):
        chunk = user_items[i:i+chunk_size]
        txt = f"👥 <b>BARCHA FOYDALANUVCHILAR ({i+1}-{min(i+chunk_size, total_users)} / {total_users})</b> ✨\n\n"
        
        for idx, (uid_str, data) in enumerate(chunk, i+1):
            name = data.get("full_name") or f"ID: {uid_str}"
            safe_name = html.escape(name)
            uname = f"(@{data['username']})" if data.get("username") else "(@username_yo'q)"
            profile_link = f'<a href="tg://user?id={uid_str}">{safe_name}</a>'
            status = data.get("status", "Standart")
            pts = data.get("points", 10)
            q_count = data.get("queries_count", 0)
            joined = data.get("joined_at", "Noma'lum")
            refs = data.get("referrals_count", 0)
            
            txt += f"{idx}. 👤 {profile_link} {uname} [ 👤 {status} ]\n🆔 ID: <code>{uid_str}</code> | 🕒 {joined}\n⭐ XP: {pts} | 💬 So'rov: {q_count} | 🎁 Refs: {refs}\n--------------------\n"
        
        if i + chunk_size >= total_users:
            txt += f"\n{BRAND_FOOTER}"
            await message.answer(txt, parse_mode=ParseMode.HTML, reply_markup=ADMIN_KB)
        else:
            await message.answer(txt, parse_mode=ParseMode.HTML)
        
        await asyncio.sleep(0.05)

@dp.message(F.text == "🚫 BLOKLAGANLAR")
async def admin_blocked_users(message: Message):
    if not is_admin(message.from_user.id): return
    st = user_state(message.from_user.id, message)
    if not st.get("admin_auth"): return
    
    blocked_list = [(uid, data) for uid, data in users.items() if data.get("blocked", False)]
    txt = f"🚫 <b>BOTNI BLOKLAGANLAR RO'YXATI</b> ✨\n\nJami: <b>{len(blocked_list)}</b>\n\n"
    if not blocked_list:
        txt += "Bloklagan foydalanuvchilar yo'q. 🎉"
    else:
        for idx, (uid, data) in enumerate(blocked_list, 1):
            name = data.get("full_name") or f"ID: {uid}"
            safe_name = html.escape(name)
            profile_link = f'<a href="tg://user?id={uid}">{safe_name}</a>'
            uname = f"(@{data['username']})" if data.get("username") else ""
            txt += f"{idx}. {profile_link} {uname} | <code>{uid}</code>\n"
    await send_long(message, txt, ADMIN_KB)

@dp.message(F.text == "🕒 OXIRGI FAOLIYATLAR")
async def admin_recent_activities(message: Message):
    if not is_admin(message.from_user.id): return
    st = user_state(message.from_user.id, message)
    if not st.get("admin_auth"): return
    
    recent = activities[:30]
    txt = "📊 <b>OXIRGI FAOLIYATLAR LOGI</b> ✨\n\n"
    if not recent:
        txt += "Faoliyatlar mavjud emas."
    else:
        for act in recent:
            safe_act_name = html.escape(act['full_name'])
            profile_link = f'<a href="tg://user?id={act["uid"]}">{safe_act_name}</a>'
            txt += (
                f"🕒 <code>{act['time']}</code>\n"
                f"👤 {profile_link} ({act['username']}) (ID: <code>{act['uid']}</code>)\n"
                f"📌 Harakat: <b>{act['action']}</b>\n"
                f"💬 Tafsilot: <i>{act['desc']}</i>\n"
                f"--------------------\n"
            )
    await send_long(message, txt, ADMIN_KB)

@dp.message(F.text == "📥 XARID SO'ROVLARI")
async def admin_purchase_requests(message: Message):
    if not is_admin(message.from_user.id): return
    st = user_state(message.from_user.id, message)
    if not st.get("admin_auth"): return
    
    txt = "📥 <b>XARID SO'ROVLARI</b> 💳✨\n\n"
    if not purchase_requests:
        txt += "Xarid so'rovlari mavjud emas."
    else:
        for req in purchase_requests[:25]:
            safe_req_name = html.escape(req['full_name'])
            profile_link = f'<a href="tg://user?id={req["uid"]}">{safe_req_name}</a>'
            txt += (
                f"🕒 <code>{req['time']}</code>\n"
                f"👤 {profile_link} (@{req['username']}) (ID: <code>{req['uid']}</code>)\n"
                f"🛒 Xizmat: <b>{req['service']}</b>\n"
                f"📝 Tafsilot: <b>{req['details']}</b>\n"
                f"--------------------\n"
            )
    await send_long(message, txt, ADMIN_KB)

@dp.message(F.text == "🎁 REFERRAL TARIXI")
async def admin_referral_history(message: Message):
    if not is_admin(message.from_user.id): return
    st = user_state(message.from_user.id, message)
    if not st.get("admin_auth"): return
    
    txt = "🎁 <b>REFERRAL TARIXI (KIM KIMNI TAKLIF QILDI)</b> 👥✨\n\n"
    if not referrals_log:
        txt += "Hozircha referral orqali qo'shilganlar mavjud emas."
    else:
        for ref in referrals_log[:30]:
            inviter_name = html.escape(ref['inviter_name'])
            inviter_id = ref['inviter_id']
            inviter_link = f'<a href="tg://user?id={inviter_id}">{inviter_name}</a>'
            
            new_name = html.escape(ref['new_user_name'])
            new_id = ref['new_user_id']
            new_link = f'<a href="tg://user?id={new_id}">{new_name}</a>'
            
            txt += (
                f"🕒 <code>{ref['time']}</code>\n"
                f"👤 Kim taklif qildi: <b>{inviter_link}</b> (ID: <code>{inviter_id}</code>)\n"
                f"➕ Kim qo'shildi: <b>{new_link}</b> (ID: <code>{new_id}</code>)\n"
                f"--------------------\n"
            )
    await send_long(message, txt, ADMIN_KB)

@dp.message(F.text == "💬 FIKR & SHARHLAR")
async def admin_feedbacks_view(message: Message):
    if not is_admin(message.from_user.id): return
    st = user_state(message.from_user.id, message)
    if not st.get("admin_auth"): return
    
    txt = "💬 <b>FIKR VA SHARHLAR</b> ✨\n\n"
    if not feedbacks:
        txt += "Fikrlar mavjud emas."
    else:
        for fb in feedbacks[:25]:
            safe_fb_name = html.escape(fb['full_name'])
            profile_link = f'<a href="tg://user?id={fb["uid"]}">{safe_fb_name}</a>'
            txt += (
                f"🕒 <code>{fb['time']}</code>\n"
                f"👤 {profile_link} (@{fb['username']}) (ID: <code>{fb['uid']}</code>)\n"
                f"✍️ Fikr: <i>{fb['text']}</i>\n"
                f"--------------------\n"
            )
    await send_long(message, txt, ADMIN_KB)

@dp.message(F.text == "📚 PDF QO'LLANMA QO'SHISH")
async def admin_add_guide_start(message: Message):
    if not is_admin(message.from_user.id): return
    st = user_state(message.from_user.id, message)
    if not st.get("admin_auth"): return
    
    st["admin_action"] = "guide_code"
    save_json(USERS_FILE, users)
    await message.answer("📚 <b>Yangi PDF qo'llanma (1/3)</b>\n\nKitob kodini kiriting:")

@dp.message(F.text == "📢 BARCHAGA XABAR")
async def broadcast_start(message: Message):
    if not is_admin(message.from_user.id): return
    st = user_state(message.from_user.id, message)
    if not st.get("admin_auth"): return
    
    st["admin_action"] = "broadcast"
    save_json(USERS_FILE, users)
    await message.answer("📢 <b>Ommaviy xabar yuborish</b> ✨\nIstalgan kontentni yuboring: <b>Matn, Rasm, Video, Ovozli xabar, Audio, Stiker, GIF yoki Hujjat!</b>\n❌ Bekor qilish: <b>/cancel</b>", reply_markup=ADMIN_KB)

@dp.message(Command("cancel"))
async def cancel_cmd(message: Message):
    uid = message.from_user.id
    st = user_state(uid, message)
    st["admin_action"] = None
    st["awaiting_purchase"] = None
    st["awaiting_feedback"] = False
    st["awaiting_guide_code"] = False
    save_json(USERS_FILE, users)
    await message.answer("❌ Amaliyot bekor qilindi. ✨", reply_markup=get_kb(uid))

async def do_broadcast(message: Message) -> bool:
    uid = message.from_user.id
    if not is_admin(uid): return False
    st = user_state(uid, message)
    if st.get("admin_action") != "broadcast": return False
    if message.text in {"📢 BARCHAGA XABAR", "📊 STATISTIKA", "⬅️ MENU", "👨‍💼 ADMIN PANEL", "👥 BARCHA FOYdALANUVCHILAR", "🚫 BLOKLAGANLAR", "🕒 OXIRGI FAOLIYATLAR", "📥 XARID SO'ROVLARI", "🎁 REFERRAL TARIXI", "💬 FIKR & SHARHLAR", "📚 PDF QO'LLANMA QO'SHISH", "🔒 ADMINDAN CHIQISH"}:
        return False

    st["admin_action"] = None
    save_json(USERS_FILE, users)
    targets = list(users.keys())
    sent = failed = blocked_count = 0

    await message.answer("🚀 Ommaviy xabar yuborilmoqda, kuting...", reply_markup=ADMIN_KB)

    for target in targets:
        try:
            tid = int(target)
            if message.text:
                await bot.send_message(tid, message.text)
            elif message.photo:
                await bot.send_photo(tid, message.photo[-1].file_id, caption=message.caption or None)
            elif message.video:
                await bot.send_video(tid, message.video.file_id, caption=message.caption or None)
            elif message.audio:
                await bot.send_audio(tid, message.audio.file_id, caption=message.caption or None)
            elif message.voice:
                await bot.send_voice(tid, message.voice.file_id, caption=message.caption or None)
            elif message.animation:
                await bot.send_animation(tid, message.animation.file_id, caption=message.caption or None)
            elif message.sticker:
                await bot.send_sticker(tid, message.sticker.file_id)
            elif message.document:
                await bot.send_document(tid, message.document.file_id, caption=message.caption or None)
            
            sent += 1
            await asyncio.sleep(0.03)
        except Exception as e:
            failed += 1
            err_str = str(e).lower()
            if "blocked" in err_str or "deactivated" in err_str or "chat not found" in err_str:
                users[target]["blocked"] = True
                blocked_count += 1
    save_json(USERS_FILE, users)
    
    log_activity(uid, message.from_user.full_name, message.from_user.username, "BROADCAST", f"Jami: {len(targets)}, Yetkazildi: {sent}, Bloklaganlar: {blocked_count}")
    await message.answer(f"📢 <b>Ommaviy xabar yakunlandi!</b> ✨\n\n👥 Jami: {len(targets)}\n✅ Yetkazildi: {sent}\n🚫 Bloklaganlar: {blocked_count}", reply_markup=ADMIN_KB)
    return True

# ================= Media & Content Handlers =================

@dp.message(F.photo)
async def photo_handler(message: Message):
    if await do_broadcast(message): return
    uid = message.from_user.id
    caption = message.caption or ""
    log_activity(uid, message.from_user.full_name, message.from_user.username, "AI_VISION", "Rasm tahlil oldi 🖼")
    try:
        await message.answer("🖼 Rasm qabul qilindi. EduMind Vision AI tahlil qilmoqda... ✨")
        file = await bot.get_file(message.photo[-1].file_id)
        buf = io.BytesIO()
        await bot.download_file(file.file_path, destination=buf)
        
        system = "You are EduMind AI Vision Tutor. Analyze educational images precisely using the 5-step format with utmost politeness."
        def run():
            return gemini_client.models.generate_content(
                model=GEMINI_VISION_MODEL,
                contents=[types.Part.from_bytes(data=buf.getvalue(), mime_type="image/jpeg"), caption or "Rasmni tahlil qiling."],
                config=types.GenerateContentConfig(system_instruction=system, temperature=0.2)
            )
        res = await asyncio.to_thread(run)
        answer = res.text or "Kechirasiz, rasmni tahlil qilib bo'lmadi."
        remember(uid, "[RASM TAHLILI]", answer)
        await send_long(message, answer, get_kb(uid))
    except Exception as e:
        await send_long(message, f"Rasm tahlilida xatolik yuz berdi: {str(e)[:200]}", get_kb(uid))

@dp.message(F.video)
async def video_handler(message: Message):
    if await do_broadcast(message): return
    uid = message.from_user.id
    log_activity(uid, message.from_user.full_name, message.from_user.username, "VIDEO_INPUT", "Video yubordi 🎥")
    await send_long(message, "🎥 <b>Video qabul qilindi!</b> EduMind AI multimediya tizimi orqali to'liq video tahlil ishga tushmoqda. Savolingizni matn yoki rasm ko'rinishida ham yuboring! 🙏✨", get_kb(uid))

@dp.message(F.document)
async def document_handler(message: Message):
    if await do_broadcast(message): return
    uid = message.from_user.id
    st = user_state(uid, message)
    
    if is_admin(uid) and st.get("admin_action") == "guide_file":
        if not message.document.file_name.lower().endswith(".pdf"):
            await message.answer("❌ Iltimos, faqat PDF formatidagi hujjat yuboring.")
            return
        
        file_id = message.document.file_id
        code = st.get("temp_guide_code")
        title = st.get("temp_guide_title")
        
        guides[code] = {"title": title, "file_id": file_id}
        save_json(GUIDES_FILE, guides)
        
        st["admin_action"] = None
        st.pop("temp_guide_code", None)
        st.pop("temp_guide_title", None)
        save_json(USERS_FILE, users)
        
        await message.answer(f"✅ <b>PDF qo'llanma qo'shildi!</b> 🎉\nKitob: {title}\nKod: <code>{code}</code>", reply_markup=ADMIN_KB)
        return

    if not message.document.file_name.lower().endswith(".pdf"):
        await send_long(message, "📄 Hozircha faqat PDF formatidagi hujjatlar qabul qilinadi. 🙏✨", get_kb(uid))
        return
        
    log_activity(uid, message.from_user.full_name, message.from_user.username, "PDF_ANALYSIS", f"PDF yukladi: {message.document.file_name} 📄")
    try:
        from pypdf import PdfReader
        file = await bot.get_file(message.document.file_id)
        buf = io.BytesIO()
        await bot.download_file(file.file_path, destination=buf)
        reader = PdfReader(buf)
        text_content = "".join([p.extract_text() or "" for p in reader.pages[:20]])
        
        answer, _ = await ask_ai(uid, f"PDF fayl mazmuni bo'yicha tahlil bering:\n{text_content[:30000]}")
        await send_long(message, answer, get_kb(uid))
    except Exception as e:
        await send_long(message, f"PDF tahlilida xatolik yuz berdi: {str(e)[:200]}", get_kb(uid))

@dp.message(F.text)
async def text_handler(message: Message):
    if await do_broadcast(message): return
    uid = message.from_user.id
    text = message.text.strip()
    st = user_state(uid, message)
    if not text: return

    if is_admin(uid) and st.get("admin_action") == "awaiting_pin":
        if text == "1011  ":
            st["admin_auth"] = True
            st["admin_action"] = None
            save_json(USERS_FILE, users)
            await message.answer("🔓 <b>PIN kod tasdiqlandi! Admin panelga xush kelibsiz.</b> ✨", reply_markup=ADMIN_KB)
        else:
            await message.answer("❌ <b>Noto'g'ri PIN kod!</b> Qaytadan kiriting:")
        return

    if is_admin(uid) and st.get("admin_action") == "guide_code":
        st["temp_guide_code"] = text.lower()
        st["admin_action"] = "guide_title"
        save_json(USERS_FILE, users)
        await message.answer("📚 <b>Yangi PDF qo'llanma (2/3)</b>\n\nKitob nomini kiriting:")
        return

    if is_admin(uid) and st.get("admin_action") == "guide_title":
        st["temp_guide_title"] = text
        st["admin_action"] = "guide_file"
        save_json(USERS_FILE, users)
        await message.answer("📚 <b>Yangi PDF qo'llanma (3/3)</b>\n\nKitobning <b>PDF faylini</b> yuboring:")
        return

    if st.get("awaiting_purchase"):
        service_type = st["awaiting_purchase"]
        st["awaiting_purchase"] = None
        save_json(USERS_FILE, users)
        
        service_names = {
            "stars": "Telegram Stars ⭐",
            "premium": "Telegram Premium 💎",
            "gemini_pro": "Gemini Pro (18 oylik) ✨"
        }
        serv_title = service_names.get(service_type, "Xarid")
        
        req_entry = {
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "uid": uid,
            "full_name": message.from_user.full_name or f"ID: {uid}",
            "username": message.from_user.username or "yo'q",
            "service": serv_title,
            "details": text
        }
        purchase_requests.insert(0, req_entry)
        save_json(REQUESTS_FILE, purchase_requests[-100:])
        
        log_activity(uid, message.from_user.full_name, message.from_user.username, "PURCHASE_REQ", f"{serv_title}: {text}")
        
        for adm_id in ADMIN_IDS:
            try:
                await bot.send_message(
                    adm_id,
                    f"🚨 <b>YANGI XARID SO'ROVI!</b> 🛒\n\n"
                    f"👤 Kimdan: <a href='tg://user?id={uid}'>{html.escape(message.from_user.full_name or 'Foydalanuvchi')}</a> (ID: <code>{uid}</code>)\n"
                    f"📦 Xizmat: <b>{serv_title}</b>\n"
                    f"📝 Tafsilot: <i>{text}</i>"
                )
            except Exception:
                pass
        
        card_info = (
            "💳 <b>Karta raqami:</b> <code>5440810319005806</code>\n"
            "👤 <b>Karta egasi:</b> Z / E\n"
            "🏦 <b>Bank:</b> UzCard / Humo"
        )
        
        reply_text = (
            f"✅ <b>So'rovingiz qabul qilindi!</b> 🎉\n\n"
            f"Xizmat: <b>{serv_title}</b>\n"
            f"Tafsilot: <i>{text}</i>\n\n"
            f"{card_info}\n\n"
            "📌 To'lovni amalga oshirgach, chekni yaratuvchimizga yuboring: 👉 @elbek_ziyodullayev"
        )
        await send_long(message, reply_text, get_kb(uid))
        return

    if st.get("awaiting_feedback"):
        st["awaiting_feedback"] = False
        save_json(USERS_FILE, users)
        
        fb_entry = {
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "uid": uid,
            "full_name": message.from_user.full_name or f"ID: {uid}",
            "username": message.from_user.username or "yo'q",
            "text": text
        }
        feedbacks.insert(0, fb_entry)
        save_json(FEEDBACK_FILE, feedbacks[-100:])
        
        log_activity(uid, message.from_user.full_name, message.from_user.username, "FEEDBACK", f"Fikr: {text}")
        
        for adm_id in ADMIN_IDS:
            try:
                await bot.send_message(
                    adm_id,
                    f"💬 <b>YANGI FIKR / SHARH!</b> ✨\n\n"
                    f"👤 Kimdan: <a href='tg://user?id={uid}'>{html.escape(message.from_user.full_name or 'Foydalanuvchi')}</a> (ID: <code>{uid}</code>)\n"
                    f"✍️ Matn: <i>{text}</i>"
                )
            except Exception:
                pass
        
        await send_long(message, "✨ <b>Samimiy fikringiz uchun rahmat!</b> 💖", get_kb(uid))
        return

    if st.get("awaiting_guide_code"):
        st["awaiting_guide_code"] = False
        save_json(USERS_FILE, users)
        
        code_input = text.lower()
        if code_input in guides:
            g_data = guides[code_input]
            await message.answer_document(
                document=g_data["file_id"],
                caption=f"📚 <b>{g_data['title']}</b>\n\n✨ EduMind AI kutubxonasidan foydalanganingiz uchun rahmat!",
                reply_markup=get_kb(uid)
            )
            log_activity(uid, message.from_user.full_name, message.from_user.username, "READ_GUIDE", f"Kitob: {g_data['title']}")
        else:
            await send_long(message, "❌ <b>Bunday kodli qo'llanma topilmadi!</b>", get_kb(uid))
        return

    log_activity(uid, message.from_user.full_name, message.from_user.username, f"AI_QUERY ({st.get('mode', 'ai_tutor').upper()})", text[:80])

    try:
        answer, _ = await ask_ai(uid, text, mode=st.get("mode"))
        await send_long(message, answer, get_kb(uid))
    except Exception as e:
        await send_long(message, f"⚠️ Kechirasiz, xatolik yuz berdi: {str(e)[:300]} 🙏✨", get_kb(uid))

async def main():
    print("EduMind AI v6.2 Super-App starting... 🚀")
    me = await bot.get_me()
    print(f"Bot: @{me.username} id={me.id}")
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("EduMind AI stopped. 👋")
