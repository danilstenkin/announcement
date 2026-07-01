import hashlib
from email.message import EmailMessage

from workers import outlook_worker
from workers.outlook_worker import _extract_attachments


def _build_email(parts):
    """parts: list of (filename, data: bytes, content_type)."""
    msg = EmailMessage()
    msg["Subject"] = "t"
    msg.set_content("body")
    for filename, data, ctype in parts:
        maintype, subtype = ctype.split("/", 1)
        msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=filename)
    return msg


def test_signature_logo_is_dropped(monkeypatch):
    logo = b"FORTE-SIGNATURE-LOGO" * 1000        # > 8 KB, проходит размерный фильтр
    report = b"%PDF-real-report-bytes"

    monkeypatch.setattr(
        outlook_worker, "SIGNATURE_IMAGE_HASHES",
        {hashlib.sha256(logo).hexdigest()},
    )

    msg = _build_email([
        ("image001.png", logo, "image/png"),
        ("report.pdf", report, "application/pdf"),
    ])

    files = _extract_attachments(msg, uid="1")

    names = {f["filename"] for f in files}
    assert "report.pdf" in names
    assert "image001.png" not in names


def test_non_signature_image_passes(monkeypatch):
    monkeypatch.setattr(outlook_worker, "SIGNATURE_IMAGE_HASHES", set())

    img = b"a-real-screenshot-the-operator-needs" * 1000
    msg = _build_email([("image001.png", img, "image/png")])

    files = _extract_attachments(msg, uid="1")

    assert {f["filename"] for f in files} == {"image001.png"}
