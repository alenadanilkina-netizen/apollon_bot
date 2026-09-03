"""Startup guard must keep Telegram long polling in one Railway project."""

import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def run(project_id: str) -> str:
    env = os.environ.copy()
    env.update({
        "RAILWAY_PROJECT_ID": project_id,
        "TELEGRAM_TOKEN": "",
        "ANTHROPIC_API_KEY": "",
    })
    result = subprocess.run(
        [sys.executable, "bot.py"], cwd=ROOT, env=env,
        text=True, capture_output=True, check=True,
    )
    return result.stdout


def main() -> None:
    duplicate = run("31503751-1393-4e54-96e3-7d2a3e77d38b")
    assert "startup skipped" in duplicate

    active = run("9b74e8cc-9d3c-403e-8c31-92d944ad5e1a")
    assert "Нужен TELEGRAM_TOKEN" in active
    assert "startup skipped" not in active
    print("OK: only hearty-stillness can start Telegram polling")


if __name__ == "__main__":
    main()
