# Дизайн: публикация, расписание и уведомления

Дата: 2026-05-29
Статус: согласован, готов к планированию реализации

## Цель

Реализовать блок «Публикация, расписание, уведомления» страницы обработки анонса:

- дата/время публикации (`publish_at`): рекомендация ИИ + ручной override тренером;
- блок подтверждения даты («без подтверждения не отправляется»);
- «Согласован» → автопубликация в назначенное время;
- повторная отправка (re-send) с настройкой времени повтора;
- история публикаций (первичная, повторные, изменение времени, отмена, автор).

Аудитория (выбор групп/подразделений) **вне области** этого блока — повтор всегда всем.
Проверка ролей (тренер vs руководитель) **вне области** — отдельный блок.

## Ключевые архитектурные решения

1. **Планировщик — внутренний asyncio-воркер** в FastAPI `lifespan`, по образцу
   `outlook_worker.py`. Деплой однонодовый (`docker-compose`, один контейнер), поэтому
   риска двойной публикации нет; `FOR UPDATE SKIP LOCKED` добавляет страховку на будущее.
2. **`publish_at` — не одна колонка, а таблица** `announcement_publications`. Она же
   очередь воркера и журнал «История публикаций» (первичная + повторные + отмены).
3. **`Announcement` создаётся в момент «Согласован»** (скрытым), а не в момент публикации.
   Тогда у воркера один источник, а повторы/история цепляются к существующему анонсу.
4. **Единый путь публикации** — функция `execute_publication(publication_id)`.
   «Опубликовать» вызывает её синхронно (`publish_at=now()`), воркер — для будущих по
   расписанию.

## Модель данных

### Новая таблица `announcement_publications`

```
id                uuid pk
announcement_id   uuid fk → announcements (ON DELETE CASCADE)
kind              enum PRIMARY | REPEAT
publish_at        timestamptz                ← воркер сканирует
status            enum SCHEDULED | PUBLISHED | CANCELED
actor_id          uuid    nullable           ← кто запланировал
actor_name        varchar nullable
created_at        timestamptz
executed_at       timestamptz nullable        ← когда реально опубликовано
canceled_at       timestamptz nullable
```

Закрывает «История публикаций»: строки `kind` = первичная/повторные, `CANCELED` = отмена,
`PUBLISHED`+`executed_at` = факт исполнения.

### Изменения в `review_tickets`

```
recommended_publish_at  timestamptz nullable  ← предложение ИИ (00:00 Asia/Almaty)
publish_at              timestamptz nullable  ← подтверждённое/ручное время тренера
publish_confirmed       bool default false    ← «без подтверждения не отправляется»
```

Расширяем `TicketStatusEnum`: добавляем `AGREED` (согласован, публикация запланирована) и
`PUBLISHED` (опубликован). Существующие `APPROVED/REJECTED` сохраняются.
В блоке ролей позже добавится `ON_APPROVAL`.

### Изменения в `announcements`

```
published_at  timestamptz nullable   ← фиксация факта публикации
```

Видимость по-прежнему через `is_hidden`: запланированный/согласованный анонс
`is_hidden=True`, публикация флипает в `False`.

### Изменения в `ai_emails` (AIEmail)

```
recommended_publish_date  ← что вернул GPT (для аудита/отладки)
```

### Миграции

Enum’ы лежат в схеме `cchub_announcements`. Новые значения существующих enum добавляются
через `ALTER TYPE ... ADD VALUE` в Alembic-миграции.

## Рекомендуемая дата

GPT возвращает новое поле `recommended_publish_date` (nullable) в structured output
(`dependencies/gpt.py`, json_schema). На нашей стороне парсим в дату со временем
00:00 Asia/Almaty; при null/ошибке — fallback на день поступления письма
(`received_at`). Пишем в `review_tickets.recommended_publish_at`.

Тренер может перекрыть рекомендацию вручную — задать любую конкретную дату **и время**
(не только 00:00) через подтверждение даты.

## Жизненный цикл и переходы

> Важно: семантика существующего `approve` **меняется** — он больше не публикует мгновенно.

1. **Подтверждение даты** — `POST /tickets/{id}/confirm-date`
   - тело: `publish_at` (опц.; если не задано — берётся `recommended_publish_at`)
   - ставит `publish_at` + `publish_confirmed=true`; ничего не публикует и не создаёт
   - без этого шага «Согласовать»/«Опубликовать» заблокированы

2. **«Согласовать»** — `POST /tickets/{id}/approve` (репокрыт)
   - требует `publish_confirmed=true`
   - создаёт `Announcement` (скрытый, поля из тикета)
   - создаёт `announcement_publications`: `kind=PRIMARY, status=SCHEDULED, publish_at=ticket.publish_at`
   - тикет → `AGREED`; письмо остаётся (не `DONE`, пока не опубликован)
   - `ReviewHistory(action=APPROVED)`

3. **«Опубликовать»** — `POST /tickets/{id}/publish`
   - то же, что «Согласовать», но `publish_at=now()` и синхронный вызов
     `execute_publication(...)`
   - тикет → `PUBLISHED`

4. **Воркер** — для запланированных на будущее: `SCHEDULED && publish_at<=now` →
   `execute_publication(...)`.

5. **Отмена публикации** — `POST /tickets/{id}/cancel-publication`
   - если публикация ещё `SCHEDULED` → `CANCELED`, анонс остаётся скрытым, тикет
     возвращается в работу
   - даёт UI-статус «просрочено»: дата была, отправку отменили, активной нет

### `execute_publication(publication_id, session)` — единая функция

- PRIMARY: `is_hidden=False`, `announcement.published_at=now()`
- REPEAT: `is_hidden` не трогаем (анонс уже видим)
- публикация → `PUBLISHED` + `executed_at`
- тикет → `PUBLISHED`, письмо → `DONE` (для PRIMARY)
- уведомление операторам:
  - PRIMARY → `event_type="NEW_ANNOUNCEMENT"`
  - REPEAT → `event_type="REPEAT_ANNOUNCEMENT"` («Напоминание: …»)
- запись в историю

## Воркер-планировщик

Новый `workers/publish_worker.py`, запуск в `lifespan`:
`asyncio.create_task(run_publish_scheduler())`.

```
run_publish_scheduler():
  while True:
      try:
          async with async_session() as session:
              now = datetime.now(Asia/Almaty)
              rows = SELECT * FROM announcement_publications
                     WHERE status='SCHEDULED' AND publish_at <= now
                     ORDER BY publish_at
                     FOR UPDATE SKIP LOCKED
              for pub in rows:
                  try:
                      await execute_publication(pub.id, session)
                      await session.commit()
                  except Exception:
                      await session.rollback()
                      logger.exception(...)
      except CancelledError:
          raise
      except Exception:
          logger.exception(...)
      await asyncio.sleep(POLL_INTERVAL)   # 60 сек, настройка PUBLISH_POLL_INTERVAL
```

Решения:
- `FOR UPDATE SKIP LOCKED` — безопасность относительно синхронной «Опубликовать» и
  будущих реплик.
- Таймзона — всё в `Asia/Almaty`, `publish_at` tz-aware.
- Изоляция ошибок — публикация по одной строке со своим commit.
- Пропущенные за время простоя (`publish_at` в прошлом) публикуются сразу при старте —
  желаемое поведение.

## Повторная отправка

`POST /announcements/{id}/republish`
- тело: `publish_at` (может быть `now()`)
- создаёт `announcement_publications`: `kind=REPEAT, status=SCHEDULED`
- исполнение через ту же `execute_publication` (REPEAT-ветка)

## История, изменение времени, отмена

- `GET /announcements/{id}/publications` — все строки по анонсу, сортировка по `created_at`;
  поля: `kind`, `publish_at`, `status`, `executed_at`/`canceled_at`, `actor_name`.
- `PATCH /publications/{pub_id}` — меняет `publish_at` (только `SCHEDULED`).
- `POST /publications/{pub_id}/cancel` — `status=CANCELED` + `canceled_at`.
- Автор и факт изменения времени/отмены фиксируются в `ReviewHistory`
  (action `RESCHEDULED` / `PUBLICATION_CANCELED`).

YAGNI: «изменение времени» не версионируем отдельной таблицей — текущее `publish_at` в
строке + запись в `ReviewHistory`.

## Статусы для UI (производные, не хранятся)

| Статус (цвет)        | Условие                                                        |
|----------------------|----------------------------------------------------------------|
| нет даты (бордовый)  | `publish_confirmed=false`                                      |
| в работе (жёлтый)    | тикет `IN_REVIEW` / `REVISION`                                 |
| на согласовании (фиол.) | тикет `ON_APPROVAL` (появится в блоке ролей)                |
| согласован           | тикет `AGREED` + есть `SCHEDULED`-публикация                   |
| просрочено (красный) | была публикация, но `CANCELED`, активной `SCHEDULED` нет       |
| опубликован          | тикет `PUBLISHED`                                              |

Отдаём вычисленный `display_status` в `TicketDetailOut` / `TicketListOut`.

## Сводка API

- `POST /tickets/{id}/confirm-date` — подтвердить/изменить дату
- `POST /tickets/{id}/approve` — *репокрыт*: согласовать → запланировать
- `POST /tickets/{id}/publish` — опубликовать сейчас
- `POST /tickets/{id}/cancel-publication` — отменить запланированную
- `GET /announcements/{id}/publications` — история публикаций
- `POST /announcements/{id}/republish` — повторная отправка
- `PATCH /publications/{pub_id}` — изменить время
- `POST /publications/{pub_id}/cancel` — отменить строку

## Тестирование (TDD при реализации)

- `execute_publication`: PRIMARY флипает `is_hidden`, REPEAT — нет; обе пишут
  `executed_at` и корректный `event_type`.
- Планировщик: берёт только `SCHEDULED && publish_at<=now`; `SKIP LOCKED` не публикует
  дважды; падение одной строки не валит остальные.
- Блокировка «Согласовать/Опубликовать» без `publish_confirmed`.
- Отмена `SCHEDULED` → `CANCELED`; повторный тик воркера её не трогает.
- Извлечение `recommended_publish_date` из GPT + fallback на день поступления.
- Производные `display_status` по всем веткам.
- Время в тестах инжектируется (параметр `now` / провайдер), без зависимости от часов.

## Вне области (последующие блоки)

- Роли и права (тренер / руководитель), статус `ON_APPROVAL`, «Отправить на согласование».
- Выбор аудитории / таргетинг.
- База знаний и её версии.
- Архив, автосохранение черновика.
