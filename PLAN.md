# PLAN.md — Vector

Этапы из Delivery Plan. Статус: `Planned | In progress | Done`. Отмечаются только факты, подтверждённые проверками.

### Этап 1. Bootstrap monorepo skeleton and pinned tooling
Status: Done
- Проверено: `pip install -e ".[dev]"` в чистом venv; импорты `app`, `worker`, `vector_core`, `vector_cli`; `npm run build` в `apps/web`.

### Этап 2. Governance documents and vault/memory
Status: In progress
- Созданы: `CLAUDE.md`, `AGENTS.md`, `PLAN.md`, `GOAL.md`.
- Память проекта синхронизирована: `memory_sync` выполнен (added 5, changed 2, unchanged 18, errors 0); `memory_status`: files 25, chunks 123, errors 0, stale_files 0, unindexed_files 0, search_mode hybrid.
- Не выполнено: синхронизация через vault MCP — vault MCP недоступен в текущей среде (инструмент не найден), результат не получен.
- Финализация: секреты для проверок взяты из локального `.env` пользователя, а не из vault.

### Этап 3. Docker Compose stack and health checks
Status: Done
- Созданы: `docker-compose.yml`, `.env.example`, `Dockerfile.backend`, `apps/web/Dockerfile`, минимальные `app/main.py` (`/health`) и `worker/main.py` (heartbeat).
- Проверено: `docker compose config` проходит; `latest` не используется.
- Проверено: `docker compose up -d --build` — все 6 контейнеров (postgres, qdrant, typesense, api, worker, web) healthy; `curl -I localhost:8081` → 200 OK. Порт web: `${WEB_PORT:-8081}` (8080 был занят на хосте). `WEB_PORT=8081` добавлен в `.env.example`.
- Цель: `docker compose up -d` поднимает весь стек без ручной настройки; вход через `.env` (`EMAIL`, `PASSWORD`, `MCP_TOKEN`).

### Этап 4. Core domain, database, projects with API keys
Status: Done
- Включает авторизацию по `EMAIL`/`PASSWORD` и `MCP_TOKEN` для агентов.
- Созданы: `vector_core` (config, domain, repositories, security, services, db/, migrations/0001 для projects/api_keys/sessions), `alembic.ini`, `apps/api/app/deps.py`, роутеры `auth`, `projects`, `keys`, `v1`, `tests/` (fakes, unit/API, test_pg_integration, test_pg_http).
- Зависимость: `psycopg[binary]==3.3.6`. `DATABASE_URL` → `postgresql+psycopg`; api запускает `alembic upgrade head` при старте.
- Проверено: `pytest -q` — 44 passed с `TEST_DATABASE_URL`; 37 passed / 7 skipped без БД.
- Проверено: `docker compose up -d --build` — 6 сервисов healthy; `alembic current` = `0001` (head).
- Проверено curl: ключ A → `/A` 200, `/B` 403, после revoke → `/A` 401; `MCP_TOKEN` + `project_id` → 200; пустые `EMAIL`/`PASSWORD` → 503 на login.
- Проверено: коммит сессии выполняется до ответа (`Depends(..., scope="function")`, fastapi 0.153.0).
- CI: job `backend` поднимает `postgres:17.6` и запускает PG-тесты с `TEST_DATABASE_URL`.
- Не выполнено / Открыто:
  - Из `.env.example` удалены `BACKEND_*`, `AI_*`, `DATABASE_URL`, `QDRANT_URL`, `TYPESENSE_*` — решить, нужны ли они.
  - Login при недоступной БД даёт 500 вместо 503.
  - Просроченные сессии не очищаются.
  - Шаг CI на GitHub не прогонялся.

### Этап 5. PostgreSQL job queue and worker lifecycle
Status: Done
- Созданы/изменены: миграция `0002_ingestion_jobs.py` (`0002`) с таблицей `ingestion_jobs`, четырьмя check-constraint и индексами по `project_id`, для claim и stale lease; `JobRow`, `JobRepository` Protocol и `SqlJobRepository` с `FOR UPDATE SKIP LOCKED` и fenced `update_leased`.
- Создан `JobQueueService` (enqueue/claim/complete/fail/release/requeue_stale) с экспоненциальным backoff `min(5·2^(n-1), 300)` с; при чужом worker или потере lease возвращается `Conflict`. Настройки: `JOB_BACKOFF_BASE_SECONDS=5`, `JOB_BACKOFF_MAX_SECONDS=300`; сохранены `JOB_LEASE_SECONDS=30`, `JOB_POLL_INTERVAL_SECONDS=1.0`, `JOB_MAX_ATTEMPTS=5`.
- Worker: `DEFAULT_HANDLERS` с `noop`; неизвестный тип задачи завершается как `failed`, ошибки БД не останавливают процесс, SIGTERM/SIGINT приводят к graceful shutdown и release задачи; heartbeat path задаётся `WORKER_HEARTBEAT_PATH` (default `/tmp/worker-heartbeat`). В Compose worker зависит от healthy-сервисов, включая api; `restart: unless-stopped`, `stop_grace_period: 30s`.
- Созданы тесты `test_job_queue.py`, `test_worker.py`, `test_pg_jobs.py`, `InMemoryJobRepository`; `ingestion_jobs` добавлена в `APP_TABLES`.
- Проверено: `pytest -q` — 70 passed с `TEST_DATABASE_URL` (PostgreSQL 17.6), 56 passed / 14 skipped без БД. `alembic upgrade head → downgrade base → upgrade head` — OK, `alembic_version` = `0002`; `docker compose config -q` — OK.
- Проверено: `docker compose up -d --build api worker` — worker healthy; `docker compose stop worker` — в логах `stop requested (signal 15)`, `stopped`. В PostgreSQL-тесте 4 потока обработали 50 задач каждый, каждая задача взята ровно один раз; subprocess с SIGTERM завершился с кодом 0.
- Не выполнено / Открыто: продление lease для долгих обработчиков (этап 6); счётчик `requeue_stale` включает задачи, переведённые в `failed`; heartbeat не перехватывает `OSError` при недоступном пути; нет тестов конкурентного `requeue_stale` и round-trip JSONB payload; `ruff`/`pyflakes` не установлены. Связь `failed` с источником появится вместе с `sources` на этапе 6; индексация и reindex ещё не реализованы.

### Этап 6. File, text, and link ingestion with versions
Status: Done
- 6a (сделано): миграция `0003_sources_versions_chunks.py` (`sources`, `source_versions`, `chunks`), `SourceRepository`/`VersionRepository`/`ChunkRepository` + `Sql*`, порты `BlobStore`/`ContentExtractor`/`VectorIndex`/`TextIndex`/`Embedder` (`ports.py`), `FsBlobStore` (`blobs.py`), `extract.py` (`.txt`, `.md`, `.pdf`, `.docx`, HTML по media type; `fetch_url` с лимитом байт), `chunking.py`.
- 6a (сделано): `services.py` разбит на пакет `vector_core/services/` (`projects`, `keys`, `auth`, `jobs`, `sources`, `common`) с реэкспортом из `__init__.py` — существующие импорты не менялись. `SourceService`: `add_text/add_file/add_url/edit/delete/list_for_project/get/versions/reindex`; оригинал в blob по ключу `{project_id}/{source_id}/{version}.{ext}`, извлечённый текст в `source_versions.extracted_text`, версия и источник `pending`, в очередь ставится job `index_version` с payload `{source_id, version_id, version}`. Недоступная/неподдерживаемая ссылка → источник `failed` с `error_message`, версия не создаётся; повторный `POST /url` с той же ссылкой повторяет загрузку на том же источнике, а если у ссылки уже есть версии — 409. Лимит `MAX_UPLOAD_BYTES` (10 МиБ) → 413 (`payload_too_large`), расширение вне `txt/md/pdf/docx` → 422 (`unsupported_format`). `delete` чистит чанки, версии (cascade) и blob-префикс.
- 6a (сделано): согласованность blob-хранилища и транзакции — `TransactionHooks` (`services/common.py`) на запрос: blob удаляется только после успешного `commit` (сбой commit при delete → 500, строка и файл остаются), blob, записанный в откаченной транзакции, удаляется (`after_rollback`); `get_session` в `deps.py` делает `rollback` + хуки при исключении. `ProjectService.delete` удаляет blob-префикс проекта так же — только после успешного commit (PG-тест: сбой commit → 500, проект, источник и файл остаются; повтор → 204, файл удалён). `edit` (включая правку только `title`), `reindex`, `delete` и повтор `add_url` берут `SELECT … FOR UPDATE` на строку источника (`SourceRepository.get_for_update`): параллельные `PATCH text` получают разные номера версий (PG-тест: 4 параллельных правки → версии 2–5, `content_hash` каждой совпадает с файлом), конкурирующие title-edit и `reindex` реально ждут блокировку (PG-тест пр��веряет ожидание и итог `title=Renamed`, `status=pending`, `error_message=None`). `TransactionHooks` обязателен в конструкторах `SourceService`/`ProjectService`; режим `immediate=True` создаётся только в `tests/fakes.py`. `POST …/sources/file` — синхронный эндпоинт (чтение, извлечение и SQL в threadpool, не блокируют event loop).
- 6a (сделано): `routers/sources.py` под `require_owner`: `POST /api/projects/{id}/sources/text|file|url`, `GET /sources`, `GET/PATCH/DELETE /sources/{sid}`, `GET /sources/{sid}/versions`, `POST /sources/{sid}/reindex` (202). Тело файла читается блоками 1 МиБ, при превышении лимита чтение прекращается (ограничивает память; multipart к этому моменту уже принят Starlette во временный файл). В `deps.py` провайдеры `get_blob_store` (`FsBlobStore(BLOB_DIR)`), `get_extractor`; `Services` дополнен `jobs`, `sources`. Compose: volume `blobs:/data/blobs` для `api` и `worker`, `BLOB_DIR=/data/blobs` фиксирован (совпадает с volume), `MAX_UPLOAD_BYTES` переопределяется через `.env`. `Dockerfile.backend` ставит зависимости из `uv.lock` (`uv export --frozen --no-dev` → `uv pip install --system`, образ `ghcr.io/astral-sh/uv:0.12.17`), параллельный список пакетов убран; `uvicorn==0.35.0` добавлен в `pyproject.toml`. `Settings.from_env` читает `CHUNK_SIZE_CHARS`/`CHUNK_OVERLAP_CHARS`.
- Проверено: `pytest -q` — 121 passed / 36 skipped без БД; 157 passed с `TEST_DATABASE_URL` (PostgreSQL 17.6 из compose, подключение по IP контейнера, порт наружу не проброшен). `alembic upgrade head → downgrade base → upgrade head → downgrade base` — OK. `docker compose config -q` — OK. `uvx ruff@0.14.0 check packages apps tests` — All checks passed. `docker compose up -d --build` — все 6 сервисов `healthy`, `GET /health` → 200, в образе импортируются `app.main` и `worker.main`. Smoke через login не выполнялся: `EMAIL`/`PASSWORD` в локальном `.env` пусты (vault недоступен), API отвечает `auth_not_configured`. Ревью кода 6a: PASS, блокеров нет.
- Известные компромиссы 6a (не блокеры): `fetch_url` выполняется внутри открытой транзакции; multipart целиком принимается Starlette до проверки лимита; при неоднозначном commit (сервер закоммитил, клиент получил ошибку соединения) `after_rollback` удалит blob уже закоммиченной версии — учесть в worker на 6b.
- 6b (сделано): `services/indexing.py` (`IndexingService`: `index_version`, `reindex_project`, `purge_source`); адаптеры `indexes/qdrant.py` (`qdrant-client==1.19.1`, коллекция `chunks`, COSINE, keyword payload-индексы) и `indexes/typesense.py` (`typesense==2.1.0`, коллекция `chunks`); in-memory индексы для тестов. Публикация версии в транзакции (`published`, `vector_indexed`, `current_version`), предыдущая версия удаляется из PG и индексов после commit; сбой → версия и источник `failed` с `error_message`, исключение уходит в retry очереди; вытесненная версия → `failed` с `superseded by version N`. `SourceService.delete` чистит индексы; при недоступности индекса ставит job `purge_source`. Worker: обработчики `index_version`, `reindex_project`, `purge_source`; `LeaseExtender` продлевает аренду (интервал lease/3); heartbeat не падает при ошибке записи. Без `AI_API_KEY` — `NullEmbedder`, только полнотекстовый индекс.
- Проверено: `pytest -q` — 121 passed / 36 skipped без БД; с `TEST_DATABASE_URL` (отдельная база `vector_test`) — 184 passed, 2 skipped; интеграционные тесты Qdrant 1.19.2 и Typesense 30.2 против контейнеров — 2 passed; `uvx ruff@0.14.0 check packages apps tests` — All checks passed; `docker compose config -q` — OK; `docker compose up -d --build api worker qdrant typesense` — сервисы healthy, тома сохранены (пересоздавать `qdrant_data`/`typesense_data` не потребовалось).
- Проверено сквозным путём в стенде: через сервисы API-контейнера создан служебный проект и текстовый источник; worker перевёл источник в `indexed` с `current_version=1` (коллекция Typesense создана, upsert 200); служебные данные удалены.
- Включение векторной индексации (шаг 2 плана, 2026-10-21): провайдер Polza (`https://polza.ai/api/v1`), модель `openai/text-embedding-3-small` (1536d), `AI_API_KEY` задан в окружении api и worker. Коллекции `chunks` в Qdrant не было (404), удалять не потребовалось; `docker compose up -d --build api worker` — сервисы healthy.
- Проверено: служебный проект `smoke-vector-indexing` и текстовый источник → `indexed`, `current_version=1`, версия `published`, `vector_indexed=true`; Qdrant count по `project_id` = 1, размерность 1536; `POST /api/v1/search` → 200, 1 хит, `warning: null`; job `reindex_project` → `succeeded`. Других проектов и непроиндексированных версий в базе не было; служебный проект оставлен в стенде.
- Проверено: `pytest -q` — 207 passed, 38 skipped (PostgreSQL-тесты без `TEST_DATABASE_URL`); `uvx ruff@0.14.0 check packages apps tests` — All checks passed.
- Не выполнено / Открыто: smoke через HTTP login — `EMAIL`/`PASSWORD` не заданы в окружении, vault недоступен; инструмент `add_note` через `/mcp` по HTTP не вызывался (источник создан через сервисы контейнера api); индексация PDF, DOCX и ссылки не проверялась; компромисс 6a при неоднозначном commit не закрыт; клиент Typesense 2.1.0 выдаёт DeprecationWarning на сервере v30 (новые API не используются).

### Этап 7. Hybrid search and context endpoints
Status: Done
- Сделано: `services/search.py` — гибридный поиск с RRF (k=60), таймаут векторной ветки; при сбое векторной ветки — warning `vector_search_unavailable`, при сбое полнотекстовой — `text_search_unavailable`; если обе недоступны — 503 `search_unavailable`.
- Сделано: `POST /api/v1/search` и `POST /api/v1/context` для owner, API-ключа проекта и `MCP_TOKEN`; `project_id` обязателен.
- Сделано: MCP на `/mcp` (Bearer `MCP_TOKEN`) с инструментами `search`, `context`, `add_note`; login при недоступной БД → 503 `db_unavailable`; `purge_expired` выполняется при login.
- Сделано: `apps/web/nginx.conf` проксирует `/api/` (`client_max_body_size 11m`) и `/mcp`.
- Проверено: `pytest -q` без БД — 211+ passed; с `TEST_DATABASE_URL` (`vector_test`) — 258 passed, 2 skipped; `uvx ruff@0.14.0 check` — All checks passed.
- Проверено live на localhost:8000: `/mcp` без токена → 401; `tools/list` = {search, context, add_note}; `add_note` → indexed, `current_version=1`; MCP `search` и `/api/v1/search` → 200, есть хит, `warning: null`; `/api/v1/context` → 200; запрос без `project_id` → 422.
- Проверено: `nginx`-конфиг проверен во временном контейнере `nginx:1.29.1-alpine`.
- Не выполнено / Открыто: web-образ не собирается (`router.tsx` импортирует отсутствующий `@/pages/KnowledgePage`), поэтому проксирование через сервис web не развёрнуто.

### Этап 7a. CLI vector sync
Status: Done
- Сделано: CLI `vector` (entry point в `pyproject.toml`, пакет `packages/cli/vector_cli`) с командами `sync`, `reindex`, `search`. `sync` сканирует локальный каталог и отправляет его в API; флаги: `--prune`, `--exclude` (повторяемый, fnmatch по относительному пути или его компоненту), `--dry-run`, `--sync-root`, `--max-bytes`.
- Сделано: ручки под `X-Api-Key` в `apps/api/app/routers/sync.py`: `POST /api/v1/projects/{id}/sources/sync` (план: `to_upload`, `unchanged`, `to_prune`), `POST /api/v1/projects/{id}/sources/sync/file` (загрузка файла, 201 или 200), `DELETE /api/v1/projects/{id}/sources/{sid}?sync_root=` (204), `POST /api/v1/projects/{id}/reindex` (202, job `reindex_project`).
- Сделано: `origin_key` синхронизированного источника хранится в БД как `sync://{quote(sync_root)}/{rel}`; prune выбирает только источники с тем же `sync_root`, поэтому материалы вне своей привязки не затрагиваются. Prune пропускается при ошибках скана или загрузки.
- Проверено: `pytest -q` без БД — 223 passed, 40 skipped; с `TEST_DATABASE_URL` — 261 passed, 2 skipped; `uvx ruff@0.14.0` — OK; `uv lock --check` — OK; в чистом venv `pip install -e .` → `vector --help` работает.
- Проверено live на стенде: `sync` → uploaded=3; повторный `sync` → uploaded=0; изменение файла → uploaded=1 (версия 2); удаление файла + `--prune` → pruned=1; `vector search` → хиты; `vector reindex` → job `reindex_project` succeeded. Тестовый ключ отозван.
- Не проверено живьём: ответы 403 и 404 на эндпоинтах sync (покрыто тестами).

### Этап 7b. Web UI
Status: Done
- Сделано: страницы Login (Bearer-токен из `/api/auth/login` в localStorage), Projects (create/rename/delete с `ConfirmDialog`), Knowledge `/projects/:projectId` (текст/файл/ссылка, статусы с `error_message`, версии, редактирование текста, удаление, API-ключи с показом секрета один раз и отзывом, тестовый поиск с warning), `DataState`; nginx проксирует `/api/` и `/mcp`.
- Закрывает открытое в этапе 7: web-образ собирается и развёрнут.
- Проверено: `npm ci && npm run build` в `apps/web` — OK (предупреждение о чанке 518 kB).
- Проверено: `docker compose up -d --build web` — web healthy, порт 8081; smoke: `/` → 200, `/projects/xyz` → 200 (SPA fallback), `/api/auth/me` без токена → 401, `/mcp` без токена → 401.
- Не выполнено / Открыто: вход и CRUD через UI с реальными `EMAIL`/`PASSWORD` не проверены — переменные не заданы, vault недоступен.

### Этап 8. Финализация
Status: Done
- Сделано: `ruff==0.14.0` в dev-extra и шаг `ruff check packages apps tests` в CI; Node `22.18.0` в CI совпадает с `apps/web/Dockerfile`; `runs-on: ubuntu-24.04`.
- Сделано: удаление проекта сначала очищает Qdrant и Typesense (`delete_by_project`); при недоступном индексе ответ 503 `index_unavailable`, проект остаётся. Промежуточная неудача индексации переводит версию в `pending` с `error_message`; `failed` ставится только на последней попытке.
- Проверено: `pytest -q` без БД — 229 passed, 40 skipped; с `TEST_DATABASE_URL` — 267 passed, 2 skipped; `ruff` — OK; `uv lock --check` — OK; `npm ci && npm run build` на Node 22.18.0 — OK; `docker compose up -d --build` — 6 сервисов healthy.
- Проверено live: login 200 по `EMAIL`/`PASSWORD`; MCP `tools/list` = `search`, `context`, `add_note`; `add_note` → indexed; PDF и DOCX через `vector sync` (uploaded=2) и URL через `POST /sources/url` — indexed, published, `vector_indexed=true`; поиск по маркерам — свои файлы первыми, `warning: null`; удаление PDF, DOCX и URL → хиты исчезли из `/api/v1/search` и `/api/v1/context`; временный ключ отозван → 401.
- Не выполнено / Открыто: `npm audit` сообщает уязвимости в зависимостях web (не исправлялись); Web UI и Reproducibility не покрыты автотестами (только build и ручные проверки); при частичной очистке индексов проекта восстановление через reindex; клиент Typesense 2.1.0 выдаёт DeprecationWarning; служебный проект `smoke-vector-indexing` оставлен на стенде.
