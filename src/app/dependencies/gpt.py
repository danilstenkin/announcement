from __future__ import annotations
import asyncio
import json as _json
from dataclasses import dataclass
import httpx
from config import settings
from logger import get_logger

logger = get_logger(__name__)


class GPTError(Exception):
    """Base Error"""


class GPTAPIError(GPTError):
    """Error from API (4xx/5xx)"""

    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        self.message = message
        super().__init__(f"GPT API {status_code}: {message}")


class GPTEmptyResponseError(GPTError):
    """Response without choices"""


@dataclass(frozen=True)
class GPTConfig:
    base_url: str = settings.GPT_URL
    model: str = "gpt-4.1-mini"
    timeout: float = 60.0
    max_retries: int = 3
    retry_backoff: float = 0.5


class GPTClient:
    _RETRY_STATUS_CODES = {429, 500, 502, 503, 504}

    def __init__(self, config: GPTConfig):
        self._config = config
        self._http = httpx.AsyncClient(
            base_url=config.base_url,
            timeout=config.timeout,
            verify=False,
            headers={
                "Content-Type": "application/json",
            },
        )

    async def __aenter__(self) -> GPTClient:
        return self

    async def __aexit__(self, *args) -> None:
        await self.close()

    async def close(self) -> None:
        await self._http.aclose()

    async def format_email(
        self,
        email_body: str,
        template: str,
        prompt: str,
        *,
        temperature: float = 0.3,
        max_tokens: int = 80000,
    ) -> str:
        payload = {
            "model": self._config.model,
            "input": [
                {"role": "system", "content": prompt},
                {"role": "user", "content": email_body},
            ],
            "temperature": temperature,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "email_analysis",
                    "schema": {
                        "type": "object",
                        "properties": {
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
                        },
                        "required": ["title", "ai_email", "script_ru", "script_kz"],
                        "additionalProperties": False,
                    },
                    "strict": True,
                },
            },
        }
        if max_tokens is not None:
            payload["max_output_tokens"] = max_tokens

        # ── Дамп payload и response в файл для отладки ──
        dump_path = "gpt_debug.json"

        data = await self._post_with_retry("/chat/completions", payload)

        with open(dump_path, "w", encoding="utf-8") as f:
            _json.dump({"request": payload, "response": data}, f, ensure_ascii=False, indent=2)

        logger.info("GPT debug dumped to {path}", path=dump_path)

        if data.get("error"):
            raise GPTAPIError(400, str(data["error"]))

        output = data.get("output") or []
        if not output:
            raise GPTEmptyResponseError("response has no output")

        for item in output:
            if item.get("type") == "message":
                for content in item.get("content", []):
                    if content.get("type") == "output_text":
                        return content["text"]

        raise GPTEmptyResponseError("no text in response output")

    async def _post_with_retry(self, path: str, payload: dict) -> dict:
        last_exc: Exception | None = None

        for attempt in range(self._config.max_retries + 1):
            if attempt > 0:
                delay = self._config.retry_backoff * (2 ** (attempt - 1))
                logger.warning(
                    "GPT retry %d/%d after %.2fs",
                    attempt, self._config.max_retries, delay,
                )
                await asyncio.sleep(delay)

            try:
                response = await self._http.post(path, json=payload)
            except httpx.TimeoutException as e:
                last_exc = GPTError(f"timeout: {e}")
                logger.warning("GPT timeout: %s", e)
                continue
            except httpx.HTTPError as e:
                last_exc = GPTError(f"network error: {e}")
                logger.warning("GPT network error: %s", e)
                continue

            if 200 <= response.status_code < 300:
                return response.json()

            if response.status_code in self._RETRY_STATUS_CODES:
                last_exc = self._build_api_error(response)
                logger.warning("GPT retryable error: %s", last_exc)
                continue

            raise self._build_api_error(response)

        assert last_exc is not None
        raise last_exc

    @staticmethod
    def _build_api_error(response: httpx.Response) -> GPTAPIError:
        try:
            body = response.json()
            message = body.get("error", {}).get("message") or response.text
        except Exception:
            message = response.text
        return GPTAPIError(response.status_code, message)
