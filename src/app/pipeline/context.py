from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from models.incoming_emails import EmailStatusEnum


@dataclass
class PipelineContext:
    """Контекст пайплайна — накапливает данные по мере прохождения шагов."""

    # ── Входные данные (от воркера) ──────────────────────
    uid: str                                    # IMAP UID письма
    message_id: str                             # Message-ID из заголовка
    source: str                   # RetailInfo / Komek / ServiceDesk
    subject: str
    sender_email: str
    body: str
    received_at: datetime
    links: list[dict] = field(default_factory=list)
    attachments: list[dict] = field(default_factory=list)
    raw_html: str | None = None                 # оригинальный HTML письма
    attachments_text: str = ""                  # текст, извлечённый из вложений (pptx и др.)

    # ── Шаг 1: save_email ───────────────────────────────
    email_db_id: UUID | None = None             # ID записи в incoming_emails
    is_duplicate: bool = False                  # дубликат по message_id

    # ── Шаг 2: analyze (GPT + Weaviate) ─────────────────
    ai_email: str | None = None                  # краткое содержание от GPT
    ai_title: str | None = None                   # Ai тема письма
    in_knowledge_base: bool | None = None       # есть ли в ForteKnowledge
    ai_emails_db_id: UUID | None = None          # ID записи в email_analysis
    script_ru: str | None = None
    script_kz: str | None = None
    ai_summary: str | None = None


    # ── Шаг 3: route ────────────────────────────────────
    status: EmailStatusEnum | None = None       # GREEN / RED / YELLOW
    auto_publish: bool = False                  # авто-публикация или ручная

    # ── Шаг 4: publish ──────────────────────────────────
    announcement_db_id: UUID | None = None      # ID созданного анонса (если авто)

    # ── Шаг 5: notify ──────────────────────────────────
    trainer_notified: bool = False              # уведомлён ли тренер
