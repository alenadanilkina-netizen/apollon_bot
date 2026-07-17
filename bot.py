#!/usr/bin/env python3
"""
Телеграм-бот Алёны Данилкиной
Анализ карты через пантеон греческих богов + HD
"""

from __future__ import annotations

import os
import json
import asyncio
import subprocess
import sys
import sqlite3
import urllib.request
import urllib.parse
from pathlib import Path
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

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
from hd_library import get_hd_context, get_cross_context, get_love_context, get_phs_context, get_profile_context

# Импортируем MCP-сервер напрямую (надёжнее чем subprocess)
import importlib.util as _ilu
_mcp_spec = _ilu.spec_from_file_location("mcp_server", Path(__file__).parent / "server.py")
_mcp_mod  = _ilu.module_from_spec(_mcp_spec)
_mcp_spec.loader.exec_module(_mcp_mod)
TOOL_HANDLERS = _mcp_mod.TOOL_HANDLERS

# ─── КОНФИГ ──────────────────────────────────────────────────────────────────

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
METHODOLOGY_FILE = Path(__file__).parent / "CLAUDE.md"
TRIAL_DAYS = int(os.environ.get("TRIAL_DAYS", "3"))
# До подключения оплаты оставляем мягкий режим: пользователь видит срок
# пробного доступа, но не блокируется внезапно. Для запуска paywall на Railway
# достаточно выставить TRIAL_ENFORCED=1.
TRIAL_ENFORCED = os.environ.get("TRIAL_ENFORCED", "0").lower() in {"1", "true", "yes", "on"}
PREMIUM_USER_IDS = {
    int(value.strip()) for value in os.environ.get("PREMIUM_USER_IDS", "").split(",")
    if value.strip().isdigit()
}
# Railway Volume: если есть /data — используем его (персистентный диск)
# Иначе fallback к локальному файлу (разработка)
_DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
if _DATA_DIR.exists() and os.access(_DATA_DIR, os.W_OK):
    DB_PATH = _DATA_DIR / "users.db"
else:
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
            lat         REAL,
            lon         REAL,
            utc_offset  REAL,
            hd_type     TEXT,
            blocks_seen TEXT DEFAULT '[]',
            trial_started TEXT,
            brand_data   TEXT DEFAULT '{}',
            first_seen  TEXT,
            last_seen   TEXT
        )
    """)
    # Миграция для уже существующей базы Railway Volume.
    existing = {row[1] for row in con.execute("PRAGMA table_info(users)").fetchall()}
    for column, sql_type in (("lat", "REAL"), ("lon", "REAL"), ("utc_offset", "REAL"),
                             ("trial_started", "TEXT"), ("brand_data", "TEXT")):
        if column not in existing:
            con.execute(f"ALTER TABLE users ADD COLUMN {column} {sql_type}")
    con.commit()
    con.close()

def db_save_user(tg_id: int, username: str, name: str, birth: dict, hd_type: str = "",
                 trial_started: datetime | None = None):
    con = sqlite3.connect(DB_PATH)
    now = datetime.now().isoformat()
    trial_started_iso = (trial_started or datetime.now()).isoformat()
    con.execute("""
        INSERT INTO users (tg_id, username, name, birth_day, birth_month, birth_year,
            birth_hour, birth_minute, city, lat, lon, utc_offset, hd_type,
            trial_started, brand_data, first_seen, last_seen)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(tg_id) DO UPDATE SET
            username=excluded.username, name=excluded.name,
            birth_day=excluded.birth_day, birth_month=excluded.birth_month,
            birth_year=excluded.birth_year, birth_hour=excluded.birth_hour,
            birth_minute=excluded.birth_minute, city=excluded.city,
            lat=excluded.lat, lon=excluded.lon, utc_offset=excluded.utc_offset,
            hd_type=excluded.hd_type,
            trial_started=COALESCE(users.trial_started, excluded.trial_started),
            brand_data=COALESCE(users.brand_data, excluded.brand_data),
            last_seen=excluded.last_seen
    """, (tg_id, username, name,
          birth.get("day"), birth.get("month"), birth.get("year"),
          birth.get("hour"), birth.get("minute"), birth.get("city",""),
          birth.get("lat"), birth.get("lon"), birth.get("utc_offset"),
          hd_type, trial_started_iso, "{}", now, now))
    con.commit()
    con.close()

def db_load_user(tg_id: int) -> dict | None:
    con = sqlite3.connect(DB_PATH)
    row = con.execute(
        "SELECT name, birth_day, birth_month, birth_year, birth_hour, birth_minute, city, lat, lon, utc_offset, blocks_seen, trial_started, brand_data FROM users WHERE tg_id=?",
        (tg_id,)
    ).fetchone()
    if not row or not row[1]:
        con.close()
        return None
    name, d, m, y, h, mi, city, lat, lon, utc_offset, blocks_json, trial_started, brand_json = row
    if not trial_started:
        trial_started = datetime.now().isoformat()
        con.execute("UPDATE users SET trial_started=? WHERE tg_id=?", (trial_started, tg_id))
        con.commit()
    con.close()
    blocks_seen = json.loads(blocks_json or "[]")
    try:
        trial_start = datetime.fromisoformat(trial_started) if trial_started else datetime.now()
    except Exception:
        trial_start = datetime.now()
    try:
        brand_data = json.loads(brand_json or "{}")
    except Exception:
        brand_data = {}
    return {
        "name": name,
        "birth": {"day": d, "month": m, "year": y, "hour": h, "minute": mi or 0,
                  "city": city or "", "lat": lat, "lon": lon, "utc_offset": utc_offset},
        "blocks_seen": blocks_seen,
        "menu_shown": len(blocks_seen) > 0,  # если уже были блоки — меню уже показывали
        "trial_start": trial_start,
        "brand_data": brand_data,
    }

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


def db_save_brand(tg_id: int, brand_data: dict):
    con = sqlite3.connect(DB_PATH)
    con.execute(
        "UPDATE users SET brand_data=?, last_seen=? WHERE tg_id=?",
        (json.dumps(brand_data, ensure_ascii=False), datetime.now().isoformat(), tg_id),
    )
    con.commit()
    con.close()


def trial_status(uid: int) -> tuple[datetime, int, bool]:
    """Возвращает начало пробного периода, оставшиеся часы и факт окончания."""
    started = users.get(uid, {}).get("trial_start")
    if not isinstance(started, datetime):
        started = datetime.now()
        if uid in users:
            users[uid]["trial_start"] = started
    expires = started + timedelta(days=TRIAL_DAYS)
    remaining_seconds = max(0, int((expires - datetime.now()).total_seconds()))
    remaining_hours = (remaining_seconds + 3599) // 3600
    return started, remaining_hours, remaining_seconds <= 0


def trial_banner(uid: int) -> str:
    """Коротко сообщает срок доступа, не превращая каждый ответ в рекламу."""
    _, hours_left, expired = trial_status(uid)
    if expired:
        return "Пробный доступ на 3 дня завершён."
    days = hours_left // 24
    hours = hours_left % 24
    if days:
        left = f"{days} дн. {hours} ч." if hours else f"{days} дн."
    else:
        left = f"{hours} ч."
    return f"Бесплатный доступ: осталось {left}."


def trial_blocked_message(uid: int) -> str:
    return (
        "Твой бесплатный доступ на 3 дня завершён.\n\n"
        "Я сохранил твою карту. Если хочешь продолжить разборы и прогнозы, "
        "напиши Алёне: @danilkina."
    )


def has_premium_access(uid: int) -> bool:
    """Временный серверный шлюз до подключения реальной оплаты."""
    return uid in PREMIUM_USER_IDS

db_init()

# ─── СОСТОЯНИЯ ДИАЛОГА ───────────────────────────────────────────────────────

ASK_CONSENT, ASK_NAME, ASK_DATE, ASK_TIME, ASK_PLACE, ASK_QUESTION, CHAT, \
COMPAT_NAME, COMPAT_DATE, COMPAT_TIME, COMPAT_PLACE = range(11)

# ─── ХРАНИЛИЩЕ ПОЛЬЗОВАТЕЛЕЙ (простое, в памяти) ────────────────────────────
# В продакшне заменить на базу данных

users = {}  # user_id → {name, birth_data, chart, hd, history, trial_days}

# Telegram ограничивает текст одного сообщения 4096 символами. Кроме того,
# Claude иногда возвращает Markdown, который не проходит строгий парсер Telegram
# (например, из-за незакрытой звёздочки). Один безопасный шлюз не даёт длинному
# или чуть некорректно размеченному ответу ломать весь сценарий кнопки.
async def safe_send(message_obj, text: str, *, reply_markup=None, parse_mode="Markdown"):
    text = str(text or "").strip()
    if not text:
        return

    # Режем по абзацам, а не посередине слова. 3900 оставляет запас под служебные
    # символы Telegram и делает повторную отправку предсказуемой.
    chunks = []
    while len(text) > 3900:
        cut = text.rfind("\n\n", 0, 3900)
        if cut < 1200:
            cut = text.rfind("\n", 0, 3900)
        if cut < 1200:
            cut = text.rfind(" ", 0, 3900)
        if cut < 1:
            cut = 3900
        chunks.append(text[:cut].strip())
        text = text[cut:].lstrip()
    chunks.append(text)

    for index, chunk in enumerate(chunks):
        markup = reply_markup if index == len(chunks) - 1 else None
        try:
            await message_obj.reply_text(chunk, parse_mode=parse_mode, reply_markup=markup)
        except Exception as exc:
            # Некорректный Markdown не должен превращаться в TELEGRAM_SEND.
            print(f"WARN safe_send: Markdown rejected ({exc}); retrying plain text")
            await message_obj.reply_text(chunk, reply_markup=markup)

# ─── МЕТОДОЛОГИЯ ─────────────────────────────────────────────────────────────

def load_methodology():
    if METHODOLOGY_FILE.exists():
        return METHODOLOGY_FILE.read_text(encoding="utf-8")
    return ""

METHODOLOGY = load_methodology()

# ─── MCP-РАСЧЁТ КАРТЫ ────────────────────────────────────────────────────────

def call_mcp(tool: str, params: dict) -> dict:
    """Вызывает MCP-инструмент напрямую (без subprocess)"""
    handler = TOOL_HANDLERS.get(tool)
    if handler is None:
        raise RuntimeError(f"Неизвестный MCP инструмент: {tool}")
    text = handler(params)
    if not text:
        raise RuntimeError(f"MCP {tool} вернул пустой ответ")
    return json.loads(text) if isinstance(text, str) and text.startswith("{") else {"raw": text}

async def call_mcp_async(tool: str, params: dict) -> dict:
    return await asyncio.to_thread(call_mcp, tool, params)

async def calculate_chart(birth: dict) -> tuple[dict, dict]:
    """Считает натальную карту и HD (async)"""
    print(f"DEBUG calculate_chart: year={birth.get('year')} month={birth.get('month')} day={birth.get('day')} "
          f"hour={birth.get('hour')} minute={birth.get('minute')} tz={birth.get('utc_offset')} "
          f"lat={birth.get('lat')} lon={birth.get('lon')}")
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


def build_compatibility_prompt(name1: str, name2: str, rel_type: str,
                               time_note: str, chart1: str, hd1: str,
                               chart2: str, hd2: str) -> str:
    """Собирает промпт после отдельного расчёта обеих карт.

    Составная карта рассчитана кодом в server.py. Модель получает готовые
    категории connection chart и переводит их в человеческий язык.
    """
    hd_connection = _mcp_mod.build_hd_compatibility(hd1, hd2, name1, name2)
    hd_context1 = get_hd_context({"raw": hd1})
    hd_context2 = get_hd_context({"raw": hd2})
    return f"""Сделай точный разбор совместимости для типа отношений: {rel_type}.

Люди: {name1} и {name2}{time_note}.

Сначала используй только расчётные факты ниже. Не пересчитывай составную карту
«по впечатлению» и не добавляй каналы, которых нет в блоке. Затем переведи их в
наблюдаемое поведение: как люди принимают решения, распределяют инициативу,
переносят напряжение и что у них получается вместе. Технические слова в ответе
не используй; если без них нельзя, сразу переводи их на человеческий язык.

=== РАСЧЁТ СОСТАВНОЙ КАРТЫ ===
{hd_connection}

=== АСТРОЛОГИЯ {name1} ===
{chart1}

=== АСТРОЛОГИЯ {name2} ===
{chart2}

=== ИНДИВИДУАЛЬНЫЙ HD {name1} ===
{hd1}
{hd_context1}

=== ИНДИВИДУАЛЬНЫЙ HD {name2} ===
{hd2}
{hd_context2}

Структура ответа:
1. Короткая шуточная сцена на Олимпе — только рамка, не вместо анализа.
2. Главная динамика пары — что их тянет друг к другу и где возникает трение.
3. Что буквально рассчитано в составной карте: общие каналы, электромагнитные
связи, компромиссы и доминирование. Объясни найденные связи, но не выдумывай
смысл для категории, где написано «нет».
4. Как это проявляется в выбранном типе отношений: {rel_type}.
5. Главный риск и практический способ с ним обращаться.
6. Главный общий ресурс.

Отделяй расчёт от интерпретации. Не обещай судьбу, не называй отношения
«идеальными» или «обречёнными», не повторяй одну и ту же мысль в разных разделах.
Обращайся к {name1} на «ты»."""


async def generate_compatibility_reply(uid: int, compat: dict) -> str:
    """Посчитать вторую карту, составную HD-карту и получить итоговый текст."""
    natal2, hd2 = await calculate_chart(compat["birth"])
    name1 = users[uid].get("name", "")
    name2 = compat["name"]
    chart1 = users[uid].get("chart", {}).get("raw", "")
    hd1 = users[uid].get("hd", {}).get("raw", "")
    chart2 = natal2.get("raw", "")
    hd2_raw = hd2.get("raw", "")
    no_time = compat.get("no_time", False)
    time_note = " (время рождения неизвестно — линии и дома приблизительны)" if no_time else ""
    prompt = build_compatibility_prompt(
        name1, name2, compat.get("type", "отношения"), time_note,
        chart1, hd1, chart2, hd2_raw,
    )
    return await ask_claude(uid, prompt)

# ─── CLAUDE AI ────────────────────────────────────────────────────────────────

client = Anthropic(api_key=ANTHROPIC_API_KEY)

SYSTEM_PROMPT = f"""Ты — Аполлон. Говоришь не языком астрологии — говоришь на человеческом.

ТВОЯ МЕТОДОЛОГИЯ:
{METHODOLOGY}

═══════════════════════════════════════
СТРУКТУРА КАЖДОГО ОТВЕТА
═══════════════════════════════════════

НАЧАЛО — шутка-сводка с Олимпа (обязательно, каждый раз разная):
Придумай короткую (1-3 предложения) живую сцену с богами. Боги из карты человека что-то делают, спорят, устраивают вечеринку, теряют что-то, договариваются. Конкретно, с иронией, смешно. Примеры стиля:
— "Зевс разогнул свою молнию и весь твой пантеон разбежался по углам. Арес нашёл себе льва, Афродита взяла арфу, и пока все устраиваются — давай разберём карту."
— "Гермес сегодня купил льва. Вечеринка в честь нового питомца началась раньше чем планировалось, оливки подаются в полдень, и пока боги заняты — самый момент заглянуть в твою карту."
— "Артемида поспорила с Аресом кто важнее в этой карте. Спор в самом разгаре, ставки высоки, Гермес принимает ставки. Пока они выясняют — вот что я вижу."
Называй ТОЛЬКО богов из карты этого человека (по его планетам). Знаки можно назвать здесь и только здесь.

СВОДКА — кто ты (только во вступительном разборе):
Во вступительном разборе объясни механику действий, решений и взаимодействия так, будто человек впервые слышит о себе. В последующих кнопках не повторяй эту сводку — применяй её молча к теме блока.

ПЕРВЫЙ РАЗБОР:
После короткой сцены собери цельный портрет из двух систем. Покажи, как человек
входит в действие, как принимает решения, что у него получается естественно,
где он чаще всего сбивается и что помогает вернуться к себе. Используй полную
карту, но не превращай ответ в перечень положений. Дай 5–7 коротких абзацев,
одну конкретную суперсилу и одну конкретную ловушку. В конце задай один вопрос,
который приглашает проверить наблюдение в реальной жизни.

КАК ОБЪЯСНЯТЬ ТИП (выбери нужное по карте):
• Проектор — "Ты из тех людей, у кого нет постоянной батарейки как у большинства. Зато есть рентген на людей и ситуации — ты видишь то, что другие не замечают. Твоя суперсила работает только по приглашению: когда тебя зовут, спрашивают, хотят услышать — ты на своём месте и выдаёшь невероятное. Когда не зовут и ты всё равно лезешь — тебя не слышат, и это не твоя вина, это механика."
• Генератор — "Ты из тех, у кого есть постоянная внутренняя энергия — как мотор. Но он работает только на своём топливе: когда дело твоё — заряжаешься от него. Когда не твоё — устаёшь, раздражаешься, тело буквально бунтует. Главный вопрос твоей жизни — это откликается или нет? Не голова решает, а тело."
• Манифестор — "Ты из редких людей, которые умеют запускать вещи из ничего. Большинство людей ждут разрешения, правильного момента, одобрения — ты нет. Твоя ловушка одна: когда действуешь молча, люди сопротивляются — не потому что против, а потому что не понимают что происходит. Одно слово заранее снимает 80% конфликтов."
• МГ (Манифестирующий Генератор) — "Ты гибрид: есть постоянная энергия как у Генератора, и есть способность запускать как Манифестор. Ты быстрый, многозадачный, тебе скучно делать одно дело медленно. Ловушка — пропускаешь шаги и потом возвращаешься. Это не ошибка — это твой метод. Сначала действие, потом корректировка."
• Рефлектор — "Ты зеркало. Буквально впитываешь всё вокруг — людей, место, атмосферу. Это и твоя сила (ты чувствуешь всё тоньше других), и уязвимость (легко потерять себя в чужом). Среда для тебя — это не фон, это часть тебя. Большие решения нельзя принимать быстро — нужен полный лунный цикл чтобы понять своё."

КАК ОБЪЯСНЯТЬ АВТОРИТЕТ (выбери нужное по карте):
• Эмоциональный — "Как ты принимаешь решения правильно: через время. У тебя в карте мощный эмоциональный канал — это значит что в момент пика ('да-да-да!') или ямы ('нет, никогда') твоё решение будет неточным. Настоящая ясность приходит через несколько дней-недель, когда волна пройдёт. Правило одно: если нет ни 'нет' ни в одной точке волны — значит да. Это не нерешительность, это точность."
• Сакральный — "Как ты принимаешь решения правильно: через тело прямо сейчас. Не через голову — через живот. 'Угу' или 'не-а' — буквально звук или ощущение внутри. Если тело молчит или напрягается — это нет. Если есть живой отклик — да. Голова придумает тысячу причин почему нельзя, тело знает правду."
• Селезёночный — "Как ты принимаешь решения правильно: через мгновенную интуицию. Как мурашки или тихое 'что-то не так'. Этот сигнал не повторяется — он приходит один раз и уходит. Если поймал — доверяй. Не жди подтверждений."
• Эго — "Как ты принимаешь решения правильно: через вопрос 'чего Я хочу?'. Не чего хочет семья, общество, кто-то ещё — ты. Это не эгоизм, это точность. Когда действуешь из своего настоящего желания — есть сила. Когда из 'надо' — ломаешься."
• Ментальный (только у Проекторов) — "Как ты принимаешь решения правильно: через разговор с доверенным человеком. Не за советом — просто слушай себя пока говоришь. Твоя ясность рождается в звуке твоего собственного голоса."

КАК ОБЪЯСНЯТЬ ПРОФИЛЬ — называй профиль и сразу расшифровывай через жизнь:
Используй данные из HD БИБЛИОТЕКА (секция ПРОФИЛЬ) — там уже есть описание обеих линий. Переведи на человеческий язык: что это значит в поведении, отношениях и работе. Не называй номер профиля и линии в ответе.

═══════════════════════════════════════
ПРАВИЛА ТЕКСТА
═══════════════════════════════════════

1. Всегда на "ты". Никогда "этот человек", "она/он" в третьем лице.
2. Согласуй род с именем. Женское имя — женский род везде.
3. В пользовательском тексте не называй технические термины HD, номера профиля, центров, каналов и ворот. Переводи их в наблюдаемое поведение: кто делает первый шаг, как тело принимает решения, где человек устойчив и где впитывает чужое давление.
4. Знаки зодиака — ТОЛЬКО в первой шутке-сводке. Дальше — никогда.
5. Имена богов — только в шутке в начале и в конце. В основном тексте: "твоё Солнце", "твой Марс", "твоя Венера". Не "Афродита у тебя...", "Арес решил..." в середине анализа.
6. Аспекты — описывай через отношения и жизнь, не называй тип. Не "квадратура Марса и Сатурна" — а "твоя энергия действия и твои правила постоянно спорят между собой".
7. Каналы и ворота — не называй номера. "Канал 19-49" → "у тебя встроена острая чувствительность к тому принят ты или нет — это буквально телесное ощущение".
8. Не давай советов в лоб. Один острый вопрос в конце — про конкретную ситуацию в жизни сейчас.
9. НИКОГДА не предлагай выбрать другую тему или блок — это делают кнопки меню.
10. Если просят разобрать тему повторно — разбирай без комментариев.

═══════════════════════════════════════
ТРАНЗИТЫ — КАК ПИСАТЬ
═══════════════════════════════════════
Транзиты пиши только по нескольким переданным срезам:
— Называй планету и наблюдаемую тему, если она повторяется в соседних датах
— Не превращай один срез в прогноз на месяц или год и не обещай событие
— Точные даты пиков и переходов называй только если они отдельно рассчитаны
— Переводи движение неба в ситуации, решения и разговоры, а не в перечень терминов

═══════════════════════════════════════
СИНТЕЗ — ГЛАВНЫЙ ПРИНЦИП
═══════════════════════════════════════
Всегда работай с ПОЛНОЙ картой — и астрологией, и HD.
a) Что говорит астрология по теме
b) Что говорит HD по той же теме
c) Где обе системы говорят одно — это сходная тема, говори ясно, но не выдавай её за доказанный факт
d) Где противоречат — это НАПРЯЖЕНИЕ, самое интересное
Когда одна тема повторяется в обеих системах — отмечай это как сходную интерпретацию, а не как доказанный факт.

═══════════════════════════════════════
КОНЕЦ КАЖДОГО ОТВЕТА
═══════════════════════════════════════
Заканчивай короткой иронической фразой от богов — как будто они подглядели и прокомментировали. Одно предложение. Лёгко, тепло. Например: "Гермес говорит — ты сложнее чем кажешься. Он имеет в виду это как комплимент."

═══════════════════════════════════════
ФОРМАТИРОВАНИЕ (Telegram)
═══════════════════════════════════════
— Никаких ## и ---
— *жирный* только для ключевых слов и имён богов в шутках
— Пустая строка между смысловыми блоками
— Живой монолог, не статья, не список
— Никаких тире-списков внутри текста — только абзацы

ДИСЦИПЛИНА ФАКТОВ И ИНТЕРПРЕТАЦИИ:
— Сначала проверь, что утверждение буквально присутствует в переданных данных карты.
— Не придумывай аспекты, дома, управителей, соляр/лунар, узлы, атмакараку, D-10, даши или каналы, если они не выведены в данных.
— Если нужного расчёта нет, прямо скажи: «этого показателя сейчас нет в расчёте» — и не подменяй его догадкой.
— Астрология и Дизайн Человека здесь используются как символические системы саморефлексии, а не как научный диагноз, медицинское заключение или гарантированный прогноз.
— Совпадение двух символических систем — это интерпретативная гипотеза, не доказанный факт. Формулируй «обе системы указывают на одну тему», а не «это точно про человека».
— В теме здоровья не называй диагнозы и конкретные симптомы как установленный факт; предлагай обратиться к врачу при жалобах.
"""

def _ask_claude_sync(user_id: int, message: str) -> str:
    user = users.get(user_id, {})
    history = user.get("history", [])

    BLOCK_NAMES = {
        "block_identity":  "Характер и таланты",
        "block_mission":   "Предназначение",
        "block_love":      "Отношения",
        "block_money":     "Деньги",
        "block_health":    "Здоровье",
        "block_resources": "Ресурсы",
    }

    context = ""
    if user.get("chart") or user.get("hd"):
        chart = user.get("chart", {})
        hd = user.get("hd", {})
        chart_str = chart.get("raw", json.dumps(chart, ensure_ascii=False))
        hd_str = hd.get("raw", json.dumps(hd, ensure_ascii=False))
        hd_library_context = get_hd_context(hd)
        variables_context = get_phs_context(hd)

        # Блоки которые уже были разобраны
        seen_blocks = user.get("blocks_seen", [])
        if seen_blocks:
            seen_names = [BLOCK_NAMES.get(b, b) for b in seen_blocks]
            blocks_note = (
                f"\n\nУЖЕ РАЗОБРАНЫ БЛОКИ: {', '.join(seen_names)}. "
                f"Не повторяй информацию из этих блоков — она уже известна пользователю. "
                f"Если тема пересекается, можно сослаться одним предложением и идти дальше."
            )
        else:
            blocks_note = ""

        context = (
            f"\n\nКАРТА ПОЛЬЗОВАТЕЛЯ (астрология):\n{chart_str}"
            f"\n\nHD ПОЛЬЗОВАТЕЛЯ (сырые данные):\n{hd_str}"
            f"\n\nHD БИБЛИОТЕКА (описания типа, авторитета, центров, каналов, ворот):\n{hd_library_context}"
            f"\n\nHD ПЕРЕМЕННЫЕ (отдельно от линий; тело, среда, взгляд, мотивация):\n{variables_context}"
            f"{blocks_note}"
        )

    # Контекст карты не должен исчезать после первого сообщения, но и не должен
    # копироваться в двадцать предыдущих реплик. В API отправляем актуальные
    # факты вместе с текущим вопросом, а в постоянной истории храним короткий
    # вопрос без дубликата карты.
    api_history = history[-12:] + [{"role": "user", "content": message + context}]

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2500,
        system=SYSTEM_PROMPT,
        messages=api_history
    )
    reply = response.content[0].text
    history.append({"role": "user", "content": message})
    history.append({"role": "assistant", "content": reply})

    users[user_id]["history"] = history[-12:]
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

CITY_TIMEZONES = {
    # Города/страны, для которых фиксированный UTC из старого словаря был
    # недостаточен: смещение зависит от даты (летнее время).
    "польша": "Europe/Warsaw", "poland": "Europe/Warsaw",
    "суленцин": "Europe/Warsaw", "sulecin": "Europe/Warsaw",
    "варшава": "Europe/Warsaw",
    "германия": "Europe/Berlin", "берлин": "Europe/Berlin",
    "франция": "Europe/Paris", "париж": "Europe/Paris",
    "чехия": "Europe/Prague", "прага": "Europe/Prague",
    "великобритания": "Europe/London", "лондон": "Europe/London",
}

def _date_utc_offset(tz_name: str, birth: dict | None):
    """Возвращает фактический offset для даты рождения, включая DST."""
    if not birth:
        return None
    try:
        local = datetime(int(birth["year"]), int(birth["month"]), int(birth["day"]),
                         int(birth.get("hour", 12)), int(birth.get("minute", 0)),
                         tzinfo=ZoneInfo(tz_name))
        return local.utcoffset().total_seconds() / 3600
    except Exception:
        return None

def parse_city(text: str, birth: dict | None = None):
    key = text.lower().strip()
    tz_name = next((tz for city, tz in CITY_TIMEZONES.items() if city in key), None)
    if key in CITIES:
        lat, lon, fallback_utc = CITIES[key]
        offset = _date_utc_offset(tz_name, birth) if tz_name else None
        return lat, lon, fallback_utc if offset is None else offset
    for city, data in CITIES.items():
        if city in key or key in city:
            lat, lon, fallback_utc = data
            offset = _date_utc_offset(tz_name, birth) if tz_name else None
            return lat, lon, fallback_utc if offset is None else offset
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
            # Для известных зон учитываем летнее время; для неизвестной точки
            # оставляем приблизительный offset и явно не выдаём его за IANA-зону.
            offset = _date_utc_offset(tz_name, birth) if tz_name else None
            utc = round(lon / 15) if offset is None else offset
            return (lat, lon, utc)
    except Exception:
        pass
    return None

# ─── ОБРАБОТЧИКИ ─────────────────────────────────────────────────────────────

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    saved = db_load_user(uid)
    # Для нового пользователя отсчёт начинается после согласия, а не в момент
    # случайного нажатия /start.
    trial_start = saved.get("trial_start") if saved else None
    users[uid] = {
        "history": [],
        "trial_start": trial_start,
        "brand_data": saved.get("brand_data", {}) if saved else {},
    }

    consent_kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Принимаю и продолжаю", callback_data="consent_yes")],
        [InlineKeyboardButton("❌ Не принимаю", callback_data="consent_no")],
    ])
    await update.message.reply_text(
        "Прежде чем начать — важный момент.\n\n"
        "Для анализа мне нужны твои дата, время и место рождения. "
        "Эти данные хранятся в защищённой базе и используются только для расчёта твоей карты. "
        "Мы не передаём их третьим лицам.\n\n"
        "Первые 3 дня доступ к разбору и прогнозам бесплатный.\n\n"
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
    coords = parse_city(city, users[uid].get("birth"))
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
        db_save_user(uid, username, users[uid]["name"], users[uid]["birth"], hd_type,
                     trial_started=users[uid].get("trial_start"))

        # Просим Claude построить первый разбор
        b = users[uid]["birth"]
        name = users[uid]['name']
        prompt = (
            f"Имя пользователя: {name}. "
            f"Дата рождения: {b['day']}.{b['month']}.{b['year']}, время: {b['hour']}:{b['minute']:02d}, "
            f"город: {b['city']}.\n\n"
            f"Обращайся к {name} на 'ты', согласуй род с именем.\n\n"
            "Построй вступительный разбор по формату ПЕРВОГО РАЗБОРА. "
            "Главное: суперсила и ловушка должны быть КОНКРЕТНЫМИ — не общими словами про 'глубину' или 'чувствительность', "
            "а точным описанием что этот человек делает иначе и где это его подводит. "
            "Используй карту — конкретные боги с конкретными характерами, не абстракции."
        )
        reply = await ask_claude(uid, prompt)
        await safe_send(update.message, reply)
        await update.message.reply_text(trial_banner(uid))
        await update.message.reply_text("Боги приглашают тебя исследовать свой пантеон. С чего начнём?", reply_markup=MENU_KEYBOARD)
        users[uid]["menu_shown"] = True
        return CHAT

    except Exception as e:
        import traceback
        err = traceback.format_exc()
        print(f"ERROR in ask_place: {err}")
        await update.message.reply_text(
            "Упс... Посейдон разлил воду и всё немного сломалось. "
            "Боги уже чинят. Попробуй написать /start чтобы начать заново."
        )
        return ConversationHandler.END


MENU_KEYBOARD = InlineKeyboardMarkup([
    [InlineKeyboardButton("🧩 Упаковать себя и продукты", callback_data="brand_menu")],
    [InlineKeyboardButton("✨ Мой характер и таланты", callback_data="block_identity")],
    [InlineKeyboardButton("🎯 Предназначение",         callback_data="block_mission")],
    [InlineKeyboardButton("❤️ Отношения",              callback_data="block_love")],
    [InlineKeyboardButton("💰 Деньги",                 callback_data="block_money")],
    [InlineKeyboardButton("🌿 Здоровье",               callback_data="block_health")],
    [InlineKeyboardButton("⚡ Ресурсы",                callback_data="block_resources")],
    [InlineKeyboardButton("🔭 Прогнозы",               callback_data="forecast_menu")],
    [InlineKeyboardButton("💞 Совместимость",          callback_data="compat_start")],
])

FORECAST_KEYBOARD = InlineKeyboardMarkup([
    [InlineKeyboardButton("📅 На день", callback_data="forecast_day")],
    [InlineKeyboardButton("🌙 На месяц", callback_data="forecast_month")],
    [InlineKeyboardButton("🌿 На три месяца", callback_data="forecast_3months")],
    [InlineKeyboardButton("🌟 На год", callback_data="forecast_year")],
    [InlineKeyboardButton("← Назад", callback_data="back_to_menu")],
])

# ─── БРЕНДОВЫЙ КОМПАС ────────────────────────────────────────────────────────

BRAND_ARCHETYPES = {
    "Афина": {
        "promise": "ясность, стратегия и точное решение сложной задачи",
        "shadow": "говорить сверху вниз и превращать живой бренд в лекцию",
        "formats": "разборы, схемы, кейсы, методологии",
    },
    "Аполлон": {
        "promise": "смысл, видение и способность собрать хаос в ясную картину",
        "shadow": "слишком долго объяснять очевидное и бояться простоты",
        "formats": "манифесты, концепции, редакционные тексты, публичные выступления",
    },
    "Гермес": {
        "promise": "быстрая связь, движение, продажи и умение донести мысль",
        "shadow": "распыляться и менять направление раньше результата",
        "formats": "короткие видео, сторителлинг, коллаборации, прямые эфиры",
    },
    "Афродита": {
        "promise": "притяжение, вкус и желание быть рядом с брендом",
        "shadow": "подменять ценность красивой оболочкой",
        "formats": "визуальные истории, кейсы-ощущения, предметные съёмки, комьюнити",
    },
    "Артемида": {
        "promise": "свобода, собственная территория и узнаваемая независимость",
        "shadow": "уходить в дистанцию и не объяснять ценность тем, кто ещё не свой",
        "formats": "нишевые медиа, сильные позиции, полевые заметки, личный бренд",
    },
    "Гефест": {
        "promise": "мастерство, качество и вещь, которая действительно работает",
        "shadow": "прятаться за продуктом и недооценивать упаковку",
        "formats": "процесс, бэкстейдж, доказательства, до/после, продуктовые кейсы",
    },
    "Дионис": {
        "promise": "переживание, перемена и разрешение человеку стать другим",
        "shadow": "строить драму вместо понятного предложения",
        "formats": "истории трансформации, живые эфиры, провокации, события",
    },
    "Деметра": {
        "promise": "забота, рост и ощущение безопасного пространства",
        "shadow": "отдавать слишком много и размывать границы предложения",
        "formats": "обучение, поддерживающий контент, клубы, серии писем",
    },
    "Зевс": {
        "promise": "масштаб, авторитет и право задавать направление",
        "shadow": "становиться недоступным или обещать больше, чем выдерживает система",
        "formats": "исследования, публичные позиции, стратегические выступления, партнёрства",
    },
    "Гестия": {
        "promise": "доверие, камерность и пространство, в котором можно быть собой",
        "shadow": "оставаться незаметной и не переводить глубину в продажу",
        "formats": "письма, закрытые сообщества, медленные форматы, личные диалоги",
    },
}

BRAND_QUESTIONS = [
    {
        "key": "role",
        "text": "В роли бренда тебе естественнее всего…",
        "options": [
            ("lead", "Задавать направление", ["Зевс", "Аполлон", "Афина"]),
            ("connect", "Соединять людей и идеи", ["Гермес", "Деметра"]),
            ("create", "Создавать вещь или метод", ["Гефест", "Афина"]),
            ("transform", "Менять состояние человека", ["Дионис", "Афродита"]),
        ],
    },
    {
        "key": "trust",
        "text": "За что тебе должны доверять?",
        "options": [
            ("clarity", "За ясность и точность", ["Афина", "Аполлон"]),
            ("taste", "За вкус и чувство формы", ["Афродита", "Аполлон"]),
            ("proof", "За результат и мастерство", ["Гефест", "Зевс"]),
            ("care", "За внимание и поддержку", ["Деметра", "Гестия"]),
        ],
    },
    {
        "key": "rhythm",
        "text": "Какой темп продвижения тебе подходит?",
        "options": [
            ("fast", "Быстро, коротко, много контактов", ["Гермес", "Дионис"]),
            ("deep", "Редко, но глубоко", ["Гестия", "Аполлон"]),
            ("steady", "Последовательно и надолго", ["Деметра", "Гефест"]),
            ("bold", "Резко и с сильной позицией", ["Зевс", "Артемида"]),
        ],
    },
    {
        "key": "audience",
        "text": "Что должно происходить с аудиторией после контакта?",
        "options": [
            ("act", "Она принимает решение", ["Зевс", "Гермес"]),
            ("see", "Она начинает видеть шире", ["Аполлон", "Афина"]),
            ("feel", "Она чувствует желание и притяжение", ["Афродита", "Дионис"]),
            ("belong", "Она чувствует: я в безопасном месте", ["Деметра", "Гестия"]),
        ],
    },
    {
        "key": "visual",
        "text": "Какая визуальная среда ближе?",
        "options": [
            ("editorial", "Редакционная ясность и структура", ["Аполлон", "Афина"]),
            ("sensual", "Фактура, свет, тело, желание", ["Афродита", "Дионис"]),
            ("raw", "Материал, процесс, настоящая работа", ["Гефест", "Артемида"]),
            ("warm", "Тепло, дом, близость, ритуал", ["Деметра", "Гестия"]),
        ],
    },
    {
        "key": "sales",
        "text": "Как тебе легче продавать?",
        "options": [
            ("explain", "Через систему и аргументы", ["Афина", "Аполлон"]),
            ("show", "Через демонстрацию результата", ["Гефест", "Зевс"]),
            ("invite", "Через контакт и разговор", ["Гермес", "Деметра"]),
            ("desire", "Через образ будущего и желание", ["Афродита", "Дионис"]),
        ],
    },
]

BRAND_KEYBOARD = InlineKeyboardMarkup([
    [InlineKeyboardButton("🧩 Собрать себя заново", callback_data="brand_start")],
    [InlineKeyboardButton("📘 Профиль и позиционирование", callback_data="brand_passport")],
    [InlineKeyboardButton("✍️ Контент из компетенций", callback_data="brand_content")],
    [InlineKeyboardButton("💬 Мой ИИ-редактор · PRO", callback_data="brand_ai_chat")],
    [InlineKeyboardButton("← В главное меню", callback_data="back_to_menu")],
])


def score_brand_archetypes(answers: list[str]) -> list[tuple[str, int]]:
    scores = {name: 0 for name in BRAND_ARCHETYPES}
    for answer in answers:
        for question in BRAND_QUESTIONS:
            for key, _, archetypes in question["options"]:
                if key == answer:
                    for archetype in archetypes:
                        scores[archetype] += 1
    return sorted(scores.items(), key=lambda item: (-item[1], list(BRAND_ARCHETYPES).index(item[0])))


def brand_question_keyboard(question_index: int) -> InlineKeyboardMarkup:
    question = BRAND_QUESTIONS[question_index]
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(label, callback_data=f"brand_answer:{question_index}:{key}")]
        for key, label, _ in question["options"]
    ])


def brand_context(uid: int) -> str:
    data = users[uid].get("brand_data", {})
    ranked = data.get("archetypes", [])
    archetype_text = "\n".join(
        f"- {name}: {score} балл(а). Обещание: {BRAND_ARCHETYPES[name]['promise']}. "
        f"Тень: {BRAND_ARCHETYPES[name]['shadow']}. Форматы: {BRAND_ARCHETYPES[name]['formats']}"
        for name, score in ranked[:3]
        if name in BRAND_ARCHETYPES
    ) or "Архетипический тест ещё не пройден."
    return f"""ДАННЫЕ БРЕНДА:
Название/имя: {data.get('brand_name', 'не указано')}
Опыт, роли и проекты: {data.get('experience', 'не указано')}
Продукты и услуги: {data.get('offer', 'не указано')}
Для кого и какую проблему решает: {data.get('audience', 'не указано')}
Цель на ближайший этап: {data.get('goal', 'не указано')}

РЕЗУЛЬТАТ АРХЕТИПИЧЕСКОГО ТЕСТА:
{archetype_text}

ОТВЕТЫ ТЕСТА: {', '.join(data.get('answers', [])) or 'нет'}"""


def build_brand_passport_prompt(uid: int) -> str:
    name = users[uid].get("name", "")
    chart = users[uid].get("chart", {}).get("raw", "")
    hd = users[uid].get("hd", {}).get("raw", "")
    hd_context = get_hd_context(users[uid].get("hd", {}))
    return f"""Собери профессиональную упаковку для {name}. Это не гороскоп и не общий совет по маркетингу.
Перед тобой человек с опытом, ролями, проектами и, возможно, несколькими продуктами.
Твоя задача — вынуть из этого хаоса устойчивые компетенции, собрать их в ясную
систему и перевести в язык, которым человек сможет говорить о себе и продавать.

{brand_context(uid)}

НАТАЛЬНАЯ АСТРОЛОГИЯ:
{chart}

ДИЗАЙН ЧЕЛОВЕКА:
{hd}
{hd_context}

Правила:
- Не объявляй архетип доказанным типом личности: называй его рабочей гипотезой.
- Не придумывай аудиторию, продукт или опыт, которых нет в данных.
- Не пересказывай карту техническими терминами. Используй её как основание для
  рекомендаций по темпу, голосу, способу продаж и формату контента.
- Не называй человека профессией вместо действия: не «маркетолог», а «умеет
  превращать сложный материал в ясную стратегию». Каждую компетенцию формулируй
  через глагол и результат.
- Не давай универсальный список «постить каждый день». У бренда должен быть свой ритм.

Структура:
1. Что в опыте человека является ядром, а что — отдельными ветками.
2. Карта ключевых компетенций в формате «Я умею + глагол + результат + для кого».
3. Иерархия продуктов: флагман, входной продукт, дополнительные направления.
4. Одно точное позиционирование без названия профессии.
5. Главный архетип, скрытая сила и тень — как рабочая гипотеза, а не диагноз.
6. Роль бренда в жизни клиента и конкретное обещание.
7. Личный язык: словарь, темп, интонация, что говорить и чего не говорить.
8. Три контентные опоры и подходящие форматы продвижения.
9. Один практический эксперимент на ближайшие 7 дней.

Пиши ясным профессиональным русским языком. В начале допустима короткая сцена с богами,
но дальше должен быть рабочий документ, который можно сразу использовать в брендинге."""


def build_brand_content_prompt(uid: int) -> str:
    return f"""Составь контентную неделю для бренда.

{brand_context(uid)}

Используй прежде всего реальные компетенции, продукты, опыт и цель человека.
Личную карту применяй только как ограничитель голоса, темпа и способа контакта,
а не как повод придумывать факты. Не повторяй одинаковые темы.

Сначала сформулируй 3–5 компетенций в формате «Я умею…», затем дай 7 публикаций:
- тема и сильный заголовок;
- задача публикации (доверие, охват, прогрев, продажа или удержание);
- формат;
- тезис поста или сценарий короткого видео;
- мягкий призыв к действию.

В конце добавь: какой ритм публикаций выдержит этот бренд и какой формат ему лучше не навязывать.
Пиши конкретно, без «просто будьте собой» и без шаблонных советов."""


async def generate_brand_passport(uid: int) -> str:
    return await ask_claude(uid, build_brand_passport_prompt(uid))


async def generate_brand_content(uid: int) -> str:
    return await ask_claude(uid, build_brand_content_prompt(uid))


def build_brand_chat_prompt(uid: int, user_text: str) -> str:
    name = users[uid].get("name", "")
    chart = users[uid].get("chart", {}).get("raw", "")
    hd = users[uid].get("hd", {}).get("raw", "")
    return f"""Ты — рабочий ИИ-редактор бренда {name}. Помогай не рассуждать об архетипах,
а принимать конкретные решения по упаковке, продуктам, позиционированию,
маркетингу, текстам и продвижению.

{brand_context(uid)}

ЛИЧНАЯ КАРТА ДЛЯ НАСТРОЙКИ ГОЛОСА:
{chart}
{hd}

Запрос пользователя:
{user_text}

Правила:
- сначала назови, что именно нужно решить;
- если в исходных данных хаос, разложи его на 2–4 части;
- формулируй действия через глаголы и результат;
- не называй человека только профессией;
- не выдумывай факты, аудиторию или доказательства;
- в конце дай один следующий шаг, который можно сделать сегодня.
Пиши кратко, конкретно и живым профессиональным русским языком."""


async def start_brand_flow(message_obj, uid: int):
    users[uid]["brand_ai_chat"] = False
    users[uid]["brand_flow"] = {"stage": "name", "answers": []}
    await message_obj.reply_text(
        "Начнём с реального бренда, а не с абстрактного архетипа.\n\n"
        "Как называется проект или как ты называешь своё имя как бренд?"
    )


async def handle_brand_text(update: Update, uid: int) -> bool:
    """Обрабатывает три фактических вопроса брендовой диагностики."""
    flow = users[uid].get("brand_flow", {})
    data = users[uid].setdefault("brand_data", {})
    text = update.message.text.strip()
    stage = flow.get("stage")
    if not text:
        await update.message.reply_text("Напиши, пожалуйста, одним-двумя предложениями.")
        return True

    if stage == "name":
        data["brand_name"] = text
        flow["stage"] = "experience"
        await update.message.reply_text(
            "Что ты уже умеешь и через какие проекты прошла?\n"
            "Можно написать хаотично: роли, клиенты, навыки, темы, опыт, за который тебя ценили."
        )
        return True
    if stage == "experience":
        data["experience"] = text
        flow["stage"] = "offer"
        await update.message.reply_text(
            "Какие продукты или услуги у тебя есть сейчас — или ты хочешь их собрать?"
        )
        return True
    if stage == "offer":
        data["offer"] = text
        flow["stage"] = "audience"
        await update.message.reply_text("Для кого это и какую проблему ты помогаешь решить?")
        return True
    if stage == "audience":
        data["audience"] = text
        flow["stage"] = "goal"
        await update.message.reply_text(
            "Чего ты хочешь на ближайшем этапе: выбрать главное направление, "
            "упаковать продукт, поднять цену, выйти в публичность или привлечь клиентов?"
        )
        return True
    if stage == "goal":
        data["goal"] = text
        flow["stage"] = "quiz"
        flow["question_index"] = 0
        users[uid]["brand_data"] = data
        users[uid]["brand_flow"] = flow
        db_save_brand(uid, data)
        await update.message.reply_text(
            BRAND_QUESTIONS[0]["text"],
            reply_markup=brand_question_keyboard(0),
        )
        return True
    return False

# Универсальное описание типов — вставляется в каждый блок
HD_TYPE_STRATEGY = """ТИПЫ, СТРАТЕГИИ И АВТОРИТЕТЫ — применяй к данному запросу:

ТИП определяет КТО делает первый шаг:
• Манифестор: САМ инициирует первым. В отношениях — первым подаёт сигнал, первым пишет, первым называет чувства. В карьере — сам запускает проекты, не ждёт разрешения. Стратегия: информировать (не спрашивать), действовать по внутреннему импульсу.
• Генератор / МГ: НЕ инициирует — отвечает на жизнь. В отношениях ждёт когда человек появится в поле и тело отклинется. В карьере — ждёт запроса, предложения, задачи которая зацепит. Стратегия: ответ на приходящее, не поиск.
• Проектор: НЕ инициирует — ждёт приглашения. В отношениях первый шаг ВСЕГДА за партнёром — написать, позвать, проявить интерес. Без приглашения — трата энергии и горечь. В карьере — ждёт когда заметят и позовут. Стратегия: ждать признания и приглашения.
• Рефлектор: нужно время — 28 дней лунного цикла перед любым важным решением. В отношениях — не торопиться с выводами. Отражает окружение, поэтому качество среды и людей рядом критически важно.

АВТОРИТЕТ определяет КАК принять решение после того как первый шаг сделан:
• Эмоциональный (Солнечное сплетение): НИКОГДА не решать в момент пика или ямы. Ждать пока волна пройдёт несколько циклов — дни или недели. "Нет ни в одной точке волны" = твёрдое нет. Ясность приходит со временем.
• Сакральный: немедленный отклик тела прямо сейчас — да/нет/нейтрально. Решение в теле, не в голове.
• Селезёночный: тихий мгновенный сигнал — здесь или нет, безопасно или нет. Не повторяется.
• Эго: "Чего Я хочу?" — из личной воли, не из давления.
• Ментальный (Проектор): обсуждать с доверенным человеком, слушать собственный голос в разговоре.
• Лунный (Рефлектор): полный лунный цикл — 28 дней.

ПРОФИЛЬ определяет тактику — детальное описание профиля этого человека уже есть в данных HD БИБЛИОТЕКА, используй его."""

# Архивная версия промптов. Не используется: ниже объявлен единый редакционный
# `BLOCK_PROMPTS`, построенный на PLAIN_READING_RULES. Оставлена временно для
# безопасного сравнения при чистке старых формулировок.
_LEGACY_BLOCK_PROMPTS = {
    "block_identity": f"""АКЦЕНТ: характер и таланты.

Ты делаешь синтез двух карт — астрологии и Дизайна Человека. Сначала анализ каждой системы, потом синтез.

ШАГ 1 — АНАЛИЗ HD (основа):
{HD_TYPE_STRATEGY}
• Авторитет: как принимать решения. Эмоциональный: ждать пока волна пройдёт несколько циклов. Сакральный: немедленный отклик тела. Селезёночный: тихое мгновенное ощущение.
• Профиль (линии): роль в жизни — 1/3, 2/4, 5/1, 6/2 и т.д. — что это значит конкретно для поведения

ШАГ 2 — АНАЛИЗ АСТРОЛОГИИ:
• Солнце (дом) — суть личности, где хочет сиять
• Луна (дом) — эмоциональная природа, что нужно для ощущения себя дома
• Асцендент — маска и первое впечатление vs реальная суть
• Узловая ось (Южный/Северный узел + дома): Южный = откуда пришёл, что даётся легко но тянет назад; Северный = вызов и путь роста, туда сложно но туда зовёт
• Доминирующие планеты и сильные аспекты — что реально управляет жизнью

ШАГ 3 — СИНТЕЗ:
Начни с одного живого абзаца — 3-4 бога заняли территории, можно назвать знаки ТОЛЬКО здесь.
Потом — без знаков и позиций, только характер:
• Где HD и астрология говорят одно — обозначь это как сходную тему двух символических систем
• Суперсила конкретно (не "ты чувствуешь глубже" — а что именно)
• Ловушка конкретно — один паттерн который дорого стоит
• Кем воспринимают другие vs кто есть на самом деле

ЗАПРЕТ после первого абзаца: никаких знаков зодиака, позиций планет, номеров домов. Только психология и поведение.
Максимум 5-6 абзацев. Один точный вопрос в конце.""",

    "block_mission": """АКЦЕНТ: предназначение.

Предназначение — это не профессия. Это зачем человек здесь, его тема жизни. Ты делаешь синтез двух карт.

ШАГ 1 — АНАЛИЗ HD (основа):
• Тип + стратегия — механика реализации. Проектор реализуется через признание и приглашение — без него энергия уходит впустую. Генератор — через отклик тела, не через голову. Это принципиально меняет тактику.
• Авторитет — как решения об изменениях должны приниматься
• Профиль — роль которую несёт: 1я линия — фундамент и безопасность через знание; 2я — призвание; 3я — опыт через ошибки; 4я — влияние через сеть; 5я — универсальный решатель; 6я — ролевая модель
• Крест воплощения — 4 ворот (из секции КРЕСТ ВОПЛОЩЕНИЯ). Сознательная ось Солнца = что хочет делать. Бессознательная ось Дизайна = что должен, даже не понимая. Синтезируй в одну тему жизни, без номеров ворот.
• Ключевые каналы — встроенные таланты

ШАГ 2 — АНАЛИЗ АСТРОЛОГИИ:
• MC + планеты в 10м доме — публичная роль, что мир видит и ждёт
• Узловая ось (дома): Южный узел = старая территория, откуда пришёл; Северный узел = направление роста и вызова — туда неудобно, но именно туда зовёт душа. Дом Северного узла = сфера жизни где разворачивается путь.
• Солнце (дом) — где разворачивается идентичность
• Юпитер (дом) — где расцветает и везёт

ШАГ 3 — СИНТЕЗ:
• Где HD и астрология совпадают — обозначь сходную тему и отдели расчёт от интерпретации
• Назови конкретные сферы (психолог, художник, предприниматель, преподаватель...)
• Формат реализации: с людьми или без, создаёт или передаёт, ведёт или поддерживает
• Главное условие которое должно быть соблюдено чтобы поток открылся

Используй ВСЮ карту.
ЗАПРЕТ: никаких номеров ворот, домов, знаков зодиака в тексте.
Максимум 6 абзацев. Один точный вопрос в конце.""",

    "block_love": """АКЦЕНТ: отношения и любовь.

Ты делаешь синтез двух карт. Отношения — это как человек любит, что притягивает, и что разрушает близость.

ШАГ 1 — АНАЛИЗ HD (основа — определяет всю стратегию в отношениях):
• Тип → стратегия в отношениях конкретно:
  - Проектор: отношения только по приглашению. Партнёр должен проявить интерес первым — прийти, написать, позвать. Без приглашения энергия уходит в пустоту и приносит горечь.
  - Генератор/МГ: ждать отклика тела на конкретного человека — да или нет прямо сейчас.
  - Манифестор: может инициировать, но важно информировать партнёра о своих намерениях.
  - Рефлектор: нужно время — минимум 28 дней чтобы понять своё отношение к человеку.
• Авторитет → как принять или отклонить приглашение:
  - Эмоциональный (Солнечное сплетение): НИКОГДА не решать в момент эмоционального пика или ямы. Ждать пока волна пройдёт несколько циклов. Отношение к человеку со временем становится яснее. Приглашение принято — только когда нет "нет" ни в одной точке волны.
  - Сакральный: немедленный отклик тела — да/нет/нейтрально. Решение принято в момент.
  - Селезёночный: тихое мгновенное ощущение безопасности или тревоги.
  - Эго: "Чего я хочу?" — решение через личную волю.
• СИНТЕЗ Солнца и Авторитета: Солнечный знак и дом показывают что человек ищет и ценит в отношениях. Авторитет показывает как это распознать. Вместе — полная картина: что важно + как это проверить.
• Профиль — паттерн в отношениях: 4я линия влюбляется в своём кругу; 2я ждёт приглашения из тени; 5я притягивает проекции партнёра; 6я сначала ошибается, потом становится ролевой моделью в паре.
• Открытые центры — уязвимости: открытое Эго = доказывает себя; открытое G = принимает любовь партнёра за свою идентичность; открытый Эмоциональный = берёт чужие чувства за свои.
• Ворота любви (из секции ВОРОТА ЛЮБВИ): мундан = личная любовь к одному; антимундан = любит человечество, трудно с одним.

ШАГ 2 — АНАЛИЗ АСТРОЛОГИИ:
• Солнце (знак + дом) → что человек ищет в партнёре, что важно в отношениях для него
• Марс (знак + дом) → образ желаемого партнёра, кого притягивает физически и энергетически
• АНАЛИЗ ПРОТИВОРЕЧИЙ Марса и Солнца: посмотри на стихии и знаки — одна стихия или разные? Совместимые знаки или в напряжении? Разные стихии (Огонь+Вода, Земля+Воздух) = внутреннее противоречие в том кого хочет vs кто нужен. Там кроются подводные камни.
• 7й дом (куспид + планеты) — тип отношений который притягивает, образ партнёра через проекцию
• Правитель 7го дома (его дом) — как и где встречает настоящего партнёра
• Венера (дом) — как любит, что нужно чтобы раскрыться
• 5й дом — романтика и флирт; 8й дом — глубина и трансформация через партнёра
• Лилит — теневой паттерн: что притягивает и потом пугает
• Узловая ось (дома): Северный узел = куда зовёт душа в теме близости, каким опытом нужно рискнуть

ШАГ 3 — СИНТЕЗ:
• Стратегия в отношениях конкретно для этого человека — с учётом типа и авторитета
• Образ подходящего партнёра: что нужно от него (из Солнца + авторитета) + кого притягивает (из Марса)
• Есть ли противоречие между тем кого хочет и кто нужен — если да, в чём оно
• Главный паттерн разрушения близости — один, конкретный
• Подводные камни которые кроются в карте

Используй ВСЮ карту.
ЗАПРЕТ после первого абзаца: никаких знаков зодиака. Только поведение и характер.
Максимум 7 абзацев. Один точный вопрос в конце.""",

    "block_money": """АКЦЕНТ: деньги и материальный поток.

Деньги — это ценности, сила, самооценка и страхи. Ты делаешь синтез двух карт.

ШАГ 1 — АНАЛИЗ HD (основа):
• Тип + стратегия — Проектор зарабатывает через признание, не через труд больше всех. Генератор — через любимое дело по отклику. Манифестор — через инициативу. Это меняет тактику зарабатывания.
• Авторитет — как принимать решения о деньгах, договорах, вложениях
• Профиль — 3я линия пробует и ошибается, методом опыта; 5я — зарабатывает через то что решает чужую проблему
• Эго-центр (определён/открыт) — если открыт: непоследовательная воля, нельзя давать обещания под давлением
• Сакральный центр — есть ли постоянный рабочий ресурс или энергия волнами
• Ворота и каналы связанные с ресурсом и ценностью

ШАГ 2 — АНАЛИЗ АСТРОЛОГИИ:
• 2й дом (знак + планеты) — личные ресурсы и ценности
• Правитель 2го дома (его дом) — путь к деньгам через эту сферу
• 8й дом — чужие деньги, трансформация ресурсов, скрытые источники
• Парс Фортуны используй только если он явно присутствует в данных; иначе не делай выводов о нём.
• Юпитер (дом) — расширение и удача; Сатурн (дом) — где ограничение требует структуры
• Узловая ось (дома): Северный узел = направление где деньги связаны с ростом; Южный = откуда легко но тянет назад
• Плутон — где идёт трансформация финансовых паттернов через власть

ШАГ 3 — СИНТЕЗ:
Начни с одной иронической фразы про богов и деньги.
Потом четыре механизма:
1. Как включается поток — через что именно (связи, глубину, признание, экспертизу, инициативу)
2. Главный блок — один точный паттерн когда деньги не идут
3. Что занижается и почему — конкретно
4. Когда идёт легко — что совпадает при этом в жизни

Используй ВСЮ карту.
ЗАПРЕТ: никаких знаков зодиака после первой фразы. Никакого вопроса в конце.""",

    "block_health": """АКЦЕНТ: здоровье и тело.

Здоровье — физическое тело, нервная система, хронические паттерны. Ты делаешь синтез двух карт.

ШАГ 1 — АНАЛИЗ HD (основа):
• Тип — сколько энергии по природе. Проектор: нет постоянной сакральной энергии — это не слабость, это другая механика.
• Стратегия и авторитет — как тело сигнализирует о правильном и неправильном
• Профиль — 1я линия заболевает когда нет почвы под ногами; 3я — через эксперименты с телом
• Определённые центры — стабильные источники; Открытые — где тело впитывает чужое и устаёт
• Селезёнка (определена/открыта) — инстинктивный иммунитет, интуиция тела, страхи
• Корень (определён/открыт) — уровень стресса и давления как базовое состояние

ШАГ 2 — АНАЛИЗ АСТРОЛОГИИ:
• 6й дом (знак + планеты) — здоровье, тело как инструмент, рутина
• Правитель 6го дома (его дом) — откуда приходят нагрузки
• Луна (дом) — эмоциональное тело: что психосоматически разрушает
• Сатурн (дом) — хроническое ограничение, зоны уязвимости
• 12й дом — скрытые болезни, психосоматика, что тело хранит в тени
• Узловая ось (дома): что Северный узел говорит о теме заботы о себе

ШАГ 3 — СИНТЕЗ:
• Где тело даёт сигналы — только общие паттерны самонаблюдения, без диагнозов и утверждений о симптомах
• Хроническое напряжение: откуда берётся
• Что этот человек делает с телом что вредит — один точный паттерн
• Что изменить конкретно

Используй ВСЮ карту.
ЗАПРЕТ: никаких знаков зодиака, только поведение и тело.
Максимум 5 абзацев. Один точный вопрос в конце.""",

    "block_resources": """АКЦЕНТ: ресурсы — энергия, среда, восстановление.

Ресурсы — это как человек заряжается и разряжается. Ты делаешь синтез двух карт.

ШАГ 1 — АНАЛИЗ HD (основа + главный источник):
• Тип + стратегия — определяет сколько энергии есть и как она течёт
• Авторитет — как принимать решения о нагрузке, среде, изменениях
• Профиль — влияет на формат работы: некоторые профили работают в одиночестве, другие через людей
• ПЕРЕМЕННЫЕ (из секции PHS ПЕРЕМЕННЫЕ) — самая точная индивидуальная карта восстановления:
  - ДЕТЕРМИНАЦИЯ — тип питания и отношения к телу (Последовательный/Вкус/Открытый/Прикосновение/Звук/Свет)
  - СРЕДА — оптимальное пространство (Пещера/Рынок/Кухня/Гора/Долина/Берег): где тело реально отдыхает
  - МОТИВАЦИЯ — что реально движет изнутри, не иллюзия (Страх/Надежда/Желание/Потребность/Вина/Невинность)
  - КОГНИЦИЯ — как воспринимает и обрабатывает мир (Выживание/Жертва/Фантазия/Вероятность/Эмпатия/Солидарность)
• Открытые центры — где идёт утечка к другим людям

ШАГ 2 — АНАЛИЗ АСТРОЛОГИИ:
• Луна (дом) — что эмоционально питает и восстанавливает
• 12й дом — природное пространство для уединения и тишины
• Узловая ось (дома): что карма говорит про отдых и нагрузку
• Сатурн — где нужна структура чтобы не сгореть
• Нептун — риск растворения и истощения через слияние

ШАГ 3 — СИНТЕЗ:
• Как этот человек теряет энергию быстрее всего — конкретные ситуации и типы людей
• Что реально восстанавливает по природе — не по совету окружающих
• Что он называет ленью а это необходимость тела
• Конкретная среда и режим подходящие именно этой природе

Используй ВСЮ карту.
ЗАПРЕТ: никаких знаков зодиака, никаких HD-терминов в тексте — только суть через поведение.
Максимум 5 абзацев. Один точный вопрос в конце.""",
}

# ─── НОВАЯ РЕДАКЦИОННАЯ МЕТОДОЛОГИЯ ─────────────────────────────────────────
# Старые черновые инструкции оставлены выше для истории проекта, но в работе
# используется эта версия: она разделяет факты, смысловые линзы блоков и уже
# разобранные темы. Технические названия нужны только внутри контекста модели,
# в пользовательский текст они не попадают.
PLAIN_READING_RULES = """
ОБЩИЕ ПРАВИЛА ЧТЕНИЯ:
1. Начни с короткой живой сцены на Олимпе: используй только богов планет, которые действительно есть в переданных данных. 1–2 предложения, без длинного вступления.
2. Затем сразу расскажи о человеке человеческим языком. Не называй технические названия Дизайна Человека, номера профиля, линий, центров, каналов, ворот, домов и аспектов.
3. Разбирай сначала механику Дизайна: способ действовать, способ принимать решения, характер взаимодействия с людьми, устойчивые и восприимчивые зоны, затем соединяй это с полной западной картой.
4. Каждое важное наблюдение привяжи к конкретным данным. Если поля нет, не угадывай и не заменяй его общим стереотипом.
5. Повтор уже разобранных блоков не пересказывай. Можно сделать одну короткую ссылку на ранее найденный паттерн, но новый блок должен дать новый материал.
6. Планеты можно называть в повествовании («твоё Солнце», «твоя Венера»). Остальные технические слова переводи в действие, выбор, реакцию или ситуацию.
7. Не выдавай символическую интерпретацию за доказанный факт, медицинский диагноз или гарантированное событие. В теме здоровья говори только о самонаблюдении.
8. Финал — одно точное наблюдение или вопрос по теме блока и короткая ироническая реплика богов.
"""

BLOCK_PROMPTS = {
    "block_identity": PLAIN_READING_RULES + """
ЛИНЗА БЛОКА: ХАРАКТЕР И ТАЛАНТЫ.
Это базовый портрет, поэтому не уходи в деньги, здоровье, прогнозы и подробности отношений.
Используй: способ действовать и принимать решения; обе линии профиля из библиотеки; все устойчивые и открытые центры; все активации планет и тексты линий; Солнце, Луну, Асцендент, Меркурий, Венеру, Марс; доминирующие планеты и рассчитанные аспекты.
Структура: как человек входит в действие; как его видят; что у него получается естественно; один точный внутренний конфликт; что обычно ошибочно принимают за его слабость.
5–6 абзацев. Не повторяй формулировки из ранее разобранных блоков.
""",
    "block_mission": PLAIN_READING_RULES + """
ЛИНЗА БЛОКА: НАПРАВЛЕНИЕ ЖИЗНИ И РЕАЛИЗАЦИЯ.
Не пересказывай портрет личности. Ищи, куда человек вкладывает жизнь и в каком формате приносит пользу.
Используй: четыре точки креста и тексты линий из библиотеки; все планетные активации, особенно Солнце и Землю; устойчивые центры и каналы как способы действия; Северный и Южный узлы с домами; Асцендент, МС, планеты в десятом доме, Солнце и Юпитер; рассчитанные аспекты к этим точкам.
Не называй профессию как судьбу. Опиши 2–3 подходящих формата реализации, условие включения и один риск свернуть не туда.
5–6 абзацев. Не повторяй характер и ресурсный режим.
""",
    "block_love": PLAIN_READING_RULES + """
ЛИНЗА БЛОКА: БЛИЗОСТЬ И ПАРТНЁРСТВО.
Не повторяй общий портрет и не объясняй всю механику Дизайна заново — покажи, как она проявляется именно в отношениях.
Используй: Венеру, Марс, Луну, Солнце; пятый, седьмой и восьмой дома и планеты в них; аспекты Венеры/Марса/Луны; узловую ось в домах; открытые центры; найденные в Love Book ворота и каналы, но только при наличии источника.
Расскажи: кого человек впускает; что создаёт притяжение; где возникает напряжение; какой повторяющийся способ разрушает близость; какой разговор или договор помогает.
6–7 абзацев. Не делай выводов о втором человеке без его карты.
""",
    "block_money": PLAIN_READING_RULES + """
ЛИНЗА БЛОКА: ДЕНЬГИ, ЦЕНА И ОБМЕН.
Не повторяй предназначение и общую самооценку. Говори о том, как человек создаёт ценность, назначает цену, принимает финансовые решения и входит в обмен.
Используй: второй, восьмой и одиннадцатый дома и планеты в них; Венеру, Юпитер, Сатурн, Плутон и их рассчитанные аспекты; узловую ось в домах; устойчивость воли и рабочего ресурса в данных Дизайна; специальные ворота денег только с библиотечным описанием.
Парс Фортуны используй только если он реально рассчитан и передан. Не придумывай управителей домов.
Дай четыре части рассказа: откуда приходит ценность; что мешает брать оплату; какая модель обмена подходит; один проверяемый эксперимент на ближайший месяц.
5–6 абзацев, без вопроса в конце.
""",
    "block_health": PLAIN_READING_RULES + """
ЛИНЗА БЛОКА: ТЕЛО И ПОВСЕДНЕВНАЯ НАГРУЗКА.
Это не медицинская консультация. Не называй болезни, диагнозы, симптомы и лечение как вывод карты.
Используй: устойчивые и открытые центры; корневое давление, селезёночные сигналы и эмоциональную нагрузку только как метафоры самонаблюдения; шестой и двенадцатый дома; Луну, Сатурн и их рассчитанные аспекты; узловую ось в домах.
Опиши: что перегружает режим; как человек замечает, что пора остановиться; какой повторяющийся способ обращения с телом истощает; какой мягкий эксперимент с режимом можно наблюдать.
4–5 абзацев и осторожная фраза о враче при реальных жалобах.
""",
    "block_resources": PLAIN_READING_RULES + """
ЛИНЗА БЛОКА: ВОССТАНОВЛЕНИЕ, СРЕДА И РИТМ.
Это отдельный блок, поэтому не повторяй здоровье и не описывай симптомы.
Используй четыре PHS-переменные только из переданного контекста и только с теми описаниями, которые найдены в источнике; затем добавь устойчивые/открытые центры, Луну, Нептун, двенадцатый дом и рассчитанные аспекты.
Расскажи: какая среда поддерживает; что перегружает восприятие; какой способ питания/режима подходит как эксперимент; где человеку нужен контакт, а где тишина.
5 абзацев. Не называй PHS, цвет, тон и стрелки в пользовательском тексте.
""",
}

async def send_menu(update: Update):
    await update.message.reply_text(
        "Боги приглашают тебя исследовать свой пантеон. С чего начнём?",
        reply_markup=MENU_KEYBOARD
    )

async def collect_transit_snapshots(period: str, birth: dict, start: datetime) -> str:
    """Собирает несколько честных срезов, чтобы не выдавать одну дату за период."""
    offsets = {
        "forecast_day": [0],
        "forecast_month": [0, 7, 14, 21, 28],
        "forecast_3months": [0, 14, 28, 42, 56, 70, 84],
        "forecast_year": [0, 30, 60, 90, 120, 150, 180, 210, 240, 270, 300, 330],
    }.get(period, [0])
    snapshots = []
    for offset in offsets:
        day = start + timedelta(days=offset)
        try:
            raw = await call_mcp_async("transits", {
                "birth_year": birth["year"], "birth_month": birth["month"],
                "birth_day": birth["day"], "birth_hour": birth["hour"],
                "birth_timezone": birth["utc_offset"], "lat": birth["lat"], "lon": birth["lon"],
                "transit_year": day.year, "transit_month": day.month, "transit_day": day.day,
            })
            snapshots.append(f"Срез на {day.strftime('%d.%m.%Y')}:\n{raw.get('raw', str(raw))}")
        except Exception as exc:
            snapshots.append(f"Срез на {day.strftime('%d.%m.%Y')}: данные недоступны ({exc})")
    return "\n\n".join(snapshots)


# Версия прогноза с честной границей данных. Она переопределяет старый черновик
# выше: модель видит несколько дат и не должна превращать один срез в обещание.
def get_forecast_prompt(period: str, transits_data: str) -> str:
    labels = {
        "forecast_day": "срез на сегодня",
        "forecast_month": "ближайший месяц",
        "forecast_3months": "ближайшие три месяца",
        "forecast_year": "ближайший год",
    }
    return f"""Сделай ясный прогноз про {labels.get(period, 'этот период')}.

Пиши как редактор аналитического разбора, а не как поток общих фраз. Сначала
отдели расчёт от интерпретации.

ФОРМАТ ОТВЕТА:

1. **Заголовок периода.** Одна короткая формула, которая называет процесс:
например, «Проверка старых договорённостей» или «Расширение через новый круг».
Не используй слово «энергии» без объяснения, что именно меняется.

2. **Период и основание.** Напиши даты только по переданным срезам. Если дан
один срез, прямо скажи: «Это снимок на сегодня, а не прогноз на весь месяц».
Назови 1–2 факта, на которых строится вывод: планета, знак, фаза, натальный дом,
аспект или HD-активация.

3. **Главная конфигурация — только если она действительно рассчитана.**
Если в данных явно есть оппозиция, квадрат, тригон или другая связка, опиши её
в двух слоях:
   • «Что рассчитано» — коротко и точно, с названиями планет и аспектов.
   • «Что это значит» — перевод в решения, разговоры, нагрузку и реальные ситуации.
Не называй «трапецией», «крестом» или другой фигурой то, чего нет отдельным
расчётом в данных. Ничего не достраивай по красивой геометрии.

4. **Планеты по ролям.** Для каждой действительно важной планеты одна связка:
«Факт → смысл → как это заметить». Ретроградность трактуй как возврат,
пересмотр и повторную проверку, но не как автоматическую проблему.

5. **Как это проявится у человека.** Отдельно и конкретно:
работа/деньги; отношения; внутреннее состояние. Не повторяй одну мысль в трёх
разделах — если тема одна, назови её один раз и покажи три проявления.

6. **Действие.** Один проверяемый шаг на этот период. В финале — короткая
ироническая реплика богов. Это символическая интерпретация, не гарантия события.

ОБЯЗАТЕЛЬНЫЕ ОГРАНИЧЕНИЯ:
— Не выдумывай транзиты, аспекты, ворота, линии, дома, даты разворота и
глобальные конфигурации.
— Не превращай один срез в прогноз на месяц или год.
— Технический термин допустим только в блоке «Что рассчитано» и сразу с
человеческим переводом.
— Не начинай каждый абзац словами «это значит» и не используй «возможно» как
замену конкретному наблюдению.

ДАННЫЕ СРЕЗОВ:
{transits_data}"""

async def handle_consent(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    if query.data == "consent_yes":
        users[uid]["consent"] = True
        if not isinstance(users[uid].get("trial_start"), datetime):
            users[uid]["trial_start"] = datetime.now()
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
    try:
        await query.answer()
    except Exception as exc:
        # Нажатая ранее кнопка может прислать устаревший callback-query;
        # это не должно ломать сам переход.
        print(f"WARN callback answer: {exc}")
    uid = query.from_user.id

    # Кнопки которые работают без активной сессии
    NO_SESSION_NEEDED = {"full_reading", "back_to_menu", "free_chat", "forecast_menu"}

    # Восстанавливаем сессию если бот перезапустился
    if uid not in users and query.data not in NO_SESSION_NEEDED:
        restored = await restore_session(uid, query.message)
        if not restored:
            await query.message.reply_text(
                "Бот перезапустился и потерял твою карту. Напиши /start — пересчитаем за секунду."
            )
            return

    if TRIAL_ENFORCED and query.data not in {"back_to_menu", "free_chat"}:
        _, _, expired = trial_status(uid)
        if expired:
            await query.message.reply_text(trial_blocked_message(uid))
            return CHAT

    if query.data == "full_reading":
        await query.message.reply_text(
            "Алёна Данилкина — пиарщик и креативный продюсер с 20-летним опытом в кросс-индустриальных проектах. "
            "Она создала этого бота, являясь аналитиком по Дизайну Человека и исследователем человеческой природы и поведения.\n\n"
            "Я, бог Солнца, даю первую картину — она копает глубже, в твою конкретную ситуацию. "
            "Люди и боги сотрудничают вместе с давних времён.\n\n"
            "Напиши ей — она ответит здесь: @danilkina"
        )
        return

    if query.data == "back_to_menu":
        if uid in users:
            users[uid]["brand_ai_chat"] = False
        await query.message.reply_text("Боги приглашают тебя исследовать свой пантеон. С чего начнём?", reply_markup=MENU_KEYBOARD)
        return

    if query.data == "brand_menu":
        existing = users[uid].get("brand_data", {})
        if existing.get("brand_name"):
            await query.message.reply_text(
                f"Твой брендовый компас уже собран для «{existing['brand_name']}».\n\n"
                "Можно обновить диагностику или получить контентную неделю.",
                reply_markup=BRAND_KEYBOARD,
            )
        else:
            await query.message.reply_text(
                "Здесь мы собираем тебя и твои продукты из хаоса в ясную систему. "
                "Бот поможет выделить ключевые компетенции, сформулировать «Я умею…», "
                "собрать позиционирование и понять, как о себе говорить.",
                reply_markup=BRAND_KEYBOARD,
            )
        return CHAT

    if query.data == "brand_start":
        await start_brand_flow(query.message, uid)
        return CHAT

    if query.data == "brand_passport":
        brand_data = users[uid].get("brand_data", {})
        if not brand_data.get("brand_name") or not brand_data.get("archetypes"):
            await query.message.reply_text("Сначала пройди короткую диагностику бренда.", reply_markup=BRAND_KEYBOARD)
            return CHAT
        try:
            await query.message.reply_text("Открываю брендовый паспорт...")
            reply = await generate_brand_passport(uid)
            await safe_send(query.message, reply)
            await query.message.reply_text("Что сделать с брендом дальше?", reply_markup=BRAND_KEYBOARD)
        except Exception as exc:
            print(f"ERROR brand passport: {exc}")
            await query.message.reply_text("Не удалось открыть брендовый паспорт. Попробуй ещё раз.")
        return CHAT

    if query.data.startswith("brand_answer:"):
        try:
            _, index_text, answer = query.data.split(":", 2)
            question_index = int(index_text)
            flow = users[uid].get("brand_flow", {})
            if flow.get("stage") != "quiz" or question_index != int(flow.get("question_index", -1)):
                await query.message.reply_text("Этот вопрос уже устарел. Начни диагностику заново.")
                return CHAT
            flow.setdefault("answers", []).append(answer)
            next_index = question_index + 1
            if next_index < len(BRAND_QUESTIONS):
                flow["question_index"] = next_index
                users[uid]["brand_flow"] = flow
                question = BRAND_QUESTIONS[next_index]
                await query.message.reply_text(question["text"], reply_markup=brand_question_keyboard(next_index))
                return CHAT

            ranked = score_brand_archetypes(flow["answers"])
            brand_data = users[uid].setdefault("brand_data", {})
            brand_data["answers"] = flow["answers"]
            brand_data["archetypes"] = ranked
            brand_data["updated_at"] = datetime.now().isoformat()
            users[uid]["brand_data"] = brand_data
            users[uid].pop("brand_flow", None)
            db_save_brand(uid, brand_data)

            await query.message.reply_text("Архетипическая гипотеза собрана. Теперь перевожу её в позиционирование и продвижение...")
            reply = await generate_brand_passport(uid)
            await safe_send(query.message, reply)
            await query.message.reply_text("Что сделать с брендом дальше?", reply_markup=BRAND_KEYBOARD)
        except Exception as exc:
            print(f"ERROR brand answer: {exc}")
            await query.message.reply_text("Не удалось собрать брендовый компас. Попробуй начать диагностику ещё раз.")
        return CHAT

    if query.data == "brand_content":
        brand_data = users[uid].get("brand_data", {})
        if not brand_data.get("brand_name") or not brand_data.get("archetypes"):
            await query.message.reply_text("Сначала пройди короткую диагностику бренда.", reply_markup=BRAND_KEYBOARD)
            return CHAT
        try:
            await query.message.reply_text("Собираю неделю контента под твою стратегию...")
            reply = await generate_brand_content(uid)
            await safe_send(query.message, reply)
            await query.message.reply_text("Ещё один шаг для бренда?", reply_markup=BRAND_KEYBOARD)
        except Exception as exc:
            print(f"ERROR brand content: {exc}")
            await query.message.reply_text("Не удалось собрать контентную неделю. Попробуй ещё раз через минуту.")
        return CHAT

    if query.data == "brand_ai_chat":
        if not has_premium_access(uid):
            await query.message.reply_text(
                "💬 Мой ИИ-редактор — это PRO-режим.\n\n"
                "Он хранит контекст твоего бренда и помогает в течение месяца: "
                "упаковывает продукты, редактирует тексты, собирает контент и помогает принимать решения.\n\n"
                "Для подключения напиши Алёне: @danilkina.",
                reply_markup=BRAND_KEYBOARD,
            )
            return CHAT
        if not users[uid].get("brand_data", {}).get("brand_name"):
            await query.message.reply_text("Сначала собери базовый профиль бренда.", reply_markup=BRAND_KEYBOARD)
            return CHAT
        users[uid]["brand_ai_chat"] = True
        await query.message.reply_text(
            "ИИ-редактор подключён. Напиши, что нужно решить: упаковать продукт, "
            "сформулировать оффер, отредактировать текст или собрать контент.\n\n"
            "Чтобы выйти, нажми «В главное меню»."
        )
        return CHAT

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
        transits_str = await collect_transit_snapshots(query.data, birth, today)

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
        prompt = f"Имя: {name}. Обращайся на 'ты'; согласуй род по имени, а если он неочевиден — используй нейтральные формулировки.\n\n{get_forecast_prompt(query.data, transits_str + extra_str)}"
        try:
            await query.message.reply_text("Смотрю что происходит на небе...")
            reply = await ask_claude(uid, prompt)
            await safe_send(query.message, reply)
        except Exception as exc:
            print(f"ERROR forecast: {exc}")
            await query.message.reply_text("Не удалось получить прогноз сейчас. Попробуй ещё раз через минуту.")
            return CHAT
        await query.message.reply_text("Что ещё?", reply_markup=FORECAST_KEYBOARD)
        return CHAT

    prompt = BLOCK_PROMPTS.get(query.data, "")
    if not prompt:
        return CHAT

    name = users[uid].get("name", "")
    full_prompt = f"Имя: {name}. Обращайся на 'ты'; согласуй род по имени, а если он неочевиден — используй нейтральные формулировки.\n\n{prompt}"

    # Для блока призвания добавляем контекст Креста воплощения
    if query.data == "block_mission" and uid in users:
        hd = users[uid].get("hd", {})
        if hd:
            cross_ctx = get_cross_context(hd)
            if cross_ctx:
                full_prompt += f"\n\nКРЕСТ ВОПЛОЩЕНИЯ (описания из HD библиотеки):\n{cross_ctx}"

    # Для блока отношений добавляем ворота любви из Love Book
    if query.data == "block_love" and uid in users:
        hd = users[uid].get("hd", {})
        if hd:
            love_ctx = get_love_context(hd)
            if love_ctx:
                full_prompt += f"\n\nВОРОТА ЛЮБВИ (из Love Book Ra Uru Hu):\n{love_ctx}"

    try:
        await query.message.reply_text("Смотрю в карту...")
        reply = await ask_claude(uid, full_prompt)
        await safe_send(query.message, reply)
        # Помечаем тему только после успешного ответа. Иначе текущий блок
        # попадал в «уже разобрано» ещё до чтения и модель сама себя просила
        # его не повторять.
        db_add_block(uid, query.data)
        users[uid].setdefault("blocks_seen", [])
        if query.data not in users[uid]["blocks_seen"]:
            users[uid]["blocks_seen"].append(query.data)
    except Exception as e:
        import traceback
        print(f"ERROR in handle_button: {traceback.format_exc()}")
        await query.message.reply_text(
            "Упс... Посейдон разлил воду — что-то пошло не так. "
            "Боги уже разбираются. Попробуй нажать кнопку ещё раз или напиши /start."
        )
        return CHAT
    await query.message.reply_text("Выбери следующую тему:", reply_markup=MENU_KEYBOARD)
    users[uid]["menu_shown"] = True
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
        if birth.get("lat") is None or birth.get("lon") is None or birth.get("utc_offset") is None:
            coords = parse_city(birth.get("city", ""), birth)
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

    if TRIAL_ENFORCED and not user_text.lower() in {"/start", "/reset", "сначала", "заново"}:
        _, _, expired = trial_status(uid)
        if expired:
            await update.message.reply_text(trial_blocked_message(uid))
            return CHAT

    # Команды
    if user_text.lower() in ["/reset", "сначала", "заново"]:
        trial_start = users[uid].get("trial_start", datetime.now())
        users[uid] = {
            "history": [],
            "trial_start": trial_start,
            "brand_data": users[uid].get("brand_data", {}),
        }
        await update.message.reply_text("Начнём заново. Как тебя зовут?")
        return ASK_NAME

    if users[uid].get("brand_ai_chat"):
        try:
            reply = await ask_claude(uid, build_brand_chat_prompt(uid, user_text))
            await safe_send(update.message, reply)
            await update.message.reply_text("Продолжить работу с брендом?", reply_markup=BRAND_KEYBOARD)
        except Exception as exc:
            print(f"ERROR brand ai chat: {exc}")
            await update.message.reply_text("ИИ-редактор не смог ответить. Попробуй сформулировать задачу ещё раз.")
        return CHAT

    if users[uid].get("brand_flow", {}).get("stage") in {"name", "experience", "offer", "audience", "goal"}:
        handled = await handle_brand_text(update, uid)
        if handled:
            return CHAT

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
            coords = parse_city(user_text, compat.get("birth"))
            if not coords:
                await update.message.reply_text(f"Не нашёл «{user_text}». Попробуй по-другому.")
                return CHAT
            lat, lon, utc = coords
            compat["birth"].update({"lat": lat, "lon": lon, "utc_offset": utc, "city": user_text})
            users[uid]["compat"] = compat
            users[uid]["compat_flow"] = False

            await update.message.reply_text("Считаю карты. Боги знакомятся...")
            try:
                reply = await generate_compatibility_reply(uid, compat)
                await safe_send(update.message, reply)
                await update.message.reply_text("Выбери следующую тему:", reply_markup=MENU_KEYBOARD)
                users[uid]["menu_shown"] = True
            except Exception as e:
                print(f"ERROR compat: {e}")
                await update.message.reply_text("Упс... Посейдон разлил воду. Попробуй ещё раз.")
            return CHAT

    try:
        reply = await ask_claude(uid, user_text)
        await safe_send(update.message, reply)
    except Exception as exc:
        print(f"ERROR chat: {exc}")
        await update.message.reply_text("Аполлон потерял связь с архивом. Попробуй ещё раз через минуту.")
        return CHAT

    # Показываем меню только если это первый раз (menu_shown не установлен)
    if not users[uid].get("menu_shown"):
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
    coords = parse_city(city, users[uid].get("compat", {}).get("birth"))
    if not coords:
        await update.message.reply_text(f"Не нашёл «{city}». Попробуй написать по-другому.")
        return COMPAT_PLACE

    lat, lon, utc = coords
    b = users[uid]["compat"]["birth"]
    b["lat"], b["lon"], b["utc_offset"] = lat, lon, utc
    b["city"] = city

    await update.message.reply_text("Считаю карты. Боги знакомятся...")

    try:
        # Здесь используется тот же единый расчёт, что и в основном CHAT-флоу.
        # Раньше этот обработчик строил второй, более слабый промпт и мог давать
        # другой результат в зависимости от того, как пользователь вошёл в меню.
        reply = await generate_compatibility_reply(uid, users[uid]["compat"])
        await safe_send(update.message, reply)
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

    # ── Health-check раз в час ──
    async def run_health_check(context):
        try:
            import health_check as hc
            hc.BOT_TOKEN     = TELEGRAM_TOKEN
            hc.ALERT_CHAT_ID = os.environ.get("ALERT_CHAT_ID", "")
            hc.ANTHROPIC_KEY = ANTHROPIC_API_KEY
            if not hc.ALERT_CHAT_ID:
                return  # некуда слать — молчим
            results = []
            failed  = []
            for name, check_fn in hc.CHECKS:
                try:
                    ok, msg = await check_fn()
                except Exception as e:
                    ok, msg = False, str(e)
                results.append((name, ok, msg))
                if not ok:
                    failed.append((name, msg))
            if failed:
                lines = ["Обнаружены проблемы:\n"]
                for name, msg in failed:
                    lines.append(f"❌ *{name}*: `{msg[:200]}`")
                lines.append(f"\nПрошло: {len(results)-len(failed)}/{len(results)}")
                await hc.send_alert("\n".join(lines), is_ok=False)
        except Exception as e:
            print(f"Health-check error: {e}")

    job_queue = app.job_queue
    if job_queue:
        job_queue.run_repeating(run_health_check, interval=3600, first=30)
        # Стартовый пинг — сразу проверяем что алерты работают
        async def startup_ping(context):
            alert_chat = os.environ.get("ALERT_CHAT_ID", "")
            if alert_chat:
                import httpx
                url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
                try:
                    async with httpx.AsyncClient(timeout=10) as c:
                        await c.post(url, json={
                            "chat_id": alert_chat,
                            "text": "🤖 Аполлон запущен. Health-check активен — буду писать если что-то сломается."
                        })
                except Exception:
                    pass
        job_queue.run_once(startup_ping, when=5)
    else:
        print("⚠️ job_queue недоступен — установи python-telegram-bot[job-queue]")

    print("🤖 Бот запущен. Ctrl+C для остановки.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
