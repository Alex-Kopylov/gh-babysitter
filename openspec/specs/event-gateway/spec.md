# event-gateway Specification

## Purpose

Приём org-webhook'ов GitHub в единственной точке входа, нормализация payload до
кортежа `(repo, event, action, number)` и маршрутизация событий подписчикам —
всё в памяти одного процесса, без персистентного состояния. Архитектурный
контекст и компоненты — в [design.md](design.md).

## Requirements

### Requirement: Единственная точка входа POST /webhook

Сервис SHALL принимать события GitHub только через `POST /webhook`
(тип события — в заголовке `X-GitHub-Event`) и SHALL проверять HMAC-подпись
`X-Hub-Signature-256` до любого парсинга тела (см.
[authorization](../authorization/spec.md)).

#### Scenario: HMAC отсутствует или не совпал

- **WHEN** запрос приходит без подписи или с неверной подписью
- **THEN** ответ `401` с ошибкой авторизации; тело запроса не парсится

#### Scenario: Тело не JSON после успешной проверки

- **WHEN** подпись верна, но тело не парсится как JSON
- **THEN** ответ `400` с телом `{"detail":"Malformed JSON payload"}`

#### Scenario: JSON-значение не объект

- **WHEN** подпись верна, но JSON-значение — не объект (`null`, `[]`, `"str"`, `42`)
- **THEN** ответ `400` с телом `{"detail":"Payload must be a JSON object"}`

#### Scenario: Событие принято

- **WHEN** подпись верна и payload — JSON-объект
- **THEN** ответ `202` с телом `{"matched":3,"delivered":2,"dropped":1}` —
  фактическими счётчиками fan-out

### Requirement: Нормализация payload

Сервис SHALL сводить каждый payload к кортежу `(repo, event, action, number)`;
матчинг MUST работать только с этим кортежем, весь разнобой GitHub-payload'ов
изолирован в одном маленьком слое. `repo` всегда берётся из
`payload.repository.full_name`, `action` — из `payload.action` (может
отсутствовать).

#### Scenario: События с номером из issue

- **WHEN** приходит событие `issues` или `issue_comment`
- **THEN** `number` извлекается из `payload.issue.number`; комментарии к **PR**
  тоже приходят как `issue_comment`, не `pull_request`

#### Scenario: События с номером из pull request

- **WHEN** приходит событие `pull_request` или `pull_request_review`
- **THEN** `number` извлекается из `payload.pull_request.number`

#### Scenario: События без номера

- **WHEN** приходит прочее событие (`release`, ...)
- **THEN** `number = NULL`; подписка на такие события возможна только без
  фильтра по номеру

### Requirement: Матчинг подписок

Matcher SHALL выбирать подписки по равенству `repo` и `event`; `action` и
`number` SHALL учитываться только если заданы в подписке. На целевом масштабе
это микросекунды, rule engine MUST NOT появляться.

#### Scenario: Фильтр без action и number

- **WHEN** подписка `('org/api', 'issues', NULL, NULL)` и приходит любое
  issues-событие репозитория `org/api`
- **THEN** подписка совпадает

#### Scenario: Фильтр по номеру

- **WHEN** подписка `('org/api', 'issues', NULL, 42)` и приходит событие
  issue #7
- **THEN** подписка не совпадает; совпадает только issue #42

#### Scenario: Перекрывающиеся фильтры одного соединения

- **WHEN** несколько фильтров одного соединения совпали с одним событием
- **THEN** событие доставляется этому соединению один раз

### Requirement: Реестр в памяти без персистентности

Сервис MUST NOT иметь персистентного состояния: подписка — атрибут открытого
SSE-соединения, реестр живёт в памяти процесса. События MUST NOT записываться
на диск.

```
Filter = (repo, event, action | NULL, number | NULL)  # что клиент объявил при подключении
Subscriber = (bounded_queue, dropped)                  # очередь соединения и счётчик потерь

connections: conn_id → (github_login, [Filter, ...], Subscriber)
index:       (repo, event) → {conn_id, ...}            # обратный индекс для матчинга
```

#### Scenario: Соединение закрылось

- **WHEN** SSE-соединение закрывается по любой причине
- **THEN** его записи удаляются из реестра немедленно

#### Scenario: Процесс перезапустился

- **WHEN** процесс сервиса перезапускается
- **THEN** соединения умерли вместе с ним и реестр пуст по построению:
  восстанавливать нечего, клиенты переподключатся и переобъявятся сами
  ([event-delivery](../event-delivery/spec.md))

### Requirement: Неблокирующий fan-out

`Subscriber.offer(envelope)` MUST NOT блокировать ingress: успешное добавление
в bounded-очередь считается доставкой, переполнение — потерей. `take_dropped()`
SHALL забирать и обнулять счётчик потерь; стрим превращает его в адресное
событие `lag` для отставшего клиента
([event-delivery](../event-delivery/spec.md)).

#### Scenario: Очередь свободна

- **WHEN** событие предлагается подписчику с местом в очереди
- **THEN** событие добавлено, `offer` возвращает `true`, событие учтено в
  `delivered`

#### Scenario: Очередь полна

- **WHEN** событие предлагается подписчику с полной очередью
- **THEN** событие потеряно, счётчик `dropped` увеличен, `offer` возвращает
  `false`; потеря адресна и не влияет на других подписчиков

### Requirement: Поверхность API

Сервис SHALL предоставлять ровно два endpoint'а: `POST /webhook` для GitHub и
`GET /events/stream?repo=&events=&number=&action=` для CLI. CRUD-endpoint'ы для
подписок MUST NOT существовать: подписка неотделима от соединения, объявлять её
отдельно от стрима некому и незачем.

#### Scenario: Подключение к стриму

- **WHEN** CLI подключается к `GET /events/stream`
- **THEN** подписки объявлены параметрами запроса, требуется
  `Authorization: Bearer <gh-token>`
  ([authorization](../authorization/spec.md)), объявление подписки и получение
  потока — одно действие
