"""
IMAP IDLE watcher — слушает почтовый ящик и обрабатывает новые письма.
Запускается как фоновая asyncio-задача внутри FastAPI lifespan.
"""

import asyncio
import base64
import hashlib
import re
from contextlib import suppress
from datetime import datetime
from email import policy
from email.parser import BytesParser
from email.utils import parsedate_to_datetime
from html import unescape

from pipeline.source import resolve_source

from config import settings
from dependencies.minio import get_minio_client
from logger import get_logger

from pipeline import run_pipeline
from pipeline.context import PipelineContext

logger = get_logger(__name__)

# Минимальный размер inline-картинки (вставленной прямо в тело письма),
# чтобы сохранить её и отправить в ИИ. Мелкие inline-картинки — это почти
# всегда логотипы/иконки из подписи, их пропускаем.
INLINE_IMAGE_MIN_BYTES = 8 * 1024

# SHA-256 логотипов/баннеров из корпоративных подписей. Они приходят почти в
# каждом письме (часто как image001.png) и не нужны ни в анализе, ни во
# вложениях анонса — отбрасываем при приёме. Чтобы добавить новый логотип:
#   python3 -c "import hashlib,sys;print(hashlib.sha256(open(sys.argv[1],'rb').read()).hexdigest())" logo.png
SIGNATURE_IMAGE_HASHES: set[str] = {
    # Логотип Forte (image001.png). Хеш получен из вставленной в чат копии —
    # если в реальных письмах байты отличаются, добавь хеш файла из MinIO.
    "97db35bf275bb6ec317823cce12f7bbfbdbc51ecdd06e2b75d911a50cb9041ff",
    # Логотип-баннер подписи (PNG 97×29, ~64.5 КБ). Хеш снят с реального письма
    # (20260604_123357) — inline-часть с Content-ID, те же байты, что в data:URI.
    "964da5efd811ff810d8b7290bb5a2ee486d308d8ad75dc43f8d8387a909441be",
}


# ── Маппинг email → источник ────────────────────────────
# Ключ — подстрока или домен адреса отправителя (lowercase).
# Порядок проверки: первое совпадение побеждает.



# ── helpers ──────────────────────────────────────────────



def _extract_links(html: str) -> list[dict]:
    """
    Извлекает все ссылки из HTML до удаления тегов.

    Пример результата:
      [
        {"text": "http://fortall.fortebank.com", "url": "http://fortall.fortebank.com/"},
        {"text": "Процедура кредитного администрирования...", "url": "https://fortall.fortebank.com/VND2/..."},
      ]
    """
    links = []
    seen_urls = set()                                              # дедупликация по URL
    for match in re.finditer(r'(?is)<a\s[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', html):
        url = match.group(1).strip()
        text = re.sub(r"(?s)<.*?>", "", match.group(2)).strip()    # убираем вложенные теги из текста ссылки
        if url not in seen_urls:
            seen_urls.add(url)
            links.append({"text": text, "url": url})
    return links


def _html_to_text(html: str) -> str:
    """
    Грубая конвертация HTML → plain text через регулярки.

    Пример:
      Вход:  "<p>Привет, <b>коллеги</b>!</p><script>alert(1)</script>
Новая тема."
      Выход: "Привет, коллеги!\n\nНовая тема."
    """
    html = re.sub(r"(?is)<(script|style).*?>.*?</\1>", "", html)  # удаляем <script> и <style> целиком
    html = re.sub(r"(?is)<o:p>\s*&nbsp;\s*</o:p>", "", html)     # Outlook пустые параграфы <o:p>&nbsp;</o:p> → ничего
    html = re.sub(r"(?is)<o:p>.*?</o:p>", "", html)              # остальные Outlook-теги <o:p></o:p> → ничего
    html = html.replace("&nbsp;", " ")                             # &nbsp; → обычный пробел (до unescape)
    html = re.sub(                                                 # <a href="URL">текст</a> → просто текст
        r'(?is)<a\s[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
        r"\2",
        html,
    )
    html = re.sub(r"(?i)<br\s*/?>", "\n", html)
    html = re.sub(r"(?i)</p\s*>", "\n\n", html)
    html = re.sub(r"(?i)</tr\s*>", "\n", html)
    html = re.sub(r"(?i)</td\s*>", " | ", html)
    html = re.sub(r"(?s)<.*?>", "", html)
    text = unescape(html)                                          # декодируем &amp; &lt; и т.д.
    text = text.replace("\xa0", " ")                               # неразрывный пробел → обычный (на всякий случай)
    text = re.sub(r"[^\S\n]+", " ", text)                         # схлопываем пробелы/табы в один (но не \n)
    text = re.sub(r"\n\s*\n", "\n\n", text)                       # строки из одних пробелов → пустая строка
    text = re.sub(r"\n{3,}", "\n\n", text)                        # 3+ пустых строк → максимум одна пустая
    text = text.replace("|", "")
    text = text.replace("/n", "")
    text ="\n".join(line for line in text.splitlines()if line.strip())
    return text.strip()                                            # убираем пробелы по краям



def _extract_text(message) -> tuple[str, list[dict]]:
    """
    Извлекает текст и ссылки из MIME-письма. Предпочитает plain text, fallback на HTML.

    Письмо может содержать несколько частей (multipart/mixed):
      - text/plain  → берём как есть (приоритет)
      - text/html   → конвертируем в текст через _html_to_text, ссылки извлекаем отдельно
      - attachment   → пропускаем (это файлы, не текст)

    Возвращает:
      (body, links) — чистый текст для GPT + список ссылок из письма
    """
    plain_parts, html_parts = [], []                               # собираем текстовые части отдельно
    links = []                                                     # ссылки из HTML
    for part in message.walk():                                    # рекурсивно обходим все MIME-части
        if part.is_multipart() or part.get_content_disposition() == "attachment":
            continue                                               # пропускаем контейнеры и вложения
        ct = part.get_content_type()                               # тип содержимого (text/plain, text/html, ...)
        try:
            content = part.get_content()                           # пытаемся получить декодированный контент
        except Exception:
            payload = part.get_payload(decode=True)                # fallback: сырые байты
            if not payload:
                continue
            charset = part.get_content_charset() or "utf-8"        # кодировка из заголовка или UTF-8
            content = payload.decode(charset, errors="replace")    # декодируем, заменяя битые символы
        content = content.strip()
        if not content:
            continue
        if ct == "text/plain":
            plain_parts.append(content)                            # чистый текст — приоритет
        elif ct == "text/html":
            links.extend(_extract_links(content))                  # ссылки извлекаем до очистки HTML
            html_parts.append(_html_to_text(content))              # HTML → текст через регулярки
    text = "\n\n".join(plain_parts) or "\n\n".join(html_parts) or ""  # plain > html > пусто
    text = text.replace("\xa0", " ")                               # неразрывный пробел → обычный
    text = text.replace("|", "")
    text = text.replace("/n", "")
    text = re.sub(r"[^\S\n]+", " ", text)                         # схлопываем пробелы/табы в один (но не \n)
    text = re.sub(r"\n{3,}", "\n\n", text)                        # 3+ пустых строк → максимум одна пустая
    text ="\n".join(line for line in text.splitlines()if line.strip())
    return text.strip(), links


def _clean_body(text: str) -> str:
    """
    Убирает из тела письма заголовки пересылки и дисклеймеры.
    Удаляет только блок заголовков в начале текста (до первого пустого разделителя).
    """
    # Убираем блок заголовков пересылки в начале (From/To/Sent/Subject/Cc подряд)
    text = re.sub(
        r"\A(?:(?:From|To|Sent|Subject|Cc|Кому|От|Отправлено|Тема|Копия):\s*.*\n?)+",
        "",
        text,
        flags=re.IGNORECASE,
    )
    # Убираем дисклеймер о конфиденциальности (обычно в конце)
    text = re.sub(
        r"(?is)(данное сообщение|this message|настоящее электронное сообщение).*?(третьими лицами|third parties)\.?",
        "",
        text,
    )
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _safe_filename(filename: str) -> str:
    """
    Очищает имя файла от опасных символов.

    Пример:
      Вход:  "../../etc/passwd"      → ".._.._etc_passwd"
      Вход:  "отчёт <final>.xlsx"   → "отчёт _final_.xlsx"
      Вход:  "report (2).pdf"        → "report (2).pdf"  (без изменений)
    """
    filename = filename.strip().replace("/", "_").replace("\\", "_")   # слеши → подчёркивания
    filename = re.sub(r"[^a-zA-Z0-9а-яА-ЯёЁ._ -]+", "_", filename)  # оставляем только безопасные символы
    return filename or "file"                                          # если пусто — дефолтное имя



def _embed_inline_images(html: str, message) -> str:
    """
    Заменяет cid:ссылки в HTML на data:base64, чтобы картинки
    отображались при открытии HTML вне email-клиента.
    """
    # Собираем маппинг Content-ID → (content_type, bytes)
    cid_map: dict[str, tuple[str, bytes]] = {}
    for part in message.walk():
        if part.is_multipart():
            continue
        content_id = part.get("Content-ID")
        if not content_id:
            continue
        ct = part.get_content_type()
        if not ct.startswith("image/"):
            continue
        payload = part.get_payload(decode=True)
        if not payload:
            continue
        cid = content_id.strip("<>")
        cid_map[cid] = (ct, payload)

    if not cid_map:
        return html

    def _replace_cid(match: re.Match) -> str:
        cid = match.group(1)
        if cid in cid_map:
            ct, data = cid_map[cid]
            b64 = base64.b64encode(data).decode("ascii")
            return f'src="data:{ct};base64,{b64}"'
        return match.group(0)

    return re.sub(r'src=["\']cid:([^"\']+)["\']', _replace_cid, html, flags=re.IGNORECASE)


def _extract_attachments(message, uid: str) -> list[dict]:
    """
    Извлекает вложения из письма (байты хранятся в памяти для загрузки в MinIO).

    Обрабатывает два типа вложений:
      - attachment  → явный файл (report.pdf, данные.xlsx)
      - inline      → встроенная картинка в HTML-теле (logo.png в подписи)

    Пример результата:
      [
        {"filename": "report.pdf", "data": b"...",
         "content_type": "application/pdf", "size": 102400,
         "inline": False, "content_id": None},
      ]
    """
    files = []
    for idx, part in enumerate(message.walk(), start=1):           # обходим все MIME-части
        if part.is_multipart():
            continue                                               # контейнер — пропускаем
        ct = part.get_content_type()                               # image/png, application/pdf, ...
        disposition = part.get_content_disposition()                # attachment / inline / None
        filename = part.get_filename()                             # имя файла из заголовка
        content_id = part.get("Content-ID")
        logger.info(
            "MIME part #{idx}: type={ct}, disposition={disp}, filename={fn}, content_id={cid}, size={sz}",
            idx=idx, ct=ct, disp=disposition, fn=filename,
            cid=content_id, sz=len(part.get_payload(decode=True) or b""),
        )
        is_attachment = disposition == "attachment"                 # явное вложение
        is_inline_image = disposition == "inline" and ct.startswith("image/")  # встроенная картинка
        if not is_attachment and not is_inline_image and not filename:
            continue                                               # не вложение — пропускаем
        payload = part.get_payload(decode=True)                    # сырые байты файла (base64 → bytes)
        if not payload:
            continue
        payload_sha256 = hashlib.sha256(payload).hexdigest()       # хеш реальных байтов вложения
        # Для картинок печатаем sha256 с заметным маркером: отправь письмо с нужным
        # логотипом, найди строку `SIGNATURE-IMAGE-HASH` в логах и добавь хеш в
        # SIGNATURE_IMAGE_HASHES выше — тогда логотип будет отбрасываться при приёме.
        if ct.startswith("image/"):
            logger.info(
                "SIGNATURE-IMAGE-HASH sha256={h} filename={fn} size={sz} inline={inl} uid={uid}",
                h=payload_sha256, fn=filename, sz=len(payload),
                inl=(disposition == "inline"), uid=uid,
            )
        # логотипы/баннеры из подписи (одинаковые байты в каждом письме) — отбрасываем
        if payload_sha256 in SIGNATURE_IMAGE_HASHES:
            logger.info("Skipping signature image {fn}", fn=filename)
            continue
        # inline-картинки берём только достаточно крупные: мелкие — логотипы из подписи
        if is_inline_image and len(payload) < INLINE_IMAGE_MIN_BYTES:
            continue
        if filename:
            filename = _safe_filename(filename)                    # очищаем имя файла
        else:
            ext = ct.split("/", 1)[-1] or "bin"                   # расширение из content-type
            filename = f"inline-{idx}.{ext}"                       # генерируем имя: inline-3.png
        files.append({
            "filename": filename,                                  # имя файла
            "data": payload,                                       # сырые байты (для MinIO)
            "content_type": ct,                                    # MIME-тип
            "size": len(payload),                                  # размер в байтах
            "inline": disposition == "inline",                     # inline или attachment
            "content_id": content_id.strip("<>") if content_id else None,  # CID без угловых скобок
        })
    return files


# ── IMAP connection ─────────────────────────────────────


async def _connect():
    """
    Подключается к IMAP серверу, логинится, выбирает папку.

    Последовательность:
      1. TCP+SSL соединение с exsrv.fortebank.com:993
      2. Ждём "* OK" приветствие от сервера
      3. LOGIN user password
      4. SELECT INBOX  → теперь можем искать и читать письма
    """
    import ssl as _ssl
    from aioimaplib import aioimaplib
    ssl_context = _ssl.create_default_context()
    ssl_context.check_hostname = False
    ssl_context.verify_mode = _ssl.CERT_NONE
    client = aioimaplib.IMAP4_SSL(host=settings.IMAP_HOST, timeout=30, ssl_context=ssl_context)
    await client.wait_hello_from_server()                          # ждём приветствие сервера
    result, data = await client.login(settings.IMAP_USER, settings.IMAP_PASSWORD)  # авторизация
    if result != "OK":
        raise RuntimeError(f"IMAP login failed: {data}")
    result, data = await client.select(settings.IMAP_FOLDER)      # выбираем папку (INBOX)
    if result != "OK":
        raise RuntimeError(f"Cannot select folder {settings.IMAP_FOLDER!r}: {data}")
    logger.info("IMAP connected, folder={folder}", folder=settings.IMAP_FOLDER)
    return client


def _parse_sender_email(from_header: str) -> str:
    """
    Извлекает email-адрес из заголовка From.

    Пример:
      "Иванов Пётр <ivanov@fortebank.com>"  → "ivanov@fortebank.com"
      "ivanov@fortebank.com"                → "ivanov@fortebank.com"
    """
    match = re.search(r"<([^>]+)>", from_header)
    return match.group(1).strip() if match else from_header.strip()


def _parse_received_at(message) -> datetime:
    """Парсит дату получения из заголовка Date. Fallback — текущее время."""
    date_str = message.get("Date")
    if date_str:
        try:
            return parsedate_to_datetime(date_str)
        except Exception:
            pass
    return datetime.now()


async def _process_unseen(client):
    """
    Находит все непрочитанные письма и отправляет каждое в пайплайн.

    Воркер занимается ТОЛЬКО:
      1. Забрать письмо из IMAP
      2. Распарсить в ParsedEmail
      3. Определить source по sender_email
      4. Передать в run_pipeline()
      5. Пометить прочитанным после успешной обработки

    Вся бизнес-логика (БД, GPT, маршрутизация) — в pipeline/steps/.
    """
    response = await client.uid_search("UNSEEN", charset=None)
    if response.result != "OK":
        raise RuntimeError(f"UNSEEN search failed: {response.lines}")
    if not response.lines or not response.lines[0]:
        return
    for uid_bytes in response.lines[0].split():
        uid = uid_bytes.decode("ascii")
        try:
            fetch = await client.uid("fetch", uid, "(BODY.PEEK[])")
            if fetch.result != "OK" or len(fetch.lines) < 2:
                raise RuntimeError(f"Fetch failed uid={uid}: {fetch.lines}")
            message = BytesParser(policy=policy.default).parsebytes(fetch.lines[1])

            subject = message.get("Subject", "")
            from_header = message.get("From", "")
            sender_email = _parse_sender_email(from_header)
            message_id = message.get("Message-ID", f"uid-{uid}")
            received_at = _parse_received_at(message)

            # Определяем источник по отправителю (общий хелпер с отчётностью)
            source = resolve_source(sender_email)

            body, links = _extract_text(message)
            body = _clean_body(body)
            raw_attachments = _extract_attachments(message, uid)

            # Извлекаем оригинальный HTML для сохранения в MinIO
            raw_html = None
            for part in message.walk():
                if part.get_content_type() == "text/html" and part.get_content_disposition() != "attachment":
                    payload = part.get_payload(decode=True)
                    if payload:
                        try:
                            raw_html = payload.decode("utf-8")
                        except UnicodeDecodeError:
                            charset = part.get_content_charset() or "windows-1251"
                            raw_html = payload.decode(charset, errors="replace")
                        # Заменяем charset на utf-8, т.к. сохраняем в UTF-8
                        raw_html = re.sub(
                            r'charset=["\']?[^"\'\s;>]+["\']?',
                            'charset="utf-8"',
                            raw_html,
                            flags=re.IGNORECASE,
                        )
                    break

            # Парсим текст из вложений (pptx и др.) пока data в памяти
            from pipeline.steps.parse_attachments import extract_attachments_text
            attachments_text = extract_attachments_text(raw_attachments) if raw_attachments else ""

            # Загружаем вложения в MinIO (I/O — до пайплайна)
            attachments = []
            if raw_attachments:
                minio = get_minio_client()
                for att in raw_attachments:
                    object_key = await minio.upload_file(
                        file_content=att["data"],
                        filename=att["filename"],
                        announcement_id=f"emails/{uid}",    # emails/{uid}/{timestamp}_{filename}
                    )
                    attachments.append({
                        "filename": att["filename"],
                        "object_key": object_key,           # ключ в MinIO (не байты)
                        "content_type": att.get("content_type"),
                        "size": att.get("size"),
                    })
                logger.info(
                    "Uploaded {count} attachments to MinIO for uid={uid}",
                    count=len(attachments), uid=uid,
                )

            # Встраиваем inline-картинки в HTML (если результат <= 1 МБ) и сохраняем в MinIO
            original_html_key = None
            if raw_html:
                embedded = _embed_inline_images(raw_html, message)
                if len(embedded.encode("utf-8")) <= 1_000_000:
                    raw_html = embedded

                minio = get_minio_client()
                original_html_key = await minio.upload_file(
                    file_content=raw_html.encode("utf-8"),
                    filename="original.html",
                    announcement_id=f"emails/{uid}",
                )
                logger.info("Saved original HTML to MinIO: {key}", key=original_html_key)

            # Формируем контекст и отправляем в пайплайн
            ctx = PipelineContext(
                uid=uid,
                message_id=message_id,
                source=source,
                subject=subject,
                sender_email=sender_email,
                body=body,
                links=links,
                attachments=attachments,        # только метаданные + object_key
                received_at=received_at,
                raw_html=original_html_key,
                attachments_text=attachments_text,
            )
            try:
                await run_pipeline(ctx)
            except Exception:
                logger.exception(
                    "Pipeline failed for uid={uid}, email saved but not fully processed",
                    uid=uid,
                )
                # Не пробрасываем — письмо помечаем прочитанным,
                # т.к. повторная обработка того же письма не поможет.
                # Данные уже в БД (status=PROCESSING), тренер увидит.

            # Помечаем прочитанным только после успешной обработки
            mark = await client.uid("store", uid, "+FLAGS.SILENT (\\Seen)")
            if mark.result != "OK":
                raise RuntimeError(f"Mark seen failed uid={uid}: {mark.lines}")
            logger.info("Processed uid={uid}", uid=uid)
        except Exception:
            logger.exception("Failed to process uid={uid}, leaving unread", uid=uid)


async def _wait_for_new_mail(client):
    """
    IDLE — держим соединение открытым и ждём push-уведомление о новом письме.

    Как работает IMAP IDLE:
      Клиент:  → IDLE
      Сервер:  ← + idling
      ... тишина, соединение висит ...
      Сервер:  ← * 5 EXISTS    ← пришло новое письмо!
      Клиент:  → DONE          ← выходим из IDLE, идём обрабатывать

    Если за 5 минут ничего не пришло — выходим по таймауту и перезапускаем IDLE
    (некоторые серверы сбрасывают соединение при долгом IDLE).
    """
    idle = await client.idle_start(timeout=settings.IMAP_IDLE_TIMEOUT)  # входим в IDLE-режим
    try:
        await client.wait_server_push(timeout=settings.IMAP_IDLE_TIMEOUT + 30)  # ждём событие от сервера
    except asyncio.TimeoutError:
        pass                                                       # таймаут — просто переподключаемся
    finally:
        if client.has_pending_idle():
            client.idle_done()                                     # выходим из IDLE
        with suppress(Exception):
            await asyncio.wait_for(idle, timeout=30)               # дожидаемся завершения IDLE-задачи


# ── public entry point ──────────────────────────────────


async def run_email_watcher():
    """
    Бесконечный цикл: подключение → IDLE → обработка.

    Жизненный цикл воркера:
      1. Подключаемся к IMAP серверу
      2. Обрабатываем накопившиеся непрочитанные письма
      3. Входим в IDLE — ждём новое письмо
      4. Пришло письмо → обрабатываем → обратно в IDLE
      5. Если соединение упало → ждём 3с → реконнект
      6. Если опять упало → ждём 6с → 12с → 24с → ... → макс 60с

    Пример лога:
      INFO  IMAP connected, folder=INBOX
      INFO  New email uid=456 from=ivanov@forte... subject=Отчёт attachments=2
      INFO  Processed uid=456
      INFO  IDLE timeout, refreshing...
      ERROR IMAP watcher crashed, reconnecting in 3s
      INFO  IMAP connected, folder=INBOX
    """
    if not settings.IMAP_HOST:
        logger.warning("IMAP_HOST not set, email watcher disabled")
        return                                                     # нет настроек — не запускаем

    backoff = 3                                                    # начальная задержка перед реконнектом
    while True:
        client = None
        try:
            client = await _connect()                              # подключаемся к серверу
            backoff = 3                                            # сбрасываем задержку после успешного коннекта
            while True:
                await _process_unseen(client)                      # обрабатываем накопившиеся письма
                await _wait_for_new_mail(client)                   # ждём новое письмо через IDLE
                await _process_unseen(client)                      # обрабатываем то, что пришло
        except asyncio.CancelledError:
            logger.info("Email watcher cancelled")
            raise                                                  # graceful shutdown — пробрасываем наверх
        except Exception:
            logger.exception("IMAP watcher crashed, reconnecting in {b}s", b=backoff)
            if client:
                with suppress(Exception):
                    await client.logout()                          # пытаемся корректно разлогиниться
            await asyncio.sleep(backoff)                           # ждём перед реконнектом
            backoff = min(backoff * 2, 60)                         # экспоненциальный backoff: 3 → 6 → 12 → ... → 60
