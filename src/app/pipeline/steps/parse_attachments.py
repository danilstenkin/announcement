"""
Извлечение текста из вложений (pptx) для передачи в GPT-анализ.
"""

from __future__ import annotations

import io

from pptx import Presentation

from logger import get_logger

logger = get_logger(__name__)

# Максимум символов из вложений, чтобы не раздувать промпт
MAX_ATTACHMENT_TEXT = 6000


def _parse_pptx(data: bytes) -> str:
    """Извлекает весь текст из PowerPoint-презентации."""
    prs = Presentation(io.BytesIO(data))
    texts: list[str] = []
    for slide_num, slide in enumerate(prs.slides, start=1):
        slide_texts: list[str] = []
        for shape in slide.shapes:
            if shape.has_text_frame:
                for paragraph in shape.text_frame.paragraphs:
                    line = paragraph.text.strip()
                    if line:
                        slide_texts.append(line)
            if shape.has_table:
                for row in shape.table.rows:
                    row_text = " | ".join(
                        cell.text.strip() for cell in row.cells if cell.text.strip()
                    )
                    if row_text:
                        slide_texts.append(row_text)
        if slide_texts:
            texts.append(f"[Слайд {slide_num}]\n" + "\n".join(slide_texts))
    return "\n\n".join(texts)


# Маппинг расширение → парсер
_PARSERS: dict[str, callable] = {
    ".pptx": _parse_pptx,
}


def parse_attachment(filename: str, data: bytes) -> str | None:
    """
    Пытается извлечь текст из вложения по расширению.
    Возвращает текст или None, если формат не поддерживается.
    """
    ext = ""
    dot_pos = filename.rfind(".")
    if dot_pos != -1:
        ext = filename[dot_pos:].lower()

    parser = _PARSERS.get(ext)
    if parser is None:
        return None

    try:
        text = parser(data)
        return text.strip() or None
    except Exception as e:
        logger.warning(
            "Failed to parse attachment {fn}: {err}",
            fn=filename, err=e,
        )
        return None


def extract_attachments_text(raw_attachments: list[dict]) -> str:
    """
    Парсит все поддерживаемые вложения и возвращает объединённый текст.
    Обрезает до MAX_ATTACHMENT_TEXT символов.
    """
    parts: list[str] = []
    for att in raw_attachments:
        text = parse_attachment(att["filename"], att["data"])
        if text:
            parts.append(f"--- {att['filename']} ---\n{text}")
            logger.info(
                "Parsed attachment {fn}: {chars} chars",
                fn=att["filename"], chars=len(text),
            )

    if not parts:
        return ""

    combined = "\n\n".join(parts)
    if len(combined) > MAX_ATTACHMENT_TEXT:
        combined = combined[:MAX_ATTACHMENT_TEXT] + "\n[...текст обрезан]"
    return combined
