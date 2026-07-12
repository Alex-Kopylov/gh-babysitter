# Архитектура и модель данных

Один процесс FastAPI, четыре логических компонента. Общая схема — в [README](../../README.md#решение).

## Компоненты

- **Ingress** — единственный `POST /webhook`, на который настроен org-level webhook GitHub (см. [github-webhook](04-github-webhook.md)). Проверяет HMAC-подпись `X-Hub-Signature-256` (см. [auth](03-auth.md#вход-от-github-hmac)), нормализует payload и передаёт событие дальше.
- **Matcher** — по нормализованному событию находит все подходящие подписки в SQLite. На целевом масштабе это микросекунды, никакой rule engine не нужен.
- **Dispatcher** — fan-out: пересекает совпавшие подписки с множеством открытых SSE-соединений и пушит событие каждому. Семантика доставки — в [delivery](02-delivery.md).
- **Control plane** — CRUD подписок и стрим-endpoint, авторизация через GitHub-токен пользователя (см. [auth](03-auth.md)). Потребитель — [CLI](05-cli.md).

## Поток события

1. GitHub присылает `POST /webhook` (тип события — в заголовке `X-GitHub-Event`).
2. Ingress сверяет HMAC-подпись; мусор отбрасывается с `401`.
3. Нормализация: из сырого payload извлекается кортеж `(repo, event, action, number)`.
4. Matcher выбирает подписки: совпадение по `repo` и `event`, `action`/`number` — только если заданы в подписке.
5. Dispatcher пушит событие в открытые SSE-соединения владельцев совпавших подписок.
6. Событие забывается. Никакой записи на диск.

## Нормализация payload

Матчинг работает только с кортежем `(repo, event, action, number)` — весь разнобой GitHub-payload'ов изолирован в одном маленьком слое:

| Событие | Откуда берётся `number` | Нюанс |
|---|---|---|
| `issues` | `payload.issue.number` | |
| `pull_request` | `payload.pull_request.number` | |
| `issue_comment` | `payload.issue.number` | Комментарии к **PR** тоже приходят как `issue_comment`, не `pull_request` |
| `pull_request_review` | `payload.pull_request.number` | |
| прочие (`release`, ...) | `NULL` | Подписка на них возможна только без фильтра по номеру |

`repo` всегда берётся из `payload.repository.full_name`, `action` — из `payload.action` (может отсутствовать).

## Модель данных

Единственная таблица — весь state сервиса:

```sql
CREATE TABLE subscriptions (
  id            INTEGER PRIMARY KEY,
  github_login  TEXT      NOT NULL,  -- владелец подписки
  repo          TEXT      NOT NULL,  -- 'org/name'
  event         TEXT      NOT NULL,  -- 'issues', 'pull_request', ...
  action        TEXT,                -- NULL = любой ('opened', 'closed', ...)
  number        INTEGER,             -- NULL = все, иначе конкретный issue/PR
  expires_at    TIMESTAMP NOT NULL   -- lease, см. delivery
);
```

Примеры из постановки задачи:

| Хочу | Строка |
|---|---|
| Все issues в `org/api` | `(login, 'org/api', 'issues', NULL, NULL)` |
| Все PR в `org/web` | `(login, 'org/web', 'pull_request', NULL, NULL)` |
| Только issue #42 | `(login, 'org/api', 'issues', NULL, 42)` |
| Только PR #24 | `(login, 'org/api', 'pull_request', NULL, 24)` |

Семантика `expires_at` (короткий TTL, продление открытым соединением, фоновая чистка) описана в [delivery](02-delivery.md#lease--heartbeat).

## API

| Метод и путь | Кто зовёт | Назначение |
|---|---|---|
| `POST /webhook` | GitHub | Приём событий, HMAC |
| `POST /subscriptions` | CLI | Создать подписку (идемпотентный upsert) |
| `GET /subscriptions` | CLI | Мои подписки |
| `DELETE /subscriptions/{id}` | CLI | Удалить подписку |
| `GET /events/stream` | CLI | SSE-поток событий по моим подпискам |

Все `/subscriptions*` и `/events/stream` требуют `Authorization: Bearer <gh-token>` — см. [auth](03-auth.md).

## Масштаб и производительность

Целевой масштаб: 10 репозиториев × 100 разработчиков × 5 подписок = **5000 строк**. Выборка по индексу `(repo, event)` — микросекунды на событие. Тысячи одновременных висящих SSE-соединений для одного uvicorn-процесса — не нагрузка. Узких мест на этом масштабе нет, поэтому: один процесс, SQLite, без очередей и внешних зависимостей.

## Стек

- Сервис: Python, FastAPI, `sse-starlette` (стрим), `httpx` (проверка токенов у GitHub API), SQLite (stdlib `sqlite3` или `aiosqlite`).
- CLI: Python, Typer; упаковка как gh-расширение — см. [cli](05-cli.md#дистрибуция).
