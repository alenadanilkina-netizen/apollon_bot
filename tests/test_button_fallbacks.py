"""Regression test: each public button gets its own reading or a relevant fallback."""

import asyncio
from types import SimpleNamespace

import bot


class FakeMessage:
    def __init__(self, text: str = "") -> None:
        self.replies = []
        self.text = text

    async def reply_text(self, text, **kwargs):
        self.replies.append((text, kwargs))


class FakeQuery:
    def __init__(self, uid: int, data: str) -> None:
        self.from_user = SimpleNamespace(id=uid)
        self.data = data
        self.message = FakeMessage()

    async def answer(self):
        return None


class ClosedStream:
    """Имитирует stdout/stderr Railway в момент остановки контейнера."""

    def write(self, _text):
        raise ValueError("I/O operation on closed file")

    def flush(self):
        raise ValueError("I/O operation on closed file")


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
    original_stderr = bot.sys.stderr
    captured = []
    prompts = []

    async def capture_send(_message, text, **kwargs):
        captured.append((text, kwargs))

    async def methodology_reply(_uid, prompt, include_history=True, context_scope="full"):
        prompts.append(prompt)
        if "Сделай ясный прогноз" in prompt:
            assert include_history is False
            assert context_scope == "forecast"
            return "\n\n".join([
                "Период собран из точных расчётных срезов: здесь видны даты, на которых держится прогноз, а не общая формула для всех.",
                "Первый отрезок показывает текущий ритм и задачу, которую стоит довести до ясной договорённости без лишней спешки.",
                "После следующей границы лунарного периода меняется фокус: полезно проверить, что из начатого действительно можно продолжать.",
                "В работе это видно по срокам и распределению сил; в отношениях — по тому, где разговор требует конкретного ответа; внутри — по возвращению к своему темпу.",
                "Не пытайся сделать из этого периода экзамен на идеальность. Достаточно заметить, в каком месте ты меняешь решение, потому что появились новые факты, а не потому что стало тревожно.",
                "Проверь этот вывод на ближайшем выборе и не выдавай символическую карту за обещание события. Боги советуют сначала сверить курс, а потом поднимать паруса.",
            ])
        assert include_history is False
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

    async def delayed_methodology_reply(_uid, prompt, include_history=True, context_scope="full"):
        delayed_started.set()
        await delayed_release.wait()
        return await methodology_reply(_uid, prompt, include_history, context_scope)

    try:
        # Каждая кнопка обязана использовать свою методологию, а не общий шаблон.
        bot.safe_send = capture_send
        # Реальный провайдер отвечает не мгновенно. Проверяем, что callback
        # остаётся ответственным за доставку до самого результата, повторное
        # нажатие не запускает второй расчёт, а итог доходит пользователю.
        delayed_started = asyncio.Event()
        delayed_release = asyncio.Event()
        bot.ask_claude = delayed_methodology_reply
        delayed_query = FakeQuery(uid, "block_identity")
        delayed_update = SimpleNamespace(callback_query=delayed_query)
        handler_task = asyncio.create_task(bot.handle_button(delayed_update, None))
        await asyncio.wait_for(delayed_started.wait(), timeout=1)
        assert not handler_task.done()
        assert (uid, "block_identity") in bot.pending_block_readings
        assert delayed_query.message.replies[0][0] == bot.block_loading_message("block_identity")

        duplicate_query = FakeQuery(uid, "block_identity")
        await bot.handle_button(SimpleNamespace(callback_query=duplicate_query), None)
        assert "уже рассчитывается" in duplicate_query.message.replies[-1][0]

        delayed_release.set()
        await asyncio.wait_for(handler_task, timeout=1)
        await asyncio.sleep(0)
        assert captured and len(captured.pop(0)[0]) > 900
        assert (uid, "block_identity") not in bot.pending_block_readings

        bot.ask_claude = methodology_reply
        prompts.clear()
        readings = {}
        for block in bot.BLOCK_PROMPTS:
            query = FakeQuery(uid, block)
            await bot.handle_button(SimpleNamespace(callback_query=query), None)
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

        # Сбой внешнего провайдера не имеет права оставить только заставку.
        async def failed_methodology_reply(_uid, _prompt, include_history=True, context_scope="full"):
            raise TimeoutError("provider unavailable")

        bot.ask_claude = failed_methodology_reply
        failed_query = FakeQuery(uid, "block_mission")
        await bot.handle_button(SimpleNamespace(callback_query=failed_query), None)
        assert failed_query.message.replies[0][0] == bot.block_loading_message("block_mission")
        assert "не завершился" in failed_query.message.replies[-1][0]
        failed_markup = failed_query.message.replies[-1][1]["reply_markup"]
        assert failed_markup.inline_keyboard[0][0].callback_data == "block_mission"
        assert (uid, "block_mission") not in bot.pending_block_readings
        bot.ask_claude = methodology_reply

        captured.clear()
        query = FakeQuery(uid, "forecast_month")
        await bot.handle_button(SimpleNamespace(callback_query=query), None)
        assert captured and len(captured[0][0]) > 500
        assert captured[0][1].get("parse_mode") is None
        assert query.message.replies[-1][1]["reply_markup"] is bot.FORECAST_KEYBOARD
        forecast_prompt = prompts[-1]
        assert "ТЕКУЩИЙ ЛУНАР" in forecast_prompt
        assert "СЛЕДУЮЩИЙ ЛУНАР" in forecast_prompt

        # Коучинг — отдельный режим, не является подменой расчёта блока и
        # всегда оставляет понятный путь назад в Олимп.
        query = FakeQuery(uid, "coach_start")
        await bot.handle_button(SimpleNamespace(callback_query=query), None)
        assert "Сегодня в этом зале" in query.message.replies[-1][0]
        assert query.message.replies[-1][1]["reply_markup"] is bot.COACH_KEYBOARD
        query = FakeQuery(uid, "coach_morning")
        await bot.handle_button(SimpleNamespace(callback_query=query), None)
        assert bot.users[uid]["coach_mode"] == "morning"
        prompt = bot.build_coach_prompt("morning", "Утром мне тревожно, а в плане три встречи.")
        assert "Один конкретный приоритет" in prompt
        assert "не живое сознание" in prompt
        coach_prompts = []

        async def coach_reply(_uid, prompt, include_history=True, context_scope="full"):
            coach_prompts.append((prompt, include_history))
            return "Сначала выбери одну встречу, которая действительно сдвигает дело.\n\nЧто станет легче, если перестать готовиться ко всем трём сразу?\n\nНе геройствуй: один ясный шаг уже меняет день."

        bot.ask_claude = coach_reply
        diary_message = FakeMessage("Я хочу всё успеть и уже устала.")
        await bot.chat(SimpleNamespace(effective_user=SimpleNamespace(id=uid), message=diary_message), None)
        assert coach_prompts and coach_prompts[-1][1] is True
        assert "Ты — Коуч Олимпа" in coach_prompts[-1][0]
        assert captured and "одну встречу" in captured.pop()[0]
        bot.ask_claude = methodology_reply
        voice = FakeMessage()
        await bot.coach_voice_unavailable(SimpleNamespace(effective_user=SimpleNamespace(id=uid), message=voice), None)
        assert "не умею надёжно его расшифровывать" in voice.replies[-1][0]
        query = FakeQuery(uid, "coach_exit")
        await bot.handle_button(SimpleNamespace(callback_query=query), None)
        assert "coach_mode" not in bot.users[uid]

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

        # Даже при закрытом системном потоке error handler не должен падать
        # поверх исходной ошибки и оставлять человека без ответа.
        bot.sys.stderr = ClosedStream()
        error_message = FakeMessage()
        await bot.telegram_error_handler(
            SimpleNamespace(effective_message=error_message),
            SimpleNamespace(error=RuntimeError("source callback failure")),
        )
        assert error_message.replies
        assert "не смогла закончить расчёт" in error_message.replies[0][0]
    finally:
        bot.safe_send = original_send
        bot.db_save_consent = original_save_consent
        bot.ask_claude = original_ask
        bot.sys.stderr = original_stderr
        bot.users.pop(uid, None)
        bot.users.pop(271828, None)

    print("OK: every public block uses its methodology, preserves paragraphs and strips birth identifiers")


if __name__ == "__main__":
    asyncio.run(main())
