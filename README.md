[main.py](https://github.com/user-attachments/files/34542892/main.py)
import os
import io
import re
import json
import math
import time
import html
import random
import asyncio
import logging
import contextlib
import shutil
from urllib.parse import urlencode, urlsplit, urlunsplit, parse_qsl
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, F, BaseMiddleware
from aiogram.filters import Command, CommandStart, BaseFilter
from aiogram.types import (
    Message, CallbackQuery, ChatMemberUpdated, ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton, BufferedInputFile, WebAppInfo, MenuButtonWebApp,
)
from aiogram.enums import ParseMode, ChatAction
from aiogram.client.default import DefaultBotProperties
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter

try:
    from openai import AsyncOpenAI
except Exception:  # pragma: no cover
    AsyncOpenAI = None
try:
    from anthropic import AsyncAnthropic
except Exception:  # pragma: no cover
    AsyncAnthropic = None
try:
    from google import genai
    from google.genai import types
except Exception:  # pragma: no cover
    genai = None
    types = None

load_dotenv()

# =====================================================================
# EduMind AI v7.0 — Next-Gen Educational Ecosystem
# =====================================================================

BOT_TOKEN = os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
DEFAULT_PROVIDER = os.getenv("AI_DEFAULT_PROVIDER", "gemini").lower()
AUTO_FALLBACK = os.getenv("AI_AUTO_FALLBACK", "true").lower() == "true"
ADMIN_IDS = {int(x.strip()) for x in os.getenv("ADMIN_IDS", "6841567673").split(",") if x.strip().isdigit()}
try:
    TZ = ZoneInfo(os.getenv("BOT_TZ", "Asia/Tashkent"))
except Exception:  # Windows'da tzdata o'rnatilmagan bo'lsa: pip install tzdata
    TZ = timezone(timedelta(hours=5), "Asia/Tashkent")

PROVIDER_TIMEOUT = float(os.getenv("AI_PROVIDER_TIMEOUT", "20"))
HEDGE_DELAY = float(os.getenv("AI_HEDGE_DELAY", "2.5"))  # birinchi provayder sekin bo'lsa, zaxirasi parallel ishga tushadi

MODELS = {
    "gemini": os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
    "deepseek": os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
    "groq": os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile"),
    "openai": os.getenv("OPENAI_MODEL", "gpt-4o"),
    "claude": os.getenv("CLAUDE_MODEL", "claude-sonnet-5-5"),
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
FAST_FALLBACK = ["groq", "gemini", "openai", "claude", "deepseek"]

CARD_NUMBER = "5440810319005806"
CARD_OWNER = "Z/E"
CREATOR_USERNAME = (os.getenv("CREATOR_USERNAME", "elbek_ziyodullayev") or "elbek_ziyodullayev").strip().lstrip("@")
# Mini App (Open tugmasi). index.html ni HTTPS hostingga joylab, shu yerga havolasini yozing (.env: WEBAPP_URL=https://...)
WEBAPP_URL = (os.getenv("WEBAPP_URL", "") or "").strip()
if WEBAPP_URL and not WEBAPP_URL.lower().startswith("https://"):
    print("⚠️ WEBAPP_URL https:// bilan boshlanishi shart — Mini App o'chirildi.")
    WEBAPP_URL = ""
BOT_USERNAME = ""
GEMINI_PROMO_NOTE = os.getenv("GEMINI_PROMO_NOTE", "")  # masalan: "Dushanbadan narx 79 000 so'mga oshadi"
NOTIFY_ADMIN_ON_BLOCK = os.getenv("NOTIFY_ADMIN_ON_BLOCK", "true").lower() == "true"

BRAND_FOOTER = "🥇 <b>EduMind AI</b> — <i>Ta'lim va Ilm Ziyo Maskani</i> ✨\n🚀 <i>crafted by: @elbek_ziyodullayev</i> 💙"

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
log = logging.getLogger("edumind")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN .env faylida topilmadi")

# ------------------------- AI clients -------------------------

gemini_client = genai.Client(api_key=KEYS["gemini"]) if (genai and KEYS["gemini"]) else None
openai_client = AsyncOpenAI(api_key=KEYS["openai"]) if (AsyncOpenAI and KEYS["openai"]) else None
deepseek_client = AsyncOpenAI(api_key=KEYS["deepseek"], base_url="https://api.deepseek.com") if (AsyncOpenAI and KEYS["deepseek"]) else None
groq_client = AsyncOpenAI(api_key=KEYS["groq"], base_url="https://api.groq.com/openai/v1") if (AsyncOpenAI and KEYS["groq"]) else None
claude_client = AsyncAnthropic(api_key=KEYS["claude"]) if (AsyncAnthropic and KEYS["claude"]) else None

CLIENTS = {
    "gemini": gemini_client, "openai": openai_client, "deepseek": deepseek_client,
    "groq": groq_client, "claude": claude_client,
}
PROVIDER_LABELS = {
    "gemini": "✨ Gemini", "deepseek": "🚀 DeepSeek", "groq": "⚡ Groq", "openai": "🧠 GPT", "claude": "🔵 Claude",
}

# =====================================================================
# Storage (debounced, atomic)
# =====================================================================

DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)


def _load(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default
    except Exception as e:
        log.error("Load error %s: %s", path, e)
        return default


USERS_FILE = DATA_DIR / "users.json"
ACTIVITIES_FILE = DATA_DIR / "activities.json"
GUIDES_FILE = DATA_DIR / "guides.json"
FEEDBACK_FILE = DATA_DIR / "feedback.json"
REQUESTS_FILE = DATA_DIR / "requests.json"
REFERRALS_FILE = DATA_DIR / "referrals_log.json"
QUERIES_FILE = DATA_DIR / "queries_log.json"
LEADS_FILE = DATA_DIR / "leads.json"
METRICS_FILE = DATA_DIR / "metrics.json"

def snapshot_data():
    """Har ishga tushganda data/ ichidagi JSON fayllarning nusxasini data/backup/ ga saqlaydi (oxirgi 10 ta). Tarix yo'qolmasligi uchun."""
    try:
        files = [f for f in DATA_DIR.glob("*.json") if f.is_file()]
        if not files:
            return
        dest = DATA_DIR / "backup" / datetime.now().strftime("%Y%m%d_%H%M%S")
        dest.mkdir(parents=True, exist_ok=True)
        for f in files:
            shutil.copy2(f, dest / f.name)
        snaps = sorted(d for d in (DATA_DIR / "backup").iterdir() if d.is_dir())
        for old in snaps[:-10]:
            shutil.rmtree(old, ignore_errors=True)
    except Exception as e:
        print("Zaxira nusxa xatosi:", e)


snapshot_data()

users: dict = _load(USERS_FILE, {})
activities: list = _load(ACTIVITIES_FILE, [])
guides: dict = _load(GUIDES_FILE, {})
feedbacks: list = _load(FEEDBACK_FILE, [])
purchases: list = _load(REQUESTS_FILE, [])
referrals_log: list = _load(REFERRALS_FILE, [])
queries_log: list = _load(QUERIES_FILE, [])
leads: list = _load(LEADS_FILE, [])
metrics: dict = _load(METRICS_FILE, {"ok": {}, "fail": {}, "ms_total": 0, "count": 0})


def _fix_types():
    """Eski versiyalardagi yoki buzilgan JSON fayllar tufayli bot yiqilmasligi uchun turlarni to'g'rilaydi."""
    global users, activities, guides, feedbacks, purchases, referrals_log, queries_log, leads, metrics
    if not isinstance(users, dict):
        users = {}
    users = {str(k): v for k, v in users.items() if isinstance(v, dict)}
    if not isinstance(guides, dict):
        guides = {}
    if not isinstance(metrics, dict):
        metrics = {}
    for k, dv in (("ok", {}), ("fail", {}), ("ms_total", 0), ("count", 0)):
        if not isinstance(metrics.get(k), type(dv)):
            metrics[k] = dv
    activities = [x for x in activities if isinstance(x, dict)] if isinstance(activities, list) else []
    feedbacks = [x for x in feedbacks if isinstance(x, dict)] if isinstance(feedbacks, list) else []
    referrals_log = [x for x in referrals_log if isinstance(x, dict)] if isinstance(referrals_log, list) else []
    queries_log = [x for x in queries_log if isinstance(x, dict)] if isinstance(queries_log, list) else []
    leads = [x for x in leads if isinstance(x, dict)] if isinstance(leads, list) else []
    purchases = [x for x in purchases if isinstance(x, dict)] if isinstance(purchases, list) else []

    def to_int(v):
        try:
            return int(str(v).strip())
        except Exception:
            return 0

    mx = max([to_int(r.get("id")) for r in purchases] + [0])
    seen = set()
    for r in purchases:
        rid = to_int(r.get("id"))
        if rid <= 0 or rid in seen:
            mx += 1
            rid = mx
        seen.add(rid)
        r["id"] = rid
        r["uid"] = to_int(r.get("uid"))
        try:
            r["epoch"] = float(r.get("epoch") or 0)
        except Exception:
            r["epoch"] = 0
        r["time"] = str(r.get("time") or "")
        r["full_name"] = str(r.get("full_name") or "")
        r["username"] = str(r.get("username") or "")
        r["product"] = str(r.get("product") or "")
        r["service"] = str(r.get("service") or r.get("product") or "Xizmat")
        r["details"] = str(r.get("details") or "")
        r["status"] = r.get("status") if r.get("status") in ("pending", "done", "rejected") else "done"


_fix_types()

FILES = {
    "users": (USERS_FILE, lambda: users),
    "activities": (ACTIVITIES_FILE, lambda: activities),
    "guides": (GUIDES_FILE, lambda: guides),
    "feedbacks": (FEEDBACK_FILE, lambda: feedbacks),
    "purchases": (REQUESTS_FILE, lambda: purchases),
    "referrals": (REFERRALS_FILE, lambda: referrals_log),
    "queries": (QUERIES_FILE, lambda: queries_log),
    "leads": (LEADS_FILE, lambda: leads),
    "metrics": (METRICS_FILE, lambda: metrics),
}
_dirty: set = set()


def touch(*names: str):
    _dirty.update(names)


def _write_atomic(path: Path, payload: str):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(payload, encoding="utf-8")
    os.replace(tmp, path)


async def flush():
    names = list(_dirty)
    _dirty.clear()
    for n in names:
        path, getter = FILES[n]
        try:
            payload = json.dumps(getter(), ensure_ascii=False)
            await asyncio.to_thread(_write_atomic, path, payload)
        except Exception as e:
            log.error("Save error %s: %s", n, e)
            _dirty.add(n)


async def flusher():
    while True:
        await asyncio.sleep(2)
        await flush()


# =====================================================================
# Helpers
# =====================================================================

def now_dt() -> datetime:
    return datetime.now(TZ)


def ts() -> str:
    return now_dt().strftime("%Y-%m-%d %H:%M:%S")


def today() -> str:
    return now_dt().strftime("%Y-%m-%d")


def yesterday() -> str:
    return datetime.fromtimestamp(time.time() - 86400, TZ).strftime("%Y-%m-%d")


def esc(s) -> str:
    return html.escape(str(s if s is not None else ""), quote=False)


def clip(s, n: int) -> str:
    s = str(s or "").replace("\n", " ").strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def ago(epoch) -> str:
    if not epoch:
        return "noma'lum"
    d = int(time.time() - float(epoch))
    if d < 45:
        return "hozirgina"
    if d < 3600:
        return f"{max(1, d // 60)} daqiqa oldin"
    if d < 86400:
        return f"{d // 3600} soat oldin"
    return f"{d // 86400} kun oldin"


def dur(seconds: float) -> str:
    seconds = int(max(0, seconds))
    if seconds < 3600:
        return f"{max(1, seconds // 60)} daqiqa"
    if seconds < 86400:
        return f"{seconds // 3600} soat"
    return f"{seconds // 86400} kun"


def pick(options):
    return random.choice(options)


GREET_RE = re.compile(r"^\s*(assalomu?\s*alaykum|assalom|salom|hayrli\s+(tong|kun|kech)|hello|hi|hey|привет|здравствуй\w*)[\s!.,]*$", re.I)
THANKS_RE = re.compile(r"(rahmat|raxmat|tashakkur|minnatdor|thanks|thank you|спасибо)", re.I)


def day_greeting() -> str:
    h = datetime.now(TZ).hour
    return "☀️ Xayrli tong" if h < 11 else ("🌤 Xayrli kun" if h < 18 else "🌙 Xayrli kech")


GREETINGS = [
    "🌟 <b>Assalomu alaykum, {n}!</b>",
    "👋 <b>Salom, {n}!</b>",
    "{day}, <b>{n}!</b> 🌿",
    "✨ <b>Xush kelibsiz, {n}!</b>",
    "🤝 <b>Assalomu alaykum, aziz {n}!</b>",
    "🎉 <b>Sizni ko'rganimdan xursandman, {n}!</b>",
    "🌈 <b>Salom-salom, {n}!</b>",
    "💫 <b>Assalomu alaykum va xush kelibsiz, {n}!</b>",
    "🌸 <b>Assalomu alaykum, {n}! Yana birgalikdamiz</b>",
    "🚀 <b>Salom, {n}! Tayyormisiz?</b>",
]


def welcome_text(st: dict, first: str) -> str:
    """Har safar boshqacha salom, qolgani doim bir xil: kartochka formati o'zgarmaydi."""
    n = esc(first or "do'stim")
    last = st.get("last_greet", -1)
    idx = random.choice([i for i in range(len(GREETINGS)) if i != last])
    st["last_greet"] = idx
    hello = GREETINGS[idx].replace("{n}", n).replace("{day}", day_greeting())
    return (
        f"{hello}\n\n"
        "Men <b>EduMind AI</b> — sizning shaxsiy o'quv yordamchingizman 🎓\n\n"
        "📚 Fanlardan yordam • 📝 Test va quiz • 🖼 Rasm tahlili • 🇬🇧 IELTS • 🎙 Ovozli savol • 🍅 Pomodoro\n\n"
        "Shunchaki savolingizni yozing — tushuntirib beraman. Yoki pastdagi bo'limlardan birini tanlang 👇\n\n"
        "💎 <i>Premium: rasm yaratish • video tahlil • Gemini Pro (18 oy) • Telegram Stars ⭐</i>\n\n" + BRAND_FOOTER
    )


WARM_TAILS = [
    "💬 Yana savolingiz bo'lsa, bemalol yozing — doim shu yerdaman 😊",
    "🌟 Ajoyib savol edi! Davom etamizmi?",
    "✨ Tushunarli bo'ldimi? Kerak bo'lsa, boshqacha qilib ham tushuntirib beraman 🤗",
    "🚀 Zo'r ketyapsiz! Keyingi savol sizdan 💪",
    "🙌 Siz bilan o'rganish juda yoqimli!",
]


def warm_tail() -> str:
    return ("\n\n" + pick(WARM_TAILS)) if random.random() < 0.3 else ""


def user_link(uid, name) -> str:
    return f'<a href="tg://user?id={uid}">{esc(clip(name or f"ID {uid}", 40))}</a>'


def uname_of(u: dict) -> str:
    return f"@{u['username']}" if u.get("username") else "username yo'q"


def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS


LEVEL_TITLES = ["🌱 Yangi boshlovchi", "📘 O'quvchi", "🔥 Izlanuvchi", "🚀 Bilimdon", "🎓 Ustoz", "👑 Daho"]


def level_of(xp: int):
    lvl = int(math.sqrt(max(xp, 0) / 40)) + 1
    title = LEVEL_TITLES[min(lvl - 1, len(LEVEL_TITLES) - 1)]
    nxt = (lvl ** 2) * 40
    return lvl, title, nxt


def progress_bar(cur: int, total: int, width: int = 10) -> str:
    total = max(total, 1)
    filled = min(width, int(width * cur / total))
    return "▰" * filled + "▱" * (width - filled)


# ------------------------- user state -------------------------

DEFAULTS = {
    "history": [], "provider": "auto", "mode": "ai_tutor", "sub": None, "lang": "uz",
    "points": 10, "queries_count": 0, "banned": False, "bot_blocked": False,
    "status": "Standart", "last_query": "", "full_name": "", "username": "",
    "joined_at": None, "joined_ts": 0, "last_seen": 0, "started": False,
    "referred_by": None, "referrals_count": 0, "reward_claimed": False,
    "awaiting": None, "admin_action": None, "streak": 0, "last_day": "",
    "interest": {}, "last_interest": 0, "blocked_ts": 0, "blocked_at": "",
    "unblocked_ts": 0, "images_made": 0, "videos_done": 0, "last_provider": "",
}


def get_user(uid: int, tg_user=None) -> dict:
    key = str(uid)
    u = users.get(key)
    if u is None:
        u = users[key] = {}
        u["joined_at"] = ts()
        u["joined_ts"] = time.time()
    for k, v in DEFAULTS.items():
        if k not in u:
            u[k] = json.loads(json.dumps(v))
    if not u.get("joined_ts"):
        try:
            u["joined_ts"] = datetime.strptime(u["joined_at"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=TZ).timestamp()
        except Exception:
            u["joined_ts"] = 0
    if tg_user is not None:
        u["full_name"] = tg_user.full_name or u.get("full_name") or f"ID {uid}"
        u["username"] = tg_user.username or ""
    elif not u.get("full_name"):
        u["full_name"] = f"ID {uid}"
    return u


def log_act(uid: int, action: str, desc: str):
    u = users.get(str(uid), {})
    activities.insert(0, {
        "time": ts(), "uid": uid, "full_name": u.get("full_name") or f"ID {uid}",
        "username": u.get("username") or "", "action": action, "desc": clip(desc, 160),
    })
    del activities[5000:]
    touch("activities")


def add_xp(st: dict, n: int):
    st["points"] = int(st.get("points", 0)) + n


def daily_bonus(st: dict) -> int:
    t = today()
    if st.get("last_day") == t:
        return 0
    st["streak"] = st.get("streak", 0) + 1 if st.get("last_day") == yesterday() else 1
    st["last_day"] = t
    bonus = min(5 * st["streak"], 50)
    add_xp(st, bonus)
    return bonus


def is_premium(uid: int) -> bool:
    return is_admin(uid) or users.get(str(uid), {}).get("status") in ("Premium", "VIP")


def add_lead(uid: int, kind: str, note: str = ""):
    st = get_user(uid)
    if not isinstance(st.get("interest"), dict):
        st["interest"] = {}
    st["interest"][kind] = st["interest"].get(kind, 0) + 1
    prev = st.get("last_interest", 0)
    st["last_interest"] = time.time()
    leads.insert(0, {"time": ts(), "uid": uid, "kind": kind, "note": clip(note, 120)})
    del leads[3000:]
    touch("leads", "users")
    return time.time() - prev > 3600  # admin'ga tez-tez xabar bermaslik uchun


# =====================================================================
# Text formatting & safe sending
# =====================================================================

_CB = re.compile(r"```[a-zA-Z0-9_+-]*\n?(.*?)```", re.S)
_IC = re.compile(r"`([^`\n]+)`")


def md_to_html(text: str) -> str:
    store = []

    def hold_block(m):
        store.append("<pre>" + html.escape(m.group(1).strip("\n"), quote=False) + "</pre>")
        return f"\x00{len(store) - 1}\x00"

    def hold_inline(m):
        store.append("<code>" + html.escape(m.group(1), quote=False) + "</code>")
        return f"\x00{len(store) - 1}\x00"

    t = _CB.sub(hold_block, text or "")
    t = _IC.sub(hold_inline, t)
    t = html.escape(t, quote=False)
    t = re.sub(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$", r"<b>\1</b>", t, flags=re.M)
    t = re.sub(r"^(\s*)[\*\-]\s+", r"\1• ", t, flags=re.M)
    t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t, flags=re.S)
    t = re.sub(r"(?<![\w*])\*(?!\s)([^*\n]+?)(?<!\s)\*(?![\w*])", r"<i>\1</i>", t)
    t = re.sub(r"(?<!\w)__(.+?)__(?!\w)", r"<b>\1</b>", t)
    t = re.sub(r"\x00(\d+)\x00", lambda m: store[int(m.group(1))], t)
    return t


def strip_tags(t: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", t))


def split_chunks(text: str, limit: int = 3600):
    text = (text or "").strip() or "…"
    out = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = text.rfind(" ", 0, limit)
        if cut < limit // 2:
            cut = limit
        out.append(text[:cut].strip())
        text = text[cut:].strip()
    if text:
        out.append(text)
    return out


def webapp_url(uid: Optional[int] = None) -> str:
    """Mini App havolasi. Foydalanuvchi ma'lumotlari (XP, seriya) ?parametrlar bilan uzatiladi — server kerak emas."""
    if not WEBAPP_URL:
        return ""
    q = {"bot": BOT_USERNAME, "by": CREATOR_USERNAME}
    st = users.get(str(uid)) if uid is not None else None
    if isinstance(st, dict):
        xp = int(st.get("points") or 0)
        lvl, title, _nxt = level_of(xp)
        q.update({
            "xp": xp, "lvl": lvl, "streak": int(st.get("streak") or 0), "q": int(st.get("queries_count") or 0),
            "prem": 1 if is_premium(uid) else 0,
        })
    sp = urlsplit(WEBAPP_URL)
    base = dict(parse_qsl(sp.query))
    base.update({k: v for k, v in q.items() if v not in ("", None)})
    return urlunsplit((sp.scheme, sp.netloc, sp.path or "/", urlencode(base), ""))


def ikb(rows) -> InlineKeyboardMarkup:
    """rows: [[(text, 'cb:data' | ('url', 'https://..') | ('web', 'https://..')), ...], ...]"""
    kb = []
    for row in rows:
        r = []
        for text, target in row:
            if isinstance(target, tuple) and target[0] == "web":
                r.append(InlineKeyboardButton(text=text, web_app=WebAppInfo(url=target[1])))
            elif isinstance(target, tuple):
                r.append(InlineKeyboardButton(text=text, url=target[1]))
            else:
                r.append(InlineKeyboardButton(text=text, callback_data=target))
        kb.append(r)
    return InlineKeyboardMarkup(inline_keyboard=kb)


async def safe_answer(message: Message, text: str, kb=None, silent: bool = False):
    for attempt in range(2):
        try:
            return await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=kb, disable_notification=silent)
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after + 0.5)
        except TelegramBadRequest:
            try:
                return await message.answer(strip_tags(text), parse_mode=None, reply_markup=kb)
            except Exception as e2:
                log.error("send fallback failed: %s", e2)
                return None
        except TelegramForbiddenError:
            if message.from_user:
                mark_blocked(message.chat.id)
            return None
        except Exception as e:
            log.error("send failed: %s", e)
            return None
    return None


async def send_html(message: Message, text: str, kb=None):
    """O'zimiz yozgan HTML matn."""
    parts = split_chunks(text, 3800)
    last = None
    for i, p in enumerate(parts):
        last = await safe_answer(message, p, kb if i == len(parts) - 1 else None)
    return last


async def send_ai(message: Message, text: str, kb=None):
    """AI matni (markdown -> Telegram HTML)."""
    parts = split_chunks(text, 3500)
    last = None
    for i, p in enumerate(parts):
        last = await safe_answer(message, md_to_html(p), kb if i == len(parts) - 1 else None)
    return last


async def notify_admins(text: str, kb=None, silent: bool = False):
    for adm in ADMIN_IDS:
        try:
            await bot.send_message(adm, text, reply_markup=kb, disable_notification=silent)
        except Exception:
            pass


def mark_blocked(uid: int):
    st = get_user(uid)
    if not st.get("bot_blocked"):
        st["bot_blocked"] = True
        st["blocked_ts"] = time.time()
        st["blocked_at"] = ts()
        touch("users")
        log_act(uid, "BOT_BLOCKED", "Botni bloklagan")


def mark_unblocked(uid: int):
    """Xabar yetib borsa — foydalanuvchi blokdan chiqqan (yoki hech bloklamagan) demak."""
    st = users.get(str(uid))
    if st and st.get("bot_blocked"):
        st["bot_blocked"] = False
        st["unblocked_ts"] = time.time()
        touch("users")
        log_act(uid, "UNBLOCKED", "Xabar yetdi — blokdan chiqqan")
        return True
    return False


@contextlib.asynccontextmanager
async def typing(chat_id: int, action=ChatAction.TYPING):
    async def loop():
        try:
            while True:
                await bot.send_chat_action(chat_id, action)
                await asyncio.sleep(4)
        except asyncio.CancelledError:
            pass
        except Exception:
            pass

    t = asyncio.create_task(loop())
    try:
        yield
    finally:
        t.cancel()


# =====================================================================
# Keyboards
# =====================================================================

def rk(rows):
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=b) for b in row] for row in rows], resize_keyboard=True,
    )


MAIN_ROWS = [
    ["🚀 START", "🧠 AI TUTOR"],
    ["📚 FANLAR", "📝 NEW TEST"],
    ["🎯 QUIZ", "🖼 RASM TAHLILI"],
    ["🍅 POMODORO TIMER"],
    ["🎨 RASM YARATISH", "👨‍🏫 O‘QITUVCHI"],
    ["🇬🇧 IELTS", "🎯 EXAM MODE"],
    ["⭐ STARS SOTIB OLISH", "💎 TELEGRAM PREMIUM"],
    ["✨ GEMINI PRO (18 OYLIK)", "🎁 REFERRAL (STARS)"],
    ["👤 PROFIL", "📚 QO'LLANMALAR"],
    ["✍️ FIKR BILDIRISH", "👨‍💻 BOT YARATUVCHISI"],
    ["🎓 UNIVERSITET", "🤖 AI TANLASH"],
    ["🌐 TIL", "🔄 YANGI SUHBAT"],
    ["🎮 O'YINLAR", "🛍 DO'KON"],
    ["ℹ️ YORDAM"],
]


def get_kb(uid: int):
    keyboard = [[KeyboardButton(text=b) for b in row] for row in MAIN_ROWS]
    url = webapp_url(uid)
    if url:  # Pomodoro yonida «🚀 OPEN APP» — ilovani ochadi
        for row in keyboard:
            if any(b.text == "🍅 POMODORO TIMER" for b in row):
                row.append(KeyboardButton(text="🚀 OPEN APP", web_app=WebAppInfo(url=url)))
    if is_admin(uid):
        keyboard.append([KeyboardButton(text="👨‍💼 ADMIN PANEL")])
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)


POMODORO_ROWS = [["▶️ Pomodoro Boshlash", "⏹️ To'xtatish"], ["⬅️ MENU"]]
AI_ROWS = [["✨ Gemini", "⚡ Groq"], ["🧠 GPT", "🔵 Claude"], ["🚀 DeepSeek", "🤖 AVTO (tavsiya)"], ["⬅️ MENU"]]
IELTS_ROWS = [
    ["🗣 IELTS SPEAKING", "✍️ IELTS WRITING"],
    ["📖 IELTS READING", "🎧 IELTS LISTENING"],
    ["📊 IELTS FULL MOCK"], ["⬅️ MENU"],
]
DOMAIN_ROWS = [
    ["🧮 OLIY MATEMATIKA", "💻 DASTURLASH"],
    ["⚛️ FIZIKA", "🧪 KIMYO"],
    ["🧬 BIOLOGIYA", "📐 GEOMETRIYA"],
    ["📊 STATISTIKA", "💼 IQTISOD"],
    ["📚 BARCHA FANLAR"], ["⬅️ MENU"],
]
# Admin tugmalari foydalanuvchi tugmalaridan farq qilishi shart (oldingi kodda "📊 STATISTIKA" to'qnashgan edi)
ADMIN_ROWS = [
    ["📈 BOT HISOBOTI", "👥 FOYDALANUVCHILAR"],
    ["🟢 YAQINDAGI FAOLLAR", "🚫 BLOKLAGANLAR"],
    ["💬 NIMA SO'RADILAR", "🛒 XARID SO'ROVLARI"],
    ["🔥 QIZIQQANLAR", "🎁 REFERRAL TARIXI"],
    ["✍️ FIKRLAR", "🔎 QIDIRISH"],
    ["📚 PDF QO'LLANMA QO'SHISH", "📢 BARCHAGA XABAR"],
    ["⬅️ MENU"],
]
LANG_ROWS = [["🇺🇿 O'zbekcha", "🇷🇺 Русский", "🇬🇧 English"], ["⬅️ MENU"]]
UNI_ROWS = [
    ["🌍 XORIJDA O'QISH", "🏆 GRANT VA STIPENDIYA"],
    ["🇺🇿 O'ZBEKISTON OTMLARI", "🧭 YO'NALISH TANLASH"],
    ["📄 HUJJATLAR RO'YXATI", "✍️ MOTIVATION LETTER"],
    ["📅 QABUL REJASI", "💰 XARAJATLAR HISOBI"],
    ["🎤 INTERVYU MASHQI", "📊 SAT / IELTS / DTM"],
    ["⬅️ MENU"],
]

POMODORO_KB, AI_KB, IELTS_KB = rk(POMODORO_ROWS), rk(AI_ROWS), rk(IELTS_ROWS)
UNI_KB = rk(UNI_ROWS)
DOMAIN_KB, ADMIN_KB, LANG_KB = rk(DOMAIN_ROWS), rk(ADMIN_ROWS), rk(LANG_ROWS)

ALL_BUTTONS = {b for rows in (MAIN_ROWS, POMODORO_ROWS, AI_ROWS, IELTS_ROWS, DOMAIN_ROWS, ADMIN_ROWS, LANG_ROWS, UNI_ROWS)
               for row in rows for b in row} | {"👨‍💼 ADMIN PANEL"}

# =====================================================================
# Modes — har bir bo'lim o'z ovozi va vazifasiga ega
# =====================================================================

MODES = {
    "ai_tutor": {
        "provider": "gemini", "temp": 0.5, "max": 1400,
        "style": "You are a patient all-round tutor. Give the direct answer first in a sentence or two, then explain simply with one concrete example if it helps. Ask a tiny check question only when it truly helps learning.",
        "intro": ["🧠 <b>AI Tutor</b> shu yerda! Istagan savolingizni yozing — oddiy gapiring, men tushunaman 😊",
                  "🧠 Tutor rejimi yoqildi. Nimani tushunmayapsiz? Yozing, birga ochamiz ✨"],
    },
    "subjects": {
        "provider": "gemini", "temp": 0.4, "max": 1400,
        "style": "You are a school and university subject tutor. Identify the subject from the question, answer accurately, and keep explanations at student level.",
        "intro": ["📚 <b>Barcha fanlar</b> rejimi. Qaysi fandan bo'lsa ham savol bering, o'zim aniqlayman 👌"],
    },
    "math": {
        "provider": "gemini", "temp": 0.15, "max": 1800,
        "style": "You are a precise mathematician. Show the solution as short numbered steps, one idea per line, then the final result in **bold**. Double-check arithmetic before answering. Use plain text math (x^2, sqrt(x), integral) since the chat has no LaTeX.",
        "intro": ["🧮 <b>Matematika</b> rejimi. Misol yoki masalani yuboring — qadam-baqadam yechib beraman.\nMasalan: <code>2x² − 5x + 3 = 0</code>",
                  "🧮 Raqamlar olamiga xush kelibsiz! Tenglama, limit, integral — yuboring, yechamiz ✍️"],
    },
    "physics": {
        "provider": "gemini", "temp": 0.2, "max": 1600,
        "style": "You are a physics teacher. State the law/formula used, list given values with units, compute step by step, and check units and magnitude of the result.",
        "intro": ["⚛️ <b>Fizika</b> rejimi. Masala shartini yuboring: berilgan, formula, hisob — hammasini ko'rsatib beraman 🔭"],
    },
    "chemistry": {
        "provider": "gemini", "temp": 0.2, "max": 1600,
        "style": "You are a chemistry teacher. Balance equations, name substances correctly, explain the mechanism briefly, and show molar calculations step by step.",
        "intro": ["🧪 <b>Kimyo</b> rejimi. Reaksiya, tenglama yoki masalani yuboring — tenglashtirib, tushuntirib beraman ⚗️"],
    },
    "biology": {
        "provider": "gemini", "temp": 0.3, "max": 1500,
        "style": "You are a biology teacher. Use correct terminology but explain every term in plain words; use short analogies to make processes memorable.",
        "intro": ["🧬 <b>Biologiya</b> rejimi. Hujayradan ekotizimgacha — nimani bilmoqchisiz? 🌿"],
    },
    "geometry": {
        "provider": "gemini", "temp": 0.2, "max": 1600,
        "style": "You are a geometry teacher. Describe the figure in words, name the theorem used, and give a clear proof or calculation with reasons for every step.",
        "intro": ["📐 <b>Geometriya</b> rejimi. Masala shartini yozing (yoki rasmini yuboring) — chizmasini tasvirlab, isbotlab beraman 📏"],
    },
    "statistics": {
        "provider": "openai", "temp": 0.2, "max": 1500,
        "style": "You are a statistics tutor. Explain the intuition first, then the formula, then a small worked numeric example. Point out common mistakes.",
        "intro": ["📊 <b>Statistika</b> rejimi. O'rtacha, dispersiya, gipoteza testi — yuboring, ma'nosini ham tushuntiraman 📈"],
    },
    "economics": {
        "provider": "openai", "temp": 0.4, "max": 1500,
        "style": "You are an economics tutor. Connect theory to real everyday examples (prices, salaries, markets in Uzbekistan when relevant). Keep graphs described in words.",
        "intro": ["💼 <b>Iqtisod</b> rejimi. Talab-taklifdan inflyatsiyagacha — hayotiy misollar bilan tushuntiraman 💰"],
    },
    "programming": {
        "provider": "groq", "temp": 0.2, "max": 2200,
        "style": "You are a senior software engineer mentoring a student. Put working code first in a fenced code block with the language name, then a short explanation of how it works and any pitfalls or complexity. Prefer simple readable solutions.",
        "intro": ["💻 <b>Dasturlash</b> rejimi. Kod, xato (error) yoki g'oyani yuboring — tuzatib, tushuntirib beraman 🛠\nXato bo'lsa, matnini to'liq tashlang.",
                  "💻 Keling kod yozamiz! Qaysi til va nima qilmoqchisiz? 🚀"],
    },
    "new_test": {
        "provider": "groq", "temp": 0.5, "max": 2600,
        "style": "You are an assessment designer. Unless the user says otherwise, create 10 multiple-choice questions (A-D) on the requested topic with increasing difficulty. Put the full answer key with a one-line reason each at the very end under 'Javoblar'.",
        "intro": ["📝 <b>Test generatori</b>. Mavzu va sinfni yozing — masalan: <i>«9-sinf, Nyuton qonunlari, 10 ta savol»</i> — tayyor test va javoblar kalitini beraman ✅"],
    },
    "quiz": {
        "provider": "groq", "temp": 0.6, "max": 1200,
        "style": "You are a fast quiz master.",
        "intro": ["🎯 <b>Quiz</b>! Mavzuni yozing (masalan: <i>Ingliz tili grammatikasi</i>) — 5 ta savol beraman, javobni tugmalar bilan belgilaysiz 🏁"],
    },
    "image_analysis": {
        "provider": "gemini", "temp": 0.2, "max": 1600,
        "style": "You are a vision tutor. Read the image carefully (text, formulas, diagrams) and solve or explain what the student needs.",
        "intro": ["🖼 <b>Rasm tahlili</b>. Daftar, kitob sahifasi, masala yoki chizmani suratga olib yuboring. Savolingizni izoh (caption) qilib yozsangiz yanada aniq bo'ladi 📸"],
    },
    "teacher": {
        "provider": "claude", "temp": 0.5, "max": 2200,
        "style": "You are an experienced teacher and curriculum designer. Produce practical, classroom-ready material: lesson plans with timing, activities, assessment rubrics, homework. Be concrete, not generic.",
        "intro": ["👨‍🏫 <b>O'qituvchi yordamchisi</b>. Dars rejasi, topshiriq, baholash mezoni, ota-onalar yig'ilishi matni — nima kerak bo'lsa ayting 📋"],
    },
    "ielts": {
        "provider": "claude", "temp": 0.3, "max": 2200,
        "style": "You are a strict but encouraging IELTS examiner. When assessing, use the official four criteria with a band score for each and an overall band, quote the student's own mistakes and show corrected versions, then give 2-3 concrete improvement tips.",
        "intro": ["🇬🇧 <b>IELTS Examiner</b> rejimi. Kerakli bo'limni tanlang 👇"],
    },
    "exam": {
        "provider": "openai", "temp": 0.4, "max": 1800,
        "style": "You are an exam-prep strategist. Build realistic study plans with time blocks, prioritise high-yield topics, and give concrete tactics for time pressure and stress.",
        "intro": ["🎯 <b>Exam Mode</b>. Qaysi imtihon va qancha vaqt qoldi? Yozing — sizga shaxsiy tayyorgarlik rejasini tuzaman 🗓"],
    },
    "university": {
        "provider": "openai", "temp": 0.4, "max": 1800,
        "style": "You are a university admissions advisor for students from Uzbekistan. Help with choosing majors, universities, scholarships and documents. Be honest that deadlines and fees change and tell the student to confirm on official websites.",
        "intro": ["🎓 <b>Universitet markazi</b> ochildi! 🌍\n\nPastdagi panellardan birini tanlang 👇\n🌍 Xorijda o'qish • 🏆 Grant • 🇺🇿 O'zbekiston OTMlari • 🧭 Yo'nalish tanlash\n📄 Hujjatlar • ✍️ Motivation letter • 📅 Qabul rejasi • 💰 Xarajatlar • 🎤 Intervyu • 📊 SAT/IELTS/DTM\n\nYoki savolingizni to'g'ridan-to'g'ri yozing — birga yechamiz 😊"],
    },
    "pdf": {
        "provider": "gemini", "temp": 0.2, "max": 1800,
        "style": "You are a document analyst. Summarise the key points, list important facts and, if relevant, give 3 study questions.",
        "intro": [""],
    },
}

IELTS_SUBS = {
    "🗣 IELTS SPEAKING": ("speaking", "Focus: Speaking. Act as the examiner: ask one Part 1/2/3 question at a time and wait. When the student answers, assess fluency, vocabulary, grammar, pronunciation (from text clues) and give a band.",
                          "🗣 <b>Speaking</b> boshlandi. Men savol beraman, siz javob yozing. Tayyor bo'lsangiz «start» deb yozing 🎤"),
    "✍️ IELTS WRITING": ("writing", "Focus: Writing. If the student sends an essay, grade Task Achievement/Response, Coherence, Lexical Resource, Grammar with band scores and a rewritten stronger paragraph. If they ask for a topic, give a Task 1 or 2 prompt.",
                         "✍️ <b>Writing</b> rejimi. Esseyingizni yuboring — 4 mezon bo'yicha baholayman. Mavzu kerak bo'lsa «mavzu bering» deb yozing 📝"),
    "📖 IELTS READING": ("reading", "Focus: Reading. Provide a short academic passage (~250 words) with 5 questions (T/F/NG, matching, fill-in) and wait for answers before revealing the key.",
                         "📖 <b>Reading</b> rejimi. «Matn ber» deb yozing — mashq tayyorlayman 🔍"),
    "🎧 IELTS LISTENING": ("listening", "Focus: Listening. Since there is no audio, give a realistic transcript-style dialogue to read aloud/mentally, then questions. Reveal answers only after the student replies.",
                           "🎧 <b>Listening</b> (matn ko'rinishida). «Boshlaymiz» deb yozing 🎧"),
    "📊 IELTS FULL MOCK": ("mock", "Focus: Full mock. Run a compact mock section by section (Listening, Reading, Writing, Speaking), one section per turn, and finish with an estimated overall band.",
                           "📊 <b>Full Mock</b>. Bo'limma-bo'lim o'tamiz. «Boshlash» deb yozing 🏁"),
}

UNI_COMMON = " Be honest that deadlines, fees and requirements change often and tell the student to confirm on the official website. Never invent scholarship names, amounts or deadlines. Ask at most one clarifying question (grade, GPA, budget, target country) if it is really needed, otherwise answer right away."

UNI_SUBS = {
    "🌍 XORIJDA O'QISH": ("abroad", "Focus: studying abroad. Compare countries (USA, UK, Germany, South Korea, Turkey, Poland, Hungary, UAE, Malaysia, Russia, Kazakhstan etc.) by tuition, living cost, language, scholarships, visa and work options, for a student from Uzbekistan. Give a short pros/cons list per country and a clear recommendation." + UNI_COMMON,
                          "🌍 <b>Xorijda o'qish</b> paneli 🛫\nQaysi mamlakat qiziqtiradi? Yoki byudjetingiz va ballingizni yozing — eng mos 3 ta mamlakatni taklif qilaman ✨"),
    "🏆 GRANT VA STIPENDIYA": ("grants", "Focus: grants and scholarships. Suggest well-known real programs open to Uzbek students (e.g. Chevening, Fulbright, DAAD, Erasmus Mundus, Korean GKS, Turkiye Burslari, Stipendium Hungaricum, Presidential/state scholarships, university-specific aid) matching the student's profile. Explain eligibility, what is usually covered, typical application steps and common mistakes." + UNI_COMMON,
                               "🏆 <b>Grant va stipendiya</b> paneli 💰\nSinfingiz, o'rtacha balingiz va qiziqqan sohangizni yozing — mos grantlarni tanlab beraman 🎯"),
    "🇺🇿 O'ZBEKISTON OTMLARI": ("uzbek_unis", "Focus: universities in Uzbekistan. Explain DTM entrance exam logic, subject blocks, grant vs contract, quotas, special exams, joint-programme and foreign branch universities (Westminster, Inha, TTPU, Webster, MDIST, etc.), and how to choose by direction and budget. Be clear that scores and quotas change every year." + UNI_COMMON,
                                "🇺🇿 <b>O'zbekiston OTMlari</b> paneli 🏛\nYo'nalish yoki ballingizni yozing — qaysi universitetlar mos kelishini birga ko'ramiz 📚"),
    "🧭 YO'NALISH TANLASH": ("major", "Focus: choosing a major and career. Ask 3 short questions in one message about interests, strongest subjects and goals if the student has not given them, then suggest 3-4 majors with career paths, salary outlook in general terms, and which subjects to strengthen." + UNI_COMMON,
                              "🧭 <b>Yo'nalish tanlash</b> paneli 🔍\nQaysi fanlar yoqadi? Kelajakda nima bo'lishni xohlaysiz? Yozing — eng mos kasblarni topamiz 🌟"),
    "📄 HUJJATLAR RO'YXATI": ("documents", "Focus: application documents. Produce a clear checklist for the country/university the student names (passport, transcript, certificates, language scores, recommendation letters, apostille/translation, financial proof, visa documents) with a realistic order of steps and typical processing time notes." + UNI_COMMON,
                               "📄 <b>Hujjatlar ro'yxati</b> paneli ✅\nQaysi mamlakat yoki universitetga hujjat topshirmoqchisiz? Yozing — to'liq checklist tuzib beraman 📋"),
    "✍️ MOTIVATION LETTER": ("letter", "Focus: motivation letter, personal statement and essays. If the student sends a draft, give honest feedback (structure, specificity, voice, cliches) and a rewritten stronger version of the weakest paragraph. If they ask to write one, first ask for their story points in one message, then write a personal, concrete, non-generic letter. Keep it in the language they request." + UNI_COMMON,
                             "✍️ <b>Motivation letter</b> paneli 💌\nQoralamangizni yuboring — tahlil qilib, kuchaytirib beraman. Yoki «yozib ber» deb yozing, avval bir necha savol beraman 😊"),
    "📅 QABUL REJASI": ("timeline", "Focus: admission timeline. Build a month-by-month plan from now to the deadline (tests, documents, applications, scholarships, visa), with priorities and buffer time. Ask for the target intake and country only if missing." + UNI_COMMON,
                         "📅 <b>Qabul rejasi</b> paneli ⏰\nQaysi yil va qaysi mamlakatga topshirmoqchisiz? Yozing — oyma-oy reja tuzaman 🗓"),
    "💰 XARAJATLAR HISOBI": ("budget", "Focus: cost planning. Estimate tuition, housing, food, transport, insurance, visa and exam fees for the chosen country/city in a simple table-like bullet list (no markdown tables), give a monthly and yearly total range, and ways to reduce cost (scholarships, dorms, part-time work rules)." + UNI_COMMON,
                              "💰 <b>Xarajatlar hisobi</b> paneli 🧮\nQaysi mamlakat va shahar? Taxminiy yillik byudjetni hisoblab beraman 💵"),
    "🎤 INTERVYU MASHQI": ("interview", "Focus: admission interview practice. Act as an admissions officer: ask ONE question at a time, wait for the answer, then give brief feedback (clarity, honesty, specificity) and a better sample answer before the next question. After 5 questions give an overall assessment." + UNI_COMMON,
                            "🎤 <b>Intervyu mashqi</b> paneli 🎯\nTayyor bo'lsangiz «boshlaymiz» deb yozing — men komissiya a'zosi bo'laman, siz javob bering 💪"),
    "📊 SAT / IELTS / DTM": ("tests", "Focus: admission tests (SAT, IELTS, TOEFL, DTM, Duolingo). Explain the format, scoring, which test fits which target, a realistic study plan with weekly hours, resources, and time-management tactics on exam day." + UNI_COMMON,
                              "📊 <b>SAT / IELTS / DTM</b> paneli 📈\nQaysi imtihonga tayyorlanyapsiz va qancha vaqt qoldi? Yozing — shaxsiy reja tuzib beraman 🔥"),
}

SUBJECT_BUTTONS = {
    "🧮 OLIY MATEMATIKA": "math", "💻 DASTURLASH": "programming", "⚛️ FIZIKA": "physics",
    "🧪 KIMYO": "chemistry", "🧬 BIOLOGIYA": "biology", "📐 GEOMETRIYA": "geometry",
    "📊 STATISTIKA": "statistics", "💼 IQTISOD": "economics", "📚 BARCHA FANLAR": "subjects",
}
MODE_BUTTONS = {
    "🧠 AI TUTOR": "ai_tutor", "📝 NEW TEST": "new_test", "🖼 RASM TAHLILI": "image_analysis",
    "👨‍🏫 O‘QITUVCHI": "teacher", "🎯 EXAM MODE": "exam",
}

LANG_NAMES = {"uz": "Uzbek (Latin script)", "ru": "Russian", "en": "English"}


def build_system(st: dict, mode: str) -> str:
    cfg = MODES.get(mode, MODES["ai_tutor"])
    lang = LANG_NAMES.get(st.get("lang", "uz"), "Uzbek (Latin script)")
    first = (st.get("full_name") or "").split(" ")[0]
    sub = ""
    if mode == "ielts" and st.get("sub"):
        sub = "\n" + next((v[1] for v in IELTS_SUBS.values() if v[0] == st["sub"]), "")
    elif mode == "university" and st.get("sub"):
        sub = "\n" + next((v[1] for v in UNI_SUBS.values() if v[0] == st["sub"]), "")
    return (
        "You are EduMind AI, a warm and natural study companion inside a Telegram bot. "
        f"Always reply in {lang}. The student's first name is {first or 'friend'} — use it rarely, only when it feels natural. "
        "Sound like a smart, kind older friend or teacher: no robotic templates, no repeated stock openings, no numbered section headers unless the task needs them. "
        "Be as short as the question allows and go deeper only when asked or when the topic needs it. "
        "Be warm, polite and encouraging: greet kindly, praise the student's effort, and use friendly fitting emojis generously (roughly one per sentence or two, varied, never spammy). Telegram formatting only: **bold** for key terms, `code` for formulas/code, ``` fenced blocks for code, '•' bullets; never markdown tables or # headers. "
        "Never invent facts or sources; say so when unsure.\n"
        f"MODE INSTRUCTIONS: {cfg['style']}{sub}"
    )


# =====================================================================
# AI providers (hedged race = eng tez javob)
# =====================================================================

def configured(name: str) -> bool:
    return bool(CLIENTS.get(name))


async def call_openai_compatible(name, messages, temp, max_tokens):
    resp = await CLIENTS[name].chat.completions.create(
        model=MODELS[name], messages=messages, temperature=temp, max_tokens=max_tokens,
    )
    return resp.choices[0].message.content or ""


async def call_claude(messages, temp, max_tokens):
    system = messages[0]["content"]
    msgs = [m for m in messages[1:] if m["role"] in ("user", "assistant")]
    resp = await claude_client.messages.create(
        model=MODELS["claude"], system=system, messages=msgs, max_tokens=max_tokens, temperature=temp,
    )
    return "".join(getattr(x, "text", "") for x in resp.content)


def gemini_config(system, temp, max_tokens):
    kwargs = dict(system_instruction=system, temperature=temp, max_output_tokens=max_tokens)
    name = MODELS["gemini"]
    if "2.5" in name and "flash" in name and types is not None:
        try:
            kwargs["thinking_config"] = types.ThinkingConfig(thinking_budget=0)  # tezlik uchun
        except Exception:
            pass
    return types.GenerateContentConfig(**kwargs)


async def call_gemini(messages, temp, max_tokens):
    system = messages[0]["content"]
    contents = [
        types.Content(role="user" if m["role"] == "user" else "model", parts=[types.Part.from_text(text=m["content"])])
        for m in messages[1:]
    ]
    res = await gemini_client.aio.models.generate_content(
        model=MODELS["gemini"], contents=contents, config=gemini_config(system, temp, max_tokens),
    )
    return res.text or ""


async def call_provider(name, messages, temp, max_tokens):
    if name == "gemini":
        return await call_gemini(messages, temp, max_tokens)
    if name == "claude":
        return await call_claude(messages, temp, max_tokens)
    return await call_openai_compatible(name, messages, temp, max_tokens)


def _metric(kind: str, name: str):
    metrics.setdefault(kind, {})
    metrics[kind][name] = metrics[kind].get(name, 0) + 1


async def run_race(candidates, messages, temp=0.4, max_tokens=1500):
    """Birinchi provayder sekin yoki xato bersa, zaxirasi parallel ishga tushadi; kim birinchi javob bersa — o'sha."""
    cands = [c for c in candidates if configured(c)]
    if not cands:
        raise RuntimeError("Hech bir AI provayder sozlanmagan (.env ni tekshiring)")
    tasks: dict = {}
    errors = []
    nxt = 0

    def launch():
        nonlocal nxt
        name = cands[nxt]
        nxt += 1
        t = asyncio.create_task(asyncio.wait_for(call_provider(name, messages, temp, max_tokens), PROVIDER_TIMEOUT))
        tasks[t] = name

    launch()
    try:
        while tasks:
            wait_for = HEDGE_DELAY if nxt < len(cands) else None
            done, _ = await asyncio.wait(list(tasks.keys()), timeout=wait_for, return_when=asyncio.FIRST_COMPLETED)
            for t in done:
                name = tasks.pop(t)
                try:
                    txt = (t.result() or "").strip()
                    if txt:
                        _metric("ok", name)
                        return txt, name
                    errors.append(f"{name}: bo'sh javob")
                except Exception as e:
                    _metric("fail", name)
                    errors.append(f"{name}: {clip(e, 80)}")
            if nxt < len(cands) and (not done or not tasks):
                launch()
        raise RuntimeError(" | ".join(errors) or "AI javob bermadi")
    finally:
        for t in tasks:
            t.cancel()


def candidate_order(st: dict, mode: str):
    pref = st.get("provider", "auto")
    first = pref if pref in CLIENTS else MODES.get(mode, MODES["ai_tutor"])["provider"]
    order = [first]
    if AUTO_FALLBACK:
        order += [p for p in FAST_FALLBACK if p != first]
    return order


def clean_history(hist):
    h = [m for m in hist if m.get("role") in ("user", "assistant") and m.get("content")][-6:]
    while h and h[0]["role"] != "user":
        h.pop(0)
    return h


async def ask_ai(uid: int, prompt: str, mode: Optional[str] = None, remember_it: bool = True):
    st = get_user(uid)
    mode = mode or st.get("mode", "ai_tutor")
    cfg = MODES.get(mode, MODES["ai_tutor"])
    messages = [{"role": "system", "content": build_system(st, mode)}]
    messages += clean_history(st.get("history", []))
    messages.append({"role": "user", "content": prompt})
    t0 = time.perf_counter()
    answer, name = await run_race(candidate_order(st, mode), messages, cfg["temp"], cfg["max"])
    ms = int((time.perf_counter() - t0) * 1000)
    metrics["ms_total"] = metrics.get("ms_total", 0) + ms
    metrics["count"] = metrics.get("count", 0) + 1
    st["last_provider"] = name
    if remember_it:
        h = st.setdefault("history", [])
        h.append({"role": "user", "content": clip(prompt, 1500)})
        h.append({"role": "assistant", "content": clip(answer, 1500)})
        del h[:-12]
        st["queries_count"] = st.get("queries_count", 0) + 1
        st["last_query"] = clip(prompt, 100)
        add_xp(st, 10)
    touch("users", "metrics")
    return answer, name, ms


async def gemini_media(data: bytes, mime: str, prompt: str, system: str, max_tokens=1800):
    if not gemini_client:
        raise RuntimeError("Gemini sozlanmagan")
    res = await gemini_client.aio.models.generate_content(
        model=GEMINI_VISION_MODEL,
        contents=[types.Part.from_bytes(data=data, mime_type=mime), prompt],
        config=gemini_config(system, 0.3, max_tokens),
    )
    return res.text or ""


async def generate_image(prompt: str) -> bytes:
    if not gemini_client:
        raise RuntimeError("Gemini sozlanmagan")
    res = await gemini_client.aio.models.generate_images(
        model=GEMINI_IMAGE_MODEL, prompt=prompt, config=types.GenerateImagesConfig(number_of_images=1),
    )
    return res.generated_images[0].image.image_bytes


# =====================================================================
# Bot, middleware, filters
# =====================================================================

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
FLOOD: dict = {}
BUSY: set = set()
BG_TASKS: set = set()
QUIZ: dict = {}
active_pomodoros: dict = {}
pending_broadcast: dict = {}


def spawn(coro):
    t = asyncio.create_task(coro)
    BG_TASKS.add(t)
    t.add_done_callback(BG_TASKS.discard)
    return t


class AdminOnly(BaseFilter):
    async def __call__(self, event) -> bool:
        return bool(getattr(event, "from_user", None) and event.from_user.id in ADMIN_IDS)


class BroadcastWaiting(BaseFilter):
    async def __call__(self, message: Message) -> bool:
        if not message.from_user or message.from_user.id not in ADMIN_IDS:
            return False
        if users.get(str(message.from_user.id), {}).get("admin_action") != "broadcast":
            return False
        if message.text and (message.text in ALL_BUTTONS or message.text.startswith("/")):
            return False
        return True


class Gate(BaseMiddleware):
    async def __call__(self, handler, event, data):
        user = getattr(event, "from_user", None)
        if user and not user.is_bot:
            st = get_user(user.id, user)
            now = time.time()
            st["last_seen"] = now
            if st.get("bot_blocked"):
                st["bot_blocked"] = False
                st["unblocked_ts"] = now
            if not is_admin(user.id):
                if st.get("banned"):
                    if isinstance(event, Message):
                        await event.answer("⛔ Kechirasiz, sizning hisobingiz cheklangan. Savol bo'lsa adminga murojaat qiling.")
                    return
                if isinstance(event, Message):
                    if now - FLOOD.get(user.id, 0) < 0.5:
                        return
                    FLOOD[user.id] = now
            touch("users")
        return await handler(event, data)


dp.message.outer_middleware(Gate())
dp.callback_query.outer_middleware(Gate())


@dp.errors()
async def on_error(event):
    log.exception("Handler error: %s", event.exception)
    return True


# =====================================================================
# Commerce
# =====================================================================

PRODUCTS = {
    "stars": {
        "emoji": "⭐", "title": "Telegram Stars", "grants": False,
        "opts": [("50 ta Stars", "13 999 so'm"), ("100 ta Stars", "23 999 so'm"), ("200 ta Stars", "46 999 so'm")],
        "pitch": ["⭐ <b>Telegram Stars</b> — rasmiy, tez va xavfsiz. Qaysi paket sizga to'g'ri keladi? 👇",
                  "⭐ Stars kerakmi? Mana tayyor paketlar — bittasini bossangiz, qolganini o'zim hal qilaman 😉"],
    },
    "premium": {
        "emoji": "💎", "title": "Telegram Premium", "grants": False,
        "opts": [("1 oylik", "45 999 so'm"), ("1 yillik", "282 999 so'm")],
        "pitch": ["💎 <b>Telegram Premium</b> — katta fayllar, tezroq yuklash, maxsus stikerlar va yana ko'p narsa. Muddatini tanlang 👇",
                  "💎 Premium'ga tayyormisiz? Yillik tarif ancha foydali chiqadi 😉 Tanlang 👇"],
    },
    "gemini": {
        "emoji": "✨", "title": "Gemini Pro (18 oylik)", "grants": True,
        "opts": [("18 oylik Gemini Pro", "79 000 so'm / oy")],
        "pitch": ["✨ <b>Google Gemini Pro (18 oylik)</b>\nKuchli AI, rasm yaratish, video tahlil — va botimizda ham <b>Premium</b> status ochiladi (rasm yaratish + video tahlil) 🎁",
                  "✨ <b>Gemini Pro</b> bilan o'qish boshqa darajaga chiqadi. Bu obunada botdagi Premium imkoniyatlar ham ochiladi 🚀"],
    },
}


def product_kb(key: str):
    p = PRODUCTS[key]
    rows = [[(f"{p['emoji']} {t} — {price}", f"buy|{key}|{i}")] for i, (t, price) in enumerate(p["opts"])]
    if key == "stars":
        rows.append([("✍️ Boshqa miqdor yozaman", "buy|stars|custom")])
    rows.append([("🛍 Barcha xizmatlar", "shop|menu")])
    return ikb(rows)


PRODUCT_INFO = {
    "stars": {
        "what": (
            "⭐ <b>Telegram Stars — nima bu?</b>\n\n"
            "Telegram ichidagi rasmiy valyuta: botlar, mini-ilovalar va kanallardagi pullik imkoniyatlar hamda sovg'alar uchun ishlatiladi.\n\n"
            "✅ Rasmiy va xavfsiz\n"
            "✅ 50 / 100 / 200 tayyor paket yoki o'zingiz yozgan miqdor (50 dan 20 000 gacha)\n"
            "✅ Narx oldindan ko'rinadi — yashirin to'lov yo'q 💙"
        ),
        "faq": [
            ("Stars qaysi akkauntga yuboriladi?", "Chek bilan birga izohga <b>@username</b>ingizni yozing — Stars shu akkauntga yuboriladi."),
            ("Boshqa miqdor kerak bo'lsa-chi?", "Hech gap yo'q! 📦 Paketlar bo'limida «Boshqa miqdor yozaman» ni bosing, 50 dan 20 000 gacha sonni yozing — narxni o'zim hisoblab beraman."),
            ("Qancha vaqtda keladi?", "Chek adminga yetgach, u qo'lda tekshirib yuboradi — imkon qadar tez. Holatni «🧾 Buyurtmalarim»da kuzatishingiz mumkin."),
        ],
    },
    "premium": {
        "what": (
            "💎 <b>Telegram Premium — nima beradi?</b>\n\n"
            "Katta fayllar yuklash, tezroq yuklab olish, maxsus stikerlar va boshqa ko'plab qulayliklar.\n\n"
            "📅 <b>1 oylik</b> — sinab ko'rish uchun\n"
            "🏆 <b>1 yillik</b> — eng foydali: 12 oy × 45 999 = 551 988 so'm bo'lardi, yillik tarif esa 282 999 so'm — deyarli ikki baravar arzon!"
        ),
        "faq": [
            ("Premium qaysi akkauntga ulanadi?", "Chek bilan birga izohga <b>@username</b>ingizni yozing — obuna shu akkauntga ulanadi."),
            ("Oylik va yillik farqi nima?", "Imkoniyatlar bir xil, farq faqat muddat va narxda. Uzoq foydalansangiz, yillik tarif ancha tejaydi 😉"),
            ("Savolim bor, kimga yozaman?", "Pastdagi «🙋 Admin bilan gaplashish» tugmasi orqali to'g'ridan-to'g'ri yozing — albatta javob beramiz."),
        ],
    },
    "gemini": {
        "what": (
            "✨ <b>Google Gemini Pro — 18 oylik obuna</b>\n\n"
            "Kuchli AI yordamchi: murakkab savollar, rasm yaratish, video tahlil — o'qish va ish uchun ajoyib vosita.\n\n"
            "🎁 <b>Bonus:</b> buyurtma tasdiqlanishi bilan botimizdagi <b>Premium</b> status ham ochiladi — rasm yaratish va video tahlil sizniki!\n"
            "💰 Narx: <b>79 000 so'm / oy</b>"
        ),
        "faq": [
            ("Botdagi Premium qachon ochiladi?", "Admin buyurtmangizni tasdiqlashi bilan avtomatik ochiladi va sizga xabar keladi 🎉"),
            ("Obunani qanday olaman?", "📦 Paketlar → tanlang → to'lang → chek skrinshotini yuboring. Keyingi qadamlarni admin sizga yozib aytadi."),
            ("Savolim bor, kimga yozaman?", "Pastdagi «🙋 Admin bilan gaplashish» tugmasi orqali to'g'ridan-to'g'ri yozing."),
        ],
    },
}

TRUST_LINE = "🛡 <i>Har bir buyurtma admin tomonidan qo'lda tekshiriladi — ishonchli va shaffof.</i>"
ORDER_STATUS = {"pending": "⏳ Kutilmoqda", "done": "✅ Bajarildi", "rejected": "❌ Bajarilmadi"}


def admin_btn():
    return ("🙋 Admin bilan gaplashish", ("url", f"https://t.me/{CREATOR_USERNAME}"))


def shop_home_view():
    text = (
        "🛍 <b>EduMind Do'koni</b> ✨\n\n"
        "Hammasi oddiy: xizmatni tanlang → karta orqali to'lang → chek skrinshotini yuboring → admin tasdiqlaydi 🙌\n\n"
        "Qaysi biri sizni qiziqtiradi? 👇"
    )
    kb = ikb([
        [("⭐ Telegram Stars", "sh|stars")],
        [("💎 Telegram Premium", "sh|premium")],
        [("✨ Gemini Pro (18 oylik)", "sh|gemini")],
        [("🧾 Buyurtmalarim", "my|list")],
        [admin_btn()],
    ])
    return text, kb


def product_view(key: str):
    p = PRODUCTS[key]
    extra = f"\n\n🔥 <i>{esc(GEMINI_PROMO_NOTE)}</i>" if key == "gemini" and GEMINI_PROMO_NOTE else ""
    text = pick(p["pitch"]) + extra + "\n\n" + TRUST_LINE
    kb = ikb([
        [("📦 Paketlar va narxlar", f"sp|{key}|pk")],
        [("ℹ️ Nima beradi", f"sp|{key}|info"), ("💳 To'lov qanday", f"sp|{key}|pay")],
        [("❓ Savol-javob", f"sp|{key}|faq"), ("🧾 Buyurtmalarim", "my|list")],
        [admin_btn()],
        [("⬅️ Do'konga qaytish", "sh|menu")],
    ])
    return text, kb


def packages_kb(key: str):
    p = PRODUCTS[key]
    rows = [[(f"{p['emoji']} {t} — {price}", f"buy|{key}|{i}")] for i, (t, price) in enumerate(p["opts"])]
    if key == "stars":
        rows.append([("✍️ Boshqa miqdor yozaman", "buy|stars|custom")])
    rows.append([("⬅️ Orqaga", f"sh|{key}")])
    return ikb(rows)


def section_view(key: str, sec: str):
    p, info = PRODUCTS[key], PRODUCT_INFO[key]
    back = [("⬅️ Orqaga", f"sh|{key}")]
    if sec == "pk":
        return f"📦 <b>{esc(p['title'])} — paketlar</b>\nBittasini bossangiz, buyurtma darrov rasmiylashadi 👇", packages_kb(key)
    if sec == "info":
        return info["what"], ikb([[("📦 Paketlar va narxlar", f"sp|{key}|pk")], back])
    if sec == "pay":
        text = (
            "💳 <b>To'lov qanday amalga oshadi?</b>\n\n"
            "1️⃣ 📦 Paketni tanlang\n"
            "2️⃣ Karta ma'lumotlari buyurtmadan so'ng shu yerda chiqadi\n"
            "3️⃣ Payme, Click yoki istalgan bank ilovasidan o'tkazing 📲\n"
            "4️⃣ Chek <b>skrinshotini</b> shu botga yuboring (izohga @username)\n"
            "5️⃣ Admin tekshiradi va shu yerning o'zida xabar beradi 🔔\n\n"
            "Savol bo'lsa — admin doim aloqada 💙"
        )
        return text, ikb([[("📦 Paketlar va narxlar", f"sp|{key}|pk")], back])
    # faq ro'yxati
    rows = [[(f"❓ {q}", f"fq|{key}|{i}")] for i, (q, _a) in enumerate(info["faq"])]
    rows.append(back)
    return f"❓ <b>{esc(p['title'])} — tez-tez so'raladigan savollar</b>\nBittasini tanlang 👇", ikb(rows)


def faq_view(key: str, i: int):
    q, a = PRODUCT_INFO[key]["faq"][i]
    text = f"❓ <b>{q}</b>\n\n{a}"
    return text, ikb([
        [("❓ Boshqa savollar", f"sp|{key}|faq"), ("📦 Paketlar", f"sp|{key}|pk")],
        [("⬅️ Orqaga", f"sh|{key}")],
    ])


def orders_view(uid: int):
    mine = [r for r in purchases if r.get("uid") == uid][:5]
    if not mine:
        return ("📭 <b>Hali buyurtmangiz yo'q.</b>\nBirinchi buyurtmangizni berishga tayyormisiz? 😊",
                ikb([[("🛍 Do'konga o'tish", "sh|menu")]]))
    lines, rows = [], []
    for r in mine:
        st_txt = ORDER_STATUS.get(r.get("status"), "✅ Bajarildi")
        need = r.get("status") == "pending" and not r.get("receipt_at")
        lines.append(
            f"<b>#{r['id']}</b> • {esc(r.get('service'))}\n"
            f"   🧾 {esc(r.get('details'))}\n"
            f"   {st_txt}" + (" • 📸 chek kutilmoqda" if need else "") + f" • 🕒 {esc(r.get('time'))}"
        )
        if need:
            rows.append([(f"📸 #{r['id']} uchun chek yuborish", f"rc|{r['id']}")])
    rows.append([("🛍 Do'kon", "sh|menu")])
    return "🧾 <b>Buyurtmalarim</b>\n\n" + "\n\n".join(lines), ikb(rows)


async def edit_panel(cb: CallbackQuery, text: str, kb):
    """Panelni joyida yangilaydi (ichma-ich navigatsiya chat'ni to'ldirib yubormaydi)."""
    try:
        await cb.message.edit_text(text, reply_markup=kb)
    except TelegramBadRequest as e:
        if "not modified" in str(e).lower():
            return
        await send_html(cb.message, text, kb)
    except Exception:
        await send_html(cb.message, text, kb)


async def show_product(target: Message, uid: int, key: str):
    add_lead(uid, f"{key}_view", PRODUCTS[key]["title"])
    text, kb = product_view(key)
    await send_html(target, text, kb)


def fmt_card(num: str) -> str:
    n = re.sub(r"\D", "", str(num))
    return " ".join(n[i:i + 4] for i in range(0, len(n), 4))


STAR_PRESETS = {50: 13999, 100: 23999, 200: 46999}


def money(n: int) -> str:
    return f"{int(n):,}".replace(",", " ") + " so'm"


def stars_price(n: int) -> int:
    if n in STAR_PRESETS:
        return STAR_PRESETS[n]
    rate = 280 if n < 100 else (240 if n < 200 else 235)
    return int(round(n * rate, -3)) - 1


def stars_intent(text: str) -> Optional[int]:
    """'stars 100 olaman', '150 ta stars sotib olmoqchiman' kabi erkin yozuvni tushunadi."""
    low = text.lower()
    m = re.search(r"(\d{2,5})\s*(?:ta\s*)?(?:stars?|yulduz)", low) or re.search(r"(?:stars?|yulduz)\w*\s*(?:ta\s*)?(\d{2,5})", low)
    if m and re.search(r"ol(?:a|i)|sotib|kerak|buyur|xohla", low):
        return int(m.group(1))
    return None


def payment_text(title: str, details: str, req_id: int) -> str:
    return (
        f"✅ <b>Buyurtma #{req_id} qabul qilindi!</b> 🎉\n\n"
        f"📦 {esc(title)}\n🧾 {esc(details)}\n\n"
        "💳 <b>To'lov ma'lumotlari</b>\n"
        f"🔢 Karta raqami: <code>{esc(CARD_NUMBER)}</code>\n"
        f"      <i>({esc(fmt_card(CARD_NUMBER))})</i>\n"
        f"👤 Karta egasi: <b>{esc(CARD_OWNER)}</b>\n"
        "🏦 UzCard / Humo • Payme, Click, Paynet va boshqa ilovalar orqali o'tkazish mumkin 📲\n\n"
        "📸 <b>Keyingi qadam:</b> to'lov chekining <b>skrinshotini</b> shu botning o'ziga yuboring — u avtomatik adminga yetib boradi.\n"
        "✍️ Chek bilan birga izohga Telegram <b>@username</b>ingizni yozing — Stars/Premium shu akkauntga yuboriladi.\n\n"
        "Admin tasdiqlashi bilan shu yerning o'zida xabar olasiz 🔔💙"
    )


def payment_kb(req_id: int):
    return ikb([
        [("📸 Chek yuborish", f"rc|{req_id}")],
        [("🛍 Boshqa xizmatlar", "shop|menu"), ("🙋 Admin", ("url", f"https://t.me/{CREATOR_USERNAME}"))],
    ])


async def create_purchase(uid: int, product: str, details: str) -> dict:
    st = get_user(uid)
    p = PRODUCTS[product]
    for r in purchases[:20]:  # takroriy so'rovdan saqlanish
        if r.get("uid") == uid and r.get("product") == product and r.get("details") == details and r.get("status") == "pending" \
                and time.time() - (r.get("epoch") or 0) < 600:
            st["awaiting"] = f"receipt:{r['id']}"
            touch("users")
            return r
    ids = []
    for r in purchases:
        with contextlib.suppress(Exception):
            ids.append(int(r.get("id")))
    req_id = max(ids + [0]) + 1
    req = {
        "id": req_id, "time": ts(), "epoch": time.time(), "uid": uid,
        "full_name": st.get("full_name"), "username": st.get("username"),
        "product": product, "service": p["title"], "details": clip(details, 200), "status": "pending",
    }
    purchases.insert(0, req)
    del purchases[500:]
    st["awaiting"] = f"receipt:{req_id}"
    touch("purchases", "users")
    with contextlib.suppress(Exception):
        log_act(uid, "PURCHASE_REQ", f"{p['title']}: {details}")
    with contextlib.suppress(Exception):
        add_lead(uid, f"{product}_buy", details)
    try:
        await notify_admins(
            f"🛒 <b>Yangi buyurtma #{req_id}</b>\n\n"
            f"👤 {user_link(uid, st.get('full_name'))} • {esc(uname_of(st))}\n"
            f"🆔 <code>{uid}</code>\n"
            f"{p['emoji']} {esc(p['title'])}\n🧾 {esc(details)}\n"
            f"💳 Karta: <code>{esc(CARD_NUMBER)}</code> • {esc(CARD_OWNER)}\n\n"
            "📸 <i>Mijoz chek skrinshotini yuborganda alohida xabar bo'lib keladi.</i>",
            ikb([[("✅ Bajarildi", f"pr|ok|{req_id}"), ("❌ Rad etish", f"pr|no|{req_id}")]]),
        )
    except Exception as e:
        log.warning("notify admins failed: %s", e)
    return req


async def place_order(message: Message, uid: int, key: str, details: str) -> dict:
    """Buyurtmani yaratadi va to'lov ma'lumotini yuboradi. Saqlashda nimadir buzilsa ham mijoz kartani oladi, savdo yo'qolmaydi."""
    req = None
    try:
        req = await create_purchase(uid, key, details)
    except Exception as e:
        log.exception("create_purchase failed: %s", e)
        req = {
            "id": int(time.time()) % 1000000, "time": ts(), "epoch": time.time(), "uid": uid,
            "full_name": get_user(uid).get("full_name"), "username": get_user(uid).get("username"),
            "product": key, "service": PRODUCTS[key]["title"], "details": clip(details, 200), "status": "pending",
        }
        with contextlib.suppress(Exception):
            purchases.insert(0, req)
            get_user(uid)["awaiting"] = f"receipt:{req['id']}"
            touch("purchases", "users")
        with contextlib.suppress(Exception):
            await notify_admins(
                f"⚠️ <b>Buyurtma #{req['id']} (zaxira rejim)</b>\n👤 {user_link(uid, req['full_name'])} • <code>{uid}</code>\n"
                f"📦 {esc(PRODUCTS[key]['title'])} — {esc(details)}\n<code>{esc(clip(repr(e), 300))}</code>",
                ikb([[("✅ Bajarildi", f"pr|ok|{req['id']}"), ("❌ Rad etish", f"pr|no|{req['id']}")]]),
            )
    text = payment_text(PRODUCTS[key]["title"], details, req["id"])
    sent = await send_html(message, text, payment_kb(req["id"]))
    if sent is None:  # tugma yoki HTML muammosi bo'lsa ham karta ma'lumoti albatta yetsin
        with contextlib.suppress(Exception):
            await message.answer(strip_tags(text), parse_mode=None)
    return req


async def forward_receipt(message: Message, st: dict, req_id: int, file_id: str, kind: str):
    """Mijozning chek skrinshotini to'liq ma'lumot bilan barcha adminlarga yuboradi."""
    uid = message.from_user.id
    req = next((r for r in purchases if r.get("id") == req_id and r.get("uid") == uid), None)
    st["awaiting"] = None
    touch("users")
    if not req:
        await send_html(message, "🤔 Bu buyurtmani topa olmadim. Xizmatni qaytadan tanlab, buyurtma bering 🙏", get_kb(uid))
        return
    if req["status"] != "pending":
        await send_html(message, f"ℹ️ Buyurtma #{req_id} allaqachon ko'rib chiqilgan ✅", get_kb(uid))
        return
    note = clip(message.caption or "", 200)
    caption = (
        f"🧾 <b>CHEK keldi — buyurtma #{req_id}</b>\n\n"
        f"👤 {user_link(uid, st.get('full_name'))} • {esc(uname_of(st))}\n"
        f"🆔 <code>{uid}</code>\n"
        f"📦 {esc(req['service'])}\n🧾 {esc(req['details'])}\n"
        f"💳 {esc(CARD_NUMBER)} • {esc(CARD_OWNER)}\n"
        + (f"💬 Izoh: <i>{esc(note)}</i>\n" if note else "")
        + f"🕒 {ts()}"
    )
    kb = ikb([[("✅ Bajarildi", f"pr|ok|{req_id}"), ("❌ Rad etish", f"pr|no|{req_id}")]])
    delivered = 0
    for adm in ADMIN_IDS:
        try:
            if kind == "photo":
                await bot.send_photo(adm, file_id, caption=caption, reply_markup=kb)
            else:
                await bot.send_document(adm, file_id, caption=caption, reply_markup=kb)
            delivered += 1
        except Exception as e:
            log.warning("receipt -> admin %s failed: %s", adm, e)
    if not delivered:
        st["awaiting"] = f"receipt:{req_id}"
        touch("users")
        await send_html(
            message,
            f"😕 Chekni adminga yetkaza olmadim. Iltimos, to'g'ridan-to'g'ri @{esc(CREATOR_USERNAME)} ga yuboring — albatta yordam beramiz 🙏",
            payment_kb(req_id),
        )
        return
    req["receipt_at"] = ts()
    req["receipt_kind"] = kind
    touch("purchases")
    log_act(uid, "RECEIPT", f"Buyurtma #{req_id} cheki")
    await send_html(
        message,
        f"📨 <b>Chek qabul qilindi!</b> 🙏\n\nBuyurtma <b>#{req_id}</b> adminga yetkazildi. "
        "Tasdiqlanishi bilan shu yerga xabar keladi 🔔\nSabringiz va ishonchingiz uchun rahmat! 💙✨",
        get_kb(uid),
    )


@dp.callback_query(F.data.startswith("rc|"))
async def cb_receipt(cb: CallbackQuery):
    uid = cb.from_user.id
    st = get_user(uid, cb.from_user)
    rid = int(cb.data.split("|")[1])
    req = next((r for r in purchases if r.get("id") == rid and r.get("uid") == uid), None)
    if not req or req["status"] != "pending":
        await cb.answer("Bu buyurtma yopilgan yoki topilmadi", show_alert=True)
        return
    st["awaiting"] = f"receipt:{rid}"
    touch("users")
    await cb.message.answer(f"📸 Buyurtma <b>#{rid}</b> uchun to'lov chekining skrinshotini yuboring.\n💡 <i>Izohga @username ingizni yozishni unutmang.</i>")
    await cb.answer()


UPSELL_TEXTS = [
    "💎 <b>Bilasizmi?</b> Gemini Pro (18 oylik) obunasi bilan botda <b>rasm yaratish</b> va <b>video tahlil</b> ham ochiladi 🎨🎥\nKo'proq imkoniyat — ko'proq natija! 🚀",
    "⭐ Botni yoqtirayotganingizdan xursandmiz! 😍\nPremium bilan o'qish yanada tez va qiziqarli bo'ladi — rasm yaratish, video tahlil va Gemini Pro 18 oy 👇",
    "🌟 Siz juda faolsiz — barakalla! 👏\nFaol o'quvchilar uchun Premium: rasm yaratish + video tahlil + Gemini Pro. Ko'rib chiqing 👇",
]


async def maybe_upsell(message: Message, st: dict, uid: int):
    if is_premium(uid):
        return
    n = st.get("queries_count", 0)
    if n not in (4, 12) and not (n > 12 and n % 15 == 0):
        return
    with contextlib.suppress(Exception):
        await send_html(message, pick(UPSELL_TEXTS), premium_pitch_kb())


def premium_pitch_kb():
    return ikb([
        [("✨ Gemini Pro + Premium olaman", "buy|gemini|0")],
        [("🛍 Boshqa xizmatlar", "shop|menu"), ("🙋 Savolim bor", ("url", f"https://t.me/{CREATOR_USERNAME}"))],
    ])


async def premium_pitch(message: Message, feature: str):
    uid = message.from_user.id
    first = esc((message.from_user.first_name or "do'stim"))
    if feature == "image":
        text = pick([
            f"🎨 {first}, tasavvuringizni rasmga aylantirmoqchimisiz? Zo'r g'oya! 😍\n\n"
            "Rasm yaratish <b>Premium</b> a'zolar uchun ochiq. Gemini Pro (18 oylik) olsangiz — bu va video tahlil darrov ishlaydi.\n\n"
            "Hozircha esa <b>🖼 Rasm tahlili</b> va barcha o'quv bo'limlari bepul sizning xizmatingizda 🙌",
            f"🎨 {first}, rasm generatsiyasi — Premium imkoniyat ✨\n\n"
            "Gemini Pro (18 oylik) obunasi bilan botdagi Premium ham ochiladi: rasm yaratish, video tahlil va boshqalar.\n"
            "Xohlasangiz hoziroq ko'ring 👇",
        ])
    else:
        text = pick([
            f"🎥 {first}, videongiz uchun rahmat! Video tahlil — <b>Premium</b> imkoniyat 🔒\n\n"
            "Gemini Pro (18 oylik) bilan videolarni tushuntirib, konspekt qilib beraman.\n"
            "Hozircha videodagi savolingizni <b>matn yoki rasm</b> ko'rinishida yuborsangiz, bepul yordam beraman 🤝",
            f"🎥 Video keldi, {first}! Uni tahlil qilish Premium'da ishlaydi ✨\n\n"
            "Obuna olsangiz video tahlil + rasm yaratish ochiladi. Hozircha esa skrinshot yuboring — bepul tahlil qilaman 📸",
        ])
    price = PRODUCTS["gemini"]["opts"][0][1]  # narx botdagi Gemini narxidan olinadi
    text += (
        f"\n\n💎 <b>Premium narxi: {esc(price)}</b>\n"
        "Pastdagi tugmani bosing — karta ma'lumotlari darrov chiqadi. To'lab, chek skrinshotini yuborasiz, admin tasdiqlashi bilan Premium ochiladi 🙌"
    )
    if add_lead(uid, "image_gen" if feature == "image" else "video", "Premium funksiyaga urindi"):
        st = get_user(uid)
        await notify_admins(
            f"🔥 <b>Qiziqish!</b> {user_link(uid, st.get('full_name'))} ({esc(uname_of(st))}) "
            f"{'🎨 rasm yaratish' if feature == 'image' else '🎥 video tahlil'}ga urindi — Premium emas.",
            silent=True,
        )
    await send_html(message, text, premium_pitch_kb())


# =====================================================================
# Command / user handlers
# =====================================================================

@dp.message(CommandStart())
async def start(message: Message):
    uid = message.from_user.id
    st = get_user(uid, message.from_user)
    st["mode"], st["sub"], st["awaiting"] = "ai_tutor", None, None
    first_time = not st.get("started")
    st["started"] = True
    parts = (message.text or "").split()

    if first_time and len(parts) > 1 and parts[1].startswith("ref_"):
        try:
            inviter_id = int(parts[1][4:])
            inv = users.get(str(inviter_id))
            if inv and inviter_id != uid and not st.get("referred_by"):
                st["referred_by"] = inviter_id
                inv["referrals_count"] = inv.get("referrals_count", 0) + 1
                add_xp(inv, 100)
                add_xp(st, 50)
                referrals_log.insert(0, {
                    "time": ts(), "inviter_id": inviter_id, "inviter_name": inv.get("full_name"),
                    "new_user_id": uid, "new_user_name": st.get("full_name"),
                })
                del referrals_log[300:]
                touch("referrals")
                with contextlib.suppress(Exception):
                    await bot.send_message(
                        inviter_id,
                        f"🎉 Yaxshi xabar! {esc(st.get('full_name'))} sizning havolangiz orqali qo'shildi.\n"
                        f"👥 Jami do'stlar: <b>{inv['referrals_count']}/15</b> • +100 XP 🏆",
                    )
                if inv["referrals_count"] >= 15 and not inv.get("reward_claimed"):
                    inv["reward_claimed"] = True
                    await notify_admins(
                        f"🎁 <b>Referral mukofoti!</b>\n{user_link(inviter_id, inv.get('full_name'))} "
                        f"(<code>{inviter_id}</code>) 15 ta do'st taklif qildi. 15 ta Stars berishingiz mumkin ⭐"
                    )
        except Exception as e:
            log.warning("ref error: %s", e)

    touch("users")
    log_act(uid, "START", "Botni ishga tushirdi")
    await setup_user_menu(uid)

    # Mini App ichidagi bo'lim tugmalari: t.me/bot?start=go_<bo'lim>
    if len(parts) > 1 and parts[1].startswith("go_"):
        route = deep_link_routes().get(parts[1][3:])
        if route:
            await route(message)
            return

    await send_html(message, welcome_text(st, message.from_user.first_name), get_kb(uid))


def deep_link_routes() -> dict:
    return {
        "ai": lambda m: activate_mode(m, "ai_tutor"),
        "test": lambda m: activate_mode(m, "new_test"),
        "image": lambda m: activate_mode(m, "image_analysis"),
        "teacher": lambda m: activate_mode(m, "teacher"),
        "exam": lambda m: activate_mode(m, "exam"),
        "subjects": subjects,
        "quiz": quiz_start,
        "ielts": ielts_menu,
        "uni": university_menu,
        "pomodoro": pomodoro_menu,
        "imagegen": image_gen_btn,
        "stars": buy_stars,
        "premium": buy_premium,
        "gemini": buy_gemini,
        "profile": profile_btn,
        "ref": referral_handler,
        "games": games_hub,
        "shop": shop_hub,
    }


async def setup_user_menu(uid: int):
    """Chap pastdagi «Menu» tugmasini «🚀 Open» ga aylantiradi (Mini App). Xato bo'lsa bot ishlashda davom etadi."""
    url = webapp_url(uid)
    if not url:
        return
    try:
        await bot.set_chat_menu_button(chat_id=uid, menu_button=MenuButtonWebApp(text="🚀 Open", web_app=WebAppInfo(url=url)))
    except Exception as e:
        log.debug("menu button failed for %s: %s", uid, e)


@dp.message(Command("webapp"))
async def webapp_status(message: Message):
    """Admin uchun: Mini App ulanganmi — tekshirish va sinash tugmasi."""
    uid = message.from_user.id
    if not is_admin(uid):
        return
    if not WEBAPP_URL:
        await send_html(
            message,
            "❌ <b>Mini App hozir o'chiq.</b>\n\n"
            "1️⃣ <code>index.html</code> ni HTTPS hostingga joylang (masalan netlify.com/drop)\n"
            "2️⃣ <code>.env</code> fayliga qo'shing: <code>WEBAPP_URL=https://sizning-havola.netlify.app/</code>\n"
            "3️⃣ Botni qayta ishga tushiring va yana /webapp yozing",
            get_kb(uid),
        )
        return
    await send_html(
        message,
        f"✅ <b>Mini App ulangan:</b>\n<code>{esc(WEBAPP_URL)}</code>\n\nSinab ko'rish uchun pastdagi tugmani bosing 👇\n"
        "<i>Birinchi ochilishda Telegram «Launch» deb so'raydi — bu odatiy holat.</i>",
        ikb([[("🚀 Ilovani ochish", ("web", webapp_url(uid)))]]),
    )


@dp.message(F.text == "🚀 START")
async def start_btn(message: Message):
    await start(message)


@dp.message(Command("help"))
@dp.message(F.text == "ℹ️ YORDAM")
async def help_cmd(message: Message):
    uid = message.from_user.id
    log_act(uid, "HELP", "Yordam")
    await send_html(
        message,
        "ℹ️ <b>Qisqacha qo'llanma</b>\n\n"
        "💬 Savolni yozing — javob beraman (istalgan tilda)\n"
        "🖼 Rasm yuboring — masala yoki chizmani tahlil qilaman\n"
        "🎙 Ovozli xabar yuboring — eshitib, javob beraman\n"
        "📄 PDF yuboring — qisqacha mazmunini chiqaraman\n"
        "🎯 Quiz — o'zingizni sinab ko'ring, XP yutib oling\n"
        "🎮 O'yinlar — tez hisob, so'z topish, mantiq va kunlik sovg'a\n"
        "🛍 Do'kon — Stars, Telegram Premium, Gemini Pro\n"
        "🎁 Referral — 15 ta do'st = 15 ⭐ Stars\n"
        "🔒 Rasm yaratish va video tahlil — Premium'da\n\n"
        "Buyruqlar: /new — yangi suhbat • /profile • /id",
        get_kb(uid),
    )


@dp.message(Command("id"))
async def id_cmd(message: Message):
    await message.answer(f"🆔 Sizning ID: <code>{message.from_user.id}</code>")


@dp.message(Command("new"))
@dp.message(F.text == "🔄 YANGI SUHBAT")
async def new_chat(message: Message):
    uid = message.from_user.id
    st = get_user(uid, message.from_user)
    st["history"], st["mode"], st["sub"], st["awaiting"] = [], "ai_tutor", None, None
    touch("users")
    await send_html(message, pick([
        "🔄 Yangi sahifa ochdik! Eski suhbat tozalandi. Nimadan boshlaymiz?",
        "🧹 Hammasi toza. Yangi savolingizni kutaman 😊",
    ]), get_kb(uid))


@dp.message(F.text == "⬅️ MENU")
async def menu(message: Message):
    uid = message.from_user.id
    st = get_user(uid, message.from_user)
    st["awaiting"] = None
    st["admin_action"] = None
    touch("users")
    await send_html(message, "🏠 Asosiy menyu. Qaysi bo'limga o'tamiz?", get_kb(uid))


# ----- AI provider choice -----

@dp.message(F.text == "🤖 AI TANLASH")
async def choose_ai(message: Message):
    st = get_user(message.from_user.id, message.from_user)
    cur = st.get("provider", "auto")
    cur_label = "🤖 Avto" if cur == "auto" else PROVIDER_LABELS.get(cur, cur)
    await send_html(
        message,
        f"🤖 <b>AI tanlash</b>\nHozir: <b>{cur_label}</b>\n\n"
        "<i>«Avto» eng yaxshisi: har bo'lim uchun mos va eng tez modelni o'zim tanlayman ⚡</i>",
        AI_KB,
    )


PROVIDER_MAP = {"✨ Gemini": "gemini", "🚀 DeepSeek": "deepseek", "⚡ Groq": "groq", "🧠 GPT": "openai", "🔵 Claude": "claude", "🤖 AVTO (tavsiya)": "auto"}


@dp.message(F.text.in_(set(PROVIDER_MAP)))
async def provider_select(message: Message):
    uid = message.from_user.id
    st = get_user(uid, message.from_user)
    name = PROVIDER_MAP[message.text]
    if name != "auto" and not configured(name):
        await send_html(message, f"😕 {PROVIDER_LABELS[name]} hozir ulanmagan. Boshqasini tanlang yoki «Avto»da qoldiring.", AI_KB)
        return
    st["provider"] = name
    touch("users")
    log_act(uid, "AI_SELECT", name)
    label = "🤖 Avto rejim" if name == "auto" else PROVIDER_LABELS[name]
    await send_html(message, f"✅ {label} faollashtirildi!", get_kb(uid))


# ----- Language -----

@dp.message(F.text == "🌐 TIL")
async def lang_menu(message: Message):
    await send_html(message, "🌐 <b>Javob tilini tanlang / Choose language:</b>", LANG_KB)


@dp.message(F.text.in_({"🇺🇿 O'zbekcha", "🇷🇺 Русский", "🇬🇧 English"}))
async def set_language(message: Message):
    uid = message.from_user.id
    st = get_user(uid, message.from_user)
    st["lang"] = {"🇺🇿 O'zbekcha": "uz", "🇷🇺 Русский": "ru", "🇬🇧 English": "en"}[message.text]
    touch("users")
    reply = {"uz": "✅ Endi o'zbek tilida javob beraman 🇺🇿", "ru": "✅ Теперь буду отвечать по-русски 🇷🇺",
             "en": "✅ I'll answer in English from now on 🇬🇧"}[st["lang"]]
    await send_html(message, reply, get_kb(uid))


# ----- Modes -----

@dp.message(F.text == "📚 FANLAR")
async def subjects(message: Message):
    uid = message.from_user.id
    st = get_user(uid, message.from_user)
    st["mode"] = "subjects"
    touch("users")
    await send_html(message, "📚 Qaysi fan bilan shug'ullanamiz? 👇", DOMAIN_KB)


async def activate_mode(message: Message, mode: str, kb=None):
    uid = message.from_user.id
    st = get_user(uid, message.from_user)
    st["mode"], st["sub"], st["awaiting"] = mode, None, None
    st["history"] = []  # bo'limlar aralashib ketmasligi uchun
    touch("users")
    log_act(uid, "MODE", f"{mode} ({message.text})")
    await send_html(message, pick(MODES[mode]["intro"]), kb or get_kb(uid))


@dp.message(F.text.in_(set(SUBJECT_BUTTONS)))
async def subject_select(message: Message):
    await activate_mode(message, SUBJECT_BUTTONS[message.text], DOMAIN_KB)


@dp.message(F.text.in_(set(MODE_BUTTONS)))
async def domain_select(message: Message):
    await activate_mode(message, MODE_BUTTONS[message.text])


@dp.message(F.text == "🇬🇧 IELTS")
async def ielts_menu(message: Message):
    await activate_mode(message, "ielts", IELTS_KB)


@dp.message(F.text.in_(set(IELTS_SUBS)))
async def ielts_skill(message: Message):
    uid = message.from_user.id
    st = get_user(uid, message.from_user)
    sub, _, intro = IELTS_SUBS[message.text]
    st["mode"], st["sub"], st["history"], st["awaiting"] = "ielts", sub, [], None
    touch("users")
    log_act(uid, "IELTS", message.text)
    await send_html(message, intro, IELTS_KB)


@dp.message(F.text == "🎓 UNIVERSITET")
async def university_menu(message: Message):
    await activate_mode(message, "university", UNI_KB)


@dp.message(F.text.in_(set(UNI_SUBS)))
async def uni_skill(message: Message):
    uid = message.from_user.id
    st = get_user(uid, message.from_user)
    sub, _, intro = UNI_SUBS[message.text]
    st["mode"], st["sub"], st["history"], st["awaiting"] = "university", sub, [], None
    touch("users")
    log_act(uid, "UNIVERSITY", message.text)
    await send_html(message, intro, UNI_KB)


# =====================================================================
# O'yinlar (o'ynab o'rganish)
# =====================================================================

GAMES: dict = {}
GAME_META = {"math": ("🧮", "Tez hisob"), "word": ("🔤", "So'z topish"), "logic": ("🧠", "Mantiq")}
GAME_ROUNDS = 5
GAME_XP_DAILY_CAP = 60
VOCAB = [
    ("book", "kitob"), ("school", "maktab"), ("teacher", "o'qituvchi"), ("knowledge", "bilim"), ("friend", "do'st"),
    ("water", "suv"), ("window", "deraza"), ("garden", "bog'"), ("market", "bozor"), ("future", "kelajak"),
    ("success", "muvaffaqiyat"), ("health", "salomatlik"), ("language", "til"), ("answer", "javob"), ("question", "savol"),
    ("morning", "ertalab"), ("river", "daryo"), ("mountain", "tog'"), ("library", "kutubxona"), ("student", "talaba"),
    ("homework", "uy vazifasi"), ("holiday", "bayram"), ("journey", "sayohat"), ("strong", "kuchli"), ("kitchen", "oshxona"),
    ("weather", "ob-havo"), ("energy", "energiya"), ("planet", "sayyora"), ("dream", "orzu"), ("brother", "aka/uka"),
]
CHEERS = ["✅ To'g'ri! 🎉", "✅ Barakalla! 👏", "✅ Zo'r! 🔥", "✅ Aynan shu! 💫", "✅ Ajoyib! 🌟"]


def _tok() -> str:
    return f"{random.randrange(16 ** 4):04x}"


def _num_opts(ans: int):
    pool = {ans}
    span_ = max(3, abs(ans) // 8)
    guard = 0
    while len(pool) < 4 and guard < 80:
        guard += 1
        v = ans + random.choice([-1, 1]) * random.randint(1, span_ + 3)
        if v > 0:
            pool.add(v)
    k = 1
    while len(pool) < 4:
        pool.add(ans + 10 + k)
        k += 1
    opts = list(pool)
    random.shuffle(opts)
    return [str(o) for o in opts], opts.index(ans)


def q_math() -> dict:
    kind = random.choice(["add", "sub", "mul", "div", "pct", "sq"])
    if kind == "add":
        a, b = random.randint(18, 99), random.randint(18, 99)
        expr, ans = f"{a} + {b}", a + b
    elif kind == "sub":
        a = random.randint(50, 150)
        b = random.randint(11, a - 1)
        expr, ans = f"{a} − {b}", a - b
    elif kind == "mul":
        a, b = random.randint(6, 15), random.randint(3, 12)
        expr, ans = f"{a} × {b}", a * b
    elif kind == "div":
        b, c = random.randint(3, 12), random.randint(4, 15)
        expr, ans = f"{b * c} ÷ {b}", c
    elif kind == "pct":
        pc, base = random.choice([10, 20, 25, 50]), random.choice(range(40, 401, 20))
        expr, ans = f"{base} ning {pc}% i", base * pc // 100
    else:
        n = random.randint(6, 20)
        expr, ans = f"{n}²", n * n
    opts, idx = _num_opts(ans)
    return {"text": f"<b>{expr} = ?</b>", "opts": opts, "ans": idx}


def q_word() -> dict:
    w, uz = random.choice(VOCAB)
    letters = list(w)
    for _ in range(20):
        random.shuffle(letters)
        if "".join(letters) != w:
            break
    others = random.sample([x for x, _ in VOCAB if x != w], 3)
    opts = [w] + others
    random.shuffle(opts)
    return {
        "text": f"Harflar aralashib ketdi 🌀\n<code>{' '.join(letters).upper()}</code>\n💡 Ma'nosi: <b>{esc(uz)}</b>\n\nQaysi inglizcha so'z?",
        "opts": opts, "ans": opts.index(w), "why": f"{w} — {uz}",
    }


def q_logic() -> dict:
    kind = random.choice(["arith", "geom", "sq", "fib"])
    if kind == "arith":
        a, d = random.randint(1, 20), random.randint(2, 9)
        seq = [a + d * i for i in range(5)]
        why = f"Har safar +{d} qo'shiladi"
    elif kind == "geom":
        a, r = random.randint(1, 5), random.choice([2, 3])
        seq = [a * r ** i for i in range(5)]
        why = f"Har safar ×{r} bo'ladi"
    elif kind == "sq":
        n = random.randint(1, 6)
        seq = [(n + i) ** 2 for i in range(5)]
        why = "Bular ketma-ket sonlarning kvadratlari"
    else:
        a, b = random.randint(1, 5), random.randint(1, 5)
        seq = [a, b]
        while len(seq) < 5:
            seq.append(seq[-1] + seq[-2])
        why = "Har bir son oldingi ikkitasining yig'indisi"
    ans = seq[-1]
    opts, idx = _num_opts(ans)
    return {"text": f"Ketma-ketlikni davom ettiring:\n<b>{', '.join(map(str, seq[:-1]))}, ?</b>", "opts": opts, "ans": idx, "why": why}


def new_question(game: str) -> dict:
    return {"math": q_math, "word": q_word, "logic": q_logic}[game]()


def game_q_text(z: dict) -> str:
    ic, name = GAME_META[z["game"]]
    return f"{ic} <b>{name}</b> • {z['i'] + 1}/{GAME_ROUNDS}   ⭐ {z['score']}\n\n{z['q']['text']}"


def game_q_kb(z: dict):
    o = z["q"]["opts"]
    cells = [(f"{'ABCD'[i]}) {t}", f"gm|a|{z['tok']}|{i}") for i, t in enumerate(o)]
    return ikb([cells[i:i + 2] for i in range(0, len(cells), 2)] + [[("⬅️ O'yinlar", "gm|menu")]])


def games_home_view(st: dict):
    gift = "✅ olingan" if st.get("gift_day") == today() else "bepul XP"
    text = (
        "🎮 <b>O'yin burchagi</b> 🎉\n\n"
        f"O'ynab o'rganing — zerikish yo'q! Har bir o'yin {GAME_ROUNDS} ta savol, har to'g'ri javobga XP 🏆\n\n"
        "🧮 <b>Tez hisob</b> — miyani charxlang\n"
        "🔤 <b>So'z topish</b> — inglizcha so'zni toping\n"
        "🧠 <b>Mantiq</b> — ketma-ketlikni davom ettiring\n"
        f"🎲 <b>Kunlik sovg'a</b> — har kuni {gift}\n\n"
        f"⭐ Sizning XP: <b>{int(st.get('points') or 0)}</b>"
    )
    kb = ikb([
        [("🧮 Tez hisob", "gm|go|math"), ("🔤 So'z topish", "gm|go|word")],
        [("🧠 Mantiq", "gm|go|logic"), (f"🎲 Kunlik sovg'a ({gift})", "gm|daily")],
    ])
    return text, kb


def award_game_xp(st: dict, gain: int):
    d = today()
    if st.get("game_xp_day") != d:
        st["game_xp_day"], st["game_xp"] = d, 0
    used = int(st.get("game_xp") or 0)
    got = max(0, min(gain, GAME_XP_DAILY_CAP - used))
    st["game_xp"] = used + got
    add_xp(st, got)
    touch("users")
    return got, got < gain


@dp.message(F.text == "🎮 O'YINLAR")
async def games_hub(message: Message):
    uid = message.from_user.id
    st = get_user(uid, message.from_user)
    log_act(uid, "GAMES", "O'yinlar")
    text, kb = games_home_view(st)
    await send_html(message, text, kb)


@dp.callback_query(F.data == "gm|menu")
async def cb_games_menu(cb: CallbackQuery):
    st = get_user(cb.from_user.id, cb.from_user)
    GAMES.pop(cb.from_user.id, None)
    await edit_panel(cb, *games_home_view(st))
    await cb.answer()


@dp.callback_query(F.data.startswith("gm|go|"))
async def cb_game_start(cb: CallbackQuery):
    uid = cb.from_user.id
    game = cb.data.split("|")[2]
    if game not in GAME_META:
        await cb.answer()
        return
    get_user(uid, cb.from_user)
    z = {"game": game, "i": 0, "score": 0, "tok": _tok(), "q": new_question(game)}
    GAMES[uid] = z
    log_act(uid, "GAME", GAME_META[game][1])
    await edit_panel(cb, game_q_text(z), game_q_kb(z))
    await cb.answer()


@dp.callback_query(F.data.startswith("gm|a|"))
async def cb_game_answer(cb: CallbackQuery):
    uid = cb.from_user.id
    z = GAMES.get(uid)
    parts = cb.data.split("|")
    if not z or len(parts) != 4 or parts[2] != z["tok"]:
        await cb.answer("Bu savol allaqachon javoblangan yoki o'yin tugagan 🙂")
        return
    q = z["q"]
    right = int(parts[3]) == q["ans"]
    if right:
        z["score"] += 1
    mark = pick(CHEERS) if right else f"❌ Deyarli! To'g'ri javob: <b>{esc(q['opts'][q['ans']])}</b>"
    if q.get("why"):
        mark += f"\n💡 {esc(q['why'])}"
    z["i"] += 1
    if z["i"] < GAME_ROUNDS:
        z["q"], z["tok"] = new_question(z["game"]), _tok()
        await edit_panel(cb, f"{mark}\n\n" + game_q_text(z), game_q_kb(z))
        await cb.answer()
        return
    # o'yin tugadi
    GAMES.pop(uid, None)
    st = get_user(uid, cb.from_user)
    gain = z["score"] * 3 + (5 if z["score"] == GAME_ROUNDS else 0)
    got, capped = award_game_xp(st, gain)
    st["games_played"] = int(st.get("games_played") or 0) + 1
    ic, name = GAME_META[z["game"]]
    verdict = ("🏆 Mukammal! Hammasi to'g'ri!" if z["score"] == GAME_ROUNDS
               else "🔥 Zo'r natija!" if z["score"] >= 3 else "💪 Hech gap yo'q, keyingisi albatta yaxshiroq bo'ladi!")
    xp_line = f"+{got} XP 🏆" + (" (bugungi o'yin XP limiti to'ldi — ertaga yana!)" if capped else "")
    promo = "\n\n💎 <i>Premium bilan rasm yaratish va video tahlil ham ochiladi.</i>" if (not is_premium(uid) and random.random() < 0.34) else ""
    kb = ikb([[("🔁 Yana o'ynash", f"gm|go|{z['game']}"), ("🎮 O'yinlar", "gm|menu")]] + ([[("💎 Premium", "sh|menu")]] if promo else []))
    await edit_panel(cb, f"{mark}\n\n{ic} <b>{name}</b> tugadi!\nNatija: <b>{z['score']}/{GAME_ROUNDS}</b> • {xp_line}\n{verdict}{promo}", kb)
    if z["score"] == GAME_ROUNDS:
        with contextlib.suppress(Exception):
            await cb.message.answer_dice(emoji="🎯")
    await cb.answer()


@dp.callback_query(F.data == "gm|daily")
async def cb_game_daily(cb: CallbackQuery):
    uid = cb.from_user.id
    st = get_user(uid, cb.from_user)
    if st.get("gift_day") == today():
        await cb.answer("🎁 Bugungi sovg'ani olgansiz! Ertaga yana keling 😊", show_alert=True)
        return
    st["gift_day"] = today()  # qayta bosishdan avval belgilanadi
    touch("users")
    val = random.randint(1, 6)
    with contextlib.suppress(Exception):
        d = await cb.message.answer_dice(emoji="🎲")
        val = int(d.dice.value)
        await asyncio.sleep(3.5)
    xp = val * 5
    add_xp(st, xp)
    touch("users")
    promo = "\n\n💎 <i>Premium'da yanada ko'p imkoniyat sizni kutmoqda — Do'konni ko'ring!</i>" if not is_premium(uid) else ""
    await send_html(cb.message, f"🎁 <b>Kunlik sovg'a!</b>\nKubik: <b>{val}</b> → <b>+{xp} XP</b> 🎉\nErtaga yana keling — yangi sovg'a kutadi 💙{promo}",
                    ikb([[("🎮 O'yinlar", "gm|menu")]]))
    await cb.answer()


# ----- Quiz (inline) -----

@dp.message(F.text == "🎯 QUIZ")
async def quiz_start(message: Message):
    uid = message.from_user.id
    st = get_user(uid, message.from_user)
    st["mode"], st["awaiting"] = "quiz", "quiz_topic"
    touch("users")
    await send_html(message, pick(MODES["quiz"]["intro"]), get_kb(uid))


def parse_quiz(raw: str):
    m = re.search(r"\[.*\]", raw, re.S)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
    except Exception:
        return []
    out = []
    for q in data:
        try:
            opts = [str(o) for o in q["options"]][:4]
            ans = int(q["answer"])
            if len(opts) == 4 and 0 <= ans < 4:
                out.append({"q": str(q["q"]), "options": opts, "answer": ans, "why": str(q.get("why", ""))})
        except Exception:
            continue
    return out[:5]


async def make_quiz(message: Message, topic: str):
    uid = message.from_user.id
    st = get_user(uid)
    lang = LANG_NAMES.get(st.get("lang", "uz"))
    sys = "You write quiz questions. Output ONLY a JSON array, no prose, no code fences."
    prompt = (
        f"Create 5 multiple-choice questions in {lang} about: {topic}. Mix easy and medium difficulty. "
        'Format: [{"q":"...","options":["A","B","C","D"],"answer":0,"why":"one short sentence"}] where answer is the index 0-3 of the correct option. '
        "Keep each option under 55 characters."
    )
    async with typing(message.chat.id):
        raw, _ = await run_race(["groq", "gemini", "openai", "claude", "deepseek"],
                                [{"role": "system", "content": sys}, {"role": "user", "content": prompt}], 0.6, 1500)
    qs = parse_quiz(raw)
    if len(qs) < 3:
        await send_html(message, "😅 Savollarni tuzib bo'lmadi. Mavzuni biroz aniqroq yozib, qayta urinib ko'ring.", get_kb(uid))
        return
    QUIZ[uid] = {"qs": qs, "i": 0, "score": 0, "topic": topic}
    await send_quiz_question(message, uid, new=True)


def quiz_text(uid: int) -> str:
    z = QUIZ[uid]
    q = z["qs"][z["i"]]
    return f"🎯 <b>{esc(clip(z['topic'], 40))}</b> — savol {z['i'] + 1}/{len(z['qs'])}\n\n<b>{esc(q['q'])}</b>"


def quiz_kb(uid: int):
    z = QUIZ[uid]
    q = z["qs"][z["i"]]
    letters = "ABCD"
    return ikb([[(f"{letters[i]}) {clip(o, 55)}", f"qz|{z['i']}|{i}")] for i, o in enumerate(q["options"])])


async def send_quiz_question(message: Message, uid: int, new: bool = False):
    await safe_answer(message, quiz_text(uid), quiz_kb(uid))


@dp.callback_query(F.data.startswith("qz|"))
async def quiz_answer(cb: CallbackQuery):
    uid = cb.from_user.id
    z = QUIZ.get(uid)
    if not z:
        await cb.answer("Quiz topilmadi. 🎯 QUIZ tugmasi orqali yangisini boshlang.", show_alert=True)
        return
    _, idx, opt = cb.data.split("|")
    idx, opt = int(idx), int(opt)
    if idx != z["i"]:
        await cb.answer("Bu savol allaqachon javoblangan 🙂")
        return
    q = z["qs"][idx]
    right = opt == q["answer"]
    if right:
        z["score"] += 1
    mark = "✅ To'g'ri!" if right else f"❌ Noto'g'ri. To'g'ri javob: <b>{'ABCD'[q['answer']]}) {esc(q['options'][q['answer']])}</b>"
    why = f"\n💡 {esc(q['why'])}" if q.get("why") else ""
    z["i"] += 1
    if z["i"] >= len(z["qs"]):
        st = get_user(uid)
        gain = z["score"] * 10
        add_xp(st, gain)
        touch("users")
        total = len(z["qs"])
        verdict = "🏆 Ajoyib natija!" if z["score"] >= total - 1 else ("👍 Yomon emas, davom eting!" if z["score"] >= total // 2 else "💪 Hech gap yo'q, mashq qilsangiz bo'ladi!")
        QUIZ.pop(uid, None)
        await cb.message.edit_text(
            f"{mark}{why}\n\n🏁 <b>Quiz tugadi!</b>\nNatija: <b>{z['score']}/{total}</b> • +{gain} XP\n{verdict}",
            reply_markup=ikb([[("🔁 Yana quiz", "qz_again")]]),
        )
        if z["score"] >= total - 1:
            with contextlib.suppress(Exception):
                await cb.message.answer_dice(emoji="🎯")
    else:
        await cb.message.edit_text(f"{mark}{why}\n\n" + quiz_text(uid), reply_markup=quiz_kb(uid))
    await cb.answer()


@dp.callback_query(F.data == "qz_again")
async def quiz_again(cb: CallbackQuery):
    st = get_user(cb.from_user.id)
    st["mode"], st["awaiting"] = "quiz", "quiz_topic"
    touch("users")
    await cb.message.answer("🎯 Yangi mavzuni yozing:")
    await cb.answer()


# ----- Pomodoro -----

async def pomodoro_loop(uid: int, chat_id: int):
    try:
        while True:
            await bot.send_message(chat_id, pick([
                "🍅 <b>Fokus vaqti!</b> 25 daqiqa — telefon chetga, miya ishga 🧠",
                "🍅 Boshladik! 25 daqiqa faqat o'qish. Siz uddalaysiz 💪",
            ]))
            await asyncio.sleep(1500)
            st = get_user(uid)
            add_xp(st, 15)
            touch("users")
            await bot.send_message(chat_id, "☕ <b>Zo'r ish!</b> 25 daqiqa tugadi (+15 XP). 5 daqiqa dam oling — suv iching, cho'zilib oling 🌿")
            with contextlib.suppress(Exception):
                await bot.send_dice(chat_id, emoji="🏀")
            await asyncio.sleep(300)
    except asyncio.CancelledError:
        pass
    except Exception as e:
        log.error("Pomodoro error %s: %s", uid, e)


@dp.message(F.text == "🍅 POMODORO TIMER")
async def pomodoro_menu(message: Message):
    log_act(message.from_user.id, "POMODORO", "Bo'limga kirdi")
    await send_html(
        message,
        "🍅 <b>Pomodoro</b>\n\n🧠 25 daqiqa — fokus\n☕ 5 daqiqa — dam\n🔄 To'xtatmaguningizcha takrorlanadi\n"
        "Har bir yakunlangan fokus uchun +15 XP 🏆",
        POMODORO_KB,
    )


@dp.message(F.text == "▶️ Pomodoro Boshlash")
async def pomodoro_start(message: Message):
    uid = message.from_user.id
    if uid in active_pomodoros and not active_pomodoros[uid].done():
        await send_html(message, "⏱ Taymer allaqachon ishlayapti. To'xtatish uchun «⏹️ To'xtatish» ni bosing.", POMODORO_KB)
        return
    active_pomodoros[uid] = asyncio.create_task(pomodoro_loop(uid, message.chat.id))
    log_act(uid, "POMODORO_START", "Boshladi")


@dp.message(F.text == "⏹️ To'xtatish")
async def pomodoro_stop(message: Message):
    uid = message.from_user.id
    t = active_pomodoros.pop(uid, None)
    if t and not t.done():
        t.cancel()
        log_act(uid, "POMODORO_STOP", "To'xtatdi")
        await send_html(message, "⏹ Taymer to'xtatildi. Yaxshi ishladingiz! 🌟", POMODORO_KB)
    else:
        await send_html(message, "Hozir faol taymer yo'q 🙂", POMODORO_KB)


# ----- Profile / leaderboard / referral / creator -----

def profile_card(uid: int, st: dict) -> str:
    lvl, title, nxt = level_of(st.get("points", 0))
    prev = ((lvl - 1) ** 2) * 40
    bar = progress_bar(st.get("points", 0) - prev, nxt - prev)
    status = "💎 Premium" if is_premium(uid) else "🆓 Standart"
    return (
        f"👤 <b>{esc(st.get('full_name'))}</b>\n"
        f"{title} • Daraja <b>{lvl}</b>\n"
        f"{bar} {st.get('points', 0)}/{nxt} XP\n\n"
        f"🏷 Status: <b>{status}</b>\n"
        f"💬 Savollar: <b>{st.get('queries_count', 0)}</b>\n"
        f"🔥 Ketma-ket kunlar: <b>{st.get('streak', 0)}</b>\n"
        f"🎁 Taklif qilingan do'stlar: <b>{st.get('referrals_count', 0)}/15</b>\n"
        f"🗓 Biz bilan: <b>{esc((st.get('joined_at') or '')[:10])}</b>\n"
        f"🆔 <code>{uid}</code>"
    )


@dp.message(Command("profile"))
@dp.message(F.text == "👤 PROFIL")
async def profile_btn(message: Message):
    uid = message.from_user.id
    st = get_user(uid, message.from_user)
    log_act(uid, "PROFILE", "Profil")
    rows = [[("🎁 Do'st taklif qilish", "go|ref")]]
    if not is_premium(uid):
        rows.append([("💎 Premium ochish", "shop|menu")])
    await send_html(message, profile_card(uid, st), ikb(rows))


@dp.message(Command("top"))
@dp.message(F.text == "🏆 REYTING")
async def leaderboard(message: Message):
    uid = message.from_user.id
    get_user(uid, message.from_user)
    if not is_admin(uid):  # reyting foydalanuvchilar uchun olib tashlangan; eski tugma bosilsa menyu yangilanadi
        await send_html(message, "🔄 Menyu yangilandi ✨ Pastdagi bo'limlardan birini tanlang 👇", get_kb(uid))
        return
    top = sorted(((k, u) for k, u in users.items() if not u.get("banned")), key=lambda x: x[1].get("points", 0), reverse=True)[:10]
    medals = ["🥇", "🥈", "🥉"] + ["🔹"] * 7
    lines = [f"{medals[i]} <b>{esc(clip(u.get('full_name'), 22))}</b> — {u.get('points', 0)} XP" for i, (k, u) in enumerate(top)]
    rank = next((i + 1 for i, (k, _) in enumerate(sorted(users.items(), key=lambda x: x[1].get("points", 0), reverse=True)) if k == str(uid)), None)
    me = f"\n\n📍 Sizning o'rningiz: <b>{rank}</b>" if rank else ""
    await send_html(message, "🏆 <b>Eng faol o'quvchilar</b>\n\n" + "\n".join(lines) + me + "\n\nSavol bering, quiz yeching — XP yig'ing! 🚀", get_kb(uid))


async def referral_text(uid: int, st: dict) -> str:
    me = await bot.get_me()
    link = f"https://t.me/{me.username}?start=ref_{uid}"
    n = st.get("referrals_count", 0)
    return (
        "🎁 <b>Do'stingni taklif qil — Stars yut!</b> ⭐\n\n"
        f"🔗 Sizning havolangiz:\n<code>{link}</code>\n\n"
        f"{progress_bar(n, 15)} <b>{n}/15</b>\n\n"
        "15 ta do'st qo'shilsa — <b>15 ta Telegram Stars</b> sovg'a! Har bir do'st uchun ham +100 XP olasiz 🏆"
    )


@dp.message(F.text == "🎁 REFERRAL (STARS)")
async def referral_handler(message: Message):
    uid = message.from_user.id
    st = get_user(uid, message.from_user)
    log_act(uid, "REFERRAL", "Referral bo'limi")
    await send_html(message, await referral_text(uid, st), get_kb(uid))


@dp.callback_query(F.data == "go|ref")
async def cb_ref(cb: CallbackQuery):
    st = get_user(cb.from_user.id, cb.from_user)
    await send_html(cb.message, await referral_text(cb.from_user.id, st))
    await cb.answer()


@dp.message(F.text == "👨‍💻 BOT YARATUVCHISI")
async def creator_info(message: Message):
    uid = message.from_user.id
    log_act(uid, "CREATOR", "Yaratuvchi haqida")
    await send_html(
        message,
        "👨‍💻 <b>Bot yaratuvchisi</b>\n\n"
        "Men Ziyodullayev Elbek — Qashqadaryo viloyati, G'uzor tumani, 68-DIMI ning 11-sinf o'quvchisiman 🎓\n"
        "Yosh va ishtiyoqli dasturchiman 💻🚀\n\n"
        f"📩 Aloqa: @{esc(CREATOR_USERNAME)}\n\n" + BRAND_FOOTER,
        get_kb(uid),
    )


# ----- Shop -----

@dp.message(F.text == "⭐ STARS SOTIB OLISH")
async def buy_stars(message: Message):
    await show_product(message, message.from_user.id, "stars")


@dp.message(F.text == "💎 TELEGRAM PREMIUM")
async def buy_premium(message: Message):
    await show_product(message, message.from_user.id, "premium")


@dp.message(F.text == "✨ GEMINI PRO (18 OYLIK)")
async def buy_gemini(message: Message):
    await show_product(message, message.from_user.id, "gemini")


@dp.message(F.text == "🛍 DO'KON")
async def shop_hub(message: Message):
    uid = message.from_user.id
    get_user(uid, message.from_user)
    log_act(uid, "SHOP", "Do'kon")
    text, kb = shop_home_view()
    await send_html(message, text, kb)


@dp.callback_query(F.data.startswith("shop|"))
async def cb_shop(cb: CallbackQuery):
    """Tashqi joylardan (to'lov xabari, profil) kirish: yangi xabar sifatida ochiladi, to'lov ma'lumotlari o'chmaydi."""
    key = cb.data.split("|")[1]
    if key == "menu":
        text, kb = shop_home_view()
    elif key in PRODUCTS:
        add_lead(cb.from_user.id, f"{key}_view", PRODUCTS[key]["title"])
        text, kb = product_view(key)
    else:
        await cb.answer()
        return
    await send_html(cb.message, text, kb)
    await cb.answer()


@dp.callback_query(F.data.startswith("sh|"))
async def cb_shop_nav(cb: CallbackQuery):
    key = cb.data.split("|")[1]
    if key == "menu":
        view = shop_home_view()
    elif key in PRODUCTS:
        add_lead(cb.from_user.id, f"{key}_view", PRODUCTS[key]["title"])
        view = product_view(key)
    else:
        await cb.answer()
        return
    await edit_panel(cb, *view)
    await cb.answer()


@dp.callback_query(F.data.startswith("sp|"))
async def cb_shop_section(cb: CallbackQuery):
    parts = cb.data.split("|")
    if len(parts) != 3 or parts[1] not in PRODUCTS or parts[2] not in ("pk", "info", "pay", "faq"):
        await cb.answer()
        return
    key, sec = parts[1], parts[2]
    if sec == "pk":
        add_lead(cb.from_user.id, f"{key}_packages", PRODUCTS[key]["title"])
    await edit_panel(cb, *section_view(key, sec))
    await cb.answer()


@dp.callback_query(F.data.startswith("fq|"))
async def cb_shop_faq(cb: CallbackQuery):
    parts = cb.data.split("|")
    try:
        key, i = parts[1], int(parts[2])
        view = faq_view(key, i)
    except Exception:
        await cb.answer()
        return
    await edit_panel(cb, *view)
    await cb.answer()


@dp.callback_query(F.data == "my|list")
async def cb_my_orders(cb: CallbackQuery):
    get_user(cb.from_user.id, cb.from_user)
    await edit_panel(cb, *orders_view(cb.from_user.id))
    await cb.answer()


@dp.callback_query(F.data.startswith("buy|"))
async def cb_buy(cb: CallbackQuery):
    uid = cb.from_user.id
    st = get_user(uid, cb.from_user)
    _, key, opt = cb.data.split("|")
    if key not in PRODUCTS:
        await cb.answer()
        return
    if opt == "custom":
        st["awaiting"] = f"purchase:{key}"
        touch("users")
        await cb.message.answer("✍️ Nechta ⭐ Stars kerak? Faqat sonni yozing (50 dan 20 000 gacha), masalan: <code>150</code>\n💡 <i>Narxni o'zim hisoblab beraman. Bekor qilish: /cancel</i>")
        await cb.answer()
        return
    try:
        title, price = PRODUCTS[key]["opts"][int(opt)]
        await place_order(cb.message, uid, key, f"{title} — {price}")
    except Exception as e:
        log.exception("buy error: %s", e)
        with contextlib.suppress(Exception):
            await notify_admins(f"⚠️ <b>Xarid xatosi</b> — <code>{uid}</code>\n<code>{esc(clip(repr(e), 300))}</code>", silent=True)
        with contextlib.suppress(Exception):
            await cb.answer("Xatolik bo'ldi, qayta urinib ko'ring 🙏", show_alert=True)
        return
    with contextlib.suppress(Exception):
        await cb.answer("Buyurtma yuborildi ✅")


@dp.callback_query(F.data.startswith("pr|"), AdminOnly())
async def cb_purchase_action(cb: CallbackQuery):
    _, action, rid = cb.data.split("|")
    req = next((r for r in purchases if r.get("id") == int(rid)), None)
    if not req:
        await cb.answer("Topilmadi", show_alert=True)
        return
    if req["status"] != "pending":
        await cb.answer("Bu buyurtma allaqachon ko'rib chiqilgan", show_alert=True)
        return
    ok = action == "ok"
    req["status"] = "done" if ok else "rejected"
    req["handled_at"] = ts()
    touch("purchases")
    buyer = get_user(req["uid"])
    if ok and PRODUCTS.get(req["product"], {}).get("grants"):
        buyer["status"] = "Premium"
        touch("users")
    with contextlib.suppress(Exception):
        if ok:
            extra = "\n💎 Botdagi <b>Premium</b> imkoniyatlar ham ochildi: rasm yaratish va video tahlil!" if PRODUCTS[req["product"]]["grants"] else ""
            await bot.send_message(req["uid"], f"🎉 <b>Buyurtma #{req['id']} tasdiqlandi!</b>\n{esc(req['service'])} — rahmat, ishonchingiz uchun 💙{extra}")
        else:
            await bot.send_message(req["uid"], f"😔 Buyurtma #{req['id']} bajarilmadi. Savol bo'lsa @{esc(CREATOR_USERNAME)} ga yozing — albatta yordam beramiz.")
    tail = f"\n\n{'✅ Bajarildi' if ok else '❌ Rad etildi'} • {ts()}"
    with contextlib.suppress(Exception):
        if cb.message.photo or cb.message.document:
            await cb.message.edit_caption(caption=(cb.message.html_text or "") + tail)
        else:
            await cb.message.edit_text((cb.message.html_text or "") + tail)
    await cb.answer("Tayyor")


# ----- Feedback / guides -----

@dp.message(F.text == "✍️ FIKR BILDIRISH")
async def feedback_start(message: Message):
    uid = message.from_user.id
    st = get_user(uid, message.from_user)
    st["awaiting"] = "feedback"
    touch("users")
    await send_html(message, pick([
        "✍️ Fikringiz biz uchun juda muhim! Taklif yoki shikoyatingizni yozing — adminga to'g'ridan-to'g'ri yetadi 💌",
        "💌 Nima yoqdi, nima yoqmadi? Ochiq yozing, hammasini o'qiymiz.",
    ]), get_kb(uid))


@dp.message(F.text == "📚 QO'LLANMALAR")
async def guides_user(message: Message):
    uid = message.from_user.id
    get_user(uid, message.from_user)
    log_act(uid, "GUIDES", "Qo'llanmalar")
    if not guides:
        await send_html(message, "📖 Kutubxona hozircha bo'sh. Tez orada foydali PDF qo'llanmalar qo'shiladi — kuzatib boring! ✨", get_kb(uid))
        return
    rows = [[(f"📘 {clip(d['title'], 40)}", f"gd|{code}")] for code, d in list(guides.items())[:30]]
    await send_html(message, "📚 <b>PDF kutubxonasi</b>\nKerakli qo'llanmani bosing 👇", ikb(rows))


@dp.callback_query(F.data.startswith("gd|"))
async def cb_guide(cb: CallbackQuery):
    code = cb.data.split("|", 1)[1]
    g = guides.get(code)
    if not g:
        await cb.answer("Topilmadi", show_alert=True)
        return
    await cb.message.answer_document(g["file_id"], caption=f"📚 <b>{esc(g['title'])}</b>\n✨ Foydali bo'lsin!")
    log_act(cb.from_user.id, "READ_GUIDE", g["title"])
    await cb.answer()


# ----- Image generation (Premium) -----

@dp.message(F.text == "🎨 RASM YARATISH")
async def image_gen_btn(message: Message):
    uid = message.from_user.id
    st = get_user(uid, message.from_user)
    log_act(uid, "IMAGE_GEN", "Rasm yaratish tugmasi")
    if not is_premium(uid):
        await premium_pitch(message, "image")
        return
    st["awaiting"] = "imagegen"
    touch("users")
    await send_html(message, "🎨 Nima chizamiz? Tasavvuringizni yozing — qanchalik batafsil bo'lsa, shunchalik chiroyli chiqadi ✨\n<i>Masalan: «kechqurun Samarqand Registoni, akvarel uslubida»</i>", get_kb(uid))


async def do_image_gen(message: Message, prompt: str):
    uid = message.from_user.id
    st = get_user(uid)
    st["awaiting"] = None
    try:
        async with typing(message.chat.id, ChatAction.UPLOAD_PHOTO):
            data = await generate_image(prompt)
        st["images_made"] = st.get("images_made", 0) + 1
        add_xp(st, 15)
        touch("users")
        await message.answer_photo(BufferedInputFile(data, "edumind.png"), caption=f"🎨 Tayyor! <i>{esc(clip(prompt, 150))}</i>")
    except Exception as e:
        log.warning("imagegen: %s", e)
        await send_html(message, "😕 Rasm chiqmadi — ehtimol so'rov cheklovga tushdi yoki servis band. Boshqacharoq ta'riflab ko'ring.", get_kb(uid))


# =====================================================================
# Media handlers
# =====================================================================

@dp.message(BroadcastWaiting())
async def broadcast_capture(message: Message):
    uid = message.from_user.id
    st = get_user(uid)
    st["admin_action"] = None
    touch("users")
    pending_broadcast[uid] = (message.chat.id, message.message_id)
    total = sum(1 for u in users.values() if not u.get("banned"))
    was_blocked = sum(1 for u in users.values() if u.get("bot_blocked") and not u.get("banned"))
    note = (
        f"\n🚫 Shundan <b>{was_blocked}</b> tasi avval botni bloklagan — ularga ham urinib ko'riladi: blokdan chiqqan bo'lsa xabar yetadi, "
        "hali bloklagan bo'lsa ro'yxatda qoladi."
    ) if was_blocked else ""
    await message.answer(
        f"📢 Yuqoridagi xabarni kimga yuboramiz?\n\n👥 <b>Hammaga:</b> {total} ta foydalanuvchi (start bosgan hamma){note}\n"
        "👤 Yoki bitta foydalanuvchini tanlab, faqat unga yuboring.",
        reply_markup=bc_menu_kb(),
        reply_to_message_id=message.message_id,
    )


@dp.message(F.photo)
async def photo_handler(message: Message):
    uid = message.from_user.id
    st = get_user(uid, message.from_user)
    aw = st.get("awaiting") or ""
    if aw.startswith("receipt:"):
        await forward_receipt(message, st, int(aw.split(":")[1]), message.photo[-1].file_id, "photo")
        return
    log_act(uid, "AI_VISION", "Rasm tahlili")
    if not gemini_client:
        await send_html(message, "😕 Rasm tahlili hozir mavjud emas.", get_kb(uid))
        return
    try:
        async with typing(message.chat.id):
            file = await bot.get_file(message.photo[-1].file_id)
            buf = io.BytesIO()
            await bot.download_file(file.file_path, destination=buf)
            system = build_system(st, "image_analysis")
            answer = await asyncio.wait_for(
                gemini_media(buf.getvalue(), "image/jpeg", message.caption or "Rasmni tahlil qiling va kerak bo'lsa masalani yeching.", system),
                timeout=40,
            )
        st["queries_count"] = st.get("queries_count", 0) + 1
        add_xp(st, 10)
        touch("users")
        await send_ai(message, answer or "Rasmdan hech narsa o'qiy olmadim, aniqroq suratga oling 🙏", get_kb(uid))
    except Exception as e:
        log.warning("vision: %s", e)
        await send_html(message, "😕 Rasmni tahlil qilib bo'lmadi. Yorug'roq joyda, aniqroq suratga olib qayta yuboring.", get_kb(uid))


@dp.message(F.video | F.video_note | F.animation)
async def video_handler(message: Message):
    uid = message.from_user.id
    st = get_user(uid, message.from_user)
    log_act(uid, "VIDEO_INPUT", "Video yubordi")
    if not is_premium(uid):
        await premium_pitch(message, "video")
        return
    media = message.video or message.video_note or message.animation
    if (media.file_size or 0) > 19 * 1024 * 1024:
        await send_html(message, "📦 Video biroz katta (limit ~19 MB). Qisqaroq qism yuboring 🙏", get_kb(uid))
        return
    try:
        async with typing(message.chat.id, ChatAction.TYPING):
            file = await bot.get_file(media.file_id)
            buf = io.BytesIO()
            await bot.download_file(file.file_path, destination=buf)
            answer = await asyncio.wait_for(
                gemini_media(buf.getvalue(), "video/mp4", message.caption or "Videoni tahlil qiling: nima haqida, asosiy fikrlar va o'quv jihatdan nimani bilib olish mumkin.", build_system(st, "ai_tutor"), 2000),
                timeout=90,
            )
        st["videos_done"] = st.get("videos_done", 0) + 1
        add_xp(st, 20)
        touch("users")
        await send_ai(message, answer, get_kb(uid))
    except Exception as e:
        log.warning("video: %s", e)
        await send_html(message, "😕 Videoni tahlil qila olmadim. Boshqa formatda (MP4) yoki qisqaroq yuboring.", get_kb(uid))


@dp.message(F.voice | F.audio)
async def voice_handler(message: Message):
    uid = message.from_user.id
    st = get_user(uid, message.from_user)
    media = message.voice or message.audio
    log_act(uid, "VOICE", "Ovozli xabar")
    if not gemini_client:
        await send_html(message, "🎙 Ovozli xabarlar hozir qo'llab-quvvatlanmaydi.", get_kb(uid))
        return
    if (media.file_size or 0) > 15 * 1024 * 1024:
        await send_html(message, "🎙 Audio juda katta. Qisqaroq yuboring 🙏", get_kb(uid))
        return
    try:
        async with typing(message.chat.id, ChatAction.RECORD_VOICE):
            file = await bot.get_file(media.file_id)
            buf = io.BytesIO()
            await bot.download_file(file.file_path, destination=buf)
            mime = getattr(media, "mime_type", None) or "audio/ogg"
            prompt = "Listen to this voice message. Start with one short line 🎙 quoting what the student asked, then answer their question helpfully."
            answer = await asyncio.wait_for(gemini_media(buf.getvalue(), mime, prompt, build_system(st, st.get("mode", "ai_tutor"))), timeout=45)
        st["queries_count"] = st.get("queries_count", 0) + 1
        add_xp(st, 10)
        touch("users")
        await send_ai(message, answer, get_kb(uid))
    except Exception as e:
        log.warning("voice: %s", e)
        await send_html(message, "😕 Ovozni tushuna olmadim. Yana bir bor, aniqroq aytib ko'ring yoki matn yozing.", get_kb(uid))


@dp.message(F.document)
async def document_handler(message: Message):
    uid = message.from_user.id
    st = get_user(uid, message.from_user)
    name = (message.document.file_name or "").lower()

    if is_admin(uid) and st.get("admin_action") == "guide_file":
        if not name.endswith(".pdf"):
            await message.answer("❌ Faqat PDF yuboring.")
            return
        code, title = st.get("temp_guide_code"), st.get("temp_guide_title")
        guides[code] = {"title": title, "file_id": message.document.file_id}
        st["admin_action"] = None
        touch("guides", "users")
        await message.answer(f"✅ Qo'llanma qo'shildi!\n📘 {esc(title)}\n🔑 Kod: <code>{esc(code)}</code>", reply_markup=ADMIN_KB)
        return

    aw = st.get("awaiting") or ""
    if aw.startswith("receipt:"):
        await forward_receipt(message, st, int(aw.split(":")[1]), message.document.file_id, "document")
        return

    if not name.endswith(".pdf"):
        await send_html(message, "📄 Hozircha faqat PDF fayllarni o'qiy olaman. PDF qilib yuboring 🙏", get_kb(uid))
        return
    log_act(uid, "PDF", message.document.file_name or "pdf")
    if (message.document.file_size or 0) > 15 * 1024 * 1024:
        await send_html(message, "📦 Fayl katta (15 MB dan oshmasin).", get_kb(uid))
        return
    try:
        from pypdf import PdfReader
        async with typing(message.chat.id):
            file = await bot.get_file(message.document.file_id)
            buf = io.BytesIO()
            await bot.download_file(file.file_path, destination=buf)

            def extract():
                reader = PdfReader(buf)
                return "".join((p.extract_text() or "") for p in reader.pages[:25])

            text = await asyncio.to_thread(extract)
            if len(text.strip()) < 30:
                await send_html(message, "🤔 PDF ichidan matn topa olmadim (skaner bo'lishi mumkin). Sahifani rasm qilib yuboring — o'qib beraman 📸", get_kb(uid))
                return
            answer, _, _ = await ask_ai(uid, f"Mana PDF mazmuni. Qisqa va foydali tahlil bering:\n\n{text[:28000]}", mode="pdf", remember_it=False)
        await send_ai(message, answer, get_kb(uid))
    except Exception as e:
        log.warning("pdf: %s", e)
        await send_html(message, "😕 PDF'ni o'qib bo'lmadi. Boshqa fayl bilan urinib ko'ring.", get_kb(uid))


# =====================================================================
# Chat member updates — kim botni bloklaganini aniq bilish
# =====================================================================

@dp.my_chat_member()
async def on_my_chat_member(ev: ChatMemberUpdated):
    if ev.chat.type != "private":
        return
    uid = ev.chat.id
    st = get_user(uid, ev.from_user if ev.from_user and ev.from_user.id == uid else None)
    status = ev.new_chat_member.status
    if status in ("kicked", "left"):
        was_blocked = st.get("bot_blocked")
        mark_blocked(uid)
        if NOTIFY_ADMIN_ON_BLOCK and not was_blocked:
            stayed = dur(time.time() - (st.get("joined_ts") or time.time()))
            await notify_admins(
                f"😔 <b>Botni blokladi:</b> {user_link(uid, st.get('full_name'))} ({esc(uname_of(st))})\n"
                f"🕒 {stayed} biz bilan bo'ldi • 💬 {st.get('queries_count', 0)} ta savol\n"
                f"👉 /u_{uid}",
                silent=True,
            )
    elif status == "member":
        if st.get("bot_blocked"):
            st["bot_blocked"] = False
            st["unblocked_ts"] = time.time()
            log_act(uid, "UNBLOCKED", "Botni qayta ochdi")
            if NOTIFY_ADMIN_ON_BLOCK:
                await notify_admins(f"💚 <b>Qaytdi!</b> {user_link(uid, st.get('full_name'))} botni blokdan chiqardi.", silent=True)
        touch("users")


# =====================================================================
# ADMIN PANEL (faqat ADMIN_IDS, PINsiz)
# =====================================================================

PER_PAGE = {"users": 7, "recent": 8, "blocked": 7, "questions": 8, "leads": 8, "purchases": 6, "referrals": 8, "feedbacks": 6}

PANEL_FILTERS = {
    "users": [("all", "👥 Hammasi"), ("prem", "💎 Premium"), ("new", "🆕 Bugun"), ("nouser", "🕳 Usernamesiz")],
    "blocked": [("blocked", "🚫 Bloklaganlar"), ("banned", "⛔ Ban qilingan")],
    "purchases": [("pending", "⏳ Kutilmoqda"), ("all", "📜 Hammasi")],
}
DEFAULT_FILTER = {"users": "all", "blocked": "blocked", "purchases": "pending"}


def user_line(n: int, uid_str: str, u: dict) -> str:
    badge = "💎" if u.get("status") in ("Premium", "VIP") else "🆓"
    flags = (" 🚫" if u.get("bot_blocked") else "") + (" ⛔" if u.get("banned") else "")
    lvl, _, _ = level_of(u.get("points", 0))
    return (
        f"<b>{n}.</b> {user_link(uid_str, u.get('full_name'))} • {esc(uname_of(u))}{flags}\n"
        f"   🆔 <code>{uid_str}</code> • {badge} • Lv{lvl} • ⭐{u.get('points', 0)} • 💬{u.get('queries_count', 0)}\n"
        f"   🗓 {esc((u.get('joined_at') or '—')[:16])} • 👁 {ago(u.get('last_seen'))}\n"
        f"   👉 /u_{uid_str}"
    )


def panel_content(panel: str, flt: str):
    """Qaytaradi: (sarlavha, kirish matni, elementlar ro'yxati)"""
    if panel == "users":
        items = sorted(users.items(), key=lambda x: x[1].get("joined_ts") or 0)  # avval qo'shilganlar birinchi
        if flt == "prem":
            items = [i for i in items if i[1].get("status") in ("Premium", "VIP")]
        elif flt == "new":
            items = [i for i in items if (i[1].get("joined_at") or "").startswith(today())]
        elif flt == "nouser":
            items = [i for i in items if not i[1].get("username")]
        lines = [user_line(n, k, u) for n, (k, u) in enumerate(items, 1)]
        return (f"👥 <b>Foydalanuvchilar</b> — {len(items)} ta (jami bazada {len(users)})",
                "Ism ustiga bossangiz — Telegram profili ochiladi. <code>/u_ID</code> — boshqaruv.", lines)

    if panel == "recent":
        act = sorted(((k, u) for k, u in users.items() if u.get("last_seen")), key=lambda x: x[1]["last_seen"], reverse=True)[:60]
        lines = []
        for n, (k, u) in enumerate(act, 1):
            online = "🟢" if time.time() - u["last_seen"] < 300 else ("🟡" if time.time() - u["last_seen"] < 3600 else "⚪️")
            lines.append(
                f"{online} <b>{n}.</b> {user_link(k, u.get('full_name'))} • {esc(uname_of(u))}\n"
                f"   ⏱ {ago(u['last_seen'])} • 🧭 {esc(u.get('mode', ''))}\n"
                f"   💭 <i>{esc(clip(u.get('last_query') or 'hali savol bermagan', 70))}</i> • /u_{k}"
            )
        return ("🟢 <b>Yaqindagi faollar</b>", "Eng oxirgi faol bo'lgan 60 kishi — 🟢 hozir • 🟡 1 soat ichida • ⚪️ oldinroq", lines)

    if panel == "blocked":
        if flt == "banned":
            items = [(k, u) for k, u in users.items() if u.get("banned")]
            lines = [f"⛔ <b>{n}.</b> {user_link(k, u.get('full_name'))} • {esc(uname_of(u))} • <code>{k}</code>\n   👉 /u_{k} (blokdan chiqarish)" for n, (k, u) in enumerate(items, 1)]
            return (f"⛔ <b>Siz ban qilganlar</b> — {len(items)} ta", "Bu foydalanuvchilarni siz cheklagansiz.", lines)
        items = sorted(((k, u) for k, u in users.items() if u.get("bot_blocked")), key=lambda x: x[1].get("blocked_ts") or 0)  # avval bloklaganlar birinchi
        lines = []
        for n, (k, u) in enumerate(items, 1):
            stayed = dur((u.get("blocked_ts") or time.time()) - (u.get("joined_ts") or 0)) if u.get("joined_ts") else "—"
            lines.append(
                f"🚫 <b>{n}.</b> {user_link(k, u.get('full_name'))} • {esc(uname_of(u))}\n"
                f"   🆔 <code>{k}</code>\n"
                f"   🕒 Bloklagan: {esc(u.get('blocked_at') or 'aniq emas')} ({ago(u.get('blocked_ts'))})\n"
                f"   📉 Biz bilan: {stayed} • 💬 {u.get('queries_count', 0)} ta savol\n"
                f"   💭 <i>{esc(clip(u.get('last_query') or '—', 60))}</i>"
            )
        return (f"🚫 <b>Botni bloklaganlar</b> — {len(items)} ta",
                "Afsus, bu odamlar botni tark etishgan 😔 Ular qaytib kelsa (blokdan chiqarsa), ro'yxatdan o'zi chiqib ketadi.", lines)

    if panel == "questions":
        lines = []
        for n, q in enumerate(queries_log, 1):
            u = users.get(str(q["uid"]), {})
            lines.append(
                f"💬 <b>{n}.</b> {user_link(q['uid'], q.get('name'))} • {esc(('@' + q['username']) if q.get('username') else 'username yo‘q')}\n"
                f"   🕒 {esc(q['time'])} • 🧭 {esc(q.get('mode', ''))}\n"
                f"   ❓ <i>{esc(clip(q['text'], 180))}</i>"
            )
        return ("💬 <b>Kim nima so'radi</b>", "Oxirgi savollar (eng yangisi tepada). Matnlarni foydalanuvchi yaxshiroq xizmat olishi uchun ko'ring 🙂", lines)

    if panel == "leads":
        kind_names = {
            "image_gen": "🎨 rasm yaratish", "video": "🎥 video tahlil", "stars_view": "⭐ Stars ko'rdi", "stars_buy": "⭐ Stars buyurtma",
            "premium_view": "💎 Premium ko'rdi", "premium_buy": "💎 Premium buyurtma", "gemini_view": "✨ Gemini ko'rdi", "gemini_buy": "✨ Gemini buyurtma",
        }
        items = sorted(((k, u) for k, u in users.items() if u.get("interest")), key=lambda x: x[1].get("last_interest", 0), reverse=True)
        lines = []
        for n, (k, u) in enumerate(items, 1):
            parts = " • ".join(f"{kind_names.get(kind, kind)} ×{c}" for kind, c in u["interest"].items())
            hot = "🔥" if any(x.endswith("_buy") for x in u["interest"]) else "👀"
            prem = "💎 allaqachon premium" if u.get("status") in ("Premium", "VIP") else "🆓 hali premium emas"
            lines.append(
                f"{hot} <b>{n}.</b> {user_link(k, u.get('full_name'))} • {esc(uname_of(u))}\n"
                f"   {parts}\n   ⏱ {ago(u.get('last_interest'))} • {prem} • /u_{k}"
            )
        return ("🔥 <b>Qiziqqanlar (potensial xaridorlar)</b>",
                "Rasm yaratish, video tahlil, Stars, Premium va Gemini'ga qiziqqanlar. 🔥 — buyurtma bergan, 👀 — faqat ko'rgan.", lines)

    if panel == "purchases":
        items = [r for r in purchases if flt == "all" or r["status"] == "pending"]
        icons = {"pending": "⏳", "done": "✅", "rejected": "❌"}
        lines = [
            f"{icons.get(r['status'], '•')} <b>#{r['id']}</b> • {esc(r['service'])}\n"
            f"   👤 {user_link(r['uid'], r.get('full_name'))} • {esc(('@' + r['username']) if r.get('username') else 'username yo‘q')}\n"
            f"   🧾 {esc(r['details'])}\n   🕒 {esc(r['time'])} • {'📸 chek bor' if r.get('receipt_at') else '⏳ chek yo‘q'} • 👉 /r_{r['id']}"
            for r in items
        ]
        return (f"🛒 <b>Xarid so'rovlari</b> — {len(items)} ta", "Har bir so'rovni /r_raqam orqali tasdiqlash yoki rad etish mumkin.", lines)

    if panel == "referrals":
        top = sorted(((k, u) for k, u in users.items() if u.get("referrals_count", 0) > 0), key=lambda x: x[1]["referrals_count"], reverse=True)[:5]
        top_txt = ""
        if top:
            top_txt = "🏅 <b>Eng ko'p taklif qilganlar:</b>\n" + "\n".join(
                f"   {i}. {user_link(k, u.get('full_name'))} — <b>{u['referrals_count']}</b> ta" for i, (k, u) in enumerate(top, 1)
            ) + "\n"
        lines = [
            f"➕ {user_link(r['new_user_id'], r.get('new_user_name'))} ← {user_link(r['inviter_id'], r.get('inviter_name'))}\n   🕒 {esc(r['time'])}"
            for r in referrals_log
        ]
        return ("🎁 <b>Referral tarixi</b>", top_txt + "Kim kimni taklif qilgani:", lines)

    if panel == "feedbacks":
        lines = [
            f"✍️ {user_link(f['uid'], f.get('full_name'))} • {esc(('@' + f['username']) if f.get('username') else 'username yo‘q')}\n"
            f"   🕒 {esc(f['time'])}\n   💌 <i>{esc(clip(f['text'], 500))}</i>"
            for f in feedbacks
        ]
        return ("✍️ <b>Fikr va takliflar</b>", "Foydalanuvchilar sizga yozgan so'zlar:", lines)

    return ("❔", "", [])


EMPTY_TEXTS = {
    "users": "Hali hech kim yo'q. Birinchi foydalanuvchini kutamiz 🌱",
    "recent": "Hozircha faollik yo'q 😴",
    "blocked": "Ajoyib xabar — hech kim botni bloklamagan! 🎉",
    "questions": "Hali savol berilmagan 🤫",
    "leads": "Hozircha hech kim premium funksiyalarga qiziqmagan.",
    "purchases": "Kutilayotgan buyurtma yo'q ✨",
    "referrals": "Hozircha referral orqali kelganlar yo'q.",
    "feedbacks": "Fikrlar hali kelmagan.",
}


def render_panel(panel: str, flt: str, page: int):
    title, intro, lines = panel_content(panel, flt)
    per = PER_PAGE.get(panel, 8)
    pages = max(1, math.ceil(len(lines) / per))
    page = max(0, min(page, pages - 1))
    chunk = lines[page * per:(page + 1) * per]
    body = "\n\n".join(chunk) if chunk else f"<i>{EMPTY_TEXTS.get(panel, 'Bo‘sh')}</i>"
    text = f"{title}\n{intro}\n\n{body}\n\n📄 Sahifa {page + 1}/{pages}"
    while len(text) > 4000 and chunk:  # Telegram limitidan oshib ketmasligi uchun
        chunk.pop()
        body = "\n\n".join(chunk)
        text = f"{title}\n{intro}\n\n{body}\n\n📄 Sahifa {page + 1}/{pages} (qisqartirildi)"
    rows = []
    if panel in PANEL_FILTERS:
        rows.append([((f"✔ {label}" if key == flt else label), f"adm|{panel}|{key}|0") for key, label in PANEL_FILTERS[panel]])
    nav = []
    if page > 0:
        nav.append(("⬅️", f"adm|{panel}|{flt}|{page - 1}"))
    nav.append((f"{page + 1}/{pages}", "adm|noop|x|0"))
    if page < pages - 1:
        nav.append(("➡️", f"adm|{panel}|{flt}|{page + 1}"))
    rows.append(nav)
    rows.append([("🔄 Yangilash", f"adm|{panel}|{flt}|{page}")])
    return text, ikb(rows)


async def open_panel(message: Message, panel: str):
    flt = DEFAULT_FILTER.get(panel, "x")
    st = get_user(message.from_user.id)
    st["admin_action"] = None
    text, kb = render_panel(panel, flt, 0)
    await safe_answer(message, text, kb)


@dp.callback_query(F.data.startswith("adm|"), AdminOnly())
async def cb_admin_panel(cb: CallbackQuery):
    _, panel, flt, page = cb.data.split("|")
    if panel == "noop":
        await cb.answer()
        return
    text, kb = render_panel(panel, flt, int(page))
    try:
        await cb.message.edit_text(text, reply_markup=kb)
    except TelegramBadRequest:
        pass  # o'zgarmagan bo'lsa
    await cb.answer()


def admin_stats_text() -> str:
    t = time.time()
    total = len(users)
    active_24 = sum(1 for u in users.values() if t - u.get("last_seen", 0) < 86400)
    online = sum(1 for u in users.values() if t - u.get("last_seen", 0) < 300)
    new_today = sum(1 for u in users.values() if (u.get("joined_at") or "").startswith(today()))
    new_week = sum(1 for u in users.values() if t - u.get("joined_ts", 0) < 7 * 86400)
    prem = sum(1 for u in users.values() if u.get("status") in ("Premium", "VIP"))
    blocked = sum(1 for u in users.values() if u.get("bot_blocked"))
    banned = sum(1 for u in users.values() if u.get("banned"))
    queries = sum(u.get("queries_count", 0) for u in users.values())
    pending = sum(1 for r in purchases if r["status"] == "pending")
    done = sum(1 for r in purchases if r["status"] == "done")
    hot = sum(1 for u in users.values() if u.get("interest"))
    refs = sum(u.get("referrals_count", 0) for u in users.values())
    avg_ms = int(metrics.get("ms_total", 0) / metrics["count"]) if metrics.get("count") else 0
    ok = metrics.get("ok", {})
    fail = metrics.get("fail", {})
    prov = "\n".join(f"   {PROVIDER_LABELS.get(k, k)}: ✅{ok.get(k, 0)} ❌{fail.get(k, 0)}" for k in CLIENTS if configured(k)) or "   —"
    return (
        "📈 <b>EduMind hisoboti</b>\n\n"
        f"👥 Jami: <b>{total}</b> • 🆕 bugun <b>{new_today}</b> • haftada <b>{new_week}</b>\n"
        f"🟢 Hozir onlayn: <b>{online}</b> • 24 soatda faol: <b>{active_24}</b>\n"
        f"💎 Premium: <b>{prem}</b> • 🔥 Qiziqqanlar: <b>{hot}</b>\n"
        f"🚫 Bloklaganlar: <b>{blocked}</b> • ⛔ Ban: <b>{banned}</b>\n"
        f"💬 Jami savollar: <b>{queries}</b> • 🎁 Referrallar: <b>{refs}</b>\n"
        f"🛒 Buyurtmalar: ⏳{pending} • ✅{done}\n\n"
        f"⚡ O'rtacha AI javob vaqti: <b>{avg_ms} ms</b>\n"
        f"🤖 Provayderlar:\n{prov}\n\n"
        f"⏰ {ts()} ({getattr(TZ, 'key', 'UTC+5')})"
    )


@dp.message(Command("admin"), AdminOnly())
@dp.message(F.text == "👨‍💼 ADMIN PANEL", AdminOnly())
async def admin_entry(message: Message):
    st = get_user(message.from_user.id, message.from_user)
    st["admin_action"] = None
    online = sum(1 for u in users.values() if time.time() - u.get("last_seen", 0) < 300)
    await send_html(message, f"👨‍💼 <b>Admin panel</b>\nXush kelibsiz! Hozir {online} kishi onlayn 🟢\nBo'limni tanlang 👇", ADMIN_KB)


@dp.message(F.text == "📈 BOT HISOBOTI", AdminOnly())
async def admin_stats(message: Message):
    await send_html(message, admin_stats_text(), ADMIN_KB)


@dp.message(F.text == "👥 FOYDALANUVCHILAR", AdminOnly())
async def admin_users(message: Message):
    await open_panel(message, "users")


@dp.message(F.text == "🟢 YAQINDAGI FAOLLAR", AdminOnly())
async def admin_recent(message: Message):
    await open_panel(message, "recent")


@dp.message(F.text == "🚫 BLOKLAGANLAR", AdminOnly())
async def admin_blocked(message: Message):
    await open_panel(message, "blocked")


@dp.message(F.text == "💬 NIMA SO'RADILAR", AdminOnly())
async def admin_questions(message: Message):
    await open_panel(message, "questions")


@dp.message(F.text == "🛒 XARID SO'ROVLARI", AdminOnly())
async def admin_purchases(message: Message):
    await open_panel(message, "purchases")


@dp.message(F.text == "🔥 QIZIQQANLAR", AdminOnly())
async def admin_leads(message: Message):
    await open_panel(message, "leads")


@dp.message(F.text == "🎁 REFERRAL TARIXI", AdminOnly())
async def admin_referrals(message: Message):
    await open_panel(message, "referrals")


@dp.message(F.text == "✍️ FIKRLAR", AdminOnly())
async def admin_feedbacks(message: Message):
    await open_panel(message, "feedbacks")


@dp.message(F.text == "🔎 QIDIRISH", AdminOnly())
async def admin_search_start(message: Message):
    st = get_user(message.from_user.id)
    st["admin_action"] = "search"
    touch("users")
    await send_html(message, "🔎 Ism, @username yoki ID yozing:", ADMIN_KB)


@dp.message(F.text == "📚 PDF QO'LLANMA QO'SHISH", AdminOnly())
async def admin_guide_start(message: Message):
    st = get_user(message.from_user.id)
    st["admin_action"] = "guide_code"
    touch("users")
    await send_html(message, "📚 <b>Yangi qo'llanma (1/3)</b>\nQisqa kod kiriting (masalan: <code>math1</code>):", ADMIN_KB)


@dp.message(F.text == "📢 BARCHAGA XABAR", AdminOnly())
async def admin_broadcast_start(message: Message):
    st = get_user(message.from_user.id)
    st["admin_action"] = "broadcast"
    touch("users")
    await send_html(message, "📢 <b>Ommaviy xabar</b>\nIstalgan kontentni yuboring: matn, rasm, video, ovoz, hujjat, stiker...\nBekor qilish: /cancel", ADMIN_KB)


@dp.message(Command("cancel"))
async def cancel_cmd(message: Message):
    uid = message.from_user.id
    st = get_user(uid, message.from_user)
    st["admin_action"] = None
    st["awaiting"] = None
    pending_broadcast.pop(uid, None)
    touch("users")
    await send_html(message, "👌 Bekor qilindi.", ADMIN_KB if is_admin(uid) else get_kb(uid))


def user_detail(uid_str: str):
    u = users.get(uid_str)
    if not u:
        return "Topilmadi", None
    uid = int(uid_str)
    recent_q = [q for q in queries_log if q["uid"] == uid][:5]
    q_txt = "\n".join(f"   • <i>{esc(clip(q['text'], 90))}</i>" for q in recent_q) or "   —"
    interest = ", ".join(f"{k}×{v}" for k, v in u.get("interest", {}).items()) or "—"
    lvl, title, _ = level_of(u.get("points", 0))
    text = (
        f"👤 <b>{user_link(uid_str, u.get('full_name'))}</b> • {esc(uname_of(u))}\n"
        f"🆔 <code>{uid_str}</code>\n"
        f"🏷 Status: <b>{esc(u.get('status'))}</b> • {title} (Lv{lvl}) • ⭐{u.get('points', 0)} XP\n"
        f"💬 Savollar: <b>{u.get('queries_count', 0)}</b> • 🔥 Seriya: {u.get('streak', 0)} • 🎁 Referral: {u.get('referrals_count', 0)}\n"
        f"🗓 Qo'shilgan: {esc(u.get('joined_at'))}\n"
        f"👁 Oxirgi faollik: {ago(u.get('last_seen'))}\n"
        f"🧭 Rejim: {esc(u.get('mode'))} • AI: {esc(u.get('provider'))} • oxirgi: {esc(u.get('last_provider') or '—')}\n"
        f"🚫 Bot bloklangan: <b>{'ha' if u.get('bot_blocked') else 'yo‘q'}</b>"
        f"{' (' + esc(u.get('blocked_at')) + ')' if u.get('bot_blocked') else ''} • ⛔ Ban: <b>{'ha' if u.get('banned') else 'yo‘q'}</b>\n"
        f"🎯 Qiziqishlari: {esc(interest)}\n\n"
        f"💭 <b>Oxirgi savollari:</b>\n{q_txt}"
    )
    prem_btn = ("💎 Premiumni olib tashlash", f"ad|unprem|{uid_str}") if u.get("status") in ("Premium", "VIP") else ("💎 Premium berish", f"ad|prem|{uid_str}")
    ban_btn = ("✅ Banni olib tashlash", f"ad|unban|{uid_str}") if u.get("banned") else ("⛔ Ban qilish", f"ad|ban|{uid_str}")
    kb = ikb([[prem_btn], [ban_btn], [("✉️ Xabar yuborish", f"ad|dm|{uid_str}"), ("➕ +100 XP", f"ad|xp|{uid_str}")]])
    return text, kb


@dp.message(F.text.regexp(r"^/u_(\d+)"), AdminOnly())
async def admin_user_open(message: Message):
    uid_str = re.match(r"^/u_(\d+)", message.text).group(1)
    text, kb = user_detail(uid_str)
    await send_html(message, text, kb)


@dp.message(F.text.regexp(r"^/r_(\d+)"), AdminOnly())
async def admin_req_open(message: Message):
    rid = int(re.match(r"^/r_(\d+)", message.text).group(1))
    r = next((x for x in purchases if x.get("id") == rid), None)
    if not r:
        await message.answer("Bunday buyurtma yo'q.")
        return
    icons = {"pending": "⏳ Kutilmoqda", "done": "✅ Bajarilgan", "rejected": "❌ Rad etilgan"}
    kb = ikb([[("✅ Bajarildi", f"pr|ok|{rid}"), ("❌ Rad etish", f"pr|no|{rid}")]]) if r["status"] == "pending" else None
    await send_html(
        message,
        f"🛒 <b>Buyurtma #{rid}</b> — {icons.get(r['status'])}\n👤 {user_link(r['uid'], r.get('full_name'))} • <code>{r['uid']}</code>\n"
        f"📦 {esc(r['service'])}\n🧾 {esc(r['details'])}\n🕒 {esc(r['time'])}",
        kb,
    )


@dp.callback_query(F.data.startswith("ad|"), AdminOnly())
async def cb_admin_user_action(cb: CallbackQuery):
    _, action, uid_str = cb.data.split("|")
    u = users.get(uid_str)
    if not u:
        await cb.answer("Topilmadi", show_alert=True)
        return
    target = int(uid_str)
    note = "Bajarildi"
    if action == "prem":
        u["status"] = "Premium"
        with contextlib.suppress(Exception):
            await bot.send_message(target, "💎 <b>Tabriklaymiz!</b> Sizga Premium status berildi — endi rasm yaratish va video tahlil ochiq ✨")
        note = "Premium berildi"
    elif action == "unprem":
        u["status"] = "Standart"
        note = "Premium olib tashlandi"
    elif action == "ban":
        u["banned"] = True
        note = "Ban qilindi"
    elif action == "unban":
        u["banned"] = False
        note = "Ban olib tashlandi"
    elif action == "xp":
        add_xp(u, 100)
        note = "+100 XP"
    elif action == "dm":
        st = get_user(cb.from_user.id)
        st["admin_action"] = f"dm:{uid_str}"
        touch("users")
        await cb.message.answer(f"✉️ {esc(u.get('full_name'))} ga yuboriladigan xabarni yozing (/cancel — bekor):")
        await cb.answer()
        return
    touch("users")
    log_act(cb.from_user.id, "ADMIN_ACTION", f"{action} → {uid_str}")
    text, kb = user_detail(uid_str)
    with contextlib.suppress(Exception):
        await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer(note)


def bc_menu_kb():
    return ikb([
        [("✅ Hammaga yuborish", "bc|go")],
        [("👤 Bittasiga yuborish", "bc|one")],
        [("❌ Bekor qilish / Tugatish", "bc|no")],
    ])


@dp.callback_query(F.data.startswith("bc|"), AdminOnly())
async def cb_broadcast(cb: CallbackQuery):
    uid = cb.from_user.id
    st = get_user(uid)
    parts = cb.data.split("|")
    action = parts[1]
    item = pending_broadcast.get(uid)
    if action == "no":
        pending_broadcast.pop(uid, None)
        if st.get("admin_action") == "bc_pick":
            st["admin_action"] = None
            touch("users")
        await cb.message.edit_text("❌ Ommaviy xabar bekor qilindi.")
        await cb.answer()
        return
    if not item:
        await cb.message.edit_text("⚠️ Xabar topilmadi (bot qayta ishga tushgan bo'lishi mumkin). 📢 BARCHAGA XABAR orqali qaytadan boshlang.")
        await cb.answer()
        return
    if action == "go":
        pending_broadcast.pop(uid, None)
        await cb.message.edit_text("🚀 Yuborish boshlandi… Tugagach hisobot beraman.")
        await cb.answer()
        spawn(run_broadcast(uid, item[0], item[1]))
        return
    if action == "one":
        st["admin_action"] = "bc_pick"
        touch("users")
        await cb.message.answer("👤 Kimga yuboramiz? <b>ID</b>, <b>@username</b> yoki <b>ism</b> yozing (/cancel — bekor):")
        await cb.answer()
        return
    if action == "to" and len(parts) == 3 and parts[2].isdigit():
        tid = int(parts[2])
        u = users.get(str(tid), {})
        res = await deliver_copy(tid, item[0], item[1])
        who = f"{user_link(tid, u.get('full_name'))} ({esc(uname_of(u))})"
        if res == "ok":
            verdict = f"✅ Yetkazildi: {who}"
        elif res == "blocked":
            verdict = f"🚫 {who} botni bloklagan — xabar yetmadi."
        else:
            verdict = f"⚠️ Yuborilmadi: {who}\n<code>{esc(res)}</code>"
        log_act(uid, "BROADCAST_ONE", f"{tid}: {res}")
        await cb.message.answer(verdict + "\n\nYana kimga yuboramiz?", reply_markup=bc_menu_kb())
        await cb.answer("Tayyor")
        return
    await cb.answer()


async def deliver_copy(tid: int, from_chat: int, msg_id: int) -> str:
    """Bitta foydalanuvchiga nusxa yuboradi va blok holatini yangilaydi. Natija: 'ok' | 'blocked' | 'fail:<sabab>'."""
    for _ in range(2):
        try:
            await bot.copy_message(tid, from_chat, msg_id)
            mark_unblocked(tid)
            return "ok"
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after + 1)
        except TelegramForbiddenError:
            mark_blocked(tid)
            return "blocked"
        except Exception as e:
            return f"fail:{clip(e, 80)}"
    return "fail:juda ko'p so'rov (keyinroq urinib ko'ring)"


async def run_broadcast(admin_id: int, from_chat: int, msg_id: int):
    # Start bosgan HAMMA (avval bloklaganlar ham): blokdan chiqqanlarga xabar yetadi, hali bloklaganlar ro'yxatda qoladi.
    targets = [int(k) for k, u in users.items() if not u.get("banned")]
    sent = failed = newly_blocked = still_blocked = returned = 0
    for tid in targets:
        was_flagged = bool(users.get(str(tid), {}).get("bot_blocked"))
        res = await deliver_copy(tid, from_chat, msg_id)
        if res == "ok":
            sent += 1
            if was_flagged:
                returned += 1
        elif res == "blocked":
            if was_flagged:
                still_blocked += 1
            else:
                newly_blocked += 1
        else:
            failed += 1
        await asyncio.sleep(0.04)
    log_act(admin_id, "BROADCAST", f"jami {len(targets)}, ok {sent}, yangi blok {newly_blocked}, qaytgan {returned}")
    touch("users")
    await bot.send_message(
        admin_id,
        f"📢 <b>Ommaviy xabar yakunlandi!</b>\n\n👥 Maqsad: {len(targets)}\n✅ Yetkazildi: {sent}\n"
        f"↩️ Blokdan chiqqanlar (qaytganlar): {returned}\n🚫 Yangi bloklaganlar: {newly_blocked}\n🔒 Hali ham bloklagan: {still_blocked}\n⚠️ Xato: {failed}",
        reply_markup=ADMIN_KB,
    )


# =====================================================================
# Main text handler
# =====================================================================

def st_pending_missing(admin_id: int) -> bool:
    return admin_id not in pending_broadcast


async def handle_admin_text(message: Message, st: dict, text: str) -> bool:
    action = st.get("admin_action")
    if not action:
        return False
    if action == "search":
        st["admin_action"] = None
        q = text.lstrip("@").lower()
        found = [(k, u) for k, u in users.items() if q == k or q == (u.get("username") or "").lower() or q in (u.get("full_name") or "").lower()][:8]
        touch("users")
        if not found:
            await send_html(message, "🤷 Hech kim topilmadi.", ADMIN_KB)
        else:
            await send_html(message, "🔎 <b>Topilganlar:</b>\n\n" + "\n\n".join(user_line(i, k, u) for i, (k, u) in enumerate(found, 1)), ADMIN_KB)
        return True
    if action == "bc_pick":
        if st_pending_missing(message.from_user.id):
            st["admin_action"] = None
            touch("users")
            await send_html(message, "⚠️ Yuboriladigan xabar topilmadi. 📢 BARCHAGA XABAR orqali qaytadan boshlang.", ADMIN_KB)
            return True
        q = text.lstrip("@").strip().lower()
        found = [(k, u) for k, u in users.items() if q and (q == k or q == (u.get("username") or "").lower() or q in (u.get("full_name") or "").lower())][:8]
        if not found:
            await send_html(message, "🤷 Hech kim topilmadi. Boshqa ID, @username yoki ism yozing (yoki /cancel):", ADMIN_KB)
            return True
        st["admin_action"] = None
        touch("users")
        rows = [[(f"{'🚫 ' if u.get('bot_blocked') else ''}{clip(u.get('full_name') or k, 22)} • {('@' + u['username']) if u.get('username') else k}", f"bc|to|{k}")]
                for k, u in found]
        rows.append([("🔁 Boshqa qidirish", "bc|one"), ("❌ Bekor qilish", "bc|no")])
        await send_html(message, "🔎 <b>Kimga yuboramiz?</b> Tanlang 👇", ikb(rows))
        return True
    if action == "guide_code":
        st["temp_guide_code"] = re.sub(r"\W+", "", text.lower())[:30] or str(int(time.time()))
        st["admin_action"] = "guide_title"
        touch("users")
        await send_html(message, "📚 <b>(2/3)</b> Qo'llanma nomini yozing:", ADMIN_KB)
        return True
    if action == "guide_title":
        st["temp_guide_title"] = text[:80]
        st["admin_action"] = "guide_file"
        touch("users")
        await send_html(message, "📚 <b>(3/3)</b> Endi PDF faylni yuboring:", ADMIN_KB)
        return True
    if action.startswith("dm:"):
        target = int(action.split(":")[1])
        st["admin_action"] = None
        touch("users")
        try:
            await bot.send_message(target, f"💌 <b>EduMind jamoasidan xabar:</b>\n\n{esc(text)}")
            await send_html(message, "✅ Xabar yetkazildi.", ADMIN_KB)
        except TelegramForbiddenError:
            mark_blocked(target)
            await send_html(message, "🚫 Foydalanuvchi botni bloklagan, xabar yetmadi.", ADMIN_KB)
        except Exception as e:
            await send_html(message, f"⚠️ Yuborilmadi: {esc(clip(e, 100))}", ADMIN_KB)
        return True
    return False


@dp.message(F.text)
async def text_handler(message: Message):
    uid = message.from_user.id
    text = message.text.strip()
    if not text or text.startswith("/"):
        return
    st = get_user(uid, message.from_user)

    if is_admin(uid) and await handle_admin_text(message, st, text):
        return

    aw = st.get("awaiting")
    if aw:
        if aw.startswith("purchase:"):
            key = aw.split(":")[1]
            details = text
            if key == "stars":
                m = re.search(r"\d+", text.replace(" ", "").replace(",", ""))
                n = int(m.group()) if m else 0
                if n < 50 or n > 20000:
                    await send_html(message, "🙂 Iltimos, 50 dan 20 000 gacha butun son yozing ⭐\nMasalan: <code>150</code>\n<i>Bekor qilish: /cancel</i>", get_kb(uid))
                    return
                details = f"{n} ta Stars — {money(stars_price(n))}"
            st["awaiting"] = None
            touch("users")
            await place_order(message, uid, key, details)
            return
        if aw.startswith("receipt:") and len(text) < 60 and re.search(r"to['’‘`]?lad|to['’‘`]?lov|tolov|chek|check|оплат|чек", text.lower()):
            await send_html(message, "📸 Rahmat! Endi shu yerga chekning <b>skrinshotini</b> (rasm) yuboring — admin darrov ko'radi 🙌", payment_kb(int(aw.split(":")[1])))
            return
        if aw == "feedback":
            st["awaiting"] = None
            feedbacks.insert(0, {"time": ts(), "uid": uid, "full_name": st.get("full_name"), "username": st.get("username"), "text": text})
            del feedbacks[200:]
            touch("feedbacks", "users")
            log_act(uid, "FEEDBACK", text)
            await notify_admins(f"💌 <b>Yangi fikr</b> — {user_link(uid, st.get('full_name'))} ({esc(uname_of(st))})\n\n<i>{esc(clip(text, 800))}</i>")
            await send_html(message, pick(["💖 Rahmat! Fikringiz adminga yetkazildi.", "🙏 Samimiy fikringiz uchun katta rahmat — albatta o'qiymiz!"]), get_kb(uid))
            return
        if aw == "quiz_topic":
            st["awaiting"] = None
            touch("users")
            log_act(uid, "QUIZ", text)
            try:
                await make_quiz(message, clip(text, 80))
            except Exception as e:
                log.warning("quiz: %s", e)
                await send_html(message, "😕 Hozir quiz tuza olmadim. Birozdan keyin qayta urinib ko'ring.", get_kb(uid))
            return
        if aw == "imagegen":
            if not is_premium(uid):
                st["awaiting"] = None
                await premium_pitch(message, "image")
                return
            await do_image_gen(message, text)
            return

    if len(text) <= 30 and GREET_RE.match(text):
        await send_html(message, welcome_text(st, message.from_user.first_name), get_kb(uid))
        return
    if len(text) <= 40 and THANKS_RE.search(text):
        await send_html(message, pick([
            "🤗 Arziydi! Sizga yordam bera olganimdan xursandman 💙",
            "😊 Marhamat! Doim xizmatingizdaman — yana savol bo'lsa yozing ✨",
            "💙 Sizga ham rahmat! Birga o'rganish quvonch 🌟",
        ]), get_kb(uid))
        return

    n_star = stars_intent(text)
    if n_star is not None:
        if n_star < 50 or n_star > 20000:
            await send_html(message, "⭐ Stars miqdori 50 dan 20 000 gacha bo'lishi kerak. Tayyor paketlardan tanlang yoki to'g'ri son yozing 👇", product_kb("stars"))
            return
        details = f"{n_star} ta Stars — {money(stars_price(n_star))}"
        await place_order(message, uid, "stars", details)
        return

    if uid in BUSY:
        await message.answer(pick(["⏳ Oldingi savolingizni hali yozyapman, bir soniya…", "✍️ Sal sabr — avvalgi javob tayyorlanyapti!"]))
        return

    mode = st.get("mode", "ai_tutor")
    log_act(uid, f"AI_QUERY", f"[{mode}] {text}")
    queries_log.insert(0, {"time": ts(), "uid": uid, "name": st.get("full_name"), "username": st.get("username"), "mode": mode, "text": clip(text, 400)})
    del queries_log[5000:]
    touch("queries")

    BUSY.add(uid)
    try:
        async with typing(message.chat.id):
            answer, provider, ms = await ask_ai(uid, text, mode=mode)
        await send_ai(message, answer + warm_tail(), get_kb(uid))
        bonus = daily_bonus(st)
        if bonus:
            touch("users")
            await message.answer(f"🔥 {st['streak']} kunlik seriya! Bugungi bonus: <b>+{bonus} XP</b> 🏆")
        await maybe_upsell(message, st, uid)
    except Exception as e:
        log.warning("AI error: %s", e)
        await send_html(message, pick([
            "😕 Hozir javob tayyorlay olmadim — servislar band bo'lishi mumkin. Bir daqiqadan so'ng qayta yozing, albatta yordam beraman 🙏",
            "🙈 Ups, texnik uzilish. Savolingizni qaytadan yuboring, bu safar bo'ladi!",
        ]), get_kb(uid))
    finally:
        BUSY.discard(uid)


# =====================================================================
# Entry point
# =====================================================================

async def main():
    global BOT_USERNAME
    me = await bot.get_me()
    BOT_USERNAME = me.username or ""
    print(f"EduMind AI v7.0 starting… @{me.username} (id={me.id})")
    if WEBAPP_URL:
        try:
            await bot.set_chat_menu_button(menu_button=MenuButtonWebApp(text="🚀 Open", web_app=WebAppInfo(url=webapp_url())))
            print("Mini App: ulandi ✅", WEBAPP_URL)
        except Exception as e:
            print("Mini App menyu tugmasi o'rnatilmadi:", e)
    else:
        print("Mini App: o'chiq (.env ga WEBAPP_URL=https://... qo'shing)")
    print("Providers:", ", ".join(k for k in CLIENTS if configured(k)) or "yo'q!")
    flush_task = asyncio.create_task(flusher())
    try:
        await bot.delete_webhook(drop_pending_updates=False)
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        flush_task.cancel()
        for t in active_pomodoros.values():
            t.cancel()
        await flush()
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("EduMind AI stopped. 👋")
