import io

import xlsxwriter

from services.reporting import MessageRow, Summary

_HEADERS = [
    "ID письма", "Дата получения", "Отправитель", "Источник", "Тема",
    "Статус письма", "Тип обработки", "Статус тикета", "Исполнитель",
    "План публикации", "Отработано", "Длительность простоя (ч)",
    "ID анонса", "Заголовок анонса", "Опубликован",
]


def _fmt_dt(dt):
    return dt.strftime("%Y-%m-%d %H:%M") if dt else ""


def build_report_xlsx(rows: list[MessageRow], summary: Summary) -> bytes:
    buf = io.BytesIO()
    wb = xlsxwriter.Workbook(buf, {"in_memory": True})
    bold = wb.add_format({"bold": True})

    s = wb.add_worksheet("Сводка")
    r = 0
    s.write(r, 0, "Всего сообщений:", bold); s.write(r, 1, summary.total); r += 2
    s.write(r, 0, "По источникам:", bold); r += 1
    for k, v in summary.by_source.items():
        s.write(r, 0, k); s.write(r, 1, v); r += 1
    r += 1
    s.write(r, 0, "По статусам:", bold); r += 1
    for k, v in summary.by_status.items():
        s.write(r, 0, k); s.write(r, 1, v); r += 1
    r += 1
    s.write(r, 0, "Авто / Вручную:", bold); r += 1
    s.write(r, 0, "авто"); s.write(r, 1, summary.auto); r += 1
    s.write(r, 0, "вручную"); s.write(r, 1, summary.manual); r += 2
    s.write(r, 0, "Неотработанные:", bold); s.write(r, 1, summary.unaddressed_total); r += 1
    for k, v in summary.unaddressed_by_source.items():
        s.write(r, 0, k); s.write(r, 1, v); r += 1

    d = wb.add_worksheet("Сообщения")
    for col, h in enumerate(_HEADERS):
        d.write(0, col, h, bold)
    for i, row in enumerate(rows, start=1):
        d.write(i, 0, str(row.email_id))
        d.write(i, 1, _fmt_dt(row.received_at))
        d.write(i, 2, row.sender_email or "")
        d.write(i, 3, row.source)
        d.write(i, 4, row.subject)
        d.write(i, 5, row.email_status)
        d.write(i, 6, row.processing_type)
        d.write(i, 7, row.ticket_status or "")
        d.write(i, 8, row.assignee_name or "")
        d.write(i, 9, _fmt_dt(row.publish_at))
        d.write(i, 10, "да" if row.addressed else "нет")
        d.write(i, 11, "" if row.idle_hours is None else row.idle_hours)
        d.write(i, 12, str(row.announcement_id) if row.announcement_id else "")
        d.write(i, 13, row.announcement_title or "")
        d.write(i, 14, _fmt_dt(row.published_at))

    wb.close()
    return buf.getvalue()
