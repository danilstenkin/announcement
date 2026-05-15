"""
Скачивает одно письмо по UID и сохраняет в raw_email.eml
Запуск: python dump_email.py 2363
"""

import asyncio
import sys

from aioimaplib import aioimaplib

IMAP_HOST = "exsrv.fortebank.com"
IMAP_USER = "DAStenkin@Fortebank.com"
IMAP_PASSWORD = "300762Ast$$$"
IMAP_FOLDER = "INBOX"


async def main(uid: str):
    client = aioimaplib.IMAP4_SSL(host=IMAP_HOST, timeout=30)
    await client.wait_hello_from_server()
    result, _ = await client.login(IMAP_USER, IMAP_PASSWORD)
    assert result == "OK", f"Login failed"
    await client.select(IMAP_FOLDER)

    fetch = await client.uid("fetch", uid, "(BODY.PEEK[])")
    assert fetch.result == "OK" and len(fetch.lines) >= 2, f"Fetch failed: {fetch.lines}"

    raw = fetch.lines[1]
    with open("raw_email.eml", "wb") as f:
        f.write(raw)

    print(f"Saved raw_email.eml ({len(raw)} bytes)")
    await client.logout()


if __name__ == "__main__":
    uid = sys.argv[1] if len(sys.argv) > 1 else "2363"
    asyncio.run(main(uid))
