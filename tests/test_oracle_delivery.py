"""Regression test: card, full Oracle reading and line buttons arrive as one update."""

import asyncio

import bot


class FakeMessage:
    def __init__(self) -> None:
        self.photo_calls = 0
        self.photo_kwargs = []
        self.text_attempts = 0

    async def reply_photo(self, **kwargs):
        self.photo_calls += 1
        self.photo_kwargs.append(kwargs)

    async def reply_text(self, text, **kwargs):
        self.text_attempts += 1


async def main() -> None:
    uid = 987654321
    bot.users[uid] = {"oracle_question": "В каком направлении мои деньги?"}
    message = FakeMessage()

    await bot.send_oracle_card(message, uid)

    assert message.photo_calls == 1
    photo_kwargs = message.photo_kwargs[0]
    assert "caption" in photo_kwargs
    text = photo_kwargs["caption"]
    assert len(text) >= 800
    assert len(text) <= 1024
    assert photo_kwargs["parse_mode"] is None
    assert photo_kwargs["reply_markup"] is bot.ORACLE_LINE_KEYBOARD
    assert message.text_attempts == 0
    assert bot.users[uid].get("current_card_gate") in range(1, 65)
    print("OK: card caption contains the full interpretation and 1-6 buttons")


if __name__ == "__main__":
    asyncio.run(main())
