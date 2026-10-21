## 1. Конфигурация
- [ ] 1.1 `vector_core/config.py`: добавить `firecrawl_api_key` (`repr=False`), `firecrawl_base_url` (по умолчанию `https://api.firecrawl.dev`), `firecrawl_timeout_seconds` (по умолчанию `60.0`) и чтение `FIRECRAWL_API_KEY`, `FIRECRAWL_BASE_URL`, `FIRECRAWL_TIMEOUT_SECONDS` в `from_env`
- [ ] 1.2 `docker-compose.yml` (`x-backend.environment`): пробросить `FIRECRAWL_API_KEY: ${FIRECRAWL_API_KEY:-}`, `FIRECRAWL_BASE_URL` и `FIRECRAWL_TIMEOUT_SECONDS` с дефолтами
- [ ] 1.3 `.env.example`: добавить пустой `FIRECRAWL_API_KEY=`, без значения
- [ ] 1.4 Тест: `repr(Settings)` не содержит значения ключа, дефолты применяются при пустом окружении

## 2. Загрузчик ссылок
- [ ] 2.1 `vector_core/extract.py`: `FetchedLink` и Protocol `LinkFetcher` (в `ports.py`, если там место портам)
- [ ] 2.2 `HttpLinkFetcher` поверх существующего `fetch_url` (поведение без изменений)
- [ ] 2.3 `FirecrawlLinkFetcher`: `POST {base_url}/v2/scrape` с `Authorization: Bearer`, телом `{url, formats:["markdown"], onlyMainContent:true, timeout}`, таймаутом `httpx` = timeout + 10 с; разбор `data.markdown` и `data.metadata.{title,statusCode}`
- [ ] 2.4 Маппинг ошибок из design.md (п. 5): 402, 429, 401/403, таймаут/сеть/5xx, `success=false`, `statusCode>=400`, пустой markdown, превышение `max_bytes`. Ни в одном сообщении нет ключа
- [ ] 2.5 Тесты в `tests/test_extract.py` на `httpx.MockTransport`: проверка тела и заголовка запроса, успешный ответ, каждый случай ошибки, отсутствие ключа в тексте исключений

## 3. Сервис источников
- [ ] 3.1 `SourceService`: принимать `LinkFetcher` в конструкторе вместо прямого вызова `fetch_url`; `_fetch_url_version` использует `FetchedLink.extension`, если оно задано, иначе `_url_extension`
- [ ] 3.2 Заголовок: если `title` не передан явно, а `FetchedLink.title` не пуст, обновлять заголовок источника через `clean_name`
- [ ] 3.3 `apps/api/app/deps.py` и `tests/fakes.py`: выбирать `FirecrawlLinkFetcher`, когда задан `firecrawl_api_key`, иначе `HttpLinkFetcher`
- [ ] 3.4 Тесты в `tests/test_sources.py`: длинный Markdown (больше 10 чанков) сохраняется целиком, `extracted_chars` равен его длине, версия `md`, заголовок из metadata, явный заголовок важнее; ошибка Firecrawl даёт `failed` без версии; повторный `POST` после `failed` повторяет попытку; без ключа запрос к Firecrawl не отправляется

## 4. Проверка
- [ ] 4.1 `uv run pytest` и `uv run ruff check .` проходят
- [ ] 4.2 Ручная проверка в compose с ключом из vault: добавить ссылку на JS-страницу, дождаться `indexed`, убедиться, что `/api/v1/search` и `/context` находят фрагменты из конца страницы
- [ ] 4.3 Ручная проверка без ключа: ссылка индексируется встроенным загрузчиком, как раньше

## 5. Документация
- [ ] 5.1 `AGENTS.md`/`CLAUDE.md` (раздел «Вход и запуск»): описать `FIRECRAWL_API_KEY` (из vault, опционален) и то, что старые URL-источники нужно добавить заново
- [ ] 5.2 Обновить `PLAN.md` и `GOAL.md` фактами проверок, синхронизировать память проекта
