# github-webhook Specification

## Purpose

Конфигурация на стороне GitHub: один org-level webhook со статическим allowlist
типов событий и одноразовой админской настройкой. Разбор альтернатив
(динамическая синхронизация, `cli/gh-webhook`, GitHub App) — в
[design.md](design.md).

## Requirements

### Requirement: Один org-level webhook

Хук SHALL вешаться не на каждый репозиторий, а **один на всю организацию**.
Настраивает org-админ один раз; рядовым разработчикам права на хуки MUST NOT
требоваться вообще.

#### Scenario: Новый репозиторий в организации

- **WHEN** в организации появляется новый репозиторий
- **THEN** он подхватывается автоматически — 10 репозиториев покрываются одной
  настройкой

#### Scenario: Фильтрация по репозиторию

- **WHEN** приходит событие любого репозитория организации
- **THEN** payload содержит `repository.full_name`, и фильтрация по репо
  остаётся у сервиса ([event-gateway](../event-gateway/spec.md))

### Requirement: Статический allowlist типов событий

Поле `events` в конфиге хука SHALL быть статическим allowlist поддерживаемых
типов; меню v1.0:

```
issues, pull_request, issue_comment, pull_request_review, release
```

Динамическая синхронизация `events` под текущие подписки MUST NOT выполняться
(разбор — в [design.md](design.md)). Конфиг хука самодокументируется: он и есть
меню того, на что можно подписаться.

#### Scenario: Шум CI отсечён

- **WHEN** в репозитории идут прогоны CI (`check_run`, `check_suite`, `status`,
  `workflow_run`)
- **THEN** эти события не отправляются сервису: их нет в allowlist

#### Scenario: Расширение меню

- **WHEN** нужен новый тип события
- **THEN** админ однократно обновляет хук (или повторно запускает `setup`)

### Requirement: Одноразовая настройка командой setup

Команда для org-админа SHALL его **собственным** `gh`-токеном создавать или
обновлять org-хук: `content_type: json`, allowlist событий и HMAC-секрет.
Токен админа MUST NOT сохраняться — сервис остаётся бесправным
([authorization](../authorization/spec.md)). Источник секрета:
`--secret-stdin`, затем `GH_BABYSITTER_WEBHOOK_SECRET`, затем генерация;
детали CLI — в [cli](../cli/spec.md).

#### Scenario: Настройка организации

- **WHEN** админ выполняет
  `gh babysitter setup --org my-org --url https://hooks.example.com/webhook`
- **THEN** org-хук создан или обновлён; переданный секрет не выводится;
  сгенерированный печатается один раз — положить в env сервиса

### Requirement: Локальная разработка без публичного туннеля

Для локальной разработки события с настоящей организации SHALL заворачиваться
в локальный сервис самим `gh-webhook`; публичный туннель (smee.io)
MUST NOT требоваться.

#### Scenario: Локальный запуск

- **WHEN** разработчик выполняет
  `gh webhook forward --org=my-org --events='*' --url=http://localhost:8000/webhook`
- **THEN** события организации приходят в локальный `POST /webhook`
