"""One-time Telegram setup, run by the owner on their machine.

Reads TELEGRAM_BOT_TOKEN from .env, finds the chat of whoever pressed Start in the
bot, saves TELEGRAM_CHAT_ID to .env, sends a test message and stores both values
as GitHub Actions secrets of this repository.

    .venv/bin/python -m backend.telegram_setup
"""
import re
import subprocess
import sys

import httpx

from . import config


def main() -> int:
    token = config.TELEGRAM_BOT_TOKEN
    if not token:
        print("В .env нет TELEGRAM_BOT_TOKEN — вставь токен от @BotFather и запусти снова.")
        return 1
    api = f"https://api.telegram.org/bot{token}"
    me = httpx.get(f"{api}/getMe", timeout=20).json()
    if not me.get("ok"):
        print("Telegram не принял токен бота. Проверь, что он скопирован целиком.")
        return 1
    bot = me["result"]["username"]

    updates = httpx.get(f"{api}/getUpdates", timeout=20).json().get("result", [])
    chats = [u["message"]["chat"] for u in updates if u.get("message", {}).get("chat", {}).get("type") == "private"]
    if not chats:
        print(f"Открой https://t.me/{bot}, нажми «Start» и запусти эту команду ещё раз.")
        return 1
    chat_id = str(chats[-1]["id"])

    env = config.ROOT / ".env"
    text = env.read_text(encoding="utf-8")
    if re.search(r"^TELEGRAM_CHAT_ID=.*$", text, flags=re.M):
        text = re.sub(r"^TELEGRAM_CHAT_ID=.*$", f"TELEGRAM_CHAT_ID={chat_id}", text, flags=re.M)
    else:
        text = text.rstrip("\n") + f"\nTELEGRAM_CHAT_ID={chat_id}\n"
    env.write_text(text, encoding="utf-8")

    sent = httpx.post(f"{api}/sendMessage", timeout=20, json={
        "chat_id": chat_id,
        "text": "✅ zipline подключён. Сюда будут приходить новые сообщения разработчиков Deadlock.",
    }).json()
    if not sent.get("ok"):
        print("Не удалось отправить тестовое сообщение:", sent.get("description"))
        return 1

    for name, value in (("TELEGRAM_BOT_TOKEN", token), ("TELEGRAM_CHAT_ID", chat_id)):
        result = subprocess.run(["gh", "secret", "set", name], input=value, text=True,
                                cwd=config.ROOT, capture_output=True)
        if result.returncode != 0:
            print(f"Не удалось сохранить {name} в GitHub:", result.stderr.strip())
            return 1
    print(f"Готово: бот @{bot} подключён, тестовое сообщение отправлено, секреты сохранены в GitHub.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
