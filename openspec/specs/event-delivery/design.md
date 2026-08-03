# event-delivery — выбор транспорта

## Почему SSE, а не WebSocket

`GET /events/stream` через `sse-starlette`:

- Поток строго односторонний (сервер → клиент), обратный канал не нужен.
  `gh-webhook` использует WebSocket потому, что пишет ответ локального сервера
  обратно в GitHub ([разбор](../github-webhook/design.md#разбор-cligh-webhook)) —
  у нас такого требования нет.
- Работает за NAT и корпоративными прокси, дебажится обычным `curl`.
- Авторизация — обычный заголовок `Authorization: Bearer <token>` при
  подключении ([authorization](../authorization/spec.md)).
