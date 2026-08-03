# event-gateway — архитектура и контекст

Один процесс FastAPI, четыре логических компонента. Общая схема — в
[README](../../../README.md#solution).

## Компоненты

- **Ingress** — единственный `POST /webhook`, на который настроен org-level
  webhook GitHub (см. [github-webhook](../github-webhook/spec.md)). Проверяет
  HMAC-подпись `X-Hub-Signature-256` (см.
  [authorization](../authorization/spec.md)), нормализует payload и передаёт
  событие дальше.
- **Matcher** — по нормализованному событию находит все подходящие подписки в
  реестре в памяти. На целевом масштабе это микросекунды, никакой rule engine
  не нужен.
- **Dispatcher** — fan-out: пересекает совпавшие подписки с множеством открытых
  SSE-соединений и пушит событие каждому. Семантика доставки — в
  [event-delivery](../event-delivery/spec.md).
- **Control plane** — единственный стрим-endpoint: подписки объявляются
  параметрами подключения, авторизация через GitHub-токен пользователя (см.
  [authorization](../authorization/spec.md)). Потребитель — [CLI](../cli/spec.md).

## Поток события

1. GitHub присылает `POST /webhook` (тип события — в заголовке `X-GitHub-Event`).
2. Ingress сверяет HMAC-подпись; мусор отбрасывается с `401`.
3. Нормализация: из сырого payload извлекается кортеж `(repo, event, action, number)`.
4. Matcher выбирает подписки: совпадение по `repo` и `event`, `action`/`number` —
   только если заданы в подписке.
5. Dispatcher предлагает событие bounded-очереди каждого совпавшего
   `Subscriber`; успешное добавление считается доставкой, переполнение — потерей.
6. Событие забывается. Никакой записи на диск.

## Примеры фильтров

| Хочу | Фильтр |
|---|---|
| Все issues в `org/api` | `('org/api', 'issues', NULL, NULL)` |
| Все PR в `org/web` | `('org/web', 'pull_request', NULL, NULL)` |
| Только issue #42 | `('org/api', 'issues', NULL, 42)` |
| Только PR #24 | `('org/api', 'pull_request', NULL, 24)` |

## Масштаб и производительность

Целевой масштаб: 10 репозиториев × 100 разработчиков × 5 подписок =
**5000 фильтров** в памяти. Выборка по dict-индексу `(repo, event)` —
микросекунды на событие. Тысячи одновременных висящих SSE-соединений для одного
uvicorn-процесса — не нагрузка. Узких мест на этом масштабе нет, поэтому: один
процесс, всё в памяти, без БД, очередей и внешних зависимостей.

## Стек

- Сервис: Python, FastAPI, `sse-starlette` (стрим), `httpx2` (проверка токенов
  у GitHub API), locust (нагрузочное тестирование). БД нет — реестр подписок в
  памяти.
- CLI: Python, Typer; упаковка как gh-расширение — см. [cli](../cli/spec.md).
- Шаблон репозитория: `uvx copier copy gh:Alex-Kopylov/ai-ready-modern-python-template project-name`
- LICENSE: TBD
