# Reporting Export (Excel) — Design

**Date:** 2026-06-02
**Status:** Approved, ready for implementation plan
**Covers:** BRD «Автоматизация анонсов» §7 «Требования к отчётности» (points 1–5 + «для КЦ/не для КЦ» from point 6). «Выяснение у оунеров» (rest of point 6) is deferred to phase 2.

## Goal

A single endpoint that returns an `.xlsx` report for a date range, with a summary
sheet (aggregates) and a detail sheet (one row per incoming message). Excel only
for the MVP.

## Endpoint

```
GET /announcements/reports/messages.xlsx
  ?date_from=2026-06-01        (required; matches received_at, Almaty date)
  &date_to=2026-06-30          (required; inclusive)
  &source=ServiceDesk          (optional, repeatable)
  &processing=AUTO|MANUAL      (optional)
```

- Response: `StreamingResponse`, `Content-Type:
  application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`,
  `Content-Disposition: attachment; filename="messages_<from>_<to>.xlsx"`.
- Auth: same as other announcement endpoints (`X-User-Id` required → 401 if missing).
- `400` on missing/invalid period (`date_from > date_to`, unparseable).

## Layers

1. `pipeline/source.py` — `resolve_source(sender_email) -> str`. Extracted from
   the inline `_SOURCE_MAP` in `outlook_worker`; reused by the worker and the
   report so source resolution has a single source of truth. Unknown → `"other"`.
2. `services/reporting.py` — `ReportingService`:
   - `get_message_rows(date_from, date_to, source, processing) -> list[MessageRow]`:
     one query `incoming_emails LEFT JOIN review_tickets (ON ticket.email_id =
     email.id) LEFT JOIN announcements (ON ann.id = ticket.announcement_id)` over
     `received_at` range, plus filters. All derivation lives here.
   - `compute_summary(rows) -> Summary`: aggregates for the summary sheet.
3. `services/report_export.py` — `build_report_xlsx(rows, summary) -> bytes`:
   renders both sheets via `XlsxWriter` into `BytesIO`. No data access.
4. `routers/reports.py` — endpoint: validate params → service → exporter → stream.
   Registered in `routers/__init__`.

Separating selection from rendering keeps each independently testable.

## Detail sheet «Сообщения» (row = incoming email)

| # | Column | Source |
|---|--------|--------|
| 1 | ID письма | `email.id` |
| 2 | Дата получения | `email.received_at` → Asia/Almaty |
| 3 | Отправитель | `email.sender_email` |
| 4 | Источник | `resolve_source(sender_email)` |
| 5 | Тема | `email.subject` |
| 6 | Статус письма | `email.status` |
| 7 | Тип обработки | derived: `MANUAL` (ticket exists) / `AUTO` (no ticket) |
| 8 | Статус тикета | `ticket.status` (if any) |
| 9 | Исполнитель | `ticket.assignee_name` |
| 10 | План публикации | `ticket.publish_at` → Asia/Almaty |
| 11 | Отработано | derived: да/нет |
| 12 | ID анонса | `ticket.announcement_id` |
| 13 | Заголовок анонса | `announcement.title` |
| 14 | Опубликован | `announcement.published_at` → Asia/Almaty |
| 15 | Длительность простоя | derived: `now − received_at` (hours) for unaddressed, else blank |
| 16 | Содержание (КЦ) | derived: «для КЦ» / «не для КЦ» |

## Summary sheet «Сводка» (answers §7.1–5)

```
Период:            date_from — date_to
Всего сообщений:   N

По источникам:        RetailInfo / komek / ServiceDesk / other        → counts
По цветовым статусам: зелёные(GREEN) / красные(RED) /
                      обработанные(DONE) / неактивные(GRAY) / прочие  → counts
Авто vs ручная:       авто(no ticket) / вручную(ticket)               → counts
Неотработанные:       всего + в разрезе источников                    → counts
Содержание (КЦ):      для КЦ / не для КЦ                              → counts
```

«прочие» row absorbs `PROCESSING`/`YELLOW` so the status counts sum to the total.

## Derivation rules (in `ReportingService`)

- **Тип обработки:** ticket exists → `MANUAL`; else → `AUTO` (no-review emails go
  through the `publish` step and are auto-published).
- **Отработано:** `MANUAL` → `ticket.status in (PUBLISHED, REJECTED)`; `AUTO` →
  `email.status == DONE`; else «нет» (unaddressed).
- **Длительность простоя:** unaddressed only, `now − received_at`.
- **Содержание (КЦ):** `RED` or has ticket/announcement → «для КЦ»; `GREEN` → «не
  для КЦ / уже в БЗ»; else «—».
- **Источник:** always `resolve_source(sender_email)` (works for all emails,
  including green/auto without a ticket).

## Testing (TDD, via docker-compose.test)

1. `resolve_source` — unit: known addresses, unknown→other, case/None.
2. `get_message_rows` — DB (`session` fixture): AUTO vs MANUAL, period filter,
   source/processing filters, отработано/длительность/КЦ derivation.
3. `compute_summary` — unit on prebuilt rows: all buckets, counts sum to total.
4. `build_report_xlsx` — unit: write to `BytesIO`, read back with `openpyxl`,
   assert sheet names («Сводка», «Сообщения»), headers, key values.
5. Endpoint via `client`: 200 + correct `Content-Type`/`Content-Disposition`;
   400 on bad period; 401 without `X-User-Id`.

## Non-goals (MVP)

- Point 6 «выяснение у оунеров» — phase 2 (needs a new tracked field + a way to set it).
- CSV (xlsx only), dashboards/charts, JSON API for the frontend.
- Linking AUTO announcements back to their email (`announcements.email_id` does not
  exist) → announcement columns are blank for AUTO rows.
- Caching/pagination.

## Dependencies

- `XlsxWriter` — already present.
- `openpyxl` — add to `requirements-test.txt` (used only to read xlsx back in tests).

## Future work

- Phase 2: `needs_owner_clarification` flag on the ticket + UI/action to set it,
  plus its column/count in the report.
- Add `announcements.email_id` FK to enrich AUTO rows and tighten auto↔email linkage.
