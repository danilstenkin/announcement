# Ручки тикетов (review-tickets) — для фронта

Раздел «Анонсы»: обработка автоматически сформированных анонсов (карточка тикета —
просмотр, редактирование, согласование, публикация, история).

- **Базовый префикс:** `/auto-announce/tickets`
- **Время в ответах:** ISO 8601 в **UTC** (`...+00:00`). Показывать в Алматы:
  `new Date(x).toLocaleString("ru-RU", { timeZone: "Asia/Almaty" })` (+05:00).
- **Авторизация:** заголовки `X-User-Id` (UUID) и `X-User-Name` (URL-encoded) —
  **опциональны**. Здесь, в отличие от `/announcements`, 401 не кидается; заголовки
  нужны только для записи «кто сделал» в историю. Если передаёшь `X-User-Id` — он
  должен быть валидным UUID (иначе действие с ним упадёт). `X-User-Name` с
  кириллицей/пробелами слать URL-encoded (на бэке `unquote`).
- Через BFF-шлюз к путям добавляется его базовый префикс — уточнить у бэка.

---

## Общие объекты ответа

### `HistoryOut` — запись истории
| Поле | Тип | Значение |
|---|---|---|
| `action` | string | `CREATED, ASSIGNED, TAKEN, SENT_TO_REVISION, EDITED, APPROVED, REJECTED, SCHEDULED, RESCHEDULED, PUBLICATION_CANCELED, PUBLISHED` |
| `actor_id` | UUID? | кто сделал (null = система) |
| `actor_name` | string? | имя автора действия |
| `comment` | string? | комментарий (доработка, отклонение, подтверждение даты) |
| `changes` | object? | при `EDITED`: `{ "поле": {"old": ..., "new": ...} }` |
| `created_at` | string? | ISO-время (UTC) |

### `AttachmentOut` — вложение письма
| Поле | Тип | Значение |
|---|---|---|
| `id` | UUID | id вложения |
| `filename` | string | имя файла |
| `object_key` | string | ключ в MinIO (**не URL**; готовой ручки скачивания вложений тикета пока нет) |
| `content_type` | string? | MIME-тип |

### `display_status` — статус для UI (поле `display_status`)
Производный статус для цветов/ярлыков (вычисляется на бэке из `status`,
`publish_confirmed` и состояния публикаций).
| Значение | Когда | Цвет (по ТЗ) |
|---|---|---|
| `NO_DATE` | дата не подтверждена | бордовый |
| `IN_PROGRESS` | дата подтверждена, в работе | жёлтый |
| `AGREED` | согласован, публикация запланирована | — |
| `OVERDUE` | дата была, но публикацию отменили | красный |
| `ON_APPROVAL` | на согласовании (задел на будущее, пока не используется) | фиолетовый |
| `PUBLISHED` | опубликован | — |

### `status` — сырой статус тикета
`PENDING_REVIEW` (создан) → `IN_REVIEW` (в работе) → `REVISION` (на доработке) →
`AGREED` (согласован, в очереди) → `PUBLISHED`. Отдельно: `REJECTED` (отклонён).
`APPROVED` — легаси-статус старых закрытых тикетов.

---

## 1. Список тикетов

```
GET /auto-announce/tickets?status=<STATUS>&assignee_id=<UUID>
```

**Query (опц.):**
- `status` — фильтр по сырому статусу (`PENDING_REVIEW`, `IN_REVIEW`, …).
- `assignee_id` — фильтр по исполнителю.
- Без `status` отдаются все тикеты, **кроме** `APPROVED` и `REJECTED` (закрытые скрыты).

**Ответ `200`: `TicketListOut[]`**
| Поле | Тип | Значение |
|---|---|---|
| `id` | UUID | id тикета |
| `email_id` | UUID | id исходного письма |
| `status` | string | сырой статус (см. выше) |
| `assignee_id` | UUID? | id исполнителя |
| `assignee_name` | string? | имя исполнителя |
| `sender_email` | string? | отправитель исходного письма |
| `source` | string? | источник: `ServiceDesk / komek / other` |
| `title` | string? | заголовок анонса |
| `ai_summary` | string? | краткое содержание от ИИ |
| `created_at` | string? | создан (ISO, UTC) |
| `updated_at` | string? | изменён (ISO, UTC) |
| `display_status` | string? | статус для UI (см. выше) |
| `recommended_publish_at` | string? | рекомендованная дата (ISO, UTC) |
| `publish_at` | string? | подтверждённая дата публикации (ISO, UTC) |
| `publish_confirmed` | bool | подтверждена ли дата |
| `announcement_id` | UUID? | id анонса (после approve/publish) |
| `history` | HistoryOut[] | история действий |

---

## 2. Карточка тикета

```
GET /auto-announce/tickets/{ticket_id}
```

**Ответ `200`: `TicketDetailOut`** — всё из `TicketListOut` **плюс**:
| Поле | Тип | Значение |
|---|---|---|
| `body` | string? | HTML-тело анонса |
| `script_ru` | string? | скрипт оператора (рус.) |
| `script_kz` | string? | скрипт оператора (каз.) |
| `original_html_key` | string? | ключ оригинального HTML письма в MinIO |
| `attachments` | AttachmentOut[] | вложения исходного письма |

**Ошибки:** `404` — тикет не найден.

---

## 3. Назначить исполнителя

```
POST /auto-announce/tickets/{ticket_id}/assign
Body: { "assignee_id": "<UUID>", "assignee_name": "<string>" }
```
Назначает исполнителя, статус → `IN_REVIEW`.
**Ответ:** `{ "status": "assigned" }`
**Ошибки:** `400` «Ticket is already closed» (статус `APPROVED`/`REJECTED`).

## 4. Взять в работу

```
POST /auto-announce/tickets/{ticket_id}/take
Body: —   (исполнитель берётся из заголовков X-User-Id / X-User-Name)
```
Назначает текущего пользователя, статус → `IN_REVIEW`.
**Ответ:** `{ "status": "taken" }`
**Ошибки:** `400` «Ticket is already closed».

## 5. Вернуть на доработку

```
POST /auto-announce/tickets/{ticket_id}/revision
Body: { "assignee_id": "<UUID>", "assignee_name": "<string>", "comment": "<string>" }
```
Статус → `REVISION`, `comment` пишется в историю.
**Ответ:** `{ "status": "revision" }`
**Ошибки:** `400` «Ticket is already closed».

## 6. Редактировать контент

```
POST /auto-announce/tickets/{ticket_id}/edit
Body: { "title"?: string, "body"?: string, "script_ru"?: string, "script_kz"?: string }
```
Меняет только переданные поля; в историю пишется diff (`changes`).
**Ответ:** `{ "status": "edited", "changed_fields": ["title", ...] }`
**Ошибки:** `400` «Ticket is already closed».

## 7. Подтвердить дату публикации

```
POST /auto-announce/tickets/{ticket_id}/confirm-date
Body: { "publish_at": "2026-06-01T03:04:00+05:00" }
```
Ставит `publish_at` + `publish_confirmed = true`. **Передавать `publish_at` с поясом
`+05:00`** (Алматы), иначе время «уедет». Если `publish_at` не передан — берётся
`recommended_publish_at` (полночь Алматы).
**Ответ:** `{ "status": "confirmed", "publish_at": "<ISO>" }`
**Ошибки:** `400` «Ticket is already closed» (статус `APPROVED/REJECTED/PUBLISHED`),
`400` «No publish date provided and no recommendation available».

## 8. Согласовать (поставить в очередь публикации)

```
POST /auto-announce/tickets/{ticket_id}/approve
Body: —
```
Создаёт скрытый анонс + строку `SCHEDULED` в очереди публикаций, статус → `AGREED`.
Воркер опубликует в `publish_at`. **Тело не нужно** — дата берётся из тикета.
**Ответ:** `{ "status": "agreed", "announcement_id": "<UUID>", "publish_at": "<ISO>" }`
**Ошибки `400`:** «Ticket is already closed», «Ticket already has a scheduled
publication» (уже `AGREED`), «Cannot approve: title is empty», «Publish date must be
confirmed first».

## 9. Опубликовать сейчас

```
POST /auto-announce/tickets/{ticket_id}/publish
Body: —
```
Публикует немедленно, минуя расписание. Если тикет уже был согласован — публикует
существующую запланированную публикацию, иначе создаёт и публикует новую.
**Ответ:** `{ "status": "published", "announcement_id": "<UUID>" }`
**Ошибки `400`:** «Already published», «Ticket is rejected», «Cannot publish: title is empty».

## 10. Отменить запланированную публикацию

```
POST /auto-announce/tickets/{ticket_id}/cancel-publication
Body: —
```
Переводит `SCHEDULED`-публикацию в `CANCELED`, статус тикета → `IN_REVIEW`.
**Ответ:** `{ "status": "publication_canceled" }`
**Ошибки:** `400` «No scheduled publication to cancel».

## 11. Отклонить тикет

```
POST /auto-announce/tickets/{ticket_id}/reject
Body: { "comment"?: string }
```
Статус → `REJECTED`, исходное письмо → `DONE`.
**Ответ:** `{ "status": "rejected" }`
**Ошибки:** `400` «Ticket is already closed».

---

## Типовой сценарий на фронте

```
take/assign  →  (edit)  →  confirm-date  →  approve   →  [воркер публикует в publish_at]
                                          ↘  publish   →  опубликовать сразу
                          revision / reject — вернуть на доработку / отклонить
```

## Памятка
- Время в ответах — **UTC**; показывать в `Asia/Almaty` (+05).
- `confirm-date` — `publish_at` строго с поясом `+05:00`.
- Вложения есть только в карточке (`GET /{id}`), в списке — нет.
- У вложений тикета пока нет ссылки на скачивание (только `object_key`).
- `approve`/`publish`/`cancel-publication` тела не требуют.
