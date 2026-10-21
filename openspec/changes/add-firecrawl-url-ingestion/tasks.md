## 1. Конфигурация
- [x] 1.1 `vector_core/config.py`: добавить `firecrawl_api_key` (`repr=False`), `firecrawl_base_url` (по умолчанию `https://api.firecrawl.dev`), `firecrawl_timeout_seconds` (по умолчанию `60.0`) и чтение `FIRECRAWL_API_KEY`, `FIRECRAWL_BASE_URL`, `FIRECRAWL_TIMEOUT_SECONDS` в `from_env`; удалить `link_fetch_timeout_seconds` / `LINK_FETCH_TIMEOUT_SECONDS`
- [x] 1.2 `docker-compose.yml` (`x-backend.environment`): пробросить `FIRECRAWL_API_KEY: ${FIRECRAWL_API_KEY:-}`, `FIRECRAWL_BASE_URL` и `FIRECRAWL_TIMEOUT_SECONDS` с дефолтами
- [x] 1.3 `.env.example`: пустой `FIRECRAWL_API_KEY=`, без значения
- [x] 1.4 Тест: `repr(Settings)` не содержит значения ключа, дефолты применяются при пустом окружении

## 2. Загрузчик ссылок
- [x] 2.1 `vector_core/extract.py`: `FetchedLink`; Protocol `LinkFetcher` в `ports.py`
- [x] 2.2 Удалить встроенный загрузчик: `fetch_url`, `_HtmlTextParser`, ветку `text/html` в `extract_text`
- [x] 2.3 `FirecrawlLinkFetcher`: `POST {base_url}/v2/scrape` с `Authorization: Bearer`, телом `{url, formats:["markdown"], onlyMainContent:true, timeout}` (1000–300000 мс), таймаутом `httpx` = timeout + 10 с; проверка схемы до запроса; разбор `data.markdown` и `data.metadata.{title,statusCode}`; `build_link_fetcher` возвращает `None` без ключа
- [x] 2.4 Маппинг ошибок из design.md (п. 6): 402, 429, 401/403, таймаут/сеть/5xx, `success=false`, `statusCode>=400`, пустой markdown, превышение `max_bytes`. Ни в одном сообщении нет ключа
- [x] 2.5 Тесты в `tests/test_extract.py` на `httpx.MockTransport`: проверка тела и заголовка запроса, клэмп таймаута, успешный ответ, каждый случай ошибки, отсутствие ключа в тексте исключений

## 3. Сервис источников
- [x] 3.1 `SourceService`: принимать `LinkFetcher | None` в конструкторе вместо `transport`/`fetch_url`; версия `md`, текст без усечения; удалить `_url_extension`
- [x] 3.2 Без загрузчика `add_url` отклоняется `ServiceUnavailable` (`503 link_ingestion_not_configured`) до создания источника
- [x] 3.3 Заголовок: если `title` не передан явно, а `FetchedLink.title` не пуст, обновлять заголовок источника через `clean_name`
- [x] 3.4 `apps/api/app/deps.py` и `tests/fakes.py`: собирать загрузчик через `build_link_fetcher` (`FirecrawlLinkFetcher` при заданном ключе, иначе `None`)
- [x] 3.5 Тесты в `tests/test_sources.py`: длинный Markdown (больше 10 чанков) сохраняется и индексируется целиком, `extracted_chars` равен его длине, версия `md`, заголовок из metadata, явный заголовок важнее; ошибки Firecrawl дают `failed` без версии; ключ не утекает в `error_message` и логи; повторный `POST` после `failed` повторяет попытку; без ключа — `503`, источник не создан, запросов ноль

## 4. Web
- [x] 4.1 `AddSourceDialog.tsx`: вкладка «Ссылка» остаётся, понятная ошибка для `link_ingestion_not_configured` и `422`; `npm run typecheck` и `npm run build` проходят

## 5. Проверка
- [x] 5.1 `uv run --extra dev pytest -q` и `uv run --extra dev ruff check .` проходят
- [ ] 5.2 Ручная проверка в compose с ключом из vault: добавить ссылку на JS-страницу, дождаться `indexed`, убедиться, что `/api/v1/search` и `/context` находят фрагменты из конца страницы
- [ ] 5.3 Ручная проверка в compose без ключа: `POST /sources/url` возвращает `503 link_ingestion_not_configured`, UI показывает понятную ошибку

## 6. Документация
- [x] 6.1 `AGENTS.md`/`CLAUDE.md` (раздел «Вход и запуск»): `FIRECRAWL_API_KEY` (из vault) обязателен для источников-ссылок; старые URL-источники нужно удалить и добавить заново
- [ ] 6.2 Обновить `PLAN.md` и `GOAL.md` фактами проверок после ручной проверки в compose, синхронизировать память проекта
