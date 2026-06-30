"""
Health-check агент для бота Аполлон.
Запускается как отдельный процесс (cron / Railway cron job).
Проверяет все компоненты и шлёт отчёт в Telegram если что-то сломалось.

Переменные окружения:
  BOT_TOKEN          — токен бота (для отправки алертов)
  ALERT_CHAT_ID      — chat_id куда слать алерты (твой личный или специальный чат)
  ANTHROPIC_API_KEY  — ключ Claude
  MCP_SERVER_URL     — если MCP запущен отдельно (опционально)
"""

import asyncio
import os
import sys
import json
import time
import traceback
from pathlib import Path
from datetime import datetime

import httpx

BOT_TOKEN     = os.environ.get("BOT_TOKEN", "")
ALERT_CHAT_ID = os.environ.get("ALERT_CHAT_ID", "")  # твой Telegram ID
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

# ─── Отправка алерта ──────────────────────────────────────────────────────────

async def send_alert(text: str, is_ok: bool = False):
    """Шлёт сообщение в Telegram."""
    if not BOT_TOKEN or not ALERT_CHAT_ID:
        print(f"[ALERT] {text}")
        return
    emoji = "✅" if is_ok else "🚨"
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": ALERT_CHAT_ID,
        "text": f"{emoji} *Аполлон health-check* [{datetime.now().strftime('%H:%M %d.%m')}]\n\n{text}",
        "parse_mode": "Markdown",
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(url, json=payload)
    except Exception as e:
        print(f"Не удалось отправить алерт: {e}")


# ─── Проверки ─────────────────────────────────────────────────────────────────

async def check_imports() -> tuple[bool, str]:
    """Проверяет что все модули импортируются без ошибок."""
    try:
        import anthropic
        import pyswisseph
        from hd_library import (
            get_hd_context, get_cross_context,
            get_love_context, get_phs_context, get_profile_context
        )
        return True, "Все импорты OK"
    except Exception as e:
        return False, f"Ошибка импорта: {e}"


async def check_hd_library() -> tuple[bool, str]:
    """Проверяет что HD библиотека загружается и выдаёт данные."""
    try:
        sys.path.insert(0, str(Path(__file__).parent))
        from hd_library import (
            get_hd_context, get_profile_context,
            get_phs_context, get_love_context, get_cross_context,
            _build_gates_index, _build_phs_index
        )

        # Проверяем индексы
        gates = _build_gates_index()
        phs   = _build_phs_index()

        errors = []
        if len(gates) < 50:
            errors.append(f"Gates index мал: {len(gates)} ворот (ожидается 64+)")
        if not phs.get('env'):
            errors.append("PHS env индекс пустой")
        if not phs.get('det'):
            errors.append("PHS det индекс пустой")

        # Тест профиля
        test_hd = {"raw": "ПРОФИЛЬ: 1/3\nТИП: Проектор\nАВТОРИТЕТ: Эмоциональный"}
        profile_ctx = get_profile_context(test_hd)
        if not profile_ctx or "Следователь" not in profile_ctx:
            errors.append("get_profile_context не вернул данные для 1/3")

        if errors:
            return False, "HD библиотека: " + "; ".join(errors)

        return True, f"HD библиотека OK ({len(gates)} ворот, PHS: env={len(phs['env'])}/det={len(phs['det'])})"

    except Exception as e:
        return False, f"HD библиотека упала: {traceback.format_exc()[-300:]}"


async def check_mcp_server() -> tuple[bool, str]:
    """Проверяет что MCP сервер отвечает (если запущен локально на 8765)."""
    try:
        # Пробуем прямой вызов через server.py если он есть
        sys.path.insert(0, str(Path(__file__).parent))
        import server as mcp_server

        # Минимальная проверка — natal_chart с тестовыми данными
        args = {
            "birth_year": 1990, "birth_month": 6, "birth_day": 15,
            "birth_hour": 12, "birth_minute": 0,
            "birth_timezone": 3.0, "lat": 55.75, "lon": 37.61
        }
        result = await asyncio.wait_for(
            asyncio.to_thread(mcp_server.tool_natal_chart, args),
            timeout=15
        )
        if not result or "error" in str(result).lower()[:50]:
            return False, f"MCP natal_chart вернул ошибку: {str(result)[:200]}"

        return True, "MCP сервер OK (natal_chart отвечает)"

    except asyncio.TimeoutError:
        return False, "MCP сервер: timeout 15s"
    except Exception as e:
        return False, f"MCP сервер: {traceback.format_exc()[-300:]}"


async def check_human_design() -> tuple[bool, str]:
    """Проверяет tool_human_design."""
    try:
        sys.path.insert(0, str(Path(__file__).parent))
        import server as mcp_server

        args = {
            "birth_year": 1990, "birth_month": 6, "birth_day": 15,
            "birth_hour": 12, "birth_minute": 0,
            "birth_timezone": 3.0
        }
        result = await asyncio.wait_for(
            asyncio.to_thread(mcp_server.tool_human_design, args),
            timeout=15
        )
        raw = result.get("raw", "") if isinstance(result, dict) else str(result)
        checks = ["ТИП:", "АВТОРИТЕТ:", "ПРОФИЛЬ:", "КАНАЛЫ", "КРЕСТ ВОПЛОЩЕНИЯ"]
        missing = [c for c in checks if c not in raw]
        if missing:
            return False, f"HD карта неполная, нет: {missing}"

        return True, "HD карта OK (все секции на месте)"

    except asyncio.TimeoutError:
        return False, "HD карта: timeout 15s"
    except Exception as e:
        return False, f"HD карта: {traceback.format_exc()[-300:]}"


async def check_claude_api() -> tuple[bool, str]:
    """Проверяет что Claude API отвечает."""
    if not ANTHROPIC_KEY:
        return False, "ANTHROPIC_API_KEY не задан"
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)

        response = await asyncio.wait_for(
            asyncio.to_thread(
                client.messages.create,
                model="claude-sonnet-4-6",
                max_tokens=50,
                messages=[{"role": "user", "content": "Ответь одним словом: работаю"}]
            ),
            timeout=20
        )
        text = response.content[0].text if response.content else ""
        if not text:
            return False, "Claude API: пустой ответ"

        return True, f"Claude API OK (ответил: '{text[:30]}')"

    except asyncio.TimeoutError:
        return False, "Claude API: timeout 20s"
    except Exception as e:
        return False, f"Claude API: {e}"


async def check_bot_prompts() -> tuple[bool, str]:
    """Проверяет что BLOCK_PROMPTS заполнены и нет Python ошибок в bot.py."""
    try:
        # Импортируем без запуска polling
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "bot_module", Path(__file__).parent / "bot.py"
        )
        # Не запускаем main(), просто проверяем что файл парсится
        import ast
        bot_code = (Path(__file__).parent / "bot.py").read_text(encoding="utf-8")
        ast.parse(bot_code)

        # Проверяем BLOCK_PROMPTS через grep
        expected_blocks = [
            "block_identity", "block_mission", "block_love",
            "block_money", "block_health", "block_resources"
        ]
        missing = [b for b in expected_blocks if f'"{b}"' not in bot_code]
        if missing:
            return False, f"BLOCK_PROMPTS: нет блоков {missing}"

        return True, f"bot.py синтаксис OK, все {len(expected_blocks)} блоков на месте"

    except SyntaxError as e:
        return False, f"bot.py синтаксическая ошибка: {e}"
    except Exception as e:
        return False, f"bot.py: {e}"


async def check_db() -> tuple[bool, str]:
    """Проверяет что БД доступна для записи."""
    try:
        import sqlite3
        from pathlib import Path

        data_dir = Path(os.environ.get("DATA_DIR", "/data"))
        if data_dir.exists() and os.access(data_dir, os.W_OK):
            db_path = data_dir / "users.db"
        else:
            db_path = Path(__file__).parent / "users.db"

        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE IF NOT EXISTS _health_test (ts INTEGER)")
        conn.execute("INSERT INTO _health_test VALUES (?)", (int(time.time()),))
        conn.execute("DELETE FROM _health_test WHERE ts < ?", (int(time.time()) - 3600,))
        conn.commit()
        conn.close()

        return True, f"БД OK ({db_path})"

    except Exception as e:
        return False, f"БД: {e}"


# ─── Запуск всех проверок ────────────────────────────────────────────────────

CHECKS = [
    ("Импорты",        check_imports),
    ("HD библиотека",  check_hd_library),
    ("bot.py",         check_bot_prompts),
    ("БД (SQLite)",    check_db),
    ("MCP сервер",     check_mcp_server),
    ("HD карта",       check_human_design),
    ("Claude API",     check_claude_api),
]


async def run_all():
    print(f"\n{'='*50}")
    print(f"Аполлон health-check  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*50}\n")

    results = []
    failed  = []

    for name, check_fn in CHECKS:
        print(f"⏳ {name}...", end=" ", flush=True)
        t0 = time.time()
        try:
            ok, msg = await check_fn()
        except Exception as e:
            ok, msg = False, f"Неожиданная ошибка: {e}"
        elapsed = time.time() - t0

        status = "✅" if ok else "❌"
        print(f"{status}  ({elapsed:.1f}s)  {msg}")
        results.append((name, ok, msg, elapsed))
        if not ok:
            failed.append((name, msg))

    print(f"\n{'='*50}")
    total_ok = sum(1 for _, ok, _, _ in results if ok)
    print(f"Итого: {total_ok}/{len(results)} проверок прошли\n")

    # Отправляем алерт только если есть проблемы
    if failed:
        lines = ["Обнаружены проблемы:\n"]
        for name, msg in failed:
            lines.append(f"❌ *{name}*: `{msg[:200]}`")
        lines.append(f"\nПрошло: {total_ok}/{len(results)}")
        await send_alert("\n".join(lines), is_ok=False)
        sys.exit(1)
    else:
        # Раз в день шлём OK-отчёт (когда запускается в 09:00)
        if datetime.now().hour == 9:
            lines = ["Все системы работают:\n"]
            for name, ok, msg, elapsed in results:
                lines.append(f"✅ {name}: {msg}")
            await send_alert("\n".join(lines), is_ok=True)
        print("Все проверки прошли ✅")
        sys.exit(0)


if __name__ == "__main__":
    asyncio.run(run_all())
