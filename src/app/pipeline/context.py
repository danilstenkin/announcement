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

    # ── Шаг 1: save_email ───────────────────────────────
    email_db_id: UUID | None = None             # ID записи в incoming_emails
    is_duplicate: bool = False                  # дубликат по message_id

    # ── Шаг 2: analyze (GPT + Weaviate) ─────────────────
    summary: str | None = None                  # краткое содержание от GPT
    confidence_score: float | None = None       # уверенность ИИ (0.0–1.0)
    ai_reasoning: str | None = None             # почему ИИ так решил
    suggested_announcement: str | None = None   # предложенный текст анонса
    in_knowledge_base: bool | None = None       # есть ли в ForteKnowledge
    analysis_db_id: UUID | None = None          # ID записи в email_analysis

    # ── Шаг 3: route ────────────────────────────────────
    status: EmailStatusEnum | None = None       # GREEN / RED / YELLOW
    auto_publish: bool = False                  # авто-публикация или ручная

    # ── Шаг 4: publish ──────────────────────────────────
    announcement_db_id: UUID | None = None      # ID созданного анонса (если авто)

    # ── Шаг 5: notify ──────────────────────────────────
    trainer_notified: bool = False              # уведомлён ли тренер
