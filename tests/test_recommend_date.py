from datetime import datetime
import pytz
from pipeline.recommend_date import parse_recommended_date

ALMATY = pytz.timezone("Asia/Almaty")


def test_parses_iso_date_to_midnight_almaty():
    dt = parse_recommended_date("2026-06-01", received_at=datetime(2026, 5, 20, tzinfo=pytz.UTC))
    assert dt.tzinfo is not None
    assert dt.hour == 0 and dt.minute == 0
    assert dt.year == 2026 and dt.month == 6 and dt.day == 1


def test_none_falls_back_to_received_day():
    received = ALMATY.localize(datetime(2026, 5, 20, 14, 30))
    dt = parse_recommended_date(None, received_at=received)
    assert dt.year == 2026 and dt.month == 5 and dt.day == 20
    assert dt.hour == 0 and dt.minute == 0


def test_garbage_falls_back():
    received = ALMATY.localize(datetime(2026, 5, 20, 14, 30))
    dt = parse_recommended_date("не раньше пятницы", received_at=received)
    assert dt.day == 20 and dt.hour == 0


def test_dotted_date_format():
    dt = parse_recommended_date("01.06.2026", received_at=datetime(2026, 5, 20, tzinfo=pytz.UTC))
    assert dt.year == 2026 and dt.month == 6 and dt.day == 1 and dt.hour == 0
