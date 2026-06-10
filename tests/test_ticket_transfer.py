import uuid

from sqlalchemy import select

from models.announcement import get_astana_time
from models.incoming_emails import IncomingEmail, EmailStatusEnum
from models.review_ticket import ReviewTicket, ReviewHistory, TicketStatusEnum


async def _seed(session, *, status=TicketStatusEnum.IN_REVIEW, assignee_id=None,
                assignee_name=None, pending_assignee_id=None, pending_assignee_name=None):
    email = IncomingEmail(outlook_id=f"m-{uuid.uuid4()}", subject="s", body="b",
                          received_at=get_astana_time(), status=EmailStatusEnum.RED)
    session.add(email)
    await session.flush()
    t = ReviewTicket(email_id=email.id, status=status, title="T", body="B",
                     source="ServiceDesk", assignee_id=assignee_id, assignee_name=assignee_name,
                     pending_assignee_id=pending_assignee_id,
                     pending_assignee_name=pending_assignee_name)
    session.add(t)
    await session.flush()
    return t


def _hdr(uid, name="Requester"):
    return {"X-User-Id": str(uid), "X-User-Name": name}


async def test_transfer_request_sets_pending_without_reassigning(client, session):
    a, b = uuid.uuid4(), uuid.uuid4()
    t = await _seed(session, assignee_id=a, assignee_name="A")

    r = await client.post(
        f"/auto-announce/tickets/{t.id}/transfer-request",
        json={"assignee_id": str(b), "assignee_name": "B", "reason": "Я в отпуске"},
        headers=_hdr(a, "A"),
    )

    assert r.status_code == 200, r.text
    await session.refresh(t)
    assert t.assignee_id == a                 # исполнитель НЕ меняется
    assert t.status == TicketStatusEnum.IN_REVIEW
    assert t.pending_assignee_id == b
    assert t.pending_assignee_name == "B"
    assert t.transfer_reason == "Я в отпуске"
    assert t.transfer_requested_by_id == a


async def test_transfer_request_requires_reason(client, session):
    a, b = uuid.uuid4(), uuid.uuid4()
    t = await _seed(session, assignee_id=a, assignee_name="A")

    r = await client.post(
        f"/auto-announce/tickets/{t.id}/transfer-request",
        json={"assignee_id": str(b), "assignee_name": "B"},
        headers=_hdr(a, "A"),
    )

    assert r.status_code == 422, r.text


async def test_transfer_request_rejected_when_closed(client, session):
    a, b = uuid.uuid4(), uuid.uuid4()
    t = await _seed(session, status=TicketStatusEnum.REJECTED, assignee_id=a)

    r = await client.post(
        f"/auto-announce/tickets/{t.id}/transfer-request",
        json={"assignee_id": str(b), "assignee_name": "B", "reason": "x"},
        headers=_hdr(a),
    )

    assert r.status_code == 400, r.text


async def test_transfer_approve_reassigns_and_clears_pending(client, session):
    a, b = uuid.uuid4(), uuid.uuid4()
    t = await _seed(session, status=TicketStatusEnum.IN_REVIEW, assignee_id=a,
                    assignee_name="A", pending_assignee_id=b, pending_assignee_name="B")

    r = await client.post(
        f"/auto-announce/tickets/{t.id}/transfer-approve",
        headers=_hdr(uuid.uuid4(), "Head"),
    )

    assert r.status_code == 200, r.text
    await session.refresh(t)
    assert t.assignee_id == b                 # передан новому
    assert t.assignee_name == "B"
    assert t.status == TicketStatusEnum.IN_REVIEW
    assert t.pending_assignee_id is None


async def test_transfer_approve_without_pending_400(client, session):
    t = await _seed(session, assignee_id=uuid.uuid4())

    r = await client.post(
        f"/auto-announce/tickets/{t.id}/transfer-approve",
        headers=_hdr(uuid.uuid4(), "Head"),
    )

    assert r.status_code == 400, r.text


async def test_transfer_reject_clears_pending_keeps_assignee(client, session):
    a, b = uuid.uuid4(), uuid.uuid4()
    t = await _seed(session, assignee_id=a, assignee_name="A",
                    pending_assignee_id=b, pending_assignee_name="B")

    r = await client.post(
        f"/auto-announce/tickets/{t.id}/transfer-reject",
        json={"comment": "не согласован"},
        headers=_hdr(uuid.uuid4(), "Head"),
    )

    assert r.status_code == 200, r.text
    await session.refresh(t)
    assert t.assignee_id == a                 # остаётся у текущего
    assert t.pending_assignee_id is None


async def test_pending_transfers_queue_lists_only_pending(client, session):
    a, b = uuid.uuid4(), uuid.uuid4()
    # one ticket with a pending transfer, one without
    pending = await _seed(session, assignee_id=a, assignee_name="A",
                          pending_assignee_id=b, pending_assignee_name="B")
    pending.transfer_requested_at = get_astana_time()
    pending.transfer_reason = "повод"
    await _seed(session, assignee_id=a, assignee_name="A")
    await session.commit()

    r = await client.get("/auto-announce/tickets/transfers/pending")

    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body) == 1
    assert body[0]["id"] == str(pending.id)
    assert body[0]["pending_assignee_name"] == "B"
    assert body[0]["transfer_reason"] == "повод"


async def test_detail_exposes_pending_transfer_fields(client, session):
    a, b = uuid.uuid4(), uuid.uuid4()
    t = await _seed(session, assignee_id=a, assignee_name="A")
    await client.post(
        f"/auto-announce/tickets/{t.id}/transfer-request",
        json={"assignee_id": str(b), "assignee_name": "B", "reason": "повод"},
        headers=_hdr(a, "A"),
    )

    r = await client.get(f"/auto-announce/tickets/{t.id}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["pending_assignee_name"] == "B"
    assert body["transfer_reason"] == "повод"
