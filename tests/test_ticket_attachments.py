import uuid

import pytest
from sqlalchemy import select

from models.announcement import get_astana_time
from models.email_attachment import EmailAttachment
from models.incoming_emails import IncomingEmail, EmailStatusEnum
from models.review_ticket import ReviewTicket, TicketStatusEnum


class FakeMinio:
    def __init__(self):
        self.uploaded: dict[str, bytes] = {}
        self.deleted: list[str] = []

    async def upload_file(self, file_content, filename, announcement_id):
        key = f"announcements/{announcement_id}/ts_{filename}"
        self.uploaded[key] = file_content
        return key

    async def delete_file(self, object_key):
        self.deleted.append(object_key)

    def get_file_url(self, object_key, expiry_seconds=3600):
        return f"http://minio/{object_key}"


@pytest.fixture
def fake_minio(monkeypatch):
    fake = FakeMinio()
    monkeypatch.setattr("routers.tickets.get_minio_client", lambda: fake, raising=False)
    return fake


async def _seed_ticket(session, *, status=TicketStatusEnum.IN_REVIEW, announcement_id=None):
    email = IncomingEmail(outlook_id=f"m-{uuid.uuid4()}", subject="s", body="b",
                          received_at=get_astana_time(), status=EmailStatusEnum.RED)
    session.add(email)
    await session.flush()
    t = ReviewTicket(email_id=email.id, status=status, title="T", body="B",
                     source="ServiceDesk", announcement_id=announcement_id)
    session.add(t)
    await session.flush()
    return t


async def test_upload_creates_email_attachments(client, session, fake_minio):
    t = await _seed_ticket(session)

    r = await client.post(
        f"/auto-announce/tickets/{t.id}/attachments",
        files=[
            ("files", ("a.png", b"\x89PNG-bytes", "image/png")),
            ("files", ("b.pdf", b"%PDF-bytes", "application/pdf")),
        ],
    )

    assert r.status_code == 200, r.text
    body = r.json()
    assert {a["filename"] for a in body} == {"a.png", "b.pdf"}

    rows = (await session.execute(
        select(EmailAttachment).where(EmailAttachment.email_id == t.email_id)
    )).scalars().all()
    assert len(rows) == 2
    assert len(fake_minio.uploaded) == 2


async def test_upload_rejected_after_ticket_converted(client, session, fake_minio):
    t = await _seed_ticket(session, status=TicketStatusEnum.AGREED,
                           announcement_id=uuid.uuid4())

    r = await client.post(
        f"/auto-announce/tickets/{t.id}/attachments",
        files=[("files", ("a.png", b"data", "image/png"))],
    )

    assert r.status_code == 400, r.text


async def test_upload_rejects_too_many_files(client, session, fake_minio):
    t = await _seed_ticket(session)

    files = [("files", (f"f{i}.txt", b"x", "text/plain")) for i in range(6)]
    r = await client.post(f"/auto-announce/tickets/{t.id}/attachments", files=files)

    assert r.status_code == 400, r.text


async def test_delete_removes_row_and_minio_object(client, session, fake_minio):
    t = await _seed_ticket(session)
    att = EmailAttachment(email_id=t.email_id, filename="a.png",
                          object_key="emails/x/a.png", content_type="image/png")
    session.add(att)
    await session.flush()

    r = await client.request(
        "DELETE", f"/auto-announce/tickets/{t.id}/attachments/{att.id}"
    )

    assert r.status_code == 200, r.text
    gone = (await session.execute(
        select(EmailAttachment).where(EmailAttachment.id == att.id)
    )).scalar_one_or_none()
    assert gone is None
    assert "emails/x/a.png" in fake_minio.deleted


async def test_ticket_detail_hides_inline_attachments(client, session, fake_minio):
    t = await _seed_ticket(session)
    session.add(EmailAttachment(email_id=t.email_id, filename="doc.pdf",
                                object_key="emails/x/doc.pdf",
                                content_type="application/pdf", is_inline=False))
    session.add(EmailAttachment(email_id=t.email_id, filename="inline-1.png",
                                object_key="emails/x/inline-1.png",
                                content_type="image/png", is_inline=True))
    await session.flush()

    r = await client.get(f"/auto-announce/tickets/{t.id}")

    assert r.status_code == 200, r.text
    names = {a["filename"] for a in r.json()["attachments"]}
    assert names == {"doc.pdf"}                     # inline-картинка скрыта


async def test_gpt_collection_includes_inline(session):
    """Inline images must still reach GPT vision even though they are hidden
    from the frontend."""
    from pipeline.steps.ai_analysis import collect_email_image_data_urls

    email = IncomingEmail(outlook_id=f"m-{uuid.uuid4()}", subject="s", body="b",
                          received_at=get_astana_time(), status=EmailStatusEnum.RED)
    session.add(email)
    await session.flush()
    session.add(EmailAttachment(email_id=email.id, filename="inline-1.png",
                                object_key="emails/x/inline-1.png", file_size=5,
                                content_type="image/png", is_inline=True))
    await session.flush()

    class _FakeMinio:
        async def download_file(self, object_key):
            return b"bytes"

    urls = await collect_email_image_data_urls(session, email.id, _FakeMinio())

    assert len(urls) == 1
    assert urls[0].startswith("data:image/png;base64,")


async def test_delete_foreign_attachment_404(client, session, fake_minio):
    t = await _seed_ticket(session)
    other_email = IncomingEmail(outlook_id=f"o-{uuid.uuid4()}", subject="s", body="b",
                                received_at=get_astana_time(), status=EmailStatusEnum.RED)
    session.add(other_email)
    await session.flush()
    foreign = EmailAttachment(email_id=other_email.id, filename="x.png",
                              object_key="emails/y/x.png", content_type="image/png")
    session.add(foreign)
    await session.flush()

    r = await client.request(
        "DELETE", f"/auto-announce/tickets/{t.id}/attachments/{foreign.id}"
    )

    assert r.status_code == 404, r.text
