# cli Specification

## Purpose

Тонкий клиент к control plane ([event-gateway](../event-gateway/spec.md)):
Python + Typer, аутентификация токеном из `gh auth token`, три команды —
`listen`, `setup`, `serve` — в одном gh-расширении. Целевой пользователь и
модель подписок — в [design.md](design.md).

## Requirements

### Requirement: Дистрибуция как gh-расширение

CLI SHALL распространяться как **gh-расширение** (паттерн
[cli/gh-webhook](../github-webhook/design.md#разбор-cligh-webhook)): установка
и обновления через штатный механизм `gh`, команда живёт в привычном
пространстве `gh <...>`. Расширения могут быть скриптами, не только
Go-бинарниками, так что Python не помеха (разработчики — целевая аудитория,
интерпретатор у них есть).

#### Scenario: Установка

- **WHEN** пользователь выполняет
  `gh extension install Alex-Kopylov/gh-babysitter`
- **THEN** доступна команда `gh babysitter --help`

### Requirement: Команда listen

`listen` — единственная команда разработчика. Что слушать — SHALL объявляться
флагами; команды `subscribe`/`unsubscribe`/`list` и конфиг-файлы MUST NOT
существовать. Одно соединение SHALL покрывать один репозиторий (+ опциональный
номер) × несколько типов событий; ещё один репозиторий или номер — ещё один
процесс `listen`; группирующих повторяемых флагов сознательно нет.

#### Scenario: Старт стрима

- **WHEN** запускается `gh babysitter listen -R org/api -E issues`
- **THEN** CLI подключается к `GET /events/stream`, передав подписки
  параметрами запроса; сервер в этот же момент проверяет доступ к репозиторию
  ([authorization](../authorization/spec.md)) и начинает стримить

#### Scenario: Вывод событий

- **WHEN** приходят события
- **THEN** они печатаются как JSON lines в stdout
  ([формат](../event-delivery/spec.md)); `--format pretty` — человекочитаемо;
  вывод pipe-friendly: `... | jaq '.payload.issue.title'`

#### Scenario: Завершение процесса

- **WHEN** Ctrl+C / kill / конец сессии агента
- **THEN** соединение закрылось — подписок больше нет
  ([event-delivery](../event-delivery/spec.md)); это и есть unsubscribe

### Requirement: Реконнект и классификация ошибок

Обрыв или retryable-ответ SHALL давать предупреждение в stderr и реконнект с
экспоненциальным бэкоффом; подписки переобъявляются из тех же argv — диск не
участвует. Бэкофф SHALL сбрасываться только после SSE-события `ready`.
`408`, `425`, `429` и любой `5xx` SHALL повторяться; прочий `4xx` — постоянная
ошибка. Каждый реконнект SHALL предваряться строкой:

```text
warning: disconnected (<reason>); events during the gap are lost; reconnecting in 1.0s
```

Причина содержит transport/read-timeout либо retryable HTTP-статус.

#### Scenario: Retryable-ответ

- **WHEN** сервер отвечает `408`, `425`, `429` или `5xx`, либо рвётся транспорт
- **THEN** предупреждение в stderr и реконнект с бэкоффом

#### Scenario: Постоянная ошибка

- **WHEN** сервер отвечает прочим `4xx` (кроме `401`/`403`)
- **THEN** его JSON-поле `detail` выводится в stderr и процесс завершается с
  кодом `1` после одного запроса — без ретраев

#### Scenario: Отказ в авторизации

- **WHEN** сервер отвечает `401` или `403`
- **THEN** процесс завершается с кодом `1`

#### Scenario: Событие lag

- **WHEN** приходит серверное событие `lag`
- **THEN** оно не попадает в stdout: CLI пишет
  `warning: server dropped N events (consumer too slow)` в stderr и продолжает
  стрим

### Requirement: Локальная валидация

Все ошибки использования SHALL проверяться до получения токена и открытия
сокета, оформляться как `BadParameter` и завершаться с кодом `2`.

#### Scenario: Правила валидации

- **WHEN** разбираются флаги команды
- **THEN** проверяется: `--repo` имеет вид `owner/name` (ровно один `/`, обе
  части непустые, допустимы только `[A-Za-z0-9._-]`); `--number >= 1`;
  `--timeout > 0`, включая строковые формы `0` и `0s`; заданный `--action`
  непустой; `--server` — `http`/`https` URL с host

### Requirement: Запрет отправки токена по plain-HTTP

CLI MUST NOT отправлять GitHub-токен по `http://` на non-loopback host.
`localhost`, `127.0.0.0/8` и `::1` SHALL быть разрешены для разработки.

#### Scenario: Небезопасный сервер

- **WHEN** `--server` указывает `http://` на non-loopback host
- **THEN** CLI отказывается подключаться; требуется HTTPS либо явный
  `GH_BABYSITTER_INSECURE=1`

### Requirement: Exit-условия

Агенту нужна семантика завершения: команда, которая сама выходит с результатом,
удобнее вечного стрима, который надо убивать. `listen` SHALL поддерживать
exit-флаги; без них SHALL стримить бесконечно (человеческий режим). Флаги
SHALL комбинироваться: `--until merged --timeout 12h` — «жди мёржа, но не
дольше 12 часов».

#### Scenario: Первое событие

- **WHEN** задан `--first-event` и приходит первое событие
- **THEN** выход с кодом `0`

#### Scenario: N событий

- **WHEN** задан `--count N` и получено N событий
- **THEN** выход с кодом `0`

#### Scenario: Терминальный статус

- **WHEN** задан `--until <status>` и объект достиг статуса (матрица ниже)
- **THEN** выход с кодом `0`

#### Scenario: Таймаут

- **WHEN** задан `--timeout 2h` и время ожидания истекло
- **THEN** выход с кодом `124`

### Requirement: Коды выхода процесса

Коды выхода SHALL следовать контракту:

| Код | Значение |
|---:|---|
| `0` | Условие `--until`, `--count` или `--first-event` выполнено |
| `1` | Runtime-ошибка: токен отклонён, подписка навсегда отвергнута, ошибка протокола |
| `2` | Ошибка использования: невалидные флаги, repo, число или таймаут |
| `124` | Истёк `--timeout` |

#### Scenario: Runtime-ошибка

- **WHEN** токен отклонён, подписка навсегда отвергнута или нарушен протокол
- **THEN** выход с кодом `1`

### Requirement: Матрица --until

Терминальные статусы SHALL зависеть от типа события. `--until` SHALL требовать
`-n`: статус есть только у конкретного объекта. Требуемый тип события CLI SHALL
сам добавлять к подписке, если он не указан в `-E`.

#### Scenario: merged

- **WHEN** `--until merged` и приходит `pull_request` с `action == "closed"` и
  `payload.pull_request.merged == true`
- **THEN** условие выполнено

#### Scenario: closed

- **WHEN** `--until closed` и приходит `pull_request` или `issues` с
  `action == "closed"` (для PR — включая merged)
- **THEN** условие выполнено

#### Scenario: approved

- **WHEN** `--until approved` и приходит `pull_request_review` с
  `action == "submitted"` и `payload.review.state == "approved"`
- **THEN** условие выполнено

#### Scenario: changes_requested

- **WHEN** `--until changes_requested` и приходит `pull_request_review` с
  `action == "submitted"` и `payload.review.state == "changes_requested"`
- **THEN** условие выполнено

### Requirement: Проверка состояния через poll при --until

Доставка at-most-once ([event-delivery](../event-delivery/spec.md)), значит
терминальное событие можно пропустить: оно случилось до запуска `listen`,
попало в окно реконнекта или потерялось при исправном соединении. Поэтому при
`--until` CLI SHALL опрашивать текущее состояние объекта напрямую у GitHub
своим же токеном (`gh api repos/{owner}/{repo}/pulls/{number}`) на старте,
после каждого обрыва перед реконнектом и периодически, пока стрим остаётся
подключённым. Poll наблюдает текущее состояние, но MUST NOT добавлять replay и
MUST NOT менять контракт at-most-once.

#### Scenario: Условие уже выполнено

- **WHEN** poll обнаруживает, что условие выполнено
- **THEN** немедленный выход с кодом `0`

#### Scenario: Периодичность poll

- **WHEN** стрим остаётся подключённым
- **THEN** первый периодический poll выполняется только после полного
  `GH_BABYSITTER_UNTIL_POLL_INTERVAL` (по умолчанию 300 секунд), потому что
  состояние уже проверено на старте; потерянное терминальное событие
  задерживает успешный выход не более чем на один интервал плюс время запроса
  к GitHub, а не до `--timeout`

### Requirement: Команда setup

Одноразовая команда org-админа SHALL создавать/обновлять org-webhook с
allowlist ([github-webhook](../github-webhook/spec.md)) и секретом, используя
собственный токен админа. Опция `--secret <value>` MUST NOT существовать:
секрет в argv виден через `ps` и остаётся в истории shell. Источники секрета в
порядке приоритета: `--secret-stdin` → `GH_BABYSITTER_WEBHOOK_SECRET` → новый
случайный секрет.

#### Scenario: Секрет из stdin

- **WHEN** задан `--secret-stdin`
- **THEN** читается и обрезается весь stdin; пустой ввод → `BadParameter`

#### Scenario: Переданный секрет не эхоится

- **WHEN** секрет пришёл из stdin или env
- **THEN** он не выводится: команда пишет
  `webhook configured; reusing the supplied GH_BABYSITTER_WEBHOOK_SECRET`

#### Scenario: Сгенерированный секрет

- **WHEN** секрет не передан и сгенерирован
- **THEN** он печатается один раз, потому что другой его копии нет

### Requirement: Команда serve

`serve` SHALL запускать один процесс uvicorn с сервером из того же
дистрибутива; отдельный серверный пакет MUST NOT требоваться.

#### Scenario: Запуск сервера

- **WHEN** выполняется `gh babysitter serve --host 0.0.0.0 --port 8000`
- **THEN** поднимается один uvicorn-процесс сервиса

### Requirement: Конвенции флагов

Флаги SHALL зеркалить `gh` и `gh-webhook`, чтобы не заставлять людей учить
новое: `-R, --repo org/name` — репозиторий; `-E, --events a,b,c` — типы
событий через запятую (из [меню](../github-webhook/spec.md));
`-n, --number N` — конкретный issue/PR; `--action opened` — фильтр по action
(опционально). Exit-флаги (`--until`, `--timeout`, `--count`, `--first-event`)
— наши собственные, у `gh` аналогов нет.

#### Scenario: Несколько типов событий

- **WHEN** задано `-E issues,pull_request`
- **THEN** одно соединение подписано на оба типа разом

### Requirement: GitHub Enterprise — резолюция базы API

По умолчанию CLI SHALL ходить в `https://api.github.com`. База API SHALL
определяться первым заданным источником: `--api-url` (полный URL) →
`GH_BABYSITTER_GITHUB_API_URL` (полный URL) → `GITHUB_API_URL` (полный URL) →
`GH_HOST` (хост, URL выводится) → `https://api.github.com`. Пустое значение
SHALL считаться незаданным и передавать очередь следующему источнику. Сервер
настраивается отдельно и SHALL читать только `GH_BABYSITTER_GITHUB_API_URL`:
рабочий процесс MUST NOT подхватывать `GH_HOST` из окружения деплоя.

#### Scenario: Вывод URL из GH_HOST

- **WHEN** база определяется из `GH_HOST`
- **THEN** хост разворачивается по правилам самого `gh`: `github.com` →
  `https://api.github.com`, `<tenant>.ghe.com` →
  `https://api.<tenant>.ghe.com`, остальное → `https://<host>/api/v3`

### Requirement: Таймауты и интервалы CLI

Таймауты и интервалы SHALL настраиваться переменными окружения (секунды):

| Переменная | По умолчанию | На что влияет |
|---|---|---|
| `GH_BABYSITTER_SERVER_TIMEOUT` | `10` | connect/write/pool для соединения с сервером |
| `GH_BABYSITTER_STREAM_TIMEOUT` | `90` | read для SSE-потока |
| `GH_BABYSITTER_GITHUB_TIMEOUT` | `10` | все таймауты запросов к GitHub API |
| `GH_BABYSITTER_UNTIL_POLL_INTERVAL` | `300` | интервал проверки терминального состояния при подключённом `listen --until` |

Read-таймаут SSE-потока MUST быть ограничен: значение по умолчанию 90 секунд
выдерживает три пропущенных серверных ping с интервалом 30 секунд. Без границы
graceful shutdown сервера мог оставить `listen` навсегда заблокированным на
полумёртвом сокете.

#### Scenario: Истечение read-таймаута

- **WHEN** SSE-поток молчит дольше `GH_BABYSITTER_STREAM_TIMEOUT`
- **THEN** это считается временным обрывом: предупреждение и реконнект

### Requirement: Эфемерная модель подписок

Подписка SHALL жить ровно столько, сколько процесс `listen`: желаемый набор —
это аргументы командной строки, серверная регистрация — производная открытого
соединения ([event-delivery](../event-delivery/spec.md)). Состояние MUST NOT
храниться ни на диске клиента, ни в БД сервера. Обоснование — в
[design.md](design.md).

#### Scenario: Агентный сценарий

- **WHEN** агент запускает
  `gh babysitter listen -R org/web -n 24 --until merged --timeout 12h`
- **THEN** подписка нужна на время задачи — «понянчить PR #24» — и мертва после
  мёржа; агент блокируется в ожидании результата и выходит

#### Scenario: Рестарт любой стороны

- **WHEN** клиент или сервер перезапускается
- **THEN** система не может оказаться в несогласованном состоянии: обе стороны
  после рестарта чисты по построению, всё восстанавливается самим фактом
  переподключения
