## Why

Если добавить источник по ссылке, его данные в базе знаний выглядят обрезанными. Встроенный загрузчик (`fetch_url`) делал обычный `GET` без рендера JavaScript, а `_HtmlTextParser` склеивал весь текст страницы в одну строку. Он удалял только `script` и `style`, а навигацию, меню и футеры оставлял. Поэтому на SPA и динамических страницах извлекалась только часть контента, а структура (заголовки, списки, таблицы) пропадала. Чтобы получать полный и чистый текст страницы, ссылки нужно извлекать только через Firecrawl (`POST /v2/scrape`, формат `markdown`). Firecrawl рендерит JS и обходит anti-bot.

## What Changes

- **BREAKING**: встроенный загрузчик ссылок удалён (`fetch_url`, `_HtmlTextParser`, ветка `text/html` в извлечении текста, `_url_extension`, настройка `LINK_FETCH_TIMEOUT_SECONDS`). Ссылки (`POST /api/projects/{id}/sources/url`) извлекаются только через Firecrawl Scrape API v2. Результат — полный Markdown основного контента страницы (`onlyMainContent: true`).
- **BREAKING**: без `FIRECRAWL_API_KEY` добавление ссылки отклоняется ошибкой `503 link_ingestion_not_configured` («link ingestion requires FIRECRAWL_API_KEY»). Источник не создаётся, запрос в Firecrawl не отправляется. Текст, файлы и синхронизация работают без ключа, запуск одной командой не ломается.
- Markdown сохраняется как версия источника с расширением `md` и индексируется существующим пайплайном: чанки, Typesense, Qdrant. Текст не усекается, единственное ограничение — общий лимит `MAX_UPLOAD_BYTES`.
- Заголовок источника по умолчанию берётся из `metadata.title` страницы; если его нет — из последнего сегмента пути URL. Явный `title` важнее.
- Ошибки Firecrawl превращаются в понятные сообщения: нет кредитов (`402`), лимит запросов (`429`), отклонённый ключ (`401`/`403`), таймаут или сбой сервиса, ошибка целевой страницы, пустой результат, превышение лимита размера. Источник в этих случаях получает статус `failed` с этим сообщением; повторный `POST` той же ссылки повторяет попытку.
- Настройки: `FIRECRAWL_API_KEY` (секрет, из vault, обязателен для ссылок), `FIRECRAWL_BASE_URL` (по умолчанию `https://api.firecrawl.dev`), `FIRECRAWL_TIMEOUT_SECONDS` (по умолчанию `60`). Ключ не попадает в логи, `repr`, ответы API и в репозиторий.
- Новых зависимостей нет: клиент пишется на уже закреплённом `httpx==0.28.1`.

## Capabilities

### New Capabilities
- `link-extraction`: извлечение содержимого веб-страниц по ссылке через внешний сервис Firecrawl. Включает обязательность ключа, формат результата, заголовок, обработку ошибок и защиту ключа.

### Modified Capabilities
- `vector-kb`: меняется требование «Knowledge sources», сценарии «Add link» и «Unreachable link», добавлен сценарий «Link ingestion not configured». Источник-ссылка индексируется по полному извлечённому содержимому страницы, а не по сырому тексту HTML.

## Impact

- Код: `packages/core/vector_core/extract.py` (удалены `fetch_url` и HTML-парсер; добавлены `FetchedLink`, `FirecrawlLinkFetcher`, `build_link_fetcher`), `packages/core/vector_core/ports.py` (порт `LinkFetcher`), `packages/core/vector_core/services/sources.py` (`add_url`, `_fetch_url_version`), `packages/core/vector_core/config.py` (поля `firecrawl_*` вместо `link_fetch_timeout_seconds`), `apps/api/app/deps.py` (сборка загрузчика), `apps/web/src/features/knowledge/AddSourceDialog.tsx` (понятная ошибка для ссылок).
- Инфраструктура: `docker-compose.yml` (проброс `FIRECRAWL_*` в `api` и `worker`), `.env.example` (пустой `FIRECRAWL_API_KEY=`).
- API: путь и тело `POST /sources/url` не меняются. Новый ответ `503` без ключа; содержимое версии — `md`, заголовок по умолчанию — из metadata страницы.
- Внешняя система: Firecrawl Cloud (`api.firecrawl.dev`). URL страниц уходит третьей стороне, вызовы расходуют кредиты аккаунта.
- Существующие URL-источники автоматически не перекачиваются. Чтобы получить полный текст, источник нужно удалить и добавить заново.
- Тесты: `tests/test_extract.py`, `tests/test_sources.py`, `tests/test_config.py`, все на `httpx.MockTransport`, без реальных вызовов Firecrawl.
