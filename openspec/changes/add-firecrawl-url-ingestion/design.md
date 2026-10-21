## Context

Сейчас ссылку обрабатывает `SourceService.add_url` → `_fetch_url_version`. Работа идёт синхронно в запросе API, в пределах транзакции:
1. `extract.fetch_url` делает `GET` через `httpx`, ограничения: `LINK_FETCH_TIMEOUT_SECONDS=15` и `MAX_UPLOAD_BYTES`.
2. `_url_extension` выбирает расширение, `StandardContentExtractor` извлекает текст. Для `text/html` это `_HtmlTextParser`: он выкидывает только `script`/`style` и склеивает весь текст через пробел.
3. `_store_version` сохраняет оригинал в blob-хранилище, создаёт `SourceVersion` и ставит job `index_version`. Worker режет текст на чанки (`CHUNK_SIZE_CHARS=800`) и пишет их в Typesense и Qdrant.

Почему текст «обрезан»: в п. 1–2 нет рендера JS, а шум навигации не отделяется от контента. Это гипотеза по коду, на конкретной ссылке пользователя не проверялась. Чанкование и индексация длину не ограничивают, поэтому их менять не нужно.

Firecrawl Scrape API v2 (документация `docs.firecrawl.dev/api-reference/endpoint/scrape`, проверено 2026-10-21):
- `POST https://api.firecrawl.dev/v2/scrape`, заголовок `Authorization: Bearer <key>`;
- тело: `url`, `formats` (`["markdown"]`), `onlyMainContent` (по умолчанию `true`), `timeout` в мс (по умолчанию 60000, допустимо от 1000 до 300000), `maxAge` (кэш, по умолчанию 2 дня);
- ответ: `{success, data: {markdown, metadata: {title, sourceURL, statusCode, contentType, error}}}`;
- коды ошибок: 400, 402 (нет кредитов), 403, 404, 409, 429 (rate limit), 500, 503.

## Goals / Non-Goals

**Goals:**
- Если задан `FIRECRAWL_API_KEY`, ссылка индексируется по полному Markdown страницы.
- Контракт REST и MCP не меняется, UI не трогаем.
- Без ключа поведение остаётся прежним, запуск одной командой работает.
- Ключ не утекает. Тесты не ходят в сеть.

**Non-Goals:**
- Crawl, Map, Search, Interact в Firecrawl. Обрабатывается только одна страница по ссылке.
- Перенос загрузки URL в worker (асинхронный fetch).
- Периодическое обновление (re-scrape) URL-источников и массовая миграция старых источников.
- Self-hosted Firecrawl. Поддерживается только через `FIRECRAWL_BASE_URL`, отдельно не тестируется.
- Улучшение встроенного `_HtmlTextParser`.

## Decisions

1. **Клиент на `httpx`, а не SDK `firecrawl-py`.** Нужен один эндпоинт. `httpx==0.28.1` уже закреплён, а `transport` уже прокидывается в `SourceService`, поэтому тесты пишутся на `httpx.MockTransport`. Альтернатива (SDK) добавляет зависимость и пин, а тестировать её сложнее.

2. **Порт загрузчика ссылок.** В `extract.py` появляется результат `FetchedLink(data: bytes, content_type: str | None, extension: str | None, title: str | None)` и две реализации:
   - `HttpLinkFetcher`: обёртка над существующим `fetch_url`, расширение и текст определяются как сейчас;
   - `FirecrawlLinkFetcher(api_key, base_url, timeout, max_bytes, transport)`: возвращает `data=markdown.encode("utf-8")`, `content_type="text/markdown"`, `extension="md"`, `title=metadata.title`.
   Выбор делается в `deps.build_request_services` (и в `tests/fakes.py`): если `settings.firecrawl_api_key` не пуст, используется `FirecrawlLinkFetcher`, иначе `HttpLinkFetcher`. `SourceService` получает загрузчик через конструктор и не знает, какой именно. Это соответствует слоям проекта (порты в `vector_core`, сборка в `deps`).

3. **Markdown хранится как `md`.** Оригинал в blob — это Markdown от Firecrawl, текст версии извлекается через существующую ветку `TEXT_EXTENSIONS` (`_decode`). Так `reindex` и пересборка индексов из PostgreSQL и blob работают без изменений.

4. **Параметры запроса:** `formats=["markdown"]`, `onlyMainContent=true`, `timeout=FIRECRAWL_TIMEOUT_SECONDS*1000` (значение ограничивается диапазоном 1000–300000). `maxAge` не передаём, остаётся значение Firecrawl по умолчанию. Таймаут `httpx` = `FIRECRAWL_TIMEOUT_SECONDS + 10` с, чтобы сначала сработал серверный таймаут Firecrawl.

5. **Ошибки маппятся в `ValidationError` и `PayloadTooLarge`** (это `DomainError`). Существующий `except DomainError` в `_fetch_url_version` переводит источник в `failed` без изменений кода.
   - `402` → `firecrawl: credits exhausted`;
   - `429` → `firecrawl: rate limit exceeded`;
   - `401`/`403` → `firecrawl: request rejected (check FIRECRAWL_API_KEY)`, без значения ключа;
   - таймаут, сетевая ошибка или `5xx` → `url could not be fetched via firecrawl`;
   - `success=false` или `metadata.statusCode >= 400` → `url returned status <code>`;
   - пустой `markdown` → `no content extracted from url`;
   - `len(data) > MAX_UPLOAD_BYTES` → `PayloadTooLarge("remote content exceeds size limit")`.
   Тихого отката на `HttpLinkFetcher` нет, иначе пользователь снова незаметно получит обрезанный текст.

6. **Заголовок.** `add_url` создаёт источник с заголовком из URL, как сейчас. После успешного fetch, если `title` не передан явно и `FetchedLink.title` не пуст, заголовок меняется на `clean_name(fetched.title)`. Для этого в `_fetch_url_version` передаётся флаг `explicit_title`.

7. **Синхронный вызов в запросе API сохраняется.** Так ведёт себя текущий `fetch_url`, и контракт ответа (`201` + статус) остаётся прежним. Цена решения: запрос может длиться до примерно 70 с.

8. **Настройки** (`Settings`, `from_env`): `firecrawl_api_key: str = field(default="", repr=False)`, `firecrawl_base_url = "https://api.firecrawl.dev"`, `firecrawl_timeout_seconds: float = 60.0`. В `docker-compose.yml` (`x-backend.environment`): `FIRECRAWL_API_KEY: ${FIRECRAWL_API_KEY:-}`, `FIRECRAWL_BASE_URL`, `FIRECRAWL_TIMEOUT_SECONDS` с дефолтами. В `.env.example` пустая строка `FIRECRAWL_API_KEY=`.

## Risks / Trade-offs

- [Долгий синхронный запрос, до 60–70 с] → таймаут настраивается. Если станет проблемой, вынести fetch в worker отдельным изменением.
- [Расход кредитов и rate limit Firecrawl] → понятные ошибки `402`/`429`, источник можно повторить повторным `POST /url`.
- [URL и контент уходят третьей стороне] → режим включается только явным ключом. Без ключа данные никуда не уходят.
- [`onlyMainContent` может отрезать нужные блоки (футер с контактами, цены в сайдбаре)] → принимаем по умолчанию. Вынести в настройку `FIRECRAWL_ONLY_MAIN_CONTENT`, если пользователь подтвердит потребность (см. Open Questions).
- [Кэш Firecrawl `maxAge` до 2 дней] → контент может отставать до 2 дней. Для базы знаний это допустимо.
- [Старые URL-источники остаются обрезанными] → в документации указано, что их нужно удалить и добавить заново.

## Migration Plan

1. Положить ключ в vault и в `.env` как `FIRECRAWL_API_KEY=...`. Значение в репозиторий не попадает.
2. `docker compose up -d --build api worker`.
3. Для старых URL-источников: `DELETE` источника, затем повторный `POST /sources/url`.
4. Откат: убрать `FIRECRAWL_API_KEY` и перезапустить сервисы, после этого снова работает встроенный загрузчик. Миграций БД нет.

## Open Questions

- Имя переменной. Пользователь написал «firecrawl», в спецификации заложено `FIRECRAWL_API_KEY`, в стиле `AI_API_KEY`. Нужно подтверждение.
- Нужна ли настройка `onlyMainContent`, или достаточно `true`.
