from __future__ import annotations

import json as _json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
)

from config import settings
from logger import get_logger
from models.ai_category import AI_CATEGORY_RULES, AiCategoryEnum

logger = get_logger(__name__)


def build_analysis_schema(include_summary: bool) -> dict[str, Any]:
    """JSON-schema для строгого ответа GPT (response_format=json_schema).

    Вынесено из format_email, чтобы контракт полей можно было тестировать без
    сетевого вызова.
    """
    properties: dict[str, Any] = {
        "title": {
            "type": "string",
            "description": "Краткий заголовок анонса (без Markdown)",
        },
        "ai_email": {
            "type": "string",
            "description": "Тело анонса с Markdown-разметкой (без заголовка)",
        },
        "script_ru": {
            "type": ["string", "null"],
            "description": "Скрипт оператора на русском или null",
        },
        "script_kz": {
            "type": ["string", "null"],
            "description": "Скрипт оператора на казахском или null",
        },
        "recommended_publish_date": {
            "type": ["string", "null"],
            "description": "Рекомендуемая дата публикации в формате YYYY-MM-DD из текста (например из 'Начало работ'), или null",
        },
        "category": {
            "type": "string",
            "enum": [e.value for e in AiCategoryEnum],
            "description": AI_CATEGORY_RULES,
        },
    }

    required = [
        "title",
        "ai_email",
        "script_ru",
        "script_kz",
        "recommended_publish_date",
        "category",
    ]

    if include_summary:
        properties["ai_summary"] = {
            "type": "string",
            "description": "Суть письма одним предложением, максимум 7 слов",
        }
        required.append("ai_summary")

    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


class GPTError(Exception):
    """Base Error"""


class GPTAPIError(GPTError):
    """Error from API (4xx/5xx)"""

    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        self.message = message
        super().__init__(f"GPT API {status_code}: {message}")


class GPTEmptyResponseError(GPTError):
    """Response without content"""


class GPTInvalidJSONError(GPTError):
    """Response content is not valid JSON"""


def build_user_content(text: str, images: list[str] | None):
    """Build a Chat Completions user `content`.

    No images -> plain string (keeps text-only requests unchanged).
    With images -> multimodal array: the text plus one image_url per image.
    Each image must be a full data URL, e.g. 'data:image/png;base64,...'.
    """
    if not images:
        return text
    content = [{"type": "text", "text": text}]
    for url in images:
        content.append({"type": "image_url", "image_url": {"url": url}})
    return content


@dataclass(frozen=True)
class GPTConfig:
    base_url: str = settings.GPT_URL
    api_key: str = settings.GPT_API_KEY
    model: str = "gpt-5.4-mini"
    timeout: float = 60.0
    max_retries: int = 3
    debug_dump: bool = False
    debug_dir: str = "debug"


class GPTClient:

    def __init__(self, config: GPTConfig):
        self._config = config
        self._client = AsyncOpenAI(
            api_key=config.api_key,
            base_url=config.base_url,
            timeout=config.timeout,
            max_retries=config.max_retries,
        )

    async def __aenter__(self) -> GPTClient:
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()

    async def close(self) -> None:
        await self._client.close()

    async def format_email(
        self,
        email_body: str,
        template: str,
        prompt: str,
        *,
        temperature: float | None = None,
        max_tokens: int = 12000,
        include_summary: bool = False,
        images: list[str] | None = None,
    ) -> dict[str, Any]:
        schema = build_analysis_schema(include_summary)

        user_content = (
            "Шаблон оформления:\n"
            f"{template}\n\n"
            "Исходное письмо:\n"
            f"{email_body}"
        )

        request_params: dict[str, Any] = {
            "model": self._config.model,
            "messages": [
                {"role": "system", "content": prompt},
                {"role": "user", "content": build_user_content(user_content, images)},
            ],
            "max_tokens": max_tokens,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "email_analysis",
                    "strict": True,
                    "schema": schema,
                },
            },
        }

        if temperature is not None:
            request_params["temperature"] = temperature

        logger.info(
            "GPT request: model={model}, url={url}, messages_len={ml}",
            model=request_params["model"],
            url=self._config.base_url,
            ml=sum(len(m["content"]) for m in request_params["messages"]
                   if isinstance(m["content"], str)),
        )
        logger.debug("GPT request params: {params}", params=_json.dumps(request_params, ensure_ascii=False, default=str))

        try:
            response = await self._client.chat.completions.create(**request_params)
        except APIStatusError as e:
            raise GPTAPIError(e.status_code, str(e.response)) from e
        except (APITimeoutError, APIConnectionError) as e:
            logger.error("GPT transport error: {err}", err=e, exc_info=True)
            raise GPTError(f"GPT transport error: {e}") from e

        if self._config.debug_dump:
            self._dump_debug_response(response)

        choice = response.choices[0] if response.choices else None
        content = choice.message.content if choice else None

        if not content:
            raise GPTEmptyResponseError("no content in response")

        try:
            return _json.loads(content)
        except _json.JSONDecodeError as e:
            raise GPTInvalidJSONError(f"invalid JSON from model: {e}") from e

    def _dump_debug_response(self, response: Any) -> None:
        debug_dir = Path(self._config.debug_dir)
        debug_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        dump_path = debug_dir / f"gpt_debug_{timestamp}.json"
        with dump_path.open("w", encoding="utf-8") as f:
            _json.dump(
                response.model_dump(),
                f,
                ensure_ascii=False,
                indent=2,
                default=str,
            )
        logger.info("GPT debug dumped to {path}", path=str(dump_path))
