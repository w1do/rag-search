# PLAN.md — Vector

Этапы из Delivery Plan. Статус: `Planned | In progress | Done`. Отмечаются только факты, подтверждённые проверками.

### Этап 1. Bootstrap monorepo skeleton and pinned tooling
Status: Done
- Проверено: `pip install -e ".[dev]"` в чистом venv; импорты `app`, `worker`, `vector_core`, `vector_cli`; `npm run build` в `apps/web`.

### Этап 2. Governance documents and vault/memory
Status: In progress
- Созданы: `CLAUDE.md`, `AGENTS.md`, `PLAN.md`, `GOAL.md`.
- Не выполнено: инициализация памяти проекта (`memory`), синхронизация через vault MCP.

### Этап 3. Docker Compose stack and health checks
Status: Planned
- Цель: `docker compose up -d` поднимает весь стек без ручной настройки; вход через `.env` (`EMAIL`, `PASSWORD`, `MCP_TOKEN`).

### Этап 4. Core domain, database, projects with API keys
Status: Planned
- Включает авторизацию по `EMAIL`/`PASSWORD` и `MCP_TOKEN` для агентов.

### Этап 5. PostgreSQL job queue and worker lifecycle
Status: Planned

### Этап 6. File, text, and link ingestion with versions
Status: Planned

### Этап 7. Hybrid search and context endpoints
Status: Planned
