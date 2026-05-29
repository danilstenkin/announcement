from sqlalchemy import text


async def test_schema_and_tables_exist(session):
    from models.review_ticket import ReviewTicket  # noqa: F401
    result = await session.execute(
        text("SELECT 1 FROM cchub_announcements.review_tickets LIMIT 0")
    )
    assert result is not None
