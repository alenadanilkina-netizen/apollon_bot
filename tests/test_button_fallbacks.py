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
    original_ask = bot.ask_claude
    captured = []
    prompts = []

    async def capture_send(_message, text, **kwargs):
        captured.append((text, kwargs))

    async def methodology_reply(_uid, prompt, include_history=True):
        assert include_history is False
        prompts.append(prompt)
        label = next(
            line.removeprefix("ЛИНЗА БЛОКА: ").strip(".")
            for line in prompt.splitlines()
            if line.startswith("ЛИНЗА БЛОКА:")
        )
        return "\n\n".join([
            f"{label}: первый расчётный вывод переведён в наблюдаемую жизненную ситуацию без учебного жаргона. Здесь достаточно конкретики, чтобы проверить мысль на практике, а не принять её за красивую формулу.",
            "Второй абзац связывает факты карты с отдельной темой этого зала. Он не повторяет общий портрет и не переносит чужой сценарий в этот разбор.",
            "Третий абзац показывает напряжение: где сильная сторона может стать перегрузкой, поспешным решением или неясной договорённостью. Это не диагноз и не приговор.",
            "Четвёртый абзац возвращает разговор к действию, которое можно наблюдать в ближайшей реальной ситуации: в проекте, разговоре или выборе условий.",
            "Пятый абзац завершает именно выбранную тему и сохраняет расстояние между расчётным фактом и его символической интерпретацией. Поэтому текст не превращается в ярлык или обещание события.",
            "Боги предлагают не торопиться с выводом: сначала посмотри, как эта мысль выдержит обычную жизнь, а не только красивую беседу на Олимпе.",
        ])

    try:
        # Каждая кнопка обязана использовать свою методологию, а не общий шаблон.
        bot.safe_send = capture_send
        bot.ask_claude = methodology_reply
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
        assert len(prompts) == len(bot.BLOCK_PROMPTS)
        assert len(set(prompts)) == len(bot.BLOCK_PROMPTS)
        assert all("ЛИНЗА БЛОКА:" in prompt for prompt in prompts)
        try:
            bot.ready_block_reading(uid, "block_identity")
        except RuntimeError as exc:
            assert "forbidden" in str(exc)
        else:
            raise AssertionError("generic personal reading path must remain forbidden")

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
        cleaned = bot._anonymized_calculation(
            "Дата: 23.02.1981 09:50 UTC+1\nКоординаты: 52.4443°N 15.1168°E\n"
            "АСЦ: Рыбы 5°20'\nСолнце: Рыбы 3°11'"
        )
        assert "1981" not in cleaned
        assert "09:50" not in cleaned
        assert "52.4443" not in cleaned
        assert "АСЦ" in cleaned
        assert bot.olympian_alias(uid) != bot.olympian_alias(other_uid)
    finally:
        bot.safe_send = original_send
        bot.db_save_consent = original_save_consent
        bot.ask_claude = original_ask
        bot.users.pop(uid, None)
        bot.users.pop(271828, None)

    print("OK: every public block uses its methodology, preserves paragraphs and strips birth identifiers")


if __name__ == "__main__":
    asyncio.run(main())
