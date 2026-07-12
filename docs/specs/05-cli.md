# CLI

Тонкий клиент к [control plane](01-architecture.md#api). Python + Typer. Аутентификация — токен из `gh auth token`, пользователь не делает ничего ([auth](03-auth.md#аутентификация-cli)).

## Дистрибуция

Упаковка как **gh-расширение** (паттерн [cli/gh-webhook](04-github-webhook.md#разбор-cligh-webhook)):

```
gh extension install my-org/gh-babysitter
gh babysitter --help
```

Установка и обновления через штатный механизм `gh`, команда живёт в привычном пространстве `gh <...>`. Расширения могут быть скриптами, не только Go-бинарниками, так что Python не помеха (разработчики — целевая аудитория, интерпретатор у них есть).

## Команды

### `subscribe` / `unsubscribe` / `list`

```
gh babysitter subscribe -R org/api -E issues              # все issues
gh babysitter subscribe -R org/api -E issues -n 42        # только issue #42
gh babysitter subscribe -R org/web -E pull_request -n 24  # только PR #24
gh babysitter subscribe -R org/api -E issues,pull_request # несколько типов разом
gh babysitter list
gh babysitter unsubscribe <id>
```

`subscribe` пишет желаемую подписку в локальный конфиг **и** сразу upsert-ит её на сервер (`POST /subscriptions`) — сервер в этот момент проверяет доступ к репозиторию и отклоняет чужие приватные репо.

### `listen`

```
gh babysitter listen                 # JSON lines в stdout
gh babysitter listen --format pretty # человекочитаемо
gh babysitter listen | jq 'select(.event == "issues") | .payload.issue.title'
```

При старте переобъявляет все подписки из локального конфига (идемпотентный upsert), затем держит SSE-соединение и печатает события ([delivery](02-delivery.md#формат-события)). Реконнект с бэкоффом при обрыве; каждый реконнект снова переобъявляет подписки — так работает lease ([delivery](02-delivery.md#lease--heartbeat)).

### `setup` (админская)

Одноразовая команда org-админа — создаёт/обновляет org-webhook с [allowlist](04-github-webhook.md#статический-allowlist) и секретом, используя собственный токен админа. Подробности — в [github-webhook](04-github-webhook.md#настройка).

## Конвенции флагов

Зеркалим `gh` и `gh-webhook`, чтобы не заставлять людей учить новое:

| Флаг | Смысл |
|---|---|
| `-R, --repo org/name` | Репозиторий |
| `-E, --events a,b,c` | Типы событий через запятую (из [меню](04-github-webhook.md#статический-allowlist)) |
| `-n, --number N` | Конкретный issue/PR |
| `--action opened` | Фильтр по action (опционально) |

## Локальный конфиг подписок

`~/.config/gh-babysitter/subscriptions.yaml` — источник истины о **желаемых** подписках разработчика:

```yaml
subscriptions:
  - repo: org/api
    event: issues
  - repo: org/api
    event: issues
    number: 42
  - repo: org/web
    event: pull_request
    number: 24
```

Серверная таблица — лишь короткоживущая проекция этого файла ([delivery](02-delivery.md#lease--heartbeat)): `listen` синхронизирует его на сервер при каждом (пере)подключении. Поэтому «перестал слушать → подписки истекли → снова запустил `listen` → всё восстановилось» — без каких-либо действий пользователя.
