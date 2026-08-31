"""Deterministic pre-deploy audit for the public Telegram bot flow."""

import ast
import re
from pathlib import Path

import bot


ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_VISIBLE_TERMS = re.compile(
    r"(?:экзальтац|ущерб|\bпадени[ея]\b|дизайн\s+человека|g[-‑]центр|"
    r"монопол|ворот[аы]?\s+\d+|\b\d{1,2}[.]\d\b)",
    re.IGNORECASE,
)


def _sentences(text: str) -> set[str]:
    return {
        re.sub(r"\s+", " ", part).strip().casefold()
        for part in re.split(r"(?<=[.!?…])\s+", text)
        if len(part.strip()) >= 40
    }


def _literal_callbacks() -> set[str]:
    tree = ast.parse((ROOT / "bot.py").read_text(encoding="utf-8"))
    callbacks: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for keyword in node.keywords:
            if keyword.arg == "callback_data" and isinstance(keyword.value, ast.Constant):
                if isinstance(keyword.value.value, str):
                    callbacks.add(keyword.value.value)
    return callbacks


def _is_handled_callback(callback: str, source_text: str) -> bool:
    """Match both exact callback routes and dynamic callback families."""
    exact = re.compile(rf"query[.]data\s*==\s*['\"]{re.escape(callback)}['\"]")
    if exact.search(source_text):
        return True
    if callback.startswith("oracle_line:"):
        return bool(re.search(r"query[.]data[.]startswith\(['\"]oracle_line:", source_text))
    if callback.startswith("brand_answer:"):
        return bool(re.search(r"query[.]data[.]startswith\(['\"]brand_answer:", source_text))
    if callback.startswith("forecast_"):
        return bool(re.search(r"query[.]data[.]startswith\(['\"]forecast_", source_text))
    if callback.startswith("compat_type_"):
        return "query.data in (\"compat_type_business\", \"compat_type_personal\")" in source_text
    return False


def main() -> None:
    assert len(bot.ORACLE_CARD_PROFILES) == 64
    assert all(bot._card_image_path(gate) for gate in range(1, 65))

    card_lengths: list[int] = []
    line_lengths: list[int] = []
    for gate in range(1, 65):
        source = bot._card_source(gate)
        assert source.get("gate") == gate, f"gate {gate}: source unavailable"
        card = bot._oracle_card_message(source, "Что важно увидеть сейчас?")
        card_lengths.append(len(card))
        assert len(card) >= 800, f"gate {gate}: card is too short ({len(card)})"
        assert not FORBIDDEN_VISIBLE_TERMS.search(card), f"gate {gate}: technical term leaked"
        assert card.rstrip().endswith((".", "!", "?", "…", "взгляд."))

        for line in range(1, 7):
            line_text = bot._oracle_line_message(source, line, "Что важно увидеть сейчас?")
            line_lengths.append(len(line_text))
            assert len(line_text) >= 650, (
                f"gate {gate}, line {line}: text is too short ({len(line_text)})"
            )
            assert not FORBIDDEN_VISIBLE_TERMS.search(line_text), (
                f"gate {gate}, line {line}: technical term leaked"
            )
            duplicates = _sentences(card) & _sentences(line_text)
            assert not duplicates, f"gate {gate}, line {line}: repeated sentences {duplicates}"

    parsed = bot.parse_birth_payload("23.02.1981, 09:50, Суленцин, Польша")
    assert parsed is not None
    birth, place = parsed
    assert birth == {"day": 23, "month": 2, "year": 1981, "hour": 9, "minute": 50}
    assert place == "Суленцин, Польша"

    callbacks = _literal_callbacks()
    generic_blocks = set(bot.BLOCK_PROMPTS)
    source_text = (ROOT / "bot.py").read_text(encoding="utf-8")
    unhandled = {
        callback
        for callback in callbacks - generic_blocks
        if not _is_handled_callback(callback, source_text)
    }
    assert not unhandled, f"unhandled callback buttons: {sorted(unhandled)}"

    print(
        "OK: 64 cards, 384 lines, birth parser and "
        f"{len(callbacks)} literal callback routes; "
        f"card chars {min(card_lengths)}-{max(card_lengths)}, "
        f"line chars {min(line_lengths)}-{max(line_lengths)}"
    )


if __name__ == "__main__":
    main()
