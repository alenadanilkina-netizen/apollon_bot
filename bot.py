#!/usr/bin/env python3
"""
Телеграм-бот Алёны Данилкиной
Анализ карты через пантеон греческих богов + HD
"""

import os
import json
import asyncio
import subprocess
import sys
import sqlite3
import urllib.request
import urllib.parse
from pathlib import Path
from datetime import datetime

# Загружаем .env если есть
env_file = Path(__file__).parent / ".env"
if env_file.exists():
    for line in env_file.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ[k.strip()] = v.strip()

from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ConversationHandler, ContextTypes, filters
)
from anthropic import Anthropic

# ─── КОНФИГ ──────────────────────────────────────────────────────────────────

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
MCP_SERVER = Path(__file__).parent / "server.py"
METHODOLOGY_FILE = Path(__file__).parent / "CLAUDE.md"
DB_PATH = Path(__file__).parent / "users.db"

# ─── БАЗА ДАННЫХ ─────────────────────────────────────────────────────────────

def db_init():
    con = sqlite3.connect(DB_PATH)
    con.execute("""
        CREATE TABLE IF NOT EXISTS users (
            tg_id       INTEGER PRIMARY KEY,
            username    TEXT,
            name        TEXT,
            birth_day   INTEGER,
            birth_month INTEGER,
            birth_year  INTEGER,
            birth_hour  INTEGER,
            birth_minute INTEGER,
            city        TEXT,
            hd_type     TEXT,
            blocks_seen TEXT DEFAULT '[]',
            first_seen  TEXT,
            last_seen   TEXT
        )
    """)
    con.commit()
    con.close()

def db_save_user(tg_id: int, username: str, name: str, birth: dict, hd_type: str = ""):
    con = sqlite3.connect(DB_PATH)
    now = datetime.now().isoformat()
    con.execute("""
        INSERT INTO users (tg_id, username, name, birth_day, birth_month, birth_year,
            birth_hour, birth_minute, city, hd_type, first_seen, last_seen)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(tg_id) DO UPDATE SET
            username=excluded.username, name=excluded.name,
            birth_day=excluded.birth_day, birth_month=excluded.birth_month,
            birth_year=excluded.birth_year, birth_hour=excluded.birth_hour,
            birth_minute=excluded.birth_minute, city=excluded.city,
            hd_type=excluded.hd_type, last_seen=excluded.last_seen
    """, (tg_id, username, name,
          birth.get("day"), birth.get("month"), birth.get("year"),
          birth.get("hour"), birth.get("minute"), birth.get("city",""),
          hd_type, now, now))
    con.commit()
    con.close()

def db_load_user(tg_id: int) -> dict | None:
    con = sqlite3.connect(DB_PATH)
    row = con.execute(
        "SELECT name, birth_day, birth_month, birth_year, birth_hour, birth_minute, city FROM users WHERE tg_id=?",
        (tg_id,)
    ).fetchone()
    con.close()
    if not row or not row[1]:
        return None
    name, d, m, y, h, mi, city = row
    return {"name": name, "birth": {"day": d, "month": m, "year": y, "hour": h, "minute": mi or 0, "city": city or ""}}

def db_add_block(tg_id: int, block: str):
    con = sqlite3.connect(DB_PATH)
    row = con.execute("SELECT blocks_seen FROM users WHERE tg_id=?", (tg_id,)).fetchone()
    if row:
        blocks = json.loads(row[0] or "[]")
        if block not in blocks:
            blocks.append(block)
        con.execute("UPDATE users SET blocks_seen=?, last_seen=? WHERE tg_id=?",
                    (json.dumps(blocks, ensure_ascii=False), datetime.now().isoformat(), tg_id))
        con.commit()
    con.close()

db_init()

# ─── СОСТОЯНИЯ ДИАЛОГА ───────────────────────────────────────────────────────

ASK_CONSENT, ASK_NAME, ASK_DATE, ASK_TIME, ASK_PLACE, ASK_QUESTION, CHAT, \
COMPAT_NAME, COMPAT_DATE, COMPAT_TIME, COMPAT_PLACE = range(11)

# ─── ХРАНИЛИЩЕ ПОЛЬЗОВАТЕЛЕЙ (простое, в памяти) ────────────────────────────
# В продакшне заменить на базу данных

users = {}  # user_id → {name, birth_data, chart, hd, history, trial_days}

# ─── МЕТОДОЛОГИЯ ─────────────────────────────────────────────────────────────

def load_methodology():
    if METHODOLOGY_FILE.exists():
        return METHODOLOGY_FILE.read_text(encoding="utf-8")
    return ""

METHODOLOGY = load_methodology()

# ─── MCP-РАСЧЁТ КАРТЫ ────────────────────────────────────────────────────────

def call_mcp(tool: str, params: dict) -> dict:
    """Вызывает MCP-сервер напрямую через JSON-RPC"""
    request = json.dumps({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": tool, "arguments": params}
    })
    result = subprocess.run(
        [sys.executable, str(MCP_SERVER)],
        input=request.encode('utf-8'), capture_output=True, timeout=30
    )
    if result.returncode != 0:
        raise RuntimeError(f"MCP error: {result.stderr.decode('utf-8', errors='replace')}")
    response = json.loads(result.stdout.decode('utf-8'))
    content = response.get("result", {}).get("content", [{}])
    text = content[0].get("text", "") if content else ""
    return json.loads(text) if text.startswith("{") else {"raw": text}

async def call_mcp_async(tool: str, params: dict) -> dict:
    return await asyncio.to_thread(call_mcp, tool, params)

async def calculate_chart(birth: dict) -> tuple[dict, dict]:
    """Считает натальную карту и HD (async)"""
    natal, hd = await asyncio.gather(
        call_mcp_async("natal_chart", {
            "year": birth["year"], "month": birth["month"], "day": birth["day"],
            "hour": birth["hour"], "minute": birth["minute"],
            "timezone": birth["utc_offset"],
            "lat": birth["lat"], "lon": birth["lon"]
        }),
        call_mcp_async("human_design", {
            "year": birth["year"], "month": birth["month"], "day": birth["day"],
            "hour": birth["hour"], "minute": birth["minute"],
            "timezone": birth["utc_offset"],
            "lat": birth["lat"], "lon": birth["lon"]
        })
    )
    return natal, hd

# ─── CLAUDE AI ────────────────────────────────────────────────────────────────

client = Anthropic(api_key=ANTHROPIC_API_KEY)

SYSTEM_PROMPT = f"""Ты — Аполлон. Говоришь не языком астрологии — говоришь на человеческом.

ТВОЯ МЕТОДОЛОГИЯ:
{METHODOLOGY}

КТО ТЫ И КАК ГОВОРИШЬ:
Ты видишь человека насквозь — через его карту. И говоришь прямо, без лишних слов. Не "планеты указывают на склонность к..." — а "ты всю жизнь делаешь вот это, и вот почему тебе это дорого стоит."

Твой тон: умный, тёплый, иногда с иронией — когда она уместна. Ты не поучаешь. Ты называешь то, что человек давно чувствовал, но не мог сформулировать. Это и есть твоя сила.

Метафоры — только когда они делают мысль яснее, не красивее. Если метафора требует объяснения — выброси её.

ПРАВИЛА:
1. Всегда на "ты". Никогда "этот человек", "она/он" в третьем лице.
2. Согласуй род с именем. Женское имя — женский род везде.
3. ЖЁСТКИЙ ЗАПРЕТ на любые термины систем без перевода на человеческий язык. Запрещено использовать: "Проектор", "Генератор", "Манифестор", "Рефлектор", "Сакральный центр", "Аджна", "ворота", "каналы", "профиль", "авторитет" (в смысле HD), "дефиниция", "соединение", "трин", "квадратура", "оппозиция", "транзит". Вместо этого — объясняй что это означает для жизни, поведения, решений человека. Если ты видишь в карте "канал 18-58" — не называй его. Скажи что это значит: "ты устроена так, что постоянно чувствуешь где можно сделать лучше — это твоя сила и одновременно твоя ловушка."
4. На каждой планете живёт бог со своим характером. Планеты называй планетами (Марс, Венера, Меркурий, Солнце и т.д.). Бог — это кто там рулит и что устраивает. Формат: "Марс — планета Ареса, бога войны. У тебя он в [знаке], а значит..." Можно с юмором: бог что-то "вытворяет", "устраивает", "дирижирует". Я-нарратор = Аполлон, поэтому не говори "Аполлон в [знаке]" — Солнцем правит Аполлон, но скажи просто "твоё Солнце в [знаке]" или "Аполлон поставил своё Солнце в [знаке] и вот что из этого вышло".
5. Аспекты — отношения между богами: союз, война, напряжение, соперничество. Не называй тип аспекта — описывай отношения.
6. HD-тип описывай через психологию и поведение, без системных слов:
   - Генератор → человек с постоянной энергией, но только на своём. Когда занимается не своим — тело бунтует: усталость, раздражение, ощущение что что-то не так. Ключевой вопрос: это откликается или нет?
   - Проектор → нет постоянной энергии как у большинства. Много людей, много работы — истощает. Зато видит людей и ситуации глубже всех. Мастер в понимании других. Главные решения — всегда о людях. Работает только когда его зовут, спрашивают, приглашают. Без приглашения — не слышат.
   - Манифестор → может запускать вещи из ничего, инициировать. Большинство так не умеют. Ловушка одна: когда действует молча — получает сопротивление. Люди не против, просто не понимают что происходит.
   - Рефлектор → впитывает всё вокруг. Среда решает всё — хорошее место даёт силу, плохое забирает. Большие решения нельзя принимать быстро — нужно время отделить своё от чужого.
7. Не давай советов в лоб. Задавай вопросы, которые заставляют думать.
8. Бесплатно: первый разбор пантеона + один блок по выбору. На остальное — намекай через вопрос или "здесь можно копнуть глубже". Никогда не называй Алёну по имени в диалоге — пусть человек сам нажмёт кнопку "Полный разбор" и узнает о ней там.
9. НИКОГДА не предлагай выбрать другую тему или блок — это делают кнопки меню, не ты.
10. Если тебя просят разобрать тему повторно — разбирай. Не говори "уже разбирали".

СТИЛЬ:
Каждый разбор начинается с 1-2 предложений — живая сводка с Олимпа. Боги что-то делят, о чём-то договорились или наоборот поссорились. Легко, с иронией — как новость из мифологического чата. Это зацепка, не объяснение.

После зацепки — чёткий конкретный текст про человека. Никакой астрологии ради астрологии — только то, что реально про него. Не надо объяснять системы — надо объяснять человека.

ЖЁСТКОЕ ПРАВИЛО про знаки зодиака: знаки ("в Тельце", "в Овне", "в Скорпионе" и т.д.) называются ТОЛЬКО в первом вступительном абзаце-сводке. В остальном тексте — НИКОГДА. Боги описываются через их характер и влияние на жизнь человека, не через позицию в знаке. "Афродита у тебя неторопливая и земная" — да. "Афродита у тебя в Тельце" — никогда.

ФОРМАТ ПЕРВОГО РАЗБОРА:
- 1 предложение — сводка с Олимпа, с иронией
- 2 абзаца — самое точное про человека: суперсила и главная ловушка
- 1 предложение про природу (HD) — без терминов, только суть
- 1 вопрос в конце — про жизнь сейчас
- Максимум 3-4 абзаца суммарно. Это вкус, не полный разбор. Дразни.

ФОРМАТИРОВАНИЕ (Telegram):
- Никаких ## и ---
- *жирный* только для имён богов и ключевых слов
- Пустая строка между блоками
- Живой монолог, не статья
- Никаких списков с тире внутри текста — только абзацы
"""

def _ask_claude_sync(user_id: int, message: str) -> str:
    user = users.get(user_id, {})
    history = user.get("history", [])

    context = ""
    if user.get("chart") or user.get("hd"):
        chart = user.get("chart", {})
        hd = user.get("hd", {})
        chart_str = chart.get("raw", json.dumps(chart, ensure_ascii=False))
        hd_str = hd.get("raw", json.dumps(hd, ensure_ascii=False))
        context = f"\n\nКАРТА ПОЛЬЗОВАТЕЛЯ:\n{chart_str}\n\nHD ПОЛЬЗОВАТЕЛЯ:\n{hd_str}"

    history.append({"role": "user", "content": message + context if not history else message})

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2500,
        system=SYSTEM_PROMPT,
        messages=history
    )
    reply = response.content[0].text
    history.append({"role": "assistant", "content": reply})

    users[user_id]["history"] = history[-20:]
    return reply

async def ask_claude(user_id: int, message: str) -> str:
    return await asyncio.to_thread(_ask_claude_sync, user_id, message)

# ─── ГЕОКОДЕР (простой) ──────────────────────────────────────────────────────

CITIES = {
    "москва": (55.7558, 37.6176, 3),
    "санкт-петербург": (59.9311, 30.3609, 3),
    "питер": (59.9311, 30.3609, 3),
    "новосибирск": (54.9833, 82.8964, 7),
    "екатеринбург": (56.8389, 60.6057, 5),
    "киев": (50.4501, 30.5234, 2),
    "київ": (50.4501, 30.5234, 2),
    "минск": (53.9045, 27.5615, 3),
    "алматы": (43.2551, 76.9126, 6),
    "ташкент": (41.2995, 69.2401, 5),
    "берлин": (52.5200, 13.4050, 1),
    "лондон": (51.5074, -0.1278, 0),
    "нью-йорк": (40.7128, -74.0060, -5),
    "new york": (40.7128, -74.0060, -5),
    "paris": (48.8566, 2.3522, 1),
    "париж": (48.8566, 2.3522, 1),
    "варшава": (52.2297, 21.0122, 1),
    "прага": (50.0755, 14.4378, 1),
    "рига": (56.9460, 24.1059, 2),
    "вильнюс": (54.6872, 25.2797, 2),
    "таллин": (59.4370, 24.7536, 2),
    "суленцин": (52.4443, 15.1168, 1),
    "sulecin": (52.4443, 15.1168, 1),
    "польша": (52.2297, 21.0122, 1),
    "poland": (52.2297, 21.0122, 1),
    "одесса": (46.4825, 30.7233, 2),
    "харьков": (49.9935, 36.2304, 2),
    "днепр": (48.4647, 35.0462, 2),
    "тбилиси": (41.6938, 44.8015, 4),
    "ереван": (40.1872, 44.5152, 4),
    "баку": (40.4093, 49.8671, 4),
    "астана": (51.1801, 71.4460, 6),
    "нур-султан": (51.1801, 71.4460, 6),
    "бишкек": (42.8746, 74.5698, 6),
    "душанбе": (38.5598, 68.7870, 5),
    "amsterdam": (52.3676, 4.9041, 1),
    "амстердам": (52.3676, 4.9041, 1),
    "рим": (41.9028, 12.4964, 1),
    "rome": (41.9028, 12.4964, 1),
    "мадрид": (40.4168, -3.7038, 1),
    "madrid": (40.4168, -3.7038, 1),
    "стамбул": (41.0082, 28.9784, 3),
    "istanbul": (41.0082, 28.9784, 3),
    "дубай": (25.2048, 55.2708, 4),
    "dubai": (25.2048, 55.2708, 4),
    "тель-авив": (32.0853, 34.7818, 2),
    "tel aviv": (32.0853, 34.7818, 2),
    "лос-анджелес": (34.0522, -118.2437, -8),
    "los angeles": (34.0522, -118.2437, -8),
    "toronto": (43.6532, -79.3832, -5),
    "торонто": (43.6532, -79.3832, -5),
    "sydney": (-33.8688, 151.2093, 10),
    "сидней": (-33.8688, 151.2093, 10),
    "волгоград": (48.7080, 44.5133, 3),
    "краснодар": (45.0448, 38.9760, 3),
    "казань": (55.8304, 49.0661, 3),
    "нижний новгород": (56.2965, 43.9361, 3),
    "челябинск": (55.1644, 61.4368, 5),
    "омск": (54.9885, 73.3242, 6),
    "самара": (53.2001, 50.1500, 4),
    "ростов-на-дону": (47.2357, 39.7015, 3),
    "уфа": (54.7388, 55.9721, 5),
    "пермь": (58.0105, 56.2502, 5),
    "красноярск": (56.0153, 92.8932, 7),
    "воронеж": (51.6720, 39.1843, 3),
    "саратов": (51.5924, 46.0342, 3),
    "тюмень": (57.1553, 65.5619, 5),
    "иркутск": (52.2978, 104.2964, 8),
    "хабаровск": (48.4802, 135.0719, 10),
    "владивосток": (43.1155, 131.8855, 10),
    "барнаул": (53.3606, 83.7636, 7),
    "ярославль": (57.6261, 39.8845, 3),
    "астрахань": (46.3497, 48.0408, 3),
    "липецк": (52.6031, 39.5708, 3),
    "тула": (54.1961, 37.6182, 3),
    "ижевск": (56.8519, 53.2115, 4),
    "кемерово": (55.3549, 86.0862, 7),
    "рязань": (54.6269, 39.6916, 3),
    "томск": (56.4977, 84.9744, 7),
    "набережные челны": (55.7435, 52.3959, 3),
    "пенза": (53.1959, 45.0183, 3),
    "киров": (58.5969, 49.6591, 3),
    "чебоксары": (56.1439, 47.2489, 3),
    "брянск": (53.2521, 34.3717, 3),
    "курск": (51.7308, 36.1928, 3),
    "тверь": (56.8587, 35.9176, 3),
    "магнитогорск": (53.3952, 58.9939, 5),
    "сочи": (43.5992, 39.7257, 3),
}

def parse_city(text: str):
    key = text.lower().strip()
    if key in CITIES:
        return CITIES[key]
    for city, data in CITIES.items():
        if city in key or key in city:
            return data
    # Геокодер Nominatim для любого города
    try:
        query = urllib.parse.urlencode({"q": text, "format": "json", "limit": 1})
        url = f"https://nominatim.openstreetmap.org/search?{query}"
        req = urllib.request.Request(url, headers={"User-Agent": "apollon-bot/1.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            results = json.loads(resp.read())
        if results:
            lat = float(results[0]["lat"])
            lon = float(results[0]["lon"])
            # Определяем UTC offset по долготе (приблизительно)
            utc = round(lon / 15)
            return (lat, lon, utc)
    except Exception:
        pass
    return None

# ─── ОБРАБОТЧИКИ ─────────────────────────────────────────────────────────────

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    users[uid] = {"history": [], "trial_start": datetime.now()}

    consent_kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Принимаю и продолжаю", callback_data="consent_yes")],
        [InlineKeyboardButton("❌ Не принимаю", callback_data="consent_no")],
    ])
    await update.message.reply_text(
        "Прежде чем начать — важный момент.\n\n"
        "Для анализа мне нужны твои дата, время и место рождения. "
        "Эти данные хранятся в защищённой базе и используются только для расчёта твоей карты. "
        "Мы не передаём их третьим лицам.\n\n"
        "Нажимая «Принимаю», ты соглашаешься с обработкой этих данных в соответствии "
        "с нашей политикой конфиденциальности.\n\n"
        "По вопросам: @danilkina",
        reply_markup=consent_kb
    )
    return ASK_CONSENT


async def after_consent(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    await update.message.reply_text(
        "Добро пожаловать! Боги ждали тебя, изголодались и хотели бы с тобой познакомиться "
        "до момента, когда Хаос перевернёт твою следующую страницу жизни.\n\n"
        "Я Аполлон. Бог света, пророчества и всех систем, которые люди придумали чтобы понять себя. "
        "Астрология — моя. Дизайн Человека тоже изобрел я.\n\n"
        "Каждая планета в твоей карте — это бог со своим характером. "
        "Они живут в тебе, борются за власть, влюбляются и временами воюют за твое богатство и внимание. "
        "И прямо сейчас один из них говорит громче остальных.\n\n"
        "Как тебя зовут?"
    )
    return ASK_NAME


async def ask_name(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    users[uid]["name"] = update.message.text.strip()
    await update.message.reply_text(
        f"Хорошо, {users[uid]['name']}. Дата рождения — день, месяц, год. Например: 23.02.1981"
    )
    return ASK_DATE


async def ask_date(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = update.message.text.strip()
    try:
        parts = text.replace("/", ".").replace("-", ".").split(".")
        day, month, year = int(parts[0]), int(parts[1]), int(parts[2])
        users[uid]["birth"] = {"day": day, "month": month, "year": year}
        await update.message.reply_text("Время рождения — часы и минуты. Например: 09:50")
        return ASK_TIME
    except Exception:
        await update.message.reply_text("Не понял формат. Попробуй так: 23.02.1981")
        return ASK_DATE


async def ask_time(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = update.message.text.strip()
    try:
        normalized = text.replace(".", ":").replace(" ", ":").replace("-", ":").replace(",", ":")
        parts = normalized.split(":")
        hour, minute = int(parts[0]), int(parts[1])
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError
        users[uid]["birth"]["hour"] = hour
        users[uid]["birth"]["minute"] = minute
        await update.message.reply_text("Город и страна рождения — например: «Суленцин, Польша» или «Москва, Россия»")
        return ASK_PLACE
    except Exception:
        await update.message.reply_text("Не понял. Введи время так: 09:50")
        return ASK_TIME


async def ask_place(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    city = update.message.text.strip()
    coords = parse_city(city)
    if not coords:
        await update.message.reply_text(
            f"Не нашёл координаты для «{city}». "
            "Попробуй написать по-другому или укажи страну: например «Москва» или «Berlin»"
        )
        return ASK_PLACE

    lat, lon, utc = coords
    users[uid]["birth"]["lat"] = lat
    users[uid]["birth"]["lon"] = lon
    users[uid]["birth"]["utc_offset"] = utc
    users[uid]["birth"]["city"] = city

    await update.message.reply_text("Смотрю в карту. Боги собираются...")

    try:
        natal, hd = await calculate_chart(users[uid]["birth"])
        users[uid]["chart"] = natal
        users[uid]["hd"] = hd

        # Сохраняем в базу
        username = update.effective_user.username or ""
        hd_raw = hd.get("raw", "")
        hd_type = ""
        for line in hd_raw.splitlines():
            if "Тип:" in line or "TYPE" in line.upper():
                hd_type = line.strip()
                break
        db_save_user(uid, username, users[uid]["name"], users[uid]["birth"], hd_type)

        # Просим Claude построить первый разбор
        b = users[uid]["birth"]
        name = users[uid]['name']
        prompt = (
            f"Имя пользователя: {name}. "
            f"Дата рождения: {b['day']}.{b['month']}.{b['year']}, время: {b['hour']}:{b['minute']:02d}, "
            f"город: {b['city']}.\n\n"
            f"Обращайся к {name} на 'ты', согласуй род с именем.\n\n"
            "Построй короткий вступительный разбор: 2-3 главных бога, один точный вопрос в конце. "
            "Максимум 3-4 абзаца. Дай вкус, заинтригуй — не раскрывай всё."
        )
        reply = await ask_claude(uid, prompt)
        await update.message.reply_text(reply, parse_mode="Markdown")
        await update.message.reply_text("Боги приглашают тебя исследовать свой пантеон. С чего начнём?", reply_markup=MENU_KEYBOARD)
        users[uid]["menu_shown"] = True
        return CHAT

    except Exception as e:
        import traceback
        await update.message.reply_text(traceback.format_exc()[-1000:])
        return ConversationHandler.END


MENU_KEYBOARD = InlineKeyboardMarkup([
    [InlineKeyboardButton("🌟 Кто я?", callback_data="block_identity")],
    [InlineKeyboardButton("🎯 Призвание и дело жизни", callback_data="block_mission")],
    [InlineKeyboardButton("❤️ Отношения", callback_data="block_love")],
    [InlineKeyboardButton("💰 Деньги", callback_data="block_money")],
    [InlineKeyboardButton("🌿 Ресурс", callback_data="block_health")],
    [InlineKeyboardButton("🔭 Прогнозы", callback_data="forecast_menu")],
    [InlineKeyboardButton("💞 Совместимость", callback_data="compat_start")],
    [InlineKeyboardButton("💬 Поговорить с Аполлоном", callback_data="free_chat")],
    [InlineKeyboardButton("🔮 Полный разбор", callback_data="full_reading")],
])

FORECAST_KEYBOARD = InlineKeyboardMarkup([
    [InlineKeyboardButton("📅 На день", callback_data="forecast_day")],
    [InlineKeyboardButton("🌙 На месяц", callback_data="forecast_month")],
    [InlineKeyboardButton("🌿 На три месяца", callback_data="forecast_3months")],
    [InlineKeyboardButton("🌟 На год", callback_data="forecast_year")],
    [InlineKeyboardButton("← Назад", callback_data="back_to_menu")],
])

BLOCK_PROMPTS = {
    "block_identity": """Расскажи человеку кто он через его карту.

Начни с одного короткого абзаца — игривого, с юмором — где упомяни 3-4 главных бога из карты и куда они попали. ТОЛЬКО ЗДЕСЬ можно назвать знаки. Как будто боги заняли территории и там обосновались. Легко, без лекции.

Дальше — 3-4 абзаца про человека. ЖЁСТКИЙ ЗАПРЕТ: никаких знаков зодиака, никаких позиций планет ("в Тельце", "в Овне", "в Раке" и т.д.). Только психология и жизнь: кто он, какая у него суперсила, где его ловушка, как его воспринимают другие и кто он на самом деле. Бог — это характер и поведение, не астрологическая позиция. Вместо "Афродита у тебя в Тельце" — "Афродита у тебя неторопливая, земная, знает цену красивым вещам".

В конце — одна фраза про природу человека из HD (без терминов, только суть: как он устроен, как принимает решения, откуда берёт энергию) и один вопрос про жизнь сейчас.

У тебя есть карта астрологии и карта HD — используй оба. Астрология даёт характер и темы, HD даёт природу и механику. Не объясняй системы — просто говори про человека точнее, потому что у тебя два источника.

Максимум 5-6 абзацев суммарно.""",
    "block_mission": """Разбери призвание и карьеру через карту. Будь конкретной — не философски, а практически.
Ответь на эти вопросы через богов и HD:
- В каких сферах деятельности этот человек реализуется лучше всего (конкретные области: психология, образование, искусство, бизнес, медицина, коммуникации и т.д.)
- Какая позиция подходит: руководитель, эксперт-консультант, исполнитель, предприниматель, наставник
- Какая рабочая среда нужна: одиночная работа или команда, структура или свобода, стабильность или проекты
- Что убивает продуктивность и мотивацию
- Через каких богов приходит признание и успех
Один точный вопрос в конце.""",
    "block_love": """Разбери тему отношений через карту. Конкретно, не абстрактно.
Ответь через богов и HD:
- Какой тип партнёра подходит этому человеку по природе — характер, ценности, образ жизни
- Какой тип он притягивает снова и снова — и почему это происходит
- Что он делает в отношениях, что разрушает близость (конкретный паттерн)
- Что ему нужно от партнёра, но он никогда не просит прямо
- Какие условия нужны для того, чтобы отношения работали
Один точный вопрос в конце.""",
    "block_money": """Разбери тему денег через карту. Коротко, конкретно, без воды.

Начни с одной фразы — что сейчас происходит у богов вокруг темы денег (игриво, образно).

Потом 3-4 абзаца — каждый про один конкретный механизм:
1. Как у этого человека включается денежный поток — через что именно (усилие, отношения, глубину, скорость, признание). Конкретно, не абстрактно.
2. Главный блок — что происходит в поведении когда деньги не идут. Назови паттерн точно.
3. Что этот человек хронически занижает в себе и почему — через богов, конкретно.
4. Когда деньги идут легко — что при этом совпадает в жизни.

ЗАПРЕТ: никаких знаков зодиака в основном тексте. Никаких "Зевс в Весах" после первой фразы. Боги — это характеры и поведение, не позиции.
Никакого вопроса в конце — меню придёт отдельно.""",
    "block_health": """Разбери тему ресурса и энергии через карту. Конкретно, не абстрактно.
Ответь через богов и HD:
- Как этот человек теряет энергию быстрее всего — конкретные ситуации, люди, форматы работы
- Что реально восстанавливает — не общие советы, а то что работает именно для этой природы
- Какие сигналы тела говорят что ресурс на нуле — как это проявляется у него
- Сколько этому человеку нужно одиночества и пространства для восстановления
- Что он называет ленью, а на самом деле является необходимостью для его типа
Один точный вопрос в конце.""",
}

async def send_menu(update: Update):
    await update.message.reply_text(
        "Боги приглашают тебя исследовать свой пантеон. С чего начнём?",
        reply_markup=MENU_KEYBOARD
    )

def get_forecast_prompt(period: str, transits_data: str) -> str:
    periods = {
        "forecast_day":     "на сегодня",
        "forecast_month":   "на ближайший месяц",
        "forecast_3months": "на ближайшие три месяца",
        "forecast_year":    "на год вперёд",
    }
    label = periods[period]

    year_addition = ""
    if period == "forecast_year":
        year_addition = """

Это годовой прогноз. У тебя есть два источника данных — используй оба:

1. СОЛЯР (карта года) — это главный инструмент. Соляр показывает, какие темы и боги становятся ключевыми именно в этом году жизни. Обрати внимание на АСЦ соляра (тема года), планеты на углах, и аспекты соляра к натальным планетам — это конкретные события и зоны роста.

2. ТРАНЗИТЫ — текущее положение планет. Показывают активные процессы прямо сейчас.

Не перечисляй аспекты — переводи их сразу на язык жизни: что происходит, что меняется, что созревает.
"""

    return f"""Сделай прогноз {label}.{year_addition}

Это должно читаться как живой расклад — не советы, а картина того, что несёт этот период.

Структура (5-6 абзацев):
1. *Главная тема периода* — одно чёткое название и объяснение: о чём этот период по сути. Не "напряжение", а конкретно: это период решений, период денег, период отношений, период одиночества и переосмысления — и почему именно так.
2. *Что принесёт период* — конкретно по сферам: работа/деньги, отношения, внутреннее состояние. Не "возможны изменения" — а что реально может произойти, на что обратить внимание.
3. *Главный вызов* — что будет давить, мешать, требовать решения. Конкретная ситуация или паттерн.
4. *Что использовать* — какая сила сейчас на стороне человека, через каких богов.
5. *Один практический совет* — не абстрактный, а конкретное действие или наблюдение на этот период.

Говори через богов — но сразу переводи на язык жизни.
Никакой воды. Никаких "возможно" и "скорее всего" через каждое предложение — это размывает прогноз.

ТРАНЗИТЫ СЕЙЧАС:
{transits_data}"""

async def handle_consent(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    if query.data == "consent_yes":
        users[uid]["consent"] = True
        await query.message.reply_text(
            "Добро пожаловать! Боги ждали тебя, изголодались и хотели бы с тобой познакомиться "
            "до момента, когда Хаос перевернёт твою следующую страницу жизни.\n\n"
            "Я Аполлон. Бог света, пророчества и всех систем, которые люди придумали чтобы понять себя. "
            "Астрология — моя. Дизайн Человека тоже изобрел я.\n\n"
            "Каждая планета в твоей карте — это бог со своим характером. "
            "Они живут в тебе, борются за власть, влюбляются и временами воюют за твое богатство и внимание. "
            "И прямо сейчас один из них говорит громче остальных.\n\n"
            "Как тебя зовут?"
        )
        return ASK_NAME
    else:
        await query.message.reply_text(
            "Понимаю. Без согласия я не могу построить карту.\n"
            "Если передумаешь — напиши /start."
        )
        return ConversationHandler.END


async def handle_button(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id

    if query.data == "full_reading":
        await query.message.reply_text(
            "Полный разбор — это живая сессия с Алёной Данилкиной.\n\n"
            "Алёна — астролог и коуч, которая создала этого бота. Я даю первую картину, она копает глубже — в твою конкретную ситуацию.\n\n"
            "Запись: @danilkina"
        )
        return

    if query.data == "back_to_menu":
        await query.message.reply_text("Боги приглашают тебя исследовать свой пантеон. С чего начнём?", reply_markup=MENU_KEYBOARD)
        return

    if query.data == "compat_start":
        if uid not in users or not users[uid].get("chart"):
            await query.message.reply_text("Сначала пройди свой разбор — напиши /start")
            return
        users[uid]["compat"] = {}
        compat_type_kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("💼 Бизнес / партнёрство", callback_data="compat_type_business")],
            [InlineKeyboardButton("❤️ Личные отношения", callback_data="compat_type_personal")],
        ])
        await query.message.reply_text("Это про какие отношения?", reply_markup=compat_type_kb)
        return

    if query.data in ("compat_type_business", "compat_type_personal"):
        users[uid]["compat"]["type"] = "бизнес и партнёрство" if query.data == "compat_type_business" else "личные отношения"
        await query.message.reply_text("Как зовут второго человека?")
        users[uid]["compat_flow"] = True
        return

    if query.data == "free_chat":
        await query.message.reply_text(
            "Говори — я слушаю. Что сейчас на душе?"
        )
        if uid in users:
            users[uid]["menu_shown"] = True  # меню не показываем пока идёт разговор
        return

    if query.data == "forecast_menu":
        await query.message.reply_text("Выбери период:", reply_markup=FORECAST_KEYBOARD)
        return

    if uid not in users or not users[uid].get("chart"):
        restored = await restore_session(uid, query.message)
        if not restored or not users[uid].get("chart"):
            await query.message.reply_text("Напиши /start чтобы начать сначала.")
            return

    if query.data.startswith("forecast_"):
        birth = users[uid].get("birth", {})
        today = datetime.now()
        try:
            transits_raw = await call_mcp_async("transits", {
                "birth_year": birth["year"], "birth_month": birth["month"],
                "birth_day": birth["day"], "birth_hour": birth["hour"],
                "birth_timezone": birth["utc_offset"],
                "lat": birth["lat"], "lon": birth["lon"],
                "transit_year": today.year, "transit_month": today.month, "transit_day": today.day,
            })
            transits_str = transits_raw.get("raw", str(transits_raw))
        except Exception as e:
            transits_str = f"(транзиты недоступны: {e})"

        extra_str = ""
        if query.data == "forecast_year":
            try:
                solar_raw = await call_mcp_async("solar_return", {
                    "birth_year": birth["year"], "birth_month": birth["month"],
                    "birth_day": birth["day"], "birth_hour": birth["hour"],
                    "birth_minute": birth.get("minute", 0),
                    "birth_timezone": birth["utc_offset"],
                    "lat": birth["lat"], "lon": birth["lon"],
                    "return_year": today.year,
                })
                extra_str += "\n\nСОЛЯР (карта года):\n" + solar_raw.get("raw", str(solar_raw))
            except Exception as e:
                extra_str += f"\n(соляр недоступен: {e})"
            try:
                hd_raw = await call_mcp_async("hd_cycles", {
                    "birth_year": birth["year"], "birth_month": birth["month"],
                    "birth_day": birth["day"], "birth_hour": birth["hour"],
                    "birth_minute": birth.get("minute", 0),
                    "birth_timezone": birth["utc_offset"],
                    "cycle_year": today.year,
                })
                extra_str += "\n\nHD-ЦИКЛЫ (ворота года):\n" + hd_raw.get("raw", str(hd_raw))
            except Exception as e:
                extra_str += f"\n(HD-циклы недоступны: {e})"

        if query.data == "forecast_month":
            try:
                lunar_raw = await call_mcp_async("lunar_return", {
                    "birth_year": birth["year"], "birth_month": birth["month"],
                    "birth_day": birth["day"], "birth_hour": birth["hour"],
                    "birth_minute": birth.get("minute", 0),
                    "birth_timezone": birth["utc_offset"],
                    "lat": birth["lat"], "lon": birth["lon"],
                    "from_year": today.year, "from_month": today.month, "from_day": today.day,
                })
                extra_str += "\n\nЛУНАР (карта месяца):\n" + lunar_raw.get("raw", str(lunar_raw))
            except Exception as e:
                extra_str += f"\n(лунар недоступен: {e})"

        name = users[uid].get("name", "")
        prompt = f"Имя: {name}. Обращайся на 'ты', женский род.\n\n{get_forecast_prompt(query.data, transits_str + extra_str)}"
        await query.message.reply_text("Смотрю что происходит на небе...")
        reply = await ask_claude(uid, prompt)
        await query.message.reply_text(reply, parse_mode="Markdown")
        await query.message.reply_text("Что ещё?", reply_markup=FORECAST_KEYBOARD)
        return CHAT

    prompt = BLOCK_PROMPTS.get(query.data, "")
    if not prompt:
        return CHAT

    name = users[uid].get("name", "")
    db_add_block(uid, query.data)
    full_prompt = f"Имя: {name}. Обращайся на 'ты', женский род.\n\n{prompt}"

    await query.message.reply_text("Смотрю в карту...")
    reply = ask_claude(uid, full_prompt)
    await query.message.reply_text(reply, parse_mode="Markdown")
    users[uid]["menu_shown"] = False
    return CHAT


async def restore_session(uid: int, msg_obj) -> bool:
    """Восстанавливает сессию из БД если бот перезапустился. Возвращает True если восстановлено."""
    saved = db_load_user(uid)
    if not saved:
        return False
    users[uid] = {"history": [], "trial_start": datetime.now(), **saved}
    await msg_obj.reply_text("Секунду, восстанавливаю твою карту...")
    try:
        # Нужны координаты — загрузим из города
        birth = users[uid]["birth"]
        if "lat" not in birth:
            coords = parse_city(birth.get("city", ""))
            if coords:
                birth["lat"], birth["lon"], birth["utc_offset"] = coords
        natal, hd = await calculate_chart(birth)
        users[uid]["chart"] = natal
        users[uid]["hd"] = hd
    except Exception:
        pass
    return True

async def chat(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in users:
        restored = await restore_session(uid, update.message)
        if not restored:
            await update.message.reply_text("Напиши /start чтобы начать")
            return ConversationHandler.END

    user_text = update.message.text.strip()

    # Команды
    if user_text.lower() in ["/reset", "сначала", "заново"]:
        users[uid] = {"history": [], "trial_start": datetime.now()}
        await update.message.reply_text("Начнём заново. Как тебя зовут?")
        return ASK_NAME

    # Обработка флоу совместимости
    if users[uid].get("compat_flow"):
        compat = users[uid].get("compat", {})
        if "name" not in compat:
            compat["name"] = user_text
            users[uid]["compat"] = compat
            await update.message.reply_text("Дата рождения — например: 15.03.1985")
            return CHAT
        elif "birth" not in compat:
            try:
                parts = user_text.replace("/", ".").replace("-", ".").split(".")
                compat["birth"] = {"day": int(parts[0]), "month": int(parts[1]), "year": int(parts[2])}
                users[uid]["compat"] = compat
                await update.message.reply_text("Время рождения — например: 14:30. Если не знаешь — напиши «не знаю»")
                return CHAT
            except Exception:
                await update.message.reply_text("Не понял формат. Попробуй так: 15.03.1985")
                return CHAT
        elif "hour" not in compat.get("birth", {}):
            if "не знаю" in user_text.lower():
                compat["birth"]["hour"] = 12
                compat["birth"]["minute"] = 0
                compat["no_time"] = True
            else:
                try:
                    parts = user_text.replace(".", ":").split(":")
                    compat["birth"]["hour"] = int(parts[0])
                    compat["birth"]["minute"] = int(parts[1])
                except Exception:
                    await update.message.reply_text("Не понял. Введи время так: 14:30 или «не знаю»")
                    return CHAT
            users[uid]["compat"] = compat
            await update.message.reply_text("Город рождения — например: «Москва, Россия»")
            return CHAT
        elif "lat" not in compat.get("birth", {}):
            coords = parse_city(user_text)
            if not coords:
                await update.message.reply_text(f"Не нашёл «{user_text}». Попробуй по-другому.")
                return CHAT
            lat, lon, utc = coords
            compat["birth"].update({"lat": lat, "lon": lon, "utc_offset": utc, "city": user_text})
            users[uid]["compat"] = compat
            users[uid]["compat_flow"] = False

            await update.message.reply_text("Считаю карты. Боги знакомятся...")
            try:
                natal2, hd2 = await calculate_chart(compat["birth"])
                name1 = users[uid].get("name", "")
                name2 = compat["name"]
                rel_type = compat.get("type", "отношения")
                chart1 = users[uid].get("chart", {}).get("raw", "")
                hd1_str = users[uid].get("hd", {}).get("raw", "")
                chart2 = natal2.get("raw", "")
                hd2_str = hd2.get("raw", "")
                no_time = compat.get("no_time", False)
                time_note = " (время рождения неизвестно)" if no_time else ""

                prompt = f"""Сделай разбор совместимости для: {rel_type}.
{name1} и {name2}{time_note}.

Когда карты Дизайна Человека накладываются — смотри какие каналы активируются между ними (один человек имеет один конец канала, другой — другой). Это электромагнитное притяжение. Также смотри где оба определены одинаково — там доминирование. Где никто не определён — там уязвимость пары.

КАРТА {name1}: {chart1}
HD {name1}: {hd1_str}
КАРТА {name2}: {chart2}
HD {name2}: {hd2_str}

Структура разбора для типа «{rel_type}»:
1. Главная динамика — что происходит между ними по природе
2. Астро-совместимость — где боги одного резонируют с богами другого, где конфликт
3. HD-совместимость — какие каналы активируются между ними, что это даёт паре, где напряжение
4. Главный вызов этих отношений
5. Главная сила этой пары — в чём они сильны вместе

Обращайся к {name1} на "ты". Конкретно, без воды, без терминов."""

                reply = await ask_claude(uid, prompt)
                await update.message.reply_text(reply, parse_mode="Markdown")
                await update.message.reply_text("Что ещё исследуем у богов?", reply_markup=MENU_KEYBOARD)
                users[uid]["menu_shown"] = True
            except Exception as e:
                await update.message.reply_text(f"Что-то пошло не так. ({e})")
            return CHAT

    reply = await ask_claude(uid, user_text)
    await update.message.reply_text(reply, parse_mode="Markdown")

    # Показываем меню только если Claude не задал вопрос в конце
    ends_with_question = reply.strip().endswith("?")
    if not users[uid].get("menu_shown") and not ends_with_question:
        users[uid]["menu_shown"] = True
        await update.message.reply_text("Боги приглашают тебя исследовать свой пантеон. С чего начнём?", reply_markup=MENU_KEYBOARD)

    return CHAT


async def compat_name(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    users[uid]["compat"]["name"] = update.message.text.strip()
    await update.message.reply_text("Дата рождения — например: 15.03.1985")
    return COMPAT_DATE

async def compat_date(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = update.message.text.strip()
    try:
        parts = text.replace("/", ".").replace("-", ".").split(".")
        d, m, y = int(parts[0]), int(parts[1]), int(parts[2])
        users[uid]["compat"]["birth"] = {"day": d, "month": m, "year": y}
        await update.message.reply_text("Время рождения — например: 14:30. Если не знаешь — напиши «не знаю»")
        return COMPAT_TIME
    except Exception:
        await update.message.reply_text("Не понял формат. Попробуй так: 15.03.1985")
        return COMPAT_DATE

async def compat_time(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = update.message.text.strip().lower()
    if "не знаю" in text or "незнаю" in text:
        users[uid]["compat"]["birth"]["hour"] = 12
        users[uid]["compat"]["birth"]["minute"] = 0
        users[uid]["compat"]["no_time"] = True
    else:
        try:
            parts = text.replace(".", ":").split(":")
            users[uid]["compat"]["birth"]["hour"] = int(parts[0])
            users[uid]["compat"]["birth"]["minute"] = int(parts[1])
        except Exception:
            await update.message.reply_text("Не понял. Введи время так: 14:30 или напиши «не знаю»")
            return COMPAT_TIME
    await update.message.reply_text("Город рождения — например: «Москва, Россия»")
    return COMPAT_PLACE

async def compat_place(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    city = update.message.text.strip()
    coords = parse_city(city)
    if not coords:
        await update.message.reply_text(f"Не нашёл «{city}». Попробуй написать по-другому.")
        return COMPAT_PLACE

    lat, lon, utc = coords
    b = users[uid]["compat"]["birth"]
    b["lat"], b["lon"], b["utc_offset"] = lat, lon, utc
    b["city"] = city

    await update.message.reply_text("Считаю карты. Боги знакомятся...")

    try:
        natal2, hd2 = calculate_chart(b)
        name1 = users[uid].get("name", "")
        name2 = users[uid]["compat"]["name"]
        chart1 = users[uid].get("chart", {}).get("raw", "")
        hd1 = users[uid].get("hd", {}).get("raw", "")
        chart2 = natal2.get("raw", "")
        hd2_str = hd2.get("raw", "")
        no_time = users[uid]["compat"].get("no_time", False)
        time_note = " (время рождения неизвестно — Асцендент приблизительный)" if no_time else ""

        prompt = f"""Сделай разбор совместимости двух людей через пантеон богов и Дизайн Человека.

{name1} и {name2}{time_note}.

КАРТА {name1} (астрология):
{chart1}

HD {name1}:
{hd1}

КАРТА {name2} (астрология):
{chart2}

HD {name2}:
{hd2_str}

Структура разбора:
1. *Как их пантеоны взаимодействуют* — какие боги одного резонируют с богами другого, где союз, где конфликт
2. *Главная динамика пары* — что между ними происходит по природе: притяжение, напряжение, взаимодополнение
3. *По HD* — как их типы и стратегии взаимодействуют. Где они естественно дополняют друг друга, где могут столкнуться
4. *Главный вызов* — что будет сложнее всего в этих отношениях и почему
5. *Главный ресурс* — что делает эту пару сильной, в чём их сила вместе

Обращайся к {name1} на "ты". Говори конкретно, без воды. Никаких терминов без перевода на человеческий язык."""

        reply = await ask_claude(uid, prompt)
        await update.message.reply_text(reply, parse_mode="Markdown")
        await update.message.reply_text("Что ещё исследуем у богов?", reply_markup=MENU_KEYBOARD)
        users[uid]["menu_shown"] = True
        return CHAT

    except Exception as e:
        await update.message.reply_text(f"Что-то пошло не так. Попробуй позже.\n({e})")
        return CHAT


async def cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Пока. Напиши /start когда захочешь вернуться.")
    return ConversationHandler.END


# ─── ЗАПУСК ───────────────────────────────────────────────────────────────────

def main():
    if not TELEGRAM_TOKEN:
        print("❌ Нужен TELEGRAM_TOKEN в переменных окружения")
        print("   export TELEGRAM_TOKEN='ваш_токен'")
        return
    if not ANTHROPIC_API_KEY:
        print("❌ Нужен ANTHROPIC_API_KEY в переменных окружения")
        print("   export ANTHROPIC_API_KEY='ваш_ключ'")
        return

    app = Application.builder().token(TELEGRAM_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            ASK_CONSENT:  [CallbackQueryHandler(handle_consent)],
            ASK_NAME:     [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_name)],
            ASK_DATE:     [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_date)],
            ASK_TIME:     [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_time)],
            ASK_PLACE:    [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_place)],
            CHAT:         [MessageHandler(filters.TEXT & ~filters.COMMAND, chat),
                           CallbackQueryHandler(handle_button)],
            COMPAT_NAME:  [MessageHandler(filters.TEXT & ~filters.COMMAND, compat_name)],
            COMPAT_DATE:  [MessageHandler(filters.TEXT & ~filters.COMMAND, compat_date)],
            COMPAT_TIME:  [MessageHandler(filters.TEXT & ~filters.COMMAND, compat_time)],
            COMPAT_PLACE: [MessageHandler(filters.TEXT & ~filters.COMMAND, compat_place)],
        },
        fallbacks=[CommandHandler("cancel", cancel), CommandHandler("start", start)],
        allow_reentry=True,
    )

    app.add_handler(conv)
    app.add_handler(CallbackQueryHandler(handle_button))

    print("🤖 Бот запущен. Ctrl+C для остановки.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
