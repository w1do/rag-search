## Context

До изменения ссылку обрабатывал `SourceService.add_url` → `_fetch_url_version`. Работа идёт синхронно в запросе API, в пределах транзакции:
1. `extract.fetch_url` делал `GET` через `httpx`, ограничения: `LINK_FETCH_TIMEOUT_SECONDS=15` и `MAX_UPLOAD_BYTES`.
2. `_url_extension` выбирал расширение, `StandardContentExtractor` извлекал текст. Для `text/html` это был `_HtmlTextParser`: он выкидывал только `script`/`style` и склеивал весь текст через пробел.
3. `_store_version` сохраняет оригинал в blob-хранилище, создаёт `SourceVersion` и ставит job `index_version`. Worker режет текст на чанки (`CHUNK_SIZE_CHARS=800`) и пишет их в Typesense и Qdrant.

Почему текст «обрезан»: в п. 1–2 нет рендера JS, а шум навигации не отделяется от контента. Это гипотеза по коду, на конкретной ссылке пользователя не проверялась. Чанкование и индексация длину не ограничивают, поэтому их менять не нужно.

Ветка `text/html` в `extract_text` использовалась только для ссылок: загрузка файлов и синхронизация принимают лишь расширения из `SUPPORTED_EXTENSIONS = {txt, md, pdf, docx}`, поэтому HTML-парсер удаляется вместе со встроенным загрузчиком.

Firecrawl Scrape API v2 (документация `docs.firecrawl.dev/api-reference/endpoint/scrape`, проверено 2026-10-21):
- `POST https://api.firecrawl.dev/v2/scrape`, заголовок `Authorization: Bearer <key>`;
- тело: `url`, `formats` (`["markdown"]`), `onlyMainContent` (по умолчанию `true`), `timeout` в мс (по умолчанию 60000, допустимо от 1000 до 300000), `maxAge` (кэш, по умолчанию 2 дня);
- ответ: `{success, data: {markdown, metadata: {title, sourceURL, statusCode, contentType, error}}}`;
- коды ошибок: 400, 402 (нет кредитов), 403, 404, 409, 429 (rate limit), 500, 503.

## Goals / Non-Goals

**Goals:**
- Ссылка индексируется только по полному Markdown страницы, полученному через Firecrawl.
- Без `FIRECRAWL_API_KEY` добавление ссылки отклоняется понятной ошибкой до создания источника; текст, файлы и синхронизация работают, запуск одной командой не ломается.
- Путь и тело REST-запроса не меняются, UI меняется минимально (понятная ошибка).
- Ключ не утекает. Тесты не ходят в сеть.

**Non-Goals:**
- Crawl, Map, Search, Interact в Firecrawl. Обрабатывается только одна страница по ссылке.
- Перенос загрузки URL в worker (асинхронный fetch).
- Периодическое обновление (re-scrape) URL-источников и массовая миграция старых источников.
- Self-hosted Firecrawl. Поддерживается только через `FIRECRAWL_BASE_URL`, отдельно не тестируется.
- Встроенный загрузчик-фолбэк. Его нет ни при отсутствии ключа, ни при ошибках Firecrawl.

## Decisions

1. **Клиент на `httpx`, а не SDK `firecrawl-py`.** Нужен один эндпоинт. `httpx==0.28.1` уже закреплён, `transport` прокидывается в загрузчик, поэтому тесты пишутся на `httpx.MockTransport`. Альтернатива (SDK) добавляет зависимость и пин, а тестировать её сложнее.

2. **Порт загрузчика ссылок.** В `extract.py` появляются `FetchedLink(markdown: str, title: str | None)`, `FirecrawlLinkFetcher(api_key, base_url, timeout, max_bytes, transport)` и `build_link_fetcher(settings, transport=None)`. Порт `LinkFetcher` (`fetch(url) -> FetchedLink`) лежит в `ports.py`. `build_link_fetcher` возвращает `FirecrawlLinkFetcher`, если `settings.firecrawl_api_key` не пуст, иначе `None`. Сборка — в `deps.build_request_services` (и в `tests/fakes.py`); `SourceService` получает загрузчик через конструктор. Это соответствует слоям проекта (порты в `vector_core`, сборка в `deps`).

3. **Без ключа — отказ до создания источника.** `add_url` проверяет проект и схему URL, затем, если загрузчик не сконфигурирован, бросает `ServiceUnavailable("link ingestion requires FIRECRAWL_API_KEY", code="link_ingestion_not_configured")` → HTTP `503`. Выбран `503`, а не `4xx`: запрос корректен, недоступна серверная возможность из-за конфигурации — так же, как `AuthNotConfigured` (`503 auth_not_configured`). Источник не создаётся, задача не ставится, запросов в сеть нет.

4. **Markdown хранится как `md`.** Оригинал в blob — это Markdown от Firecrawl (`markdown.encode("utf-8")`), текст версии — сам Markdown без усечения, `content_type` источника — `text/markdown`. `reindex` и пересборка индексов из PostgreSQL и blob работают без изменений.

5. **Параметры запроса:** `formats=["markdown"]`, `onlyMainContent=true`, `timeout=FIRECRAWL_TIMEOUT_SECONDS*1000` (значение ограничивается диапазоном 1000–300000). `maxAge` не передаём, остаётся значение Firecrawl по умолчанию. Таймаут `httpx` = `FIRECRAWL_TIMEOUT_SECONDS + 10` с, чтобы сначала сработал серверный таймаут Firecrawl. Схема URL (`http`/`https`) проверяется до запроса.

6. **Ошибки маппятся в `ValidationError` и `PayloadTooLarge`** (это `DomainError`). Существующий `except DomainError` в `_fetch_url_version` переводит источник в `failed`.
   - `402` → `firecrawl: credits exhausted`;
   - `429` → `firecrawl: rate limit exceeded`;
   - `401`/`403` → `firecrawl: request rejected (check FIRECRAWL_API_KEY)`, без значения ключа;
   - таймаут, сетевая ошибка, `5xx` или неразборчивый ответ → `url could not be fetched via firecrawl`;
   - `success=false` или `metadata.statusCode >= 400` → `url returned status <code>` (если код неизвестен — `url could not be fetched via firecrawl`);
   - пустой `markdown` → `no content extracted from url`;
   - размер Markdown в UTF-8 > `MAX_UPLOAD_BYTES` → `PayloadTooLarge("remote content exceeds size limit")`.
   Сообщения формируются из констант; текст исключений `httpx` и тело ответа в них не попадают, поэтому ключ не утекает.

7. **Заголовок.** `add_url` создаёт источник с заголовком из URL. После успешного fetch, если `title` не передан явно и `FetchedLink.title` не пуст, заголовок меняется на `clean_name(fetched.title)`. Для этого в `_fetch_url_version` передаётся флаг `explicit_title`.

8. **Синхронный вызов в запросе API сохраняется**, контракт ответа (`201` + статус) остаётся прежним. Цена решения: запрос может длиться до примерно 70 с.

9. **Настройки** (`Settings`, `from_env`): `firecrawl_api_key: str = field(default="", repr=False)`, `firecrawl_base_url = "https://api.firecrawl.dev"`, `firecrawl_timeout_seconds: float = 60.0`; `link_fetch_timeout_seconds` / `LINK_FETCH_TIMEOUT_SECONDS` удалены. В `docker-compose.yml` (`x-backend.environment`): `FIRECRAWL_API_KEY: ${FIRECRAWL_API_KEY:-}`, `FIRECRAWL_BASE_URL`, `FIRECRAWL_TIMEOUT_SECONDS` с дефолтами. В `.env.example` пустая строка `FIRECRAWL_API_KEY=`.

## Risks / Trade-offs

- [Без ключа ссылки недоступны] → понятная ошибка `503 link_ingestion_not_configured` в API и в UI; ключ берётся из vault.
- [Долгий синхронный запрос, до 60–70 с] → таймаут настраивается. Если станет проблемой, вынести fetch в worker отдельным изменением.
- [Расход кредитов и rate limit Firecrawl] → понятные ошибки `402`/`429`, источник можно повторить повторным `POST /url`.
- [URL и контент уходят третьей стороне] → это единственный способ загрузки ссылок; для чувствительных страниц используйте текстовые или файловые источники.
- [`onlyMainContent` может отрезать нужные блоки (футер с контактами, цены в сайдбаре)] → принимаем по умолчанию. Вынести в настройку `FIRECRAWL_ONLY_MAIN_CONTENT`, если пользователь подтвердит потребность (см. Open Questions).
- [Кэш Firecrawl `maxAge` до 2 дней] → контент может отставать до 2 дней. Для базы знаний это допустимо.
- [Старые URL-источники остаются обрезанными] → в документации указано, что их нужно удалить и добавить заново.

## Migration Plan

1. Положить ключ в vault и в `.env` как `FIRECRAWL_API_KEY=...`. Значение в репозиторий не попадает.
2. `docker compose up -d --build api worker`.
3. Для старых URL-источников: `DELETE` источника, затем повторный `POST /sources/url`.
4. Откат: вернуть предыдущую версию кода (встроенный загрузчик). Миграций БД нет.

## Open Questions

- Нужна ли настройка `onlyMainContent`, или достаточно `true`.
