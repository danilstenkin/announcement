import json

from fastapi.background import P
from sqlalchemy.ext.asyncio import AsyncSession
import re
from dependencies.gpt import GPTClient, GPTConfig
# from dependencies.weaviate import get_weaviate
from logger import get_logger
from models.ai_analysis import AIEmail
from pipeline.context import PipelineContext
from pipeline.prompts.service_desk import select_prompt, extract_system

logger = get_logger(__name__)


def _clean_script(value: str | None) -> str | None:
    if not value:
        return value
    value = re.sub(r"[',]+script_(ru|kz)[':]+.*", "", value, flags=re.DOTALL)
    return value.strip() or None


async def analyze(ctx:PipelineContext, session: AsyncSession) -> None:
    email_text = f"Тема {ctx.subject}\n\n{ctx.body}"

    system_name = extract_system(ctx.body)

    if system_name:
        logger.info("Detected system: {system}", system=system_name)
    else:
        logger.warning("Sustem not delected in email body, using default promt")

    promt = select_prompt(ctx.body)

    try:
        async with GPTClient(GPTConfig()) as gpt:
            raw_response = await gpt.format_email(
                email_body=email_text,
                template="",
                prompt=promt,
            )
        result = json.loads(raw_response)
        ctx.ai_title = result.get("title", "")
        ctx.ai_email = result.get("ai_email", "")
        ctx.script_ru = _clean_script(result.get("script_ru",""))
        ctx.script_kz = _clean_script(result.get("script_kz", ""))

    except Exception as err:
        logger.error("GPT analysis failrd: {err}", err = err, exc_info=True)
        ctx.ai_email = f"GPT анализ не выполнен: {err}"

    analysis = AIEmail(
        email_id=ctx.email_db_id,
        ai_email=ctx.ai_email,
        in_knowledge_base=False,
        script_ru = ctx.script_ru,
        script_kz = ctx.script_kz,
    )

    session.add(analysis)
    await session.flush()
    ctx.ai_emails_db_id = analysis.id

    logger.info(
        "Analysis done: email={eid}",
        eid=ctx.ai_emails_db_id
    )
