"""Regression test: a card image must always be followed by text and line buttons."""

import asyncio

import bot


class FakeMessage:
    def __init__(self) -> None:
        self.photo_calls = 0
        self.photo_kwargs = []
        self.text_attempts = 0
        self.delivered = []

    async def reply_photo(self, **kwargs):
        self.photo_calls += 1
        self.photo_kwargs.append(kwargs)

    async def reply_text(self, text, **kwargs):
        self.text_attempts += 1
        if self.text_attempts == 1:
            raise RuntimeError("temporary Telegram connection failure")
        self.delivered.append((text, kwargs))


async def main() -> None:
    uid = 987654321
    bot.users[uid] = {"oracle_question": "В каком направлении мои деньги?"}
    message = FakeMessage()

    await bot.send_oracle_card(message, uid)

    assert message.photo_calls == 1
    photo_kwargs = message.photo_kwargs[0]
    assert photo_kwargs["reply_markup"] is bot.ORACLE_LINE_KEYBOARD
    assert photo_kwargs["parse_mode"] is None
    assert "Оракул достал карту" in photo_kwargs["caption"]
    assert message.text_attempts == 2
    assert len(message.delivered) == 1
    text, kwargs = message.delivered[0]
    assert len(text) >= 800
    assert kwargs["parse_mode"] is None
    assert kwargs["reply_markup"] is None
    assert bot.users[uid].get("current_card_gate") in range(1, 65)
    print("OK: photo delivered, transient text failure retried, interpretation and 1-6 buttons delivered")


if __name__ == "__main__":
    asyncio.run(main())
