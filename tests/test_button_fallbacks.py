"""Regression test: each public button gets its own reading or a relevant fallback."""

import asyncio
from types import SimpleNamespace

import bot


class FakeMessage:
    def __init__(self) -> None:
        self.replies = []

    async def reply_text(self, text, **kwargs):
        self.replies.append((text, kwargs))


class FakeQuery:
    def __init__(self, uid: int, data: str) -> None:
        self.from_user = SimpleNamespace(id=uid)
        self.data = data
        self.message = FakeMessage()

    async def answer(self):
        return None


async def main() -> None:
    uid = 314159
    bot.users[uid] = {
        "chart": {"raw": "calculated"},
        "birth": {"year": 1981, "month": 2, "day": 23, "hour": 9, "minute": 50, "utc_offset": 1, "lat": 52.4, "lon": 15.1},
        "hd": {"raw": "ТИП: Проектор\nАВТОРИТЕТ: Эмоциональный\nПРОФИЛЬ: 5/1"},
        "persona_gender": "f",
    }
    original_send = bot.safe_send
    original_save_consent = bot.db_save_consent
    captured = []

    async def capture_send(_message, text, **kwargs):
        captured.append((text, kwargs))

    try:
        # Каждая кнопка отдаёт завершённый расчёт без внешнего ИИ.
        bot.safe_send = capture_send
        readings = {}
        for block in bot.BLOCK_PROMPTS:
            query = FakeQuery(uid, block)
            await bot.handle_button(SimpleNamespace(callback_query=query), None)
            await asyncio.sleep(0)
            assert query.message.replies[0][0] == bot.block_loading_message(block)
            assert captured, block
            text, kwargs = captured.pop(0)
            readings[block] = text
            assert len(text) > 900, block
            assert "Алёна" not in text
            assert kwargs["parse_mode"] is None
        assert len(set(readings.values())) == len(readings)

        captured.clear()
        query = FakeQuery(uid, "forecast_month")
        await bot.handle_button(SimpleNamespace(callback_query=query), None)
        assert captured and "ближайший месяц" in captured[0][0]
        assert captured[0][1]["parse_mode"] is None
        assert query.message.replies[-1][1]["reply_markup"] is bot.FORECAST_KEYBOARD

        # Политика одна: сначала открыть, затем отдельно принять.
        bot.users[uid] = {"history": []}
        keyboard = bot.personal_data_documents_keyboard(uid)
        assert any(button.callback_data == "privacy_accept" for row in keyboard.inline_keyboard for button in row)
        bot.db_save_consent = lambda *_args: None
        query = FakeQuery(uid, "privacy_policy")
        await bot.handle_consent(SimpleNamespace(callback_query=query), None)
        query = FakeQuery(uid, "privacy_accept")
        result = await bot.handle_consent(SimpleNamespace(callback_query=query), None)
        assert result == bot.ASK_NAME
        assert bot.users[uid]["consent"] is True

        # Вторая сессия не получает имя, дату или обращение первой.
        other_uid = 271828
        bot.users[other_uid] = {
            "name": "Алёна",
            "birth": {"year": 1981, "month": 2, "day": 23},
            "chart": {"raw": "calculated"},
            "hd": {"raw": "ТИП: Генератор\nАВТОРИТЕТ: Сакральный"},
            "persona_gender": "m",
        }
        isolated_reading = bot.ready_block_reading(uid, "block_identity")
        assert "Алёна" not in isolated_reading
        assert "1981" not in isolated_reading
        assert bot.olympian_alias(uid) != bot.olympian_alias(other_uid)
    finally:
        bot.safe_send = original_send
        bot.db_save_consent = original_save_consent
        bot.users.pop(uid, None)
        bot.users.pop(271828, None)

    print("OK: every public block and forecast return without external AI or leaked names")


if __name__ == "__main__":
    asyncio.run(main())
