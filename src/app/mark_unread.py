"""
Скрипт для пометки писем как непрочитанных (убирает флаг \\Seen).
Помечает последние N писем от Service Desk и Komek.

Использование:
    python mark_unread.py          # последние 5 писем
    python mark_unread.py 10       # последние 10 писем
"""

import asyncio
import ssl
import sys

from aioimaplib import aioimaplib
from config import settings

TARGET_SENDERS = ["sd_info@Fortebank.com", "komek@Fortebank.com"]


async def _mark_from(client, sender: str, count: int) -> int:
    result, data = await client.search("FROM", sender)
    if result != "OK":
        print(f"  Search FROM {sender} failed: {data}")
        return 0

    msg_ids = data[0].split()
    if not msg_ids:
        print(f"  Нет писем от {sender}")
        return 0

    target = msg_ids[-count:]
    marked = 0
    for msg_id in target:
        mid = msg_id.decode() if isinstance(msg_id, bytes) else msg_id
        res, _ = await client.store(mid, "-FLAGS.SILENT (\\Seen)")
        status = "OK" if res == "OK" else "FAIL"
        print(f"  MSG {mid} [{sender}]: {status}")
        if res == "OK":
            marked += 1
    return marked


async def mark_unread(count: int = 5):
    ssl_context = ssl.create_default_context()
    ssl_context.check_hostname = False
    ssl_context.verify_mode = ssl.CERT_NONE

    client = aioimaplib.IMAP4_SSL(host=settings.IMAP_HOST, timeout=30, ssl_context=ssl_context)
    await client.wait_hello_from_server()
    await client.login(settings.IMAP_USER, settings.IMAP_PASSWORD)
    await client.select(settings.IMAP_FOLDER)

    total = 0
    for sender in TARGET_SENDERS:
        print(f"Ищу письма от {sender}...")
        total += await _mark_from(client, sender, count)

    print(f"Готово! Помечено {total} писем как непрочитанные.")
    await client.logout()


if __name__ == "__main__":
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    asyncio.run(mark_unread(count))
