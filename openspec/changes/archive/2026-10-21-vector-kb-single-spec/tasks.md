## 1. Core and repositories
- [x] 1.1 Define entities and repository Protocols in `vector_core`
- [x] 1.2 Schema migrations and SQL repository implementations
- [x] 1.3 In-memory fakes and repository tests

## 2. Auth and projects
- [x] 2.1 `.env` auth (EMAIL/PASSWORD, MCP_TOKEN)
- [x] 2.2 Project service and router (CRUD)
- [x] 2.3 API key issue/revoke and verification

## 3. Knowledge and worker
- [x] 3.1 Source service: file, text, link, versions, delete
  - [x] 3.1.1 `SourceService` + `/api/projects/{id}/sources/*`, blob-хранилище, извлечение текста, job `index_version` (этап 6a)
  - [x] 3.1.2 Публикация версий и синхронное удаление из индексов (этап 6b; финализация: PDF, DOCX, URL опубликованы и удалены из поиска и контекста)
- [x] 3.2 Job queue and worker indexing
  - [x] 3.2.1 PostgreSQL job queue and worker lifecycle (этап 5)
  - [x] 3.2.2 Indexing and reindexing (этап 6; reindex из PostgreSQL-чанков)

## 4. Search
- [x] 4.1 Search/context endpoints with required `project_id`
- [x] 4.2 Full-text fallback

## 5. Web UI
- [x] 5.1 API client and shared components
- [x] 5.2 Projects page
- [x] 5.3 Knowledge page with test search

## 6. Verification
- [x] 6.1 pytest for repositories and API
- [x] 6.2 `npm run build` in `apps/web`
