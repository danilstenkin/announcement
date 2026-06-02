import base64
import uuid

from models.email_attachment import EmailAttachment
from models.incoming_emails import IncomingEmail, EmailStatusEnum
from models.announcement import get_astana_time
from pipeline.steps.ai_analysis import collect_email_image_data_urls


class FakeMinio:
    def __init__(self, blobs):
        self.blobs = blobs

    async def download_file(self, object_key):
        return self.blobs[object_key]


async def _email(session):
    e = IncomingEmail(outlook_id=f"o-{uuid.uuid4()}", subject="s", body="b",
                      received_at=get_astana_time(), status=EmailStatusEnum.RED)
    session.add(e); await session.flush()
    return e


async def _att(session, email_id, *, key, ct, size=10):
    a = EmailAttachment(email_id=email_id, filename=key, object_key=key,
                        content_type=ct, file_size=size)
    session.add(a); await session.flush()
    return a


async def test_only_images_returned_as_data_urls(session):
    e = await _email(session)
    await _att(session, e.id, key="pic.png", ct="image/png")
    await _att(session, e.id, key="doc.pdf", ct="application/pdf")
    minio = FakeMinio({"pic.png": b"\x89PNG", "doc.pdf": b"%PDF"})

    urls = await collect_email_image_data_urls(session, e.id, minio)

    assert len(urls) == 1
    b64 = base64.b64encode(b"\x89PNG").decode()
    assert urls[0] == f"data:image/png;base64,{b64}"


async def test_respects_max_images(session):
    e = await _email(session)
    blobs = {}
    for i in range(5):
        await _att(session, e.id, key=f"p{i}.jpg", ct="image/jpeg")
        blobs[f"p{i}.jpg"] = b"x"
    minio = FakeMinio(blobs)

    urls = await collect_email_image_data_urls(session, e.id, minio, max_images=3)

    assert len(urls) == 3


async def test_skips_oversized(session):
    e = await _email(session)
    await _att(session, e.id, key="big.png", ct="image/png", size=999_999_999)
    minio = FakeMinio({"big.png": b"x"})

    urls = await collect_email_image_data_urls(session, e.id, minio, max_bytes=1000)

    assert urls == []
