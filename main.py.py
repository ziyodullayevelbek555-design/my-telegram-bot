import os
import io
import json
import base64
import asyncio
import html
import logging
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

from openai import AsyncOpenAI
from anthropic import AsyncAnthropic
from google import genai
from google.genai import types

load_dotenv()

# =========================================================
# EduMind AI — fast multi-AI educational engine
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
DEFAULT_PROVIDER = os.getenv("AI_DEFAULT_PROVIDER", "gemini").lower()
AUTO_FALLBACK = os.getenv("AI_AUTO_FALLBACK", "true").lower() == "true"
ADMIN_IDS = {int(x.strip()) for x in os.getenv("ADMIN_IDS", "6841567673").split(",") if x.strip().isdigit()}

MODELS = {
    "gemini": os.getenv("GEMINI_MODEL", "gemini-3.7-flash"),
    "deepseek": os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash"),
    "groq": os.getenv("GROQ_MODEL", "openai/gpt-oss-120b"),
    "openai": os.getenv("OPENAI_MODEL", "gpt-5.6-luna"),
    "claude": os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6"),
}

GEMINI_VISION_MODEL = os.getenv("GEMINI_VISION_MODEL", MODELS["gemini"])
GEMINI_IMAGE_MODEL = os.getenv("GEMINI_IMAGE_MODEL", "gemini-3.1-flash-image")

KEYS = {
    "gemini": os.getenv("GEMINI_API_KEY"),
    "deepseek": os.getenv("DEEPSEEK_API_KEY"),
    "groq": os.getenv("GROQ_API_KEY"),
    "openai": os.getenv("OPENAI_API_KEY"),
    "claude": os.getenv("ANTHROPIC_API_KEY") or os.getenv("CLAUDE_API_KEY"),
}

# DeepSeek is kept available for manual selection, but it is NOT in the
# automatic chain because an empty balance causes a needless 402 delay.
FAST_FALLBACK = ["gemini", "groq", "openai", "claude", "deepseek"]
PROVIDER_TIMEOUT = float(os.getenv("AI_PROVIDER_TIMEOUT", "22"))

BRAND_FOOTER = "<i>crafted by @elbek_ziyodullayev</i>"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger("edumind")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN .env faylida topilmadi")

# ------------------------- clients -------------------------

gemini_client = genai.Client(api_key=KEYS["gemini"]) if KEYS["gemini"] else None

openai_client = AsyncOpenAI(api_key=KEYS["openai"]) if KEYS["openai"] else None
deepseek_client = (
    AsyncOpenAI(api_key=KEYS["deepseek"], base_url="https://api.deepseek.com")
    if KEYS["deepseek"] else None
)
groq_client = (
    AsyncOpenAI(api_key=KEYS["groq"], base_url="https://api.groq.com/openai/v1")
    if KEYS["groq"] else None
)
claude_client = AsyncAnthropic(api_key=KEYS["claude"]) if KEYS["claude"] else None

PROVIDER_LABELS = {
    "gemini": "✨ Gemini",
    "deepseek": "🚀 DeepSeek",
    "groq": "⚡ Groq",
    "openai": "🧠 GPT",
    "claude": "🔵 Claude",
}

# ------------------------- persistent state -------------------------

DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)
USERS_FILE = DATA_DIR / "users.json"
IELTS_PDFS_FILE = DATA_DIR / "ielts_pdfs.json"
try:
    ielts_pdfs = json.loads(IELTS_PDFS_FILE.read_text(encoding="utf-8")) if IELTS_PDFS_FILE.exists() else {}
except Exception:
    ielts_pdfs = {}
def save_ielts_pdfs():
    IELTS_PDFS_FILE.write_text(json.dumps(ielts_pdfs, ensure_ascii=False, indent=2), encoding="utf-8")
PDF_CATEGORIES = {"📖 READING PDF":"reading","🎧 LISTENING PDF":"listening","✍️ WRITING PDF":"writing","🗣 SPEAKING PDF":"speaking","📚 VOCABULARY PDF":"vocabulary","📐 GRAMMAR PDF":"grammar","📕 CAMBRIDGE IELTS":"cambridge","📂 BOSHQA IELTS PDF":"other"}

try:
    users = json.loads(USERS_FILE.read_text(encoding="utf-8")) if USERS_FILE.exists() else {}
except Exception:
    users = {}

def save_users():
    USERS_FILE.write_text(
        json.dumps(users, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

def save_telegram_profile(tg_user):
    """Save only the public profile data Telegram sends to the bot."""
    if not tg_user:
        return
    key = str(tg_user.id)
    st = user_state(tg_user.id) if key in users else None
    if st is None:
        users[key] = {
            "history": [],
            "provider": DEFAULT_PROVIDER,
            "mode": "ai_tutor",
            "created": True,
        }
        st = users[key]
    st["first_name"] = tg_user.first_name or ""
    st["last_name"] = tg_user.last_name or ""
    st["username"] = tg_user.username or ""
    save_users()

def user_state(uid: int):
    key = str(uid)
    if key not in users:
        users[key] = {
            "history": [],
            "provider": DEFAULT_PROVIDER,
            "mode": "ai_tutor",
            "created": True,
        }
    return users[key]

# ------------------------- UI -------------------------

MAIN_KB = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🚀 START"), KeyboardButton(text="🧠 AI TUTOR")],
        [KeyboardButton(text="📚 FANLAR"), KeyboardButton(text="📝 NEW TEST")],
        [KeyboardButton(text="🎯 QUIZ"), KeyboardButton(text="🖼 RASM TAHLILI")],
        [KeyboardButton(text="🎨 RASM YARATISH"), KeyboardButton(text="👨‍🏫 O‘QITUVCHI")],
        [KeyboardButton(text="🇬🇧 IELTS"), KeyboardButton(text="🎯 EXAM MODE")],
        [KeyboardButton(text="👤 PROFIL"), KeyboardButton(text="🚀 LOYIHA")],
        [KeyboardButton(text="🎓 UNIVERSITET"), KeyboardButton(text="🌐 TIL")],
        [KeyboardButton(text="🤖 AI TANLASH"), KeyboardButton(text="🔄 YANGI SUHBAT")],
        [KeyboardButton(text="ℹ️ YORDAM"), KeyboardButton(text="👨‍💻 BOT YARATUVCHISI")],
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
        [KeyboardButton(text="📚 IELTS KUTUBXONASI")],
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
        [KeyboardButton(text="📢 BARCHAGA XABAR"), KeyboardButton(text="📊 STATISTIKA")],
        [KeyboardButton(text="👥 FOYDALANUVCHILAR")],
        [KeyboardButton(text="📚 IELTS PDF BOSHQARISH")],
        [KeyboardButton(text="⬅️ MENU")],
    ],
    resize_keyboard=True,
)

ADMIN_MAIN_KB = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🚀 START"), KeyboardButton(text="🧠 AI TUTOR")],
        [KeyboardButton(text="📚 FANLAR"), KeyboardButton(text="📝 NEW TEST")],
        [KeyboardButton(text="🎯 QUIZ"), KeyboardButton(text="🖼 RASM TAHLILI")],
        [KeyboardButton(text="🎨 RASM YARATISH"), KeyboardButton(text="👨‍🏫 O‘QITUVCHI")],
        [KeyboardButton(text="🇬🇧 IELTS"), KeyboardButton(text="🎯 EXAM MODE")],
        [KeyboardButton(text="👤 PROFIL"), KeyboardButton(text="🚀 LOYIHA")],
        [KeyboardButton(text="🎓 UNIVERSITET"), KeyboardButton(text="🌐 TIL")],
        [KeyboardButton(text="🤖 AI TANLASH"), KeyboardButton(text="🔄 YANGI SUHBAT")],
        [KeyboardButton(text="ℹ️ YORDAM")],
        [KeyboardButton(text="👨‍💼 ADMIN PANEL")],
    ],
    resize_keyboard=True,
)

IELTS_LIBRARY_KB = ReplyKeyboardMarkup(keyboard=[
    [KeyboardButton(text="📖 READING PDF"), KeyboardButton(text="🎧 LISTENING PDF")],
    [KeyboardButton(text="✍️ WRITING PDF"), KeyboardButton(text="🗣 SPEAKING PDF")],
    [KeyboardButton(text="📚 VOCABULARY PDF"), KeyboardButton(text="📐 GRAMMAR PDF")],
    [KeyboardButton(text="📕 CAMBRIDGE IELTS"), KeyboardButton(text="📂 BOSHQA IELTS PDF")],
    [KeyboardButton(text="⬅️ IELTS MENU")]], resize_keyboard=True)
PDF_ADMIN_KB = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="➕ PDF QO'SHISH")],[KeyboardButton(text="📋 PDF RO'YXATI"), KeyboardButton(text="🗑 PDF O'CHIRISH")],[KeyboardButton(text="⬅️ ADMIN")]], resize_keyboard=True)

def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS

async def send_admin_menu(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Bu bo‘lim faqat bot administratori uchun.")
        return
    await message.answer(
        "👨‍💼 <b>EduMind AI — ADMIN PANEL</b>\n\n"
        "📢 Barcha foydalanuvchilarga xabar yuborish\n"
        "📊 Foydalanuvchilar statistikasini ko‘rish\n"
        "👥 Foydalanuvchilar ro‘yxati va profillari",
        reply_markup=ADMIN_KB,
    )

# ------------------------- domain architecture -------------------------

DOMAIN = {
    "ai_tutor": {
        "provider": "gemini",
        "system": "Universal AI tutor. Explain accurately, clearly, step-by-step, with examples.",
    },
    "subjects": {
        "provider": "gemini",
        "system": "Expert school/university subject tutor. Cover any subject and adapt to level.",
    },
    "math": {
        "provider": "gemini",
        "system": "Expert mathematician. For problems show formulas, derivation, calculation and final verification.",
    },
    "physics": {
        "provider": "gemini",
        "system": "Expert physics tutor. Use laws, units, formulas, derivations and physical interpretation.",
    },
    "chemistry": {
        "provider": "gemini",
        "system": "Expert chemistry tutor. Balance equations, explain mechanisms, calculations and safety.",
    },
    "biology": {
        "provider": "gemini",
        "system": "Expert biology tutor. Explain processes, terminology and cause-effect relationships.",
    },
    "geometry": {
        "provider": "gemini",
        "system": "Expert geometry tutor. Give construction logic, theorem, proof and calculation.",
    },
    "statistics": {
        "provider": "openai",
        "system": "Expert statistics/data-analysis tutor. Show assumptions, formulas, calculations and interpretation.",
    },
    "economics": {
        "provider": "openai",
        "system": "Expert economics tutor. Explain models, assumptions, examples and implications.",
    },
    "programming": {
        "provider": "groq",
        "system": "Senior software engineer and programming tutor. Give correct runnable code and explain it simply.",
    },
    "new_test": {
        "provider": "groq",
        "system": "Assessment generator. Create high-quality tests with answer keys and explanations.",
    },
    "quiz": {
        "provider": "groq",
        "system": "Fast quiz master. Ask one question at a time, evaluate the answer, explain mistakes.",
    },
    "image_analysis": {
        "provider": "gemini",
        "system": "Vision tutor. Analyze diagrams, equations, pages, charts and educational photos precisely.",
    },
    "image_generate": {
        "provider": "gemini",
        "system": "Educational creative designer. Create useful diagrams, illustrations and study visuals.",
    },
    "teacher": {
        "provider": "claude",
        "system": "Experienced teacher and curriculum designer. Build lessons, rubrics, activities and feedback.",
    },
    "ielts": {
        "provider": "claude",
        "system": "Strict IELTS examiner. Simulate real test conditions and provide criterion-based feedback.",
    },
    "exam": {
        "provider": "openai",
        "system": "Exam preparation specialist. Produce realistic tasks, timing, scoring and explanations.",
    },
    "profile": {
        "provider": "gemini",
        "system": "Learning coach. Analyze study patterns and create a practical improvement plan.",
    },
    "project": {
        "provider": "openai",
        "system": "Academic project supervisor. Help plan research, structure, methodology, implementation and presentation.",
    },
    "university": {
        "provider": "openai",
        "system": "University-level academic advisor. Cover higher mathematics, science, CS, economics and research.",
    },
    "language": {
        "provider": "gemini",
        "system": "Multilingual language tutor. Correct grammar, vocabulary, style and natural usage.",
    },
    "pdf": {
        "provider": "gemini",
        "system": "Document/PDF analyst. Extract key information, explain, summarize, answer questions and cite page numbers when available.",
    },
}

# ------------------------- helper functions -------------------------

def footer(text: str) -> str:
    if BRAND_FOOTER in text:
        return text
    return f"{text}\n\n{BRAND_FOOTER}"

def clean(text: str) -> str:
    return text.strip() if text else "Javob tayyorlashda xatolik yuz berdi."

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

def active_provider(uid: int, requested: Optional[str] = None):
    st = user_state(uid)
    if requested:
        return requested
    mode = st.get("mode", "ai_tutor")
    return DOMAIN.get(mode, DOMAIN["ai_tutor"])["provider"]

def provider_is_configured(name: str) -> bool:
    return bool(KEYS.get(name))

def make_messages(uid: int, prompt: str, system: str):
    st = user_state(uid)
    history = st.get("history", [])[-8:]
    msgs = [{"role": "system", "content": system}]
    msgs.extend(history)
    msgs.append({"role": "user", "content": prompt})
    return msgs

def remember(uid: int, user_text: str, answer: str):
    st = user_state(uid)
    st.setdefault("history", []).append({"role": "user", "content": user_text})
    st["history"].append({"role": "assistant", "content": answer})
    st["history"] = st["history"][-16:]
    save_users()

def is_balance_error(exc: Exception) -> bool:
    s = str(exc).lower()
    return "402" in s or "insufficient balance" in s or "insufficient_balance" in s

async def call_openai_compatible(name: str, messages):
    if name == "openai":
        client = openai_client
    elif name == "deepseek":
        client = deepseek_client
    elif name == "groq":
        client = groq_client
    else:
        raise ValueError(name)
    if not client:
        raise RuntimeError(f"{name} API key sozlanmagan")
    response = await client.chat.completions.create(
        model=MODELS[name],
        messages=messages,
        temperature=0.35,
        max_tokens=3500,
    )
    return response.choices[0].message.content or ""

async def call_claude(messages):
    if not claude_client:
        raise RuntimeError("Claude API key sozlanmagan")
    system = messages[0]["content"]
    resp = await claude_client.messages.create(
        model=MODELS["claude"],
        system=system,
        messages=messages[1:],
        max_tokens=3500,
        temperature=0.35,
    )
    return "".join(getattr(x, "text", "") for x in resp.content)

async def call_gemini(messages, model=None):
    if not gemini_client:
        raise RuntimeError("Gemini API key sozlanmagan")
    model = model or MODELS["gemini"]
    system = messages[0]["content"]
    contents = []
    for m in messages[1:]:
        role = "user" if m["role"] == "user" else "model"
        contents.append(types.Content(
            role=role,
            parts=[types.Part.from_text(text=m["content"])]
        ))
    def run():
        return gemini_client.models.generate_content(
            model=model,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=system,
                temperature=0.35,
                max_output_tokens=3500,
            ),
        )
    result = await asyncio.to_thread(run)
    return result.text or ""

async def call_provider(name: str, messages):
    if name == "gemini":
        return await call_gemini(messages)
    if name in ("openai", "deepseek", "groq"):
        return await call_openai_compatible(name, messages)
    if name == "claude":
        return await call_claude(messages)
    raise RuntimeError(f"Noma'lum provider: {name}")

async def ask_ai(uid: int, prompt: str, mode: Optional[str] = None, force_provider: Optional[str] = None):
    st = user_state(uid)
    mode = mode or st.get("mode", "ai_tutor")
    cfg = DOMAIN.get(mode, DOMAIN["ai_tutor"])
    selected = force_provider or cfg["provider"]

    # Fast automatic chain: don't waste time on a known empty-balance DeepSeek key.
    candidates = [selected]
    if AUTO_FALLBACK:
        for p in FAST_FALLBACK:
            if p != selected and p not in candidates:
                candidates.append(p)

    system = cfg["system"] + """
You are inside EduMind AI.
Rules:
- Answer in the user's language.
- Accuracy is more important than sounding confident.
- If a question is ambiguous, state the assumption briefly.
- For calculations, verify the result.
- For academic work, structure the answer with headings and examples.
- Never invent a source, citation, score or factual detail.
- If you cannot reliably answer, say what is missing and give the safest useful next step.
- Make answers warm and engaging: use a few relevant emojis (about 2-6) in headings, key points, or conclusions. Do not spam emojis and never replace important words with emojis.
- When appropriate, use friendly encouragement such as “Ajoyib!”, “Keling, bosqichma-bosqich ko‘ramiz 👇” or “Xulosa ✅”.
"""

    messages = make_messages(uid, prompt, system)

    errors = []
    for name in candidates:
        if not provider_is_configured(name):
            continue
        try:
            started = asyncio.get_running_loop().time()
            answer = await asyncio.wait_for(
                call_provider(name, messages),
                timeout=PROVIDER_TIMEOUT
            )
            elapsed = asyncio.get_running_loop().time() - started
            answer = clean(answer)
            if answer:
                st["last_provider"] = name
                st["last_latency"] = round(elapsed, 2)
                remember(uid, prompt, answer)
                log.info("AI=%s mode=%s %.2fs", name, mode, elapsed)
                return answer, name
        except asyncio.TimeoutError:
            errors.append(f"{name}: timeout")
            log.warning("Timeout provider=%s mode=%s", name, mode)
        except Exception as e:
            errors.append(f"{name}: {str(e)[:160]}")
            log.warning("Provider %s failed: %s", name, e)
            # A 402 balance error is not retried in this request.
            if is_balance_error(e):
                continue

    raise RuntimeError(" | ".join(errors) if errors else "Hech bir AI provider sozlanmagan.")

# ------------------------- specialized prompts -------------------------

IELTS_PROMPTS = {
    "speaking": """You are an IELTS Speaking examiner.
Run a realistic mock. Evaluate the candidate using exactly 4 criteria:
1) Fluency & Coherence
2) Lexical Resource
3) Grammatical Range & Accuracy
4) Pronunciation
For text-only answers, clearly state that pronunciation cannot be fully measured from text and do not fabricate it.
Give estimated band 0-9 per criterion, overall estimate, strengths, exact mistakes, improved examples, and an actionable plan.
Ask one realistic examiner question at a time when conducting practice.""",

    "writing": """You are an IELTS Writing examiner.
Evaluate using exactly 4 criteria:
1) Task Response / Task Achievement
2) Coherence & Cohesion
3) Lexical Resource
4) Grammatical Range & Accuracy
Give band 0-9 for each, estimated overall, evidence from the response, corrections, better alternatives, explanation of why, and a model improvement plan.
Do not inflate scores.""",

    "reading": """You are an IELTS Reading examiner/tutor.
Evaluate exactly 4 dimensions:
1) Answer Accuracy
2) Reading Comprehension
3) Vocabulary-in-Context
4) Question Strategy
Give correct answers, explanations and score conversion when enough questions are provided.
If no passage/questions are supplied, start a realistic timed-style reading mini-test.""",

    "listening": """You are an IELTS Listening examiner/tutor.
Evaluate exactly 4 dimensions:
1) Answer Accuracy
2) Detail Recognition
3) Context Comprehension
4) Listening Strategy
Do not pretend to hear audio that was not provided. If audio is unavailable, offer a transcript-based simulation and label it clearly.
Give explanations and score conversion when enough questions are provided.""",
}

async def ielts_response(uid: int, prompt: str, skill: str):
    system = IELTS_PROMPTS[skill]
    st = user_state(uid)
    st["mode"] = "ielts"
    st["ielts_skill"] = skill
    save_users()

    # IELTS deliberately uses Claude first, with the same safety fallback.
    messages = make_messages(uid, prompt, system)
    candidates = ["claude", "gemini", "openai", "groq"]
    for name in candidates:
        if not provider_is_configured(name):
            continue
        try:
            answer = await asyncio.wait_for(call_provider(name, messages), timeout=PROVIDER_TIMEOUT)
            if answer:
                remember(uid, prompt, answer)
                return clean(answer), name
        except Exception as e:
            log.warning("IELTS %s failed on %s: %s", skill, name, e)
    raise RuntimeError("IELTS examiner vaqtincha javob bera olmadi.")

# ------------------------- vision -------------------------

async def analyze_photo(uid: int, prompt: str, image_bytes: bytes):
    if not gemini_client:
        raise RuntimeError("Gemini API key sozlanmagan.")
    system = """You are EduMind AI Vision Tutor.
Analyze the uploaded educational image carefully: text, equations, diagrams, charts, tables, homework or documents.
If text is unreadable, say exactly what is unreadable.
Solve/explain step-by-step. Do not guess visual facts."""
    def run():
        return gemini_client.models.generate_content(
            model=GEMINI_VISION_MODEL,
            contents=[
                types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg"),
                prompt or "Rasmni to'liq tahlil qiling va o'quvchiga tushunarli qilib tushuntiring."
            ],
            config=types.GenerateContentConfig(
                system_instruction=system,
                temperature=0.2,
                max_output_tokens=4000,
            ),
        )
    result = await asyncio.to_thread(run)
    answer = result.text or "Rasmni tahlil qilishning iloji bo'lmadi."
    remember(uid, "[RASM] " + (prompt or "tahlil"), answer)
    return answer

# ------------------------- PDF -------------------------

async def analyze_pdf(uid: int, filename: str, pdf_bytes: bytes, prompt: str):
    # pypdf is optional. If installed, extract text locally and send relevant text to AI.
    text = ""
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(pdf_bytes))
        chunks = []
        for i, page in enumerate(reader.pages[:40], start=1):
            page_text = page.extract_text() or ""
            if page_text.strip():
                chunks.append(f"\n--- PAGE {i} ---\n{page_text[:7000]}")
        text = "".join(chunks)
    except Exception as e:
        log.warning("PDF extraction failed: %s", e)

    if not text.strip():
        return (
            "PDF matni avtomatik olinmadi. Bu skanerlangan/image-only PDF bo'lishi mumkin.\n"
            "Iltimos PDFdan kerakli sahifani rasm sifatida yuboring — men Vision orqali tahlil qilaman."
        )

    prompt2 = f"""Fayl: {filename}
Foydalanuvchi topshirig'i: {prompt or 'PDFni tahlil qiling, asosiy mazmunini tushuntiring va muhim savollarga javob bering.'}

Quyidagi PDF matni asosida javob bering. Muhim fikrlarni hujjatdagi sahifa raqami bilan belgilang.
Hech qachon PDFda yo'q ma'lumotni PDFdan olingandek ko'rsatmang.

{text[:120000]}"""

    answer, _ = await ask_ai(uid, prompt2, mode="pdf")
    return answer

# ------------------------- image generation -------------------------

async def generate_image(prompt: str):
    if not gemini_client:
        raise RuntimeError("Gemini API key sozlanmagan.")
    def run():
        return gemini_client.models.generate_content(
            model=GEMINI_IMAGE_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_modalities=["TEXT", "IMAGE"]
            ),
        )
    return await asyncio.to_thread(run)

# ------------------------- mode activation -------------------------

MODE_BUTTONS = {
    "🧠 AI TUTOR": ("ai_tutor", "✨ Gemini"),
    "📚 FANLAR": ("subjects", "✨ Gemini"),
    "📝 NEW TEST": ("new_test", "⚡ Groq"),
    "🎯 QUIZ": ("quiz", "⚡ Groq"),
    "🖼 RASM TAHLILI": ("image_analysis", "✨ Gemini"),
    "🎨 RASM YARATISH": ("image_generate", "✨ Gemini"),
    "👨‍🏫 O‘QITUVCHI": ("teacher", "🔵 Claude"),
    "🎯 EXAM MODE": ("exam", "🧠 GPT"),
    "👤 PROFIL": ("profile", "✨ Gemini"),
    "🚀 LOYIHA": ("project", "🧠 GPT"),
    "🎓 UNIVERSITET": ("university", "🧠 GPT"),
    "🌐 TIL": ("language", "✨ Gemini"),
}

SUBJECT_BUTTONS = {
    "🧮 OLIY MATEMATIKA": "math",
    "💻 DASTURLASH": "programming",
    "⚛️ FIZIKA": "physics",
    "🧪 KIMYO": "chemistry",
    "🧬 BIOLOGIYA": "biology",
    "📐 GEOMETRIYA": "geometry",
    "📊 STATISTIKA": "statistics",
    "💼 IQTISOD": "economics",
    "📚 BARCHA FANLAR": "subjects",
}

# ------------------------- bot -------------------------

bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)
dp = Dispatcher()

@dp.message.middleware()
async def register_user_middleware(handler, event, data):
    if getattr(event, "from_user", None):
        save_telegram_profile(event.from_user)
    return await handler(event, data)

@dp.message(CommandStart())
async def start(message: Message):
    st = user_state(message.from_user.id)
    st["mode"] = "ai_tutor"
    save_users()
    text = (
        "🚀 <b>EduMind AI</b>\n\n"
        "Sizning universal AI o'quv yordamchingiz.\n"
        "Fan, universitet, loyiha, PDF, rasm, test va IELTS uchun alohida AI yo'nalishi ishlaydi.\n\n"
        "Kerakli bo'limni tanlang yoki savolingizni yozing."
    )
    await send_long(message, text, ADMIN_MAIN_KB if is_admin(message.from_user.id) else MAIN_KB)

@dp.message(Command("help"))
async def help_cmd(message: Message):
    await send_long(
        message,
        "ℹ️ <b>Yordam</b>\n\n"
        "Savolni oddiy yozing — joriy bo'limga mos AI javob beradi.\n"
        "PDF yuborsangiz — PDF tahlil rejimi.\n"
        "Rasm yuborsangiz — Vision tahlil.\n"
        "IELTS bo'limida Speaking/Writing/Reading/Listening va Full Mock mavjud.\n"
        "/ai — AI tanlash\n"
        "/new — yangi suhbat"
    )

@dp.message(Command("new"))
async def new_cmd(message: Message):
    st = user_state(message.from_user.id)
    st["history"] = []
    st["mode"] = "ai_tutor"
    save_users()
    await send_long(message, "🔄 Yangi suhbat boshlandi.", MAIN_KB)

@dp.message(Command("ai"))
async def ai_cmd(message: Message):
    await message.answer("🤖 <b>AI tanlang:</b>", reply_markup=AI_KB)

@dp.message(F.text == "⬅️ MENU")
async def menu(message: Message):
    await message.answer("🏠 Asosiy menyu", reply_markup=MAIN_KB)

@dp.message(F.text == "🔄 YANGI SUHBAT")
async def new_chat(message: Message):
    st = user_state(message.from_user.id)
    st["history"] = []
    st["mode"] = "ai_tutor"
    save_users()
    await send_long(message, "🔄 Suhbat tozalandi. Endi yangi savol berishingiz mumkin.", MAIN_KB)

@dp.message(F.text == "🤖 AI TANLASH")
async def choose_ai(message: Message):
    await message.answer("🤖 Qaysi AI bilan ishlaymiz?", reply_markup=AI_KB)

@dp.message(F.text.in_(set(PROVIDER_LABELS.values())))
async def provider_select(message: Message):
    mapping = {
        "✨ Gemini": "gemini",
        "🚀 DeepSeek": "deepseek",
        "⚡ Groq": "groq",
        "🧠 GPT": "openai",
        "🔵 Claude": "claude",
    }
    name = mapping[message.text]
    st = user_state(message.from_user.id)
    st["provider"] = name
    st["manual_provider"] = True
    save_users()
    if not provider_is_configured(name):
        await send_long(message, f"{PROVIDER_LABELS[name]} tanlandi, lekin API key sozlanmagan.", MAIN_KB)
    else:
        await send_long(message, f"{PROVIDER_LABELS[name]} tanlandi. Endi savolingizni yozing.", MAIN_KB)

@dp.message(F.text == "🇬🇧 IELTS")
async def ielts_menu(message: Message):
    st = user_state(message.from_user.id)
    st["mode"] = "ielts"
    st["manual_provider"] = False
    save_users()
    await message.answer(
        "🇬🇧 <b>IELTS Real Examiner Mode</b>\n\n"
        "4 skill: Speaking, Writing, Reading, Listening.\n"
        "Har birida 4 ta baholash mezoni, band estimate, xatolar, feedback va explanation beriladi.",
        reply_markup=IELTS_KB
    )

@dp.message(F.text == "📚 IELTS KUTUBXONASI")
async def ielts_library(message: Message):
    await message.answer("""📚 <b>IELTS PDF KUTUBXONASI</b>

Kerakli bo‘limni tanlang. 📖✨""", reply_markup=IELTS_LIBRARY_KB)

@dp.message(F.text == "⬅️ IELTS MENU")
async def back_to_ielts(message: Message):
    await message.answer("🇬🇧 IELTS bo‘limiga qaytdingiz.", reply_markup=IELTS_KB)

@dp.message(F.text.in_(set(PDF_CATEGORIES.keys())))
async def pdf_category(message: Message):
    uid=message.from_user.id; st=user_state(uid); category=PDF_CATEGORIES[message.text]
    if is_admin(uid) and st.get("admin_action")=="choose_pdf_category":
        pending=st.get("pending_pdf")
        if not pending:
            await message.answer("⚠️ PDF topilmadi.", reply_markup=PDF_ADMIN_KB); return
        pending["category"]=category; st["admin_action"]="choose_pdf_name"; save_users()
        await message.answer("""📝 Endi PDF uchun foydalanuvchilarga ko‘rinadigan nomni yuboring.

Masalan: IELTS Writing Task 2 Guide""", reply_markup=ReplyKeyboardRemove()); return
    items=[(pid,pdf) for pid,pdf in ielts_pdfs.items() if pdf.get("category")==category]
    if not items:
        await message.answer("📂 Hozircha bu kategoriyada material yo‘q.", reply_markup=IELTS_LIBRARY_KB); return
    st["pdf_library_category"]=category; save_users()
    lines=["📚 <b>PDF MATERIALS</b>",""]
    for i,(pid,pdf) in enumerate(items,1): lines += [f"{i}. 📄 <b>{pdf.get('name')}</b>",f"Kod: <code>{pid}</code>"]
    lines.append("\n📥 Kerakli PDF kodini yuboring.")
    await send_long(message,"\n".join(lines),IELTS_LIBRARY_KB)

@dp.message(F.text == "🗣 IELTS SPEAKING")
async def ielts_speaking(message: Message):
    st = user_state(message.from_user.id)
    st["mode"] = "ielts"
    st["ielts_skill"] = "speaking"
    st["manual_provider"] = False
    save_users()
    await send_long(message, "🗣 IELTS Speaking boshlandi.\nExaminer savoliga javob bering. Men 4 kriteriya bo'yicha baholayman.", IELTS_KB)

@dp.message(F.text == "✍️ IELTS WRITING")
async def ielts_writing(message: Message):
    st = user_state(message.from_user.id)
    st["mode"] = "ielts"
    st["ielts_skill"] = "writing"
    st["manual_provider"] = False
    save_users()
    await send_long(message, "✍️ IELTS Writing.\nTask 1 yoki Task 2 javobingizni yuboring. Men 4 kriteriya bo'yicha real examiner uslubida baholayman.", IELTS_KB)

@dp.message(F.text == "📖 IELTS READING")
async def ielts_reading(message: Message):
    st = user_state(message.from_user.id)
    st["mode"] = "ielts"
    st["ielts_skill"] = "reading"
    st["manual_provider"] = False
    save_users()
    await send_long(message, "📖 IELTS Reading.\nPassage va savollarni yuboring yoki 'test ber' deb yozing.", IELTS_KB)

@dp.message(F.text == "🎧 IELTS LISTENING")
async def ielts_listening(message: Message):
    st = user_state(message.from_user.id)
    st["mode"] = "ielts"
    st["ielts_skill"] = "listening"
    st["manual_provider"] = False
    save_users()
    await send_long(message, "🎧 IELTS Listening.\nAudio yuborsangiz transkript/mazmun bilan ishlayman; audio bo'lmasa transcript-based simulation qilaman.", IELTS_KB)

@dp.message(F.text == "📊 IELTS FULL MOCK")
async def ielts_full(message: Message):
    st = user_state(message.from_user.id)
    st["mode"] = "ielts"
    st["ielts_skill"] = "speaking"
    st["ielts_full"] = True
    st["manual_provider"] = False
    save_users()
    await send_long(
        message,
        "📊 <b>IELTS FULL MOCK</b> boshlandi.\n\n"
        "Real muhitga yaqin tartibda Speaking → Writing → Reading → Listening qilamiz.\n"
        "Boshlash uchun: <b>START</b> deb yozing.",
        IELTS_KB
    )

@dp.message(F.text == "📚 FANLAR")
async def subjects(message: Message):
    st = user_state(message.from_user.id)
    st["mode"] = "subjects"
    st["manual_provider"] = False
    save_users()
    await message.answer("📚 Fan yo'nalishini tanlang:", reply_markup=DOMAIN_KB)

@dp.message(F.text.in_(set(SUBJECT_BUTTONS)))
async def subject_select(message: Message):
    mode = SUBJECT_BUTTONS[message.text]
    st = user_state(message.from_user.id)
    st["mode"] = mode
    st["manual_provider"] = False
    save_users()
    cfg = DOMAIN[mode]
    await send_long(
        message,
        f"📚 <b>{message.text}</b>\n\n"
        f"Yo'nalish uchun {PROVIDER_LABELS[cfg['provider']]} asosiy AI tanlandi.\n"
        "Savolingizni yuboring.",
        MAIN_KB
    )

@dp.message(F.text.in_(set(MODE_BUTTONS)))
async def domain_select(message: Message):
    mode, provider = MODE_BUTTONS[message.text]
    st = user_state(message.from_user.id)
    st["mode"] = mode
    st["provider"] = DOMAIN[mode]["provider"]
    st["manual_provider"] = False
    save_users()

    prompts = {
        "🧠 AI TUTOR": "Har qanday savolni yozing — tushuntirib beraman.",
        "📚 FANLAR": "Faningiz va mavzuni yozing.",
        "📝 NEW TEST": "Masalan: 'Oliy matematika, integral, 10 ta test'.",
        "🎯 QUIZ": "Mavzu va darajani yozing. Savollarni birma-bir beraman.",
        "🖼 RASM TAHLILI": "Endi rasm yuboring.",
        "🎨 RASM YARATISH": "Qanday ta'limiy rasm/diagramma kerakligini yozing.",
        "👨‍🏫 O‘QITUVCHI": "Dars, lesson plan yoki o'quvchi feedbackini yozing.",
        "🎯 EXAM MODE": "Imtihon fanini va darajasini yozing.",
        "👤 PROFIL": "O'qish maqsadingizni yozing — shaxsiy reja tuzaman.",
        "🚀 LOYIHA": "Loyiha mavzusi va talablarini yozing.",
        "🎓 UNIVERSITET": "Universitet fani yoki topshirig'ingizni yozing.",
        "🌐 TIL": "Til va maqsadni yozing.",
    }
    await send_long(
        message,
        f"✅ <b>{message.text}</b>\n\n"
        f"Asosiy AI: {provider}\n"
        f"{prompts.get(message.text, 'Savolingizni yuboring.')}",
        MAIN_KB
    )

@dp.message(F.text == "🚀 START")
async def start_button(message: Message):
    await start(message)

@dp.message(F.text == "ℹ️ YORDAM")
async def help_button(message: Message):
    await help_cmd(message)

@dp.message(F.text == "👨‍💻 BOT YARATUVCHISI")
async def bot_creator(message: Message):
    text="""👨‍💻 <b>EduMind AI yaratuvchisi</b>

Assalomu alaykum! 👋

EduMind AI ta'lim olishni yanada qulay, tushunarli va zamonaviy qilish maqsadida yaratilgan loyiha.

📚 <b>Bot nima uchun yaratilgan?</b>
• Murakkab mavzularni sodda tushuntirish
• AI yordamida savollarga javob topish
• IELTS va til ko‘nikmalarini rivojlantirish
• Test va imtihonlarga tayyorlanish
• PDF va o‘quv materiallarini qulay ulashish
• Mustaqil o‘rganishni osonlashtirish

🌱 EduMind AI doimiy rivojlanishda. Har bir yangi funksiya foydalanuvchilarga yanada foydali bo‘lish uchun yaratiladi.

💙 Botdan foydalanganingiz uchun rahmat! Birgalikda o‘rganamiz va katta maqsadlar sari harakat qilamiz. 🚀✨

📬 Aloqa: @elbek_ziyodullayev"""
    await send_long(message,text,ADMIN_MAIN_KB if is_admin(message.from_user.id) else MAIN_KB)

@dp.message(F.text == "👤 PROFIL")
async def profile_button(message: Message):
    uid = message.from_user.id
    st = user_state(uid)
    answer = (
        f"👤 <b>Profil</b>\n\n"
        f"AI: {PROVIDER_LABELS.get(st.get('last_provider', st.get('provider', DEFAULT_PROVIDER)), st.get('provider', DEFAULT_PROVIDER))}\n"
        f"Yo'nalish: {st.get('mode', 'ai_tutor')}\n"
        f"Suhbat xabarlari: {len(st.get('history', []))}\n\n"
        "Profilingizni yaxshilash uchun maqsadingiz, darajangiz va kunlik vaqtingizni yozing."
    )
    await send_long(message, answer, MAIN_KB)

@dp.message(Command("admin"))
async def admin_cmd(message: Message):
    await send_admin_menu(message)

@dp.message(F.text == "👨‍💼 ADMIN PANEL")
async def admin_panel_button(message: Message):
    await send_admin_menu(message)

@dp.message(F.text == "📚 IELTS PDF BOSHQARISH")
async def pdf_management(message: Message):
    if not is_admin(message.from_user.id): await message.answer("⛔ Bu bo‘lim faqat administrator uchun."); return
    await message.answer("📚 <b>IELTS PDF BOSHQARUV PANELI</b>", reply_markup=PDF_ADMIN_KB)

@dp.message(F.text == "⬅️ ADMIN")
async def back_admin(message: Message):
    await send_admin_menu(message)

@dp.message(F.text == "➕ PDF QO'SHISH")
async def add_pdf_start(message: Message):
    if not is_admin(message.from_user.id): return
    st=user_state(message.from_user.id); st["admin_action"]="add_pdf"; save_users()
    await message.answer("📎 IELTS kutubxonasiga qo‘shmoqchi bo‘lgan PDF faylni yuboring.", reply_markup=ReplyKeyboardRemove())

@dp.message(F.text == "📋 PDF RO'YXATI")
async def admin_pdf_list(message: Message):
    if not is_admin(message.from_user.id): return
    if not ielts_pdfs: await message.answer("📂 Hozircha PDF yo‘q.",reply_markup=PDF_ADMIN_KB); return
    await send_long(message,"\n".join(["📚 <b>IELTS PDF RO'YXATI</b>",""]+[f"📄 <b>{v.get('name')}</b> — <code>{k}</code>" for k,v in ielts_pdfs.items()]),PDF_ADMIN_KB)

@dp.message(F.text == "🗑 PDF O'CHIRISH")
async def delete_pdf_start(message: Message):
    if not is_admin(message.from_user.id): return
    st=user_state(message.from_user.id); st["admin_action"]="delete_pdf"; save_users(); await message.answer("🗑 O‘chirmoqchi bo‘lgan PDF kodini yuboring.",reply_markup=PDF_ADMIN_KB)

@dp.message(F.text == "👥 FOYDALANUVCHILAR")
async def admin_users(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Bu bo‘lim faqat administrator uchun.")
        return

    total = len(users)
    lines = [
        "👥 <b>EDUMIND AI FOYDALANUVCHILARI</b>",
        "",
        f"📊 Jami foydalanuvchilar: <b>{total}</b>",
        "",
    ]

    for i, (uid, data) in enumerate(users.items(), 1):
        first = html.escape(str(data.get("first_name") or "Noma'lum"))
        last = html.escape(str(data.get("last_name") or ""))
        username = str(data.get("username") or "").strip()
        full_name = f"{first} {last}".strip()

        if username:
            profile = f"<a href=\"https://t.me/{html.escape(username)}\">👤 {full_name}</a>"
            user_line = f"🔗 @{html.escape(username)}"
        else:
            profile = f"<a href=\"tg://user?id={uid}\">👤 {full_name}</a>"
            user_line = "🔗 Username mavjud emas"

        lines.extend([
            f"<b>{i}.</b> {profile}",
            user_line,
            f"🆔 <code>{uid}</code>",
            "",
        ])

    await send_long(message, "\n".join(lines), ADMIN_KB)

@dp.message(F.text == "📢 BARCHAGA XABAR")
async def broadcast_start(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Bu bo‘lim faqat administrator uchun.")
        return
    st = user_state(message.from_user.id)
    st["admin_action"] = "broadcast"
    save_users()
    await message.answer(
        "📢 <b>Barchaga xabar yuborish</b>\n\n"
        "Yubormoqchi bo‘lgan <b>matn, rasm, video yoki PDF</b>ni yuboring.\n"
        "❌ Bekor qilish: <b>/cancel</b>",
        reply_markup=ADMIN_KB,
    )

@dp.message(Command("cancel"))
async def cancel_cmd(message: Message):
    st = user_state(message.from_user.id)
    if is_admin(message.from_user.id) and st.get("admin_action") == "broadcast":
        st.pop("admin_action", None)
        save_users()
        await message.answer("❌ Ommaviy xabar bekor qilindi.", reply_markup=ADMIN_KB)
        return
    await new_cmd(message)

@dp.message(F.text == "📊 STATISTIKA")
async def admin_stats(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Bu bo‘lim faqat administrator uchun.")
        return
    total = len(users)
    active = sum(1 for u in users.values() if u.get("history"))
    await message.answer(
        f"📊 <b>EduMind AI statistikasi</b>\n\n"
        f"👥 Jami foydalanuvchilar: <b>{total}</b>\n"
        f"💬 AI bilan suhbatlashganlar: <b>{active}</b>",
        reply_markup=ADMIN_KB,
    )

async def do_broadcast(message: Message) -> bool:
    uid = message.from_user.id
    if not is_admin(uid):
        return False
    st = user_state(uid)
    if st.get("admin_action") != "broadcast":
        return False
    if message.text in {"📢 BARCHAGA XABAR", "📊 STATISTIKA", "⬅️ MENU", "👨‍💼 ADMIN PANEL"}:
        return False

    st.pop("admin_action", None)
    save_users()
    targets = list(users.keys())
    sent = failed = 0

    for target in targets:
        try:
            tid = int(target)
            if message.text is not None:
                await bot.send_message(tid, message.text)
            elif message.photo:
                await bot.send_photo(tid, message.photo[-1].file_id, caption=message.caption or None)
            elif message.video:
                await bot.send_video(tid, message.video.file_id, caption=message.caption or None)
            elif message.document:
                await bot.send_document(tid, message.document.file_id, caption=message.caption or None)
            elif message.voice:
                await bot.send_voice(tid, message.voice.file_id, caption=message.caption or None)
            else:
                failed += 1
                continue
            sent += 1
            await asyncio.sleep(0.04)
        except Exception as e:
            failed += 1
            log.warning("Broadcast failed user=%s: %s", target, e)

    await message.answer(
        "📢 <b>Ommaviy xabar yakunlandi!</b>\n\n"
        f"👥 Jami: <b>{len(targets)}</b>\n"
        f"✅ Yetkazildi: <b>{sent}</b>\n"
        f"❌ Yetkazilmadi: <b>{failed}</b>",
        reply_markup=ADMIN_KB,
    )
    return True

@dp.message(F.photo)
async def photo_handler(message: Message):
    if await do_broadcast(message):
        return
    uid = message.from_user.id
    caption = message.caption or ""
    try:
        await message.answer("🖼 Rasm qabul qilindi. Vision AI tahlil qilmoqda...")
        file = await bot.get_file(message.photo[-1].file_id)
        buf = io.BytesIO()
        await bot.download_file(file.file_path, destination=buf)
        answer = await analyze_photo(uid, caption, buf.getvalue())
        await send_long(message, answer, MAIN_KB)
    except Exception as e:
        log.exception("Photo error")
        await send_long(message, f"Rasm tahlilida xatolik: {str(e)[:300]}", MAIN_KB)

@dp.message(F.video)
async def video_handler(message: Message):
    if await do_broadcast(message):
        return
    await message.answer("🎬 Video qabul qilindi. Hozircha video tahlili uchun rasm yoki PDF yuborishingiz mumkin.")

@dp.message(F.document)
async def document_handler(message: Message):
    if await do_broadcast(message):
        return
    uid=message.from_user.id; st=user_state(uid)
    if is_admin(uid) and st.get("admin_action")=="add_pdf":
        if not (message.document.file_name or "").lower().endswith(".pdf"):
            await message.answer("⚠️ Iltimos faqat PDF yuboring."); return
        st["pending_pdf"]={"file_id":message.document.file_id,"file_name":message.document.file_name}; st["admin_action"]="choose_pdf_category"; save_users()
        await message.answer("✅ PDF qabul qilindi. Endi kategoriyasini tanlang 👇",reply_markup=IELTS_LIBRARY_KB); return
    if not message.document.file_name.lower().endswith(".pdf"):
        await send_long(message, "📄 Hozircha PDF hujjatlarni tahlil qilish rejimi faol. PDF yuboring.", MAIN_KB)
        return
    uid = message.from_user.id
    st = user_state(uid)
    st["mode"] = "pdf"
    st["manual_provider"] = False
    save_users()
    try:
        await message.answer("📄 PDF qabul qilindi. Matn ajratilib, AI tahliliga tayyorlanmoqda...")
        file = await bot.get_file(message.document.file_id)
        buf = io.BytesIO()
        await bot.download_file(file.file_path, destination=buf)
        answer = await analyze_pdf(uid, message.document.file_name, buf.getvalue(), message.caption or "")
        await send_long(message, answer, MAIN_KB)
    except Exception as e:
        log.exception("PDF error")
        await send_long(message, f"PDF tahlilida xatolik: {str(e)[:300]}", MAIN_KB)

@dp.message(F.text)
async def text_handler(message: Message):
    if await do_broadcast(message):
        return
    uid = message.from_user.id
    text = message.text.strip()
    st = user_state(uid)

    if not text:
        return

    if is_admin(uid) and st.get("admin_action")=="choose_pdf_name":
        p=st.get("pending_pdf")
        if not p: await message.answer("⚠️ PDF topilmadi.",reply_markup=PDF_ADMIN_KB); return
        n=1
        while f"ielts_{n}" in ielts_pdfs: n+=1
        pid=f"ielts_{n}"; ielts_pdfs[pid]={"name":text,"file_id":p["file_id"],"file_name":p.get("file_name"),"category":p["category"]}; save_ielts_pdfs()
        st.pop("pending_pdf",None); st.pop("admin_action",None); save_users()
        await message.answer(f"🎉 <b>PDF muvaffaqiyatli qo‘shildi!</b>\n\n📄 {text}\n🆔 Kod: <code>{pid}</code>",reply_markup=PDF_ADMIN_KB); return

    if is_admin(uid) and st.get("admin_action")=="delete_pdf":
        if text in ielts_pdfs:
            name=ielts_pdfs[text].get("name",text); del ielts_pdfs[text]; save_ielts_pdfs(); st.pop("admin_action",None); save_users(); await message.answer(f"🗑 PDF o‘chirildi: {name}",reply_markup=PDF_ADMIN_KB)
        else: await message.answer("❌ Bunday PDF kodi topilmadi.")
        return

    if st.get("pdf_library_category") and text in ielts_pdfs and ielts_pdfs[text].get("category")==st.get("pdf_library_category"):
        pdf=ielts_pdfs[text]
        await message.answer_document(pdf["file_id"],caption=f"📚 <b>{pdf.get('name')}</b>\n\n📥 Faylni ochishingiz yoki qurilmangizga yuklab olishingiz mumkin.\nYaxshi o‘qishlar! 🚀📖"); return

    # Image generation request
    if st.get("mode") == "image_generate":
        try:
            await message.answer("🎨 Rasm tayyorlanmoqda...")
            result = await generate_image(
                "Create a clean, high-quality educational visual for this request. "
                "Do not add fake logos or fake citations. Request: " + text
            )
            sent_image = False
            if getattr(result, "candidates", None):
                for cand in result.candidates:
                    content = getattr(cand, "content", None)
                    for part in getattr(content, "parts", []) if content else []:
                        data = getattr(part, "inline_data", None)
                        if data and getattr(data, "data", None):
                            await message.answer_photo(
                                __import__("aiogram").types.BufferedInputFile(
                                    data.data, filename="edumind_visual.png"
                                )
                            )
                            sent_image = True
                            break
                    if sent_image:
                        break
            if not sent_image:
                answer, provider = await ask_ai(uid, text, mode="image_generate")
                await send_long(message, "🎨 Image generation provider matnli prompt qaytardi:\n" + answer, MAIN_KB)
            else:
                await message.answer(BRAND_FOOTER, parse_mode=ParseMode.HTML, reply_markup=MAIN_KB)
        except Exception as e:
            log.warning("Image generation failed: %s", e)
            await send_long(message, f"Rasm yaratish hozir ishlamadi: {str(e)[:300]}", MAIN_KB)
        return

    # Full mock first command
    if st.get("ielts_full") and text.lower() == "start":
        st["ielts_skill"] = "speaking"
        save_users()
        answer, provider = await ielts_response(
            uid,
            "Start the IELTS full mock. Act as the examiner and ask the first realistic Speaking Part 1 question. Do not explain the test yet.",
            "speaking"
        )
        await send_long(message, answer, IELTS_KB)
        return

    # IELTS active skill
    if st.get("mode") == "ielts":
        skill = st.get("ielts_skill", "speaking")
        try:
            answer, provider = await ielts_response(uid, text, skill)
            await send_long(message, answer, IELTS_KB)
        except Exception as e:
            await send_long(message, f"IELTS rejimida xatolik: {str(e)[:300]}", IELTS_KB)
        return

    # Manual AI overrides domain selection.
    force = st.get("provider") if st.get("manual_provider") else None
    mode = st.get("mode", "ai_tutor")

    try:
        answer, provider = await ask_ai(uid, text, mode=mode, force_provider=force)
        await send_long(message, answer, MAIN_KB)
    except Exception as e:
        log.exception("Text AI error")
        await send_long(
            message,
            "⚠️ Hozirgi AI javob bera olmadi.\n"
            "Avtomatik zaxira AIlar ham tekshirildi.\n\n"
            f"Texnik sabab: {str(e)[:500]}",
            MAIN_KB
        )

async def main():
    print("EduMind AI starting...")
    print(f"Default provider: {DEFAULT_PROVIDER}")
    print(f"Auto fallback: {AUTO_FALLBACK}")
    for p in ["gemini", "deepseek", "groq", "openai", "claude"]:
        print(f"Provider {p}: {'ON' if provider_is_configured(p) else 'OFF'} | model={MODELS[p]}")
    me = await bot.get_me()
    print(f"Bot: @{me.username} id={me.id}")
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("EduMind AI stopped")
