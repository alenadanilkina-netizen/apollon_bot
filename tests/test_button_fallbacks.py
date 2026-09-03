"""Regression test: public buttons always answer when an external layer fails."""

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
        "name": "Алёна",
        "chart": {"raw": "calculated"},
        "birth": {"year": 1981, "month": 2, "day": 23, "hour": 9, "minute": 50, "utc_offset": 1, "lat": 52.4, "lon": 15.1},
        "hd": {"raw": "ТИП: Проектор\nАВТОРИТЕТ: Эмоциональный"},
    }
    original_ask = bot.ask_claude
    original_send = bot.safe_send
    original_transits = bot.collect_transit_snapshots
    original_save_consent = bot.db_save_consent
    captured = []

    async def failing_ask(*_args, **_kwargs):
        raise RuntimeError("provider unavailable")

    async def failing_transits(*_args, **_kwargs):
        raise RuntimeError("ephemeris unavailable")

    async def capture_send(_message, text, **kwargs):
        captured.append((text, kwargs))

    try:
        bot.ask_claude = failing_ask
        bot.safe_send = capture_send
        query = FakeQuery(uid, "block_identity")
        await bot.handle_button(SimpleNamespace(callback_query=query), None)
        assert captured and "базовый разбор" in captured[-1][0]
        assert captured[-1][1]["parse_mode"] is None

        captured.clear()
        bot.collect_transit_snapshots = failing_transits
        query = FakeQuery(uid, "forecast_month")
        await bot.handle_button(SimpleNamespace(callback_query=query), None)
        assert captured and "базовую навигацию" in captured[-1][0]
        assert captured[-1][1]["reply_markup"] is bot.FORECAST_KEYBOARD

        # Пока два документа не открыты, кнопки согласия в интерфейсе нет.
        bot.users[uid] = {"history": []}
        keyboard = bot.personal_data_documents_keyboard(uid)
        assert all(button.callback_data != "personal_consent_yes" for row in keyboard.inline_keyboard for button in row)
        bot.db_save_consent = lambda *_args: None
        query = FakeQuery(uid, "privacy_policy")
        await bot.handle_consent(SimpleNamespace(callback_query=query), None)
        query = FakeQuery(uid, "personal_data_consent")
        await bot.handle_consent(SimpleNamespace(callback_query=query), None)
        keyboard = bot.personal_data_documents_keyboard(uid)
        assert any(button.callback_data == "personal_consent_yes" for row in keyboard.inline_keyboard for button in row)
        query = FakeQuery(uid, "personal_consent_yes")
        result = await bot.handle_consent(SimpleNamespace(callback_query=query), None)
        assert result == bot.ASK_BIRTH
        assert bot.users[uid]["consent"] is True
    finally:
        bot.ask_claude = original_ask
        bot.safe_send = original_send
        bot.collect_transit_snapshots = original_transits
        bot.db_save_consent = original_save_consent
        bot.users.pop(uid, None)

    print("OK: personal blocks and forecasts return a readable fallback after external failure")


if __name__ == "__main__":
    asyncio.run(main())
