"""
Скрипт для пометки писем как непрочитанных (убирает флаг \\Seen).
Помечает последние N писем в папке IMAP.

Использование:
    python mark_unread.py          # последние 5 писем
    python mark_unread.py 10       # последние 10 писем
"""

import asyncio
import ssl
import sys

from aioimaplib import aioimaplib
from config import settings


async def mark_unread(count: int = 5):
    ssl_context = ssl.create_default_context()
    ssl_context.check_hostname = False
    ssl_context.verify_mode = ssl.CERT_NONE

    client = aioimaplib.IMAP4_SSL(host=settings.IMAP_HOST, timeout=30, ssl_context=ssl_context)
    await client.wait_hello_from_server()
    await client.login(settings.IMAP_USER, settings.IMAP_PASSWORD)
    await client.select(settings.IMAP_FOLDER)

    # Получаем все UID
    result, data = await client.uid("search", "ALL")
    if result != "OK":
        print(f"Search failed: {data}")
        return

    uids = data[0].split()
    if not uids:
        print("Нет писем в папке")
        return

    # Берём последние N
    target_uids = uids[-count:]
    print(f"Помечаю {len(target_uids)} писем как непрочитанные...")

    for uid in target_uids:
        uid_str = uid.decode() if isinstance(uid, bytes) else uid
        res, _ = await client.uid("store", uid_str, "-FLAGS.SILENT (\\Seen)")
        status = "OK" if res == "OK" else "FAIL"
        print(f"  UID {uid_str}: {status}")

    print("Готово!")
    await client.logout()


if __name__ == "__main__":
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    asyncio.run(mark_unread(count))
