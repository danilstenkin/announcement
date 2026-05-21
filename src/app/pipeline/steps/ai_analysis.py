import re

from sqlalchemy.ext.asyncio import AsyncSession

from dependencies.gpt import GPTClient, GPTConfig
from logger import get_logger
from models.ai_analysis import AIEmail
from pipeline.context import PipelineContext
from pipeline.prompts import extract_system, select_prompt

logger = get_logger(__name__)


def _clean_script(value: str | None) -> str | None:
    if not value:
        return value

    value = re.sub(r"[',]+script_(ru|kz)[':]+.*", "", value, flags=re.DOTALL)
    return value.strip() or None


async def analyze(ctx: PipelineContext, session: AsyncSession) -> None:
    links_block = ""

    if ctx.links:
        links_block = "\n\nСсылки из письма:\n" + "\n".join(
            f"- {link['text']}: {link['url']}" for link in ctx.links
        )

    email_text = f"Тема {ctx.subject}\n\n{ctx.body}{links_block}"
    system_name = extract_system(ctx.body)

    if system_name:
        logger.info("Detected system: {system}", system=system_name)
    else:
        logger.warning("System not detected in email body, using default prompt")

    prompt, prompt_type = select_prompt(ctx.body, ctx.sender_email)
    logger.info("Selected prompt type: {pt}", pt=prompt_type)

    is_default = prompt_type in ("default", "service_desk_default")

    try:
        async with GPTClient(GPTConfig()) as gpt:
            result = await gpt.format_email(
                email_body=email_text,
                template="",
                prompt=prompt,
                include_summary=is_default,
            )

        ctx.ai_title = result.get("title", "")
        ctx.ai_email = result.get("ai_email", "")
        ctx.script_ru = _clean_script(result.get("script_ru"))
        ctx.script_kz = _clean_script(result.get("script_kz"))

        if is_default:
            ctx.ai_summary = result.get("ai_summary")

    except Exception as err:
        logger.error("GPT analysis failed: {err}", err=err, exc_info=True)
        ctx.ai_email = f"GPT анализ не выполнен: {err}"

    analysis = AIEmail(
        email_id=ctx.email_db_id,
        ai_email=ctx.ai_email,
        in_knowledge_base=False,
        script_ru=ctx.script_ru,
        script_kz=ctx.script_kz,
        ai_title=ctx.ai_title,
        ai_summary=ctx.ai_summary,
    )

    session.add(analysis)
    await session.flush()

    ctx.ai_emails_db_id = analysis.id

    logger.info("Analysis done: email={eid}", eid=ctx.ai_emails_db_id)
