# Фронтенд: страница обработки анонса — публикация и расписание

Документ для фронтенд-команды по блоку «Публикация, расписание, уведомления».
Описывает, что готово на бэкенде, какие эндпоинты есть, их форматы, статусы и поток.

> Базовый префикс. Сервис смонтирован под `root_path=/announce`. В проде пути выглядят как
> `/announce/auto-announce/tickets/...`. Ниже пути даны без `/announce` (как их видит само
> приложение). Подставляй базовый URL вашего окружения.

> Заголовки. Действия принимают идентичность пользователя через заголовки
> `X-User-Id`, `X-User-Name` (имя — URL-encoded). Создание/чтение анонсов требует `X-User-Id`.
> Время везде — ISO-8601 **с таймзоной** (Asia/Almaty, `+05:00`). Отправляй и парси с оффсетом.

---

## 1. Что сделано (суть)

- Анонс из письма попадает в **тикет на ревью**. На странице обработки тренер правит контент,
  **подтверждает дату публикации**, отправляет на **«Согласовать»** — анонс создаётся, но
  **скрыт** и запланирован на выбранное время.
- В назначенное время **фоновый воркер сам публикует** анонс (делает его видимым) и шлёт
  событие операторам. Можно опубликовать **сразу** кнопкой «Опубликовать».
- Есть **повторная отправка** уже опубликованного анонса (re-send) и **история публикаций**
  (первичная, повторные, отменённые), перенос времени и отмена запланированной публикации.
- Для бейджей статуса бэкенд отдаёт готовый `display_status`.

---

## 2. Статусы

### 2.1. `display_status` тикета → бейдж/цвет (бери это поле, не собирай сам)
| `display_status` | Значение | Цвет по ТЗ |
|---|---|---|
| `NO_DATE` | дата не подтверждена | бордовый |
| `IN_PROGRESS` | в работе (взят/на доработке) | жёлтый |
| `AGREED` | согласован, публикация запланирована | (зелёный/синий) |
| `OVERDUE` | была дата, публикацию отменили — просрочено | красный |
| `PUBLISHED` | опубликован | (серый/зелёный) |
| `ON_APPROVAL` | на согласовании у руководителя | фиолетовый — **зарезервировано, пока не используется** (блок ролей) |

### 2.2. Технические статусы (для отладки, не для бейджей напрямую)
- Тикет `status`: `PENDING_REVIEW | IN_REVIEW | REVISION | AGREED | PUBLISHED | APPROVED | REJECTED`
  (`APPROVED`/`REJECTED` — легаси/закрытые; для UI ориентируйся на `display_status`).
- Публикация `status`: `SCHEDULED | PUBLISHED | CANCELED`; `kind`: `PRIMARY | REPEAT`.

---

## 3. Объект тикета (GET)

### `GET /auto-announce/tickets` — список
Query: `?status=<TicketStatus>` (опц.), `?assignee_id=<uuid>` (опц.). Без `status` скрывает закрытые (APPROVED/REJECTED).
Возвращает массив объектов:
```jsonc
{
  "id": "uuid",
  "email_id": "uuid",
  "status": "AGREED",
  "assignee_id": "uuid|null",
  "assignee_name": "string|null",
  "sender_email": "string|null",
  "source": "ServiceDesk|komek|...|null",
  "title": "string|null",
  "ai_summary": "string|null",
  "created_at": "ISO|null",
  "updated_at": "ISO|null",
  "display_status": "AGREED",                 // ← бейдж
  "recommended_publish_at": "ISO|null",        // дата, предложенная ИИ (00:00 Алматы)
  "publish_at": "ISO|null",                    // подтверждённая/выбранная дата
  "publish_confirmed": false,                  // подтверждена ли дата
  "announcement_id": "uuid|null",              // появляется после «Согласовать»/«Опубликовать»
  "history": [ /* см. ниже */ ]
}
```

### `GET /auto-announce/tickets/{id}` — деталь
Те же поля + контент и вложения:
```jsonc
{
  "...": "все поля списка",
  "body": "string|null",                 // тело анонса (HTML/markdown)
  "script_ru": "string|null",
  "script_kz": "string|null",
  "original_html_key": "string|null",    // ключ оригинального HTML письма в MinIO
  "attachments": [
    { "id": "uuid", "filename": "string", "object_key": "string", "content_type": "string|null" }
  ]
}
```

### `history[]` (элемент)
```jsonc
{ "action": "CREATED|ASSIGNED|TAKEN|SENT_TO_REVISION|EDITED|APPROVED|SCHEDULED|RESCHEDULED|PUBLICATION_CANCELED|PUBLISHED|REJECTED",
  "actor_id": "uuid|null", "actor_name": "string|null",
  "comment": "string|null", "changes": { }, "created_at": "ISO|null" }
```

---

## 4. Действия на странице (поток)

Порядок на «странице обработки анонса»:

1. **Редактирование контента** — `POST /auto-announce/tickets/{id}/edit`
   body: `{ "title?": str, "body?": str, "script_ru?": str, "script_kz?": str }` → `{status, changed_fields[]}`
2. **Взять в работу / назначить**
   - `POST /auto-announce/tickets/{id}/take` → `{status:"taken"}`
   - `POST /auto-announce/tickets/{id}/assign` body `{assignee_id, assignee_name}`
3. **Подтвердить дату публикации** — `POST /auto-announce/tickets/{id}/confirm-date`
   body: `{ "publish_at": "ISO с оффсетом" }` — или `{}` чтобы взять `recommended_publish_at`.
   → `{ "status": "confirmed", "publish_at": "ISO" }`. Ставит `publish_confirmed=true`.
   **Без этого шага `approve` вернёт 400.** (Это блок «Подтвердить/Изменить» из ТЗ.)
4. **Согласовать (запланировать)** — `POST /auto-announce/tickets/{id}/approve`
   Требует подтверждённую дату. Создаёт **скрытый** анонс + планирует публикацию.
   → `{ "status": "agreed", "announcement_id": "uuid", "publish_at": "ISO" }`. Тикет → `AGREED`.
5. **Опубликовать сейчас** — `POST /auto-announce/tickets/{id}/publish`
   Публикует немедленно (если уже согласован — публикует ту же запланированную запись, без дубля).
   → `{ "status": "published", "announcement_id": "uuid" }`. Тикет → `PUBLISHED`, анонс становится видимым.
6. **Отменить запланированную публикацию** — `POST /auto-announce/tickets/{id}/cancel-publication`
   → `{ "status": "publication_canceled" }`. Анонс остаётся скрытым, тикет → `IN_REVIEW`, бейдж → `OVERDUE`.
7. **Вернуть на доработку** — `POST /auto-announce/tickets/{id}/revision` body `{assignee_id, assignee_name, comment}`.
8. **Отклонить** — `POST /auto-announce/tickets/{id}/reject` body `{comment?}`.

Коды ошибок: `400` — нарушение потока (нет подтверждённой даты для approve; повторный approve по уже
согласованному; тикет закрыт), `404` — нет тикета/публикации.

---

## 5. История публикаций и повторная отправка (на уровне анонса)

После того как у тикета есть `announcement_id`:

- **История** — `GET /announcements/{announcement_id}/publications`
  → массив (по возрастанию `created_at`):
  ```jsonc
  { "id":"uuid","kind":"PRIMARY|REPEAT","publish_at":"ISO|null","status":"SCHEDULED|PUBLISHED|CANCELED",
    "executed_at":"ISO|null","canceled_at":"ISO|null","actor_name":"string|null","created_at":"ISO|null" }
  ```
- **Повторная отправка** — `POST /announcements/{announcement_id}/republish`
  body `{ "publish_at": "ISO" }`. Будущее время → `{status:"scheduled", publication_id}`;
  прошедшее/сейчас → публикует сразу `{status:"republished", publication_id}`.
- **Перенести время** (только `SCHEDULED`) — `PATCH /publications/{pub_id}` body `{ "publish_at": "ISO" }`.
- **Отменить строку** (только `SCHEDULED`) — `POST /publications/{pub_id}/cancel`.

---

## 6. Объект анонса (для предпросмотра «как у операторов»)

`GET /announcements/{id}` (нужен `X-User-Id`) возвращает анонс. Новое поле:
- `published_at: ISO|null` — момент фактической публикации (заполняется при публикации).
- `is_hidden: bool` — запланированный/несогласованный анонс `true`; после публикации `false`.

---

## 7. События операторам (SSE/webhook)

В момент **фактической** публикации (по расписанию или «Опубликовать сейчас») бэкенд шлёт событие:
- `NEW_ANNOUNCEMENT` — первичная публикация;
- `REPEAT_ANNOUNCEMENT` — повторная отправка («Напоминание: …»).
Событие приходит **в момент публикации**, не в момент планирования. Если у анонса
запланирована дата в будущем — пока ничего не приходит и анонс не виден операторам.

---

## 8. Что фронту нужно добавить (важно)

Текущая страница `static/tickets.html` **не покрывает новый поток**. Минимум для UI:
- **Блок даты публикации**: показать `recommended_publish_at` как предложение, дать выбрать своё
  `publish_at` (дата+время), кнопки **«Подтвердить» / «Изменить»** → вызывают `confirm-date`.
  Пока `publish_confirmed=false` — кнопки «Согласовать»/«Опубликовать» блокировать (иначе 400).
- Развести кнопки **«Согласовать»** (`approve`, планирует) и **«Опубликовать»** (`publish`, сразу).
- **«Отменить публикацию»** (`cancel-publication`) для согласованных.
- **Блок «История публикаций»** на основе `/announcements/{id}/publications` + кнопка
  **«Повторная отправка»** (`republish`) с выбором времени, перенос/отмена строк.
- Маппинг **`display_status` → цвет** по таблице из раздела 2.1.
- Отрисовать новые статусы `AGREED`/`PUBLISHED`/`OVERDUE` (старый UI считал закрытыми только
  `APPROVED`/`REJECTED`).

---

## 9. Вне области (бэкенд пока не делает)

Роли/права (тренер vs руководитель) и статус `ON_APPROVAL`; выбор аудитории (повтор всегда всем);
база знаний; архив; автосохранение. Это отдельные блоки ТЗ.
