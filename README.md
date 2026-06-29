# Астро-HD Бот Алёны Данилкиной

## Запуск

### 1. Получи токены

**Telegram токен:**
- Открой @BotFather в Telegram
- /newbot → дай имя → получи токен

**Anthropic API ключ:**
- console.anthropic.com → API Keys → Create Key

### 2. Установи зависимости

```bash
pip3 install python-telegram-bot anthropic pyswisseph
```

### 3. Запусти

```bash
export TELEGRAM_TOKEN="твой_токен_от_botfather"
export ANTHROPIC_API_KEY="твой_ключ_anthropic"

cd ~/Documents/astro-mcp
python3 bot.py
```

### 4. Протестируй

Найди своего бота в Telegram → /start → введи дату рождения

---

## Файлы

- `bot.py` — основной бот
- `server.py` — MCP-сервер для расчёта карт (Swiss Ephemeris)
- `CLAUDE.md` — методология и концепция пантеона богов

---

## Чтобы бот работал 24/7

Запусти на сервере (VPS) или через Railway/Render:

```bash
# На сервере
nohup python3 bot.py &
```

Или через systemd-сервис.
