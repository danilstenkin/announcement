import base64
import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dependencies.gpt import GPTClient, GPTConfig
from logger import get_logger
from models.ai_analysis import AIEmail
from models.email_attachment import EmailAttachment
from models.incoming_emails import EmailStatusEnum
from pipeline.context import PipelineContext
from pipeline.prompts import extract_system, select_prompt

logger = get_logger(__name__)

MAX_IMAGES = 5
MAX_IMAGE_BYTES = 8 * 1024 * 1024  # 8 MB per image


async def collect_email_image_data_urls(
    session: AsyncSession,
    email_id,
    minio,
    max_images: int = MAX_IMAGES,
    max_bytes: int = MAX_IMAGE_BYTES,
) -> list[str]:
    """Fetch image attachments of an email from MinIO as base64 data URLs."""
    rows = (await session.execute(
        select(EmailAttachment)
        .where(EmailAttachment.email_id == email_id)
        .where(EmailAttachment.content_type.like("image/%"))
        .order_by(EmailAttachment.uploaded_at)
    )).scalars().all()

    urls: list[str] = []
    for att in rows:
        if len(urls) >= max_images:
            break
        if att.file_size and att.file_size > max_bytes:
            logger.info("Skipping oversized image {f} ({s} bytes)", f=att.filename, s=att.file_size)
            continue
        try:
            data = await minio.download_file(att.object_key)
        except Exception as e:
            logger.error("Failed to read image {f}: {err}", f=att.filename, err=e)
            continue
        if len(data) > max_bytes:
            continue
        b64 = base64.b64encode(data).decode()
        urls.append(f"data:{att.content_type};base64,{b64}")
    return urls


def _tighten_paragraph(tag: str) -> str:
    """Force margin:0 on a single <p ...> opening tag so lines sit tight."""
    style_m = re.search(r'style\s*=\s*"([^"]*)"', tag)
    if style_m:
        style = style_m.group(1)
        if re.search(r'margin\s*:', style):
            new_style = re.sub(r'margin\s*:\s*[^;"]+;?', 'margin: 0;', style)
        else:
            new_style = 'margin: 0;' + style
        return tag[:style_m.start(1)] + new_style + tag[style_m.end(1):]
    return re.sub(r'<p\b', '<p style="margin: 0"', tag, count=1)


def _normalize_block_spacing(html: str) -> str:
    """Normalize spacing: uniform gaps between div blocks, tight lines within them."""
    if not html:
        return html
    # Remove any whitespace/br between closing </div> and opening <div
    html = re.sub(r'</div>\s*(?:<br\s*/?>|\s)*\s*<div', '</div>\n<div', html)
    # Set uniform margin on all top-level div blocks (0 → блоки встык, без зазора)
    html = re.sub(
        r'<div\s+style="[^"]*margin:[^"]*"',
        lambda m: re.sub(r'margin:\s*[^;"]+', 'margin: 0', m.group(0)),
        html,
    )
    # Drop empty paragraphs (blank lines the model sometimes inserts)
    html = re.sub(r'<p\b[^>]*>(?:\s|&nbsp;|<br\s*/?>)*</p>', '', html)
    # Collapse runs of <br> into a single break
    html = re.sub(r'(?:<br\s*/?>\s*){2,}', '<br>', html)
    # Kill the browser-default <p> margins so lines inside a block don't gap
    html = re.sub(r'<p\b[^>]*>', lambda m: _tighten_paragraph(m.group(0)), html)
    return html


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

    attachments_block = ""
    if ctx.attachments_text:
        attachments_block = "\n\nТекст из вложений:\n" + ctx.attachments_text

    email_text = f"Тема {ctx.subject}\n\n{ctx.body}{links_block}{attachments_block}"
    system_name = extract_system(ctx.body)

    if system_name:
        logger.info("Detected system: {system}", system=system_name)
    else:
        logger.warning("System not detected in email body, using default prompt")

    prompt, prompt_type = select_prompt(ctx.body, ctx.sender_email)
    logger.info("Selected prompt type: {pt}", pt=prompt_type)

    is_default = prompt_type in ("default", "service_desk_default")

    images: list[str] = []
    if ctx.email_db_id:
        try:
            from dependencies.minio import get_minio_client
            images = await collect_email_image_data_urls(
                session, ctx.email_db_id, get_minio_client()
            )
            if images:
                logger.info("Passing {n} image(s) to GPT for email={eid}",
                            n=len(images), eid=ctx.email_db_id)
        except Exception as e:
            logger.error("Image collection failed for email={eid}: {err}",
                         eid=ctx.email_db_id, err=e)

    try:
        async with GPTClient(GPTConfig()) as gpt:
            result = await gpt.format_email(
                email_body=email_text,
                template="",
                prompt=prompt,
                include_summary=is_default,
                images=images,
            )

        ctx.ai_title = result.get("title", "")
        ctx.ai_email = _normalize_block_spacing(result.get("ai_email", ""))
        ctx.script_ru = _clean_script(result.get("script_ru"))
        ctx.script_kz = _clean_script(result.get("script_kz"))
        ctx.ai_category = result.get("category")

        from pipeline.recommend_date import parse_recommended_date
        ctx.recommended_publish_at = parse_recommended_date(
            result.get("recommended_publish_date"), ctx.received_at
        )

        if prompt_type in ("default", "service_desk_default"):
            ctx.ai_summary = result.get("ai_summary")
            ctx.status = EmailStatusEnum.RED

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
        ai_category=ctx.ai_category,
        recommended_publish_date=ctx.recommended_publish_at,
    )

    session.add(analysis)
    await session.flush()

    ctx.ai_emails_db_id = analysis.id

    logger.info("Analysis done: email={eid}", eid=ctx.ai_emails_db_id)
